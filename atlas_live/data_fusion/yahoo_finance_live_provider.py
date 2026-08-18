"""YahooFinanceLiveProvider -- primer proveedor real del Data Fusion Engine.

REGLA DE ARQUITECTURA (declarada oficialmente, 2026-08-02, ver
DATA_FUSION_ENGINE_PROPUESTA.md, sección "REGLA DE ARQUITECTURA", y
DECISION_LOG.md): esta clase es el **único punto autorizado** para
construir un `Quote` en vivo dentro de `atlas_live` -- Single Source of
Truth para el precio. Ningún módulo nuevo debe llamar a `yfinance`
directamente ni instanciar `YahooFinanceProvider` (la clase base) por su
cuenta para obtener datos de mercado en vivo. Toda funcionalidad nueva
debe obtener su `Quote` exclusivamente vía
`DataCollector(YahooFinanceLiveProvider())`.

Excepciones ya existentes, anteriores a esta regla, documentadas como
deuda técnica (no resueltas acá, ver DECISION_LOG.md para el detalle
completo de cada una):
  - Hallazgo A: `atlas_live/memory/live_integration.py::_grade_pending()`
    usa un fetch independiente con `YahooFinanceProvider` (base, sin
    sesión) -- decisión explícita del usuario de no tocar Prediction
    Journal durante la corrección de precio.
  - Hallazgo B: los 5 motores de `/atlas` Core tienen un fallback
    dormido a `YahooFinanceProvider()` -- Core está congelado, no se
    toca en esta etapa.
  - Hallazgo C: `atlas/data/collectors/investigator.py` es código
    huérfano, nunca importado, no participa del flujo en vivo.
  - Hallazgo D: `atlas_live/backtest/` es un pipeline de backtesting
    independiente, no forma parte del flujo de precios en producción.

Causa raíz confirmada en vivo el 2026-08-02 (`yf.Ticker(symbol).info`,
mismo entorno): Yahoo Finance sí expone `preMarketPrice`, `postMarketPrice`
y `marketState` -- `YahooFinanceProvider._quote_from_info()`
(`/atlas` Core, congelado) nunca los lee, solo `regularMarketPrice`/
`currentPrice`. Por eso Atlas mostraba siempre el precio de sesión
regular sin importar la sesión de mercado real, lo que generaba la
confusión al compararlo con TradingView (que sí distingue sesión).

Esta clase **no modifica `/atlas` Core** -- hereda de `YahooFinanceProvider`
y sobrescribe únicamente `_quote_from_info()`, reutilizando el cálculo
existente (`super()._quote_from_info()`) para todo lo que no cambia
(volumen, sector, relative_volume, market_cap, etc.) y reemplazando solo
los campos de precio con la selección consciente de la sesión. Como
`get_quote()`/`get_quotes()` de la clase base ya llaman a
`self._quote_from_info(...)` de forma polimórfica, ambos quedan
corregidos automáticamente sin tener que sobrescribirlos.

Ver DATA_FUSION_ENGINE_PROPUESTA.md (raíz del repo) para el diseño
completo aprobado.
"""

import dataclasses
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from atlas.data.models.quote import Quote
from atlas.data.providers.yahoo_finance import YahooFinanceProvider

SOURCE_NAME = "yahoo_finance"

# Fallback documentado (requisito Nº3): si el estado de mercado no mapea
# a uno conocido, se trata como sesión regular -- nunca se inventa un
# premarket/afterhours que Yahoo no reportó explícitamente.
MARKET_STATE_TO_PRICE_TYPE: Dict[str, str] = {
    "PRE": "premarket",
    "PREPRE": "premarket",
    "REGULAR": "regular",
    "POST": "afterhours",
    "POSTPOST": "afterhours",
    "CLOSED": "regular",
}


def _select_session_price(info: Dict[str, Any], market_state: Optional[str]) -> Tuple[str, Optional[float], Optional[int]]:
    """Devuelve (price_type, precio, epoch) según `marketState`.

    Fallback documentado: si el tipo de precio esperado para la sesión
    detectada no viene con valor (ej. sesión PRE pero `preMarketPrice`
    ausente), se cae al precio de sesión regular y `price_type` pasa a
    "regular" -- la etiqueta siempre describe honestamente el dato que
    efectivamente se está usando, nunca la sesión que se esperaba."""
    regular_price = info.get("regularMarketPrice") or info.get("currentPrice")
    regular_time = info.get("regularMarketTime")

    normalized = MARKET_STATE_TO_PRICE_TYPE.get(market_state or "", "regular")

    if normalized == "premarket":
        premarket_price = info.get("preMarketPrice")
        if premarket_price is not None:
            return "premarket", premarket_price, info.get("preMarketTime")

    if normalized == "afterhours":
        afterhours_price = info.get("postMarketPrice")
        if afterhours_price is not None:
            return "afterhours", afterhours_price, info.get("postMarketTime")

    return "regular", regular_price, regular_time


class YahooFinanceLiveProvider(YahooFinanceProvider):
    """Mismo proveedor de Core, con selección de precio consciente de la
    sesión de mercado (Regular/Premarket/After-hours) -- ver docstring
    del módulo. Se adapta automáticamente en cada consulta (requisito
    Nº8): no cachea `marketState` entre ciclos, cada llamada relee el
    estado actual de Yahoo."""

    def _quote_from_info(self, symbol: str, info: Dict[str, Any]) -> Quote:
        base = super()._quote_from_info(symbol, info)

        market_state = info.get("marketState")
        price_type, selected_price, epoch = _select_session_price(info, market_state)

        # STALE_SESSION_FALLBACK (Fase 8, 2026-08-18, caso real PTEN): si se
        # esperaba premarket/after-hours (según `market_state`) y no había
        # ese precio, `_select_session_price` ya cayó a "regular" -- acá se
        # marca esa caída explícitamente. Nunca `True` cuando `market_state`
        # ya es REGULAR/CLOSED: ahí "regular" es la sesión correcta, no un
        # fallback.
        expected_price_type = MARKET_STATE_TO_PRICE_TYPE.get(market_state or "", "regular")
        stale_session_fallback = expected_price_type != "regular" and price_type == "regular"

        # `change_percent` se recalcula con la MISMA fórmula que ya usa
        # `YahooFinanceProvider` (precio actual vs. cierre del día
        # anterior) -- no se redefine su significado, solo se le pasa un
        # "precio actual" más preciso (el de la sesión real) en vez de
        # siempre el de sesión regular.
        if selected_price is not None and base.previous_close:
            change_percent = ((selected_price - base.previous_close) / base.previous_close) * 100
        else:
            change_percent = base.change_percent

        if epoch:
            as_of = datetime.fromtimestamp(epoch, tz=timezone.utc)
        else:
            as_of = base.timestamp

        return dataclasses.replace(
            base,
            last_price=selected_price if selected_price is not None else base.last_price,
            change_percent=change_percent,
            timestamp=as_of,
            source=SOURCE_NAME,
            price_type=price_type,
            market_state=market_state,
            price_regular=info.get("regularMarketPrice") or info.get("currentPrice"),
            price_premarket=info.get("preMarketPrice"),
            price_afterhours=info.get("postMarketPrice"),
            stale_session_fallback=stale_session_fallback,
        )
