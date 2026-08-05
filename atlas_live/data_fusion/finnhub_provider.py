"""FinnhubProvider -- segundo proveedor real del Data Fusion Engine (2026-08-04).

Implementa `atlas.data.providers.base.DataProvider` -- mismo contrato que
`YahooFinanceLiveProvider`, para que `DataCollector` (y por lo tanto todo
lo que ya consume `DataCollector`, ver la regla de arquitectura en
`DATA_FUSION_ENGINE_PROPUESTA.md`) no note ninguna diferencia entre
proveedores.

Verificado en vivo antes de escribir este archivo (2026-08-04, ver
`scripts/diagnostico_finnhub.py`): `GET /api/v1/quote?symbol=AAPL` con
`FINNHUB_API_KEY` real devuelve HTTP 200 con datos reales.

**Limitación real de la API gratuita de Finnhub, documentada explícitamente,
no oculta**: el endpoint `/quote` solo devuelve precio (`c`/`o`/`h`/`l`/`pc`)
y hora (`t`) -- a diferencia de Yahoo, **no incluye volumen, market cap,
sector, industria, float ni promedio de volumen**. `Quote.volume`,
`.average_volume`, `.relative_volume`, `.market_cap`, `.sector`,
`.industry`, `.float_shares` quedan en `None` cuando el dato viene de
Finnhub -- nunca se inventa un valor. Esto hace que Finnhub sirva hoy
como respaldo de PRECIO ante una falla de Yahoo, no como reemplazo
funcional completo (Radar Explosivo necesita `dollar_volume`/`relative_volume`,
que dependen de `volume`).

Finnhub tampoco distingue sesión (Regular/Premarket/After-hours) en este
endpoint -- `price_type`/`market_state` quedan en sus valores por defecto
("unknown"/`None`), igual que cualquier `Quote` sin ese dato.

Símbolo inexistente: Finnhub responde HTTP 200 con todos los campos en 0
(`{"c":0,"d":null,...}`), no un 404 -- verificado en vivo. Se trata como
`QuoteNotFoundError`, nunca como una cotización real de $0.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List

import pandas as pd
import requests

from atlas.data.models.quote import Quote
from atlas.data.providers.base import DataProvider, ProviderError, QuoteNotFoundError

QUOTE_URL = "https://finnhub.io/api/v1/quote"
CANDLE_URL = "https://finnhub.io/api/v1/stock/candle"
SOURCE_NAME = "finnhub"
REQUEST_TIMEOUT_SECONDS = 10


class FinnhubProvider(DataProvider):
    """Proveedor de respaldo basado en Finnhub -- ver docstring del módulo
    para las limitaciones reales (sin volumen, sin distinción de sesión)."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("FinnhubProvider requiere una API key no vacía.")
        self._api_key = api_key

    def get_quote(self, symbol: str) -> Quote:
        try:
            response = requests.get(
                QUOTE_URL,
                params={"symbol": symbol, "token": self._api_key},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"Fallo de red al consultar Finnhub para '{symbol}': {exc}") from exc

        if response.status_code != 200:
            raise ProviderError(f"Finnhub devolvió HTTP {response.status_code} para '{symbol}': {response.text}")

        data = response.json()
        current_price = data.get("c")

        # Finnhub no usa 404 para símbolos inexistentes -- devuelve 200
        # con todos los campos en 0/None (verificado en vivo, 2026-08-04).
        if current_price is None or current_price == 0:
            raise QuoteNotFoundError(symbol)

        previous_close = data.get("pc")
        epoch = data.get("t")
        timestamp = datetime.fromtimestamp(epoch, tz=timezone.utc) if epoch else datetime.now(timezone.utc)

        return Quote(
            symbol=symbol,
            name=None,  # Finnhub /quote no devuelve el nombre -- no se inventa.
            last_price=current_price,
            change_percent=data.get("dp"),  # ya viene calculado por Finnhub, no se recalcula.
            volume=None,       # /quote de Finnhub no incluye volumen -- limitación real, ver docstring.
            open=data.get("o"),
            high=data.get("h"),
            low=data.get("l"),
            previous_close=previous_close,
            average_volume=None,
            relative_volume=None,
            timestamp=timestamp,
            source=SOURCE_NAME,
        )

    def get_quotes(self, symbols: List[str]) -> List[Quote]:
        """Finnhub (tier gratuito) no tiene endpoint de lote -- itera
        get_quote() por símbolo, igual que la clase base de DataProvider
        documenta como comportamiento por defecto. Un símbolo inválido o
        sin datos no interrumpe al resto (mismo criterio que
        YahooFinanceProvider.get_quotes())."""
        quotes: List[Quote] = []
        for symbol in symbols:
            try:
                quotes.append(self.get_quote(symbol))
            except (ProviderError, QuoteNotFoundError):
                continue
        return quotes

    def get_history(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        """Barras diarias vía /stock/candle. `period`/`interval` se
        traducen de forma aproximada (mismos nombres que usa
        YahooFinanceProvider, para cumplir el mismo contrato) -- Finnhub
        usa resolución + rango de fechas unix, no strings de yfinance."""
        days_by_period = {"1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "2y": 730, "5y": 1825}
        days = days_by_period.get(period, 180)
        resolution = "D" if interval in ("1d", "D") else interval

        now = datetime.now(timezone.utc)
        start = now.timestamp() - days * 86400

        try:
            response = requests.get(
                CANDLE_URL,
                params={
                    "symbol": symbol,
                    "resolution": resolution,
                    "from": int(start),
                    "to": int(now.timestamp()),
                    "token": self._api_key,
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"Fallo de red al consultar historial de '{symbol}' en Finnhub: {exc}") from exc

        if response.status_code != 200:
            raise ProviderError(f"Finnhub devolvió HTTP {response.status_code} para historial de '{symbol}': {response.text}")

        data = response.json()
        if data.get("s") != "ok" or not data.get("c"):
            raise QuoteNotFoundError(symbol)

        df = pd.DataFrame({
            "Open": data["o"],
            "High": data["h"],
            "Low": data["l"],
            "Close": data["c"],
            "Volume": data["v"],
        }, index=pd.to_datetime(data["t"], unit="s", utc=True))
        return df
