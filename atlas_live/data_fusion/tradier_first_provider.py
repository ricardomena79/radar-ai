"""Adaptador `DataProvider` que unifica la Cabina con CAPA 1 (Fase 5, 2026-08-17).

Hasta esta fase, `atlas_live/scan_worker.py` (tarjetas de la Cabina: Dashboard,
Oportunidad del día, etc.) usaba `get_default_provider()` (Yahoo+Finnhub,
`atlas_live/data_fusion/registry.py`) -- un pipeline de datos COMPLETAMENTE
separado del que ya usa el radar en vivo (`atlas_live/radar/radar_worker.py`,
Tradier primero vía `fetch_universe_quotes()`). Ese diagnóstico (dos
pipelines nunca fusionados) es el hallazgo central de la sesión 2026-08-17
que motiva este archivo.

`TradierFirstProvider` implementa el mismo contrato `DataProvider` que ya
espera `DataCollector`, así que reemplazar `get_default_provider()` por
`TradierFirstProvider()` en `scan_worker.py` no requiere tocar
`DataCollector` ni ningún motor de Atlas Core -- por dentro reutiliza
`fetch_universe_quotes()` (CAPA 1, ya construida y probada el 2026-08-14),
la MISMA función que ya usa el radar: Tradier primero, símbolo por símbolo,
y Yahoo/Finnhub solo para lo que Tradier no resuelva.

Se descartó a propósito agregar Tradier directo a la lista de
`MultiProvider` (`registry.py`): `MultiProvider.get_quotes()` solo cae al
siguiente proveedor si el proveedor ENTERO lanza `ProviderError` -- un
símbolo que Tradier simplemente no devuelve (no es un error, solo ausente
de la respuesta) se quedaría sin dato, sin intentar el respaldo.
`fetch_universe_quotes()` ya resuelve esto símbolo por símbolo.

`get_history()` es un caso distinto, tratado con cuidado: el endpoint de
historial de Tradier (`/v1/markets/history`, usado por
`TradierProvider.get_history()`) es EXCLUSIVAMENTE diario -- no existe
traducción válida para el pedido intradía real que ya hace Atlas Core
(`period="1d", interval="5m"`, usado por `decision_engine.py`,
`atlas_score.py` y `momentum_engine.py` para ATR/momentum). Tradier sí
tiene un endpoint de velas intradía (`get_intraday_timesales()`), pero es
un formato de parámetros distinto (`interval="1min"`, `start`/`end`, no
`period`) que ese mismo docstring marca como "NO se usa todavía en ningún
flujo de escaneo" -- traducirlo acá sería un cambio de alcance mayor, no
una simple sustitución de proveedor. Por eso: pedidos de historial DIARIO
(`interval` en `1d`/`daily`/`D`) pasan por Tradier primero, con el mismo
patrón de respaldo; cualquier otro `interval` (intradía) va directo al
proveedor de respaldo, igual que el comportamiento de hoy -- no se cambia
ni se arriesga el cálculo de indicadores intradía en este pase.
"""

from typing import List, Optional

import pandas as pd

from atlas.data.models.quote import Quote
from atlas.data.providers.base import DataProvider, ProviderError, QuoteNotFoundError
from atlas.data.providers.tradier_provider import TradierProvider
from atlas_live.data_fusion.registry import get_default_provider
from atlas_live.data_fusion.universe_quotes import build_tradier_provider, fetch_universe_quotes

_DAILY_INTERVALS = ("1d", "daily", "D")


class TradierFirstProvider(DataProvider):
    """Envuelve `fetch_universe_quotes()` (CAPA 1) con la interfaz `DataProvider`
    -- reutilizable donde hoy se pasa `get_default_provider()`."""

    def __init__(
        self,
        tradier_provider: Optional[TradierProvider] = None,
        fallback_provider: Optional[DataProvider] = None,
    ) -> None:
        self._tradier_provider = tradier_provider if tradier_provider is not None else build_tradier_provider()
        self._fallback_provider = fallback_provider if fallback_provider is not None else get_default_provider()

    def get_quote(self, symbol: str) -> Quote:
        result = fetch_universe_quotes(
            [symbol], tradier_provider=self._tradier_provider, fallback_provider=self._fallback_provider
        )
        quote = result.quotes.get(symbol)
        if quote is None:
            raise QuoteNotFoundError(symbol)
        return quote

    def get_quotes(self, symbols: List[str]) -> List[Quote]:
        result = fetch_universe_quotes(
            symbols, tradier_provider=self._tradier_provider, fallback_provider=self._fallback_provider
        )
        return [result.quotes[s] for s in symbols if s in result.quotes]

    def get_history(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        if interval not in _DAILY_INTERVALS or self._tradier_provider is None:
            return self._fallback_provider.get_history(symbol, period=period, interval=interval)
        try:
            return self._tradier_provider.get_history(symbol, period=period, interval=interval)
        except (ProviderError, QuoteNotFoundError):
            return self._fallback_provider.get_history(symbol, period=period, interval=interval)
