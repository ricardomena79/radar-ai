"""TradierProvider -- proveedor PRINCIPAL de datos de mercado del Data Fusion
Engine (Fase Tradier, 2026-08-14).

Implementa `atlas.data.providers.base.DataProvider` -- mismo contrato que
`YahooFinanceLiveProvider`/`FinnhubProvider`, así que nada que ya consuma un
`DataProvider` necesita cambiar.

Validado en vivo antes de escribir este archivo (ver informe de sesión
2026-08-14): cobertura 95.1% cruda / 96.7% tras normalización sobre el
universo neto de 2.575 símbolos del universo Racional; ciclo completo del
universo en ~15s vía 11 requests de 250 símbolos, cero HTTP 429 ni
degradación; historial diario e intradía (1 min, con VWAP y volumen por
minuto) confirmados con datos reales.

Batching real: a diferencia de Finnhub (que no tiene endpoint de lote y
por eso `FinnhubProvider.get_quotes()` itera símbolo por símbolo),
`/v1/markets/quotes` de Tradier acepta cientos de símbolos separados por
coma en un solo request -- `get_quotes()` lo aprovecha en chunks de
`TRADIER_CHUNK_SIZE`.

Símbolo no reconocido: Tradier responde HTTP 200 con el símbolo listado en
`quotes.unmatched_symbols` (o simplemente ausente de `quotes.quote`) -- NO
un 404. Se trata como ausencia normal del símbolo en ese proveedor, nunca
como `ProviderError`; el símbolo normalizado que se le pasa a Tradier viene
siempre de `tradier_symbol_map.normalize()`, nunca se reformatea acá.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from atlas.data.models.quote import Quote
from atlas.data.providers.base import DataProvider, ProviderError, QuoteNotFoundError

SOURCE_NAME = "tradier"
QUOTES_PATH = "/v1/markets/quotes"
HISTORY_PATH = "/v1/markets/history"
TIMESALES_PATH = "/v1/markets/timesales"
REQUEST_TIMEOUT_SECONDS = 20

# Probado en vivo (2026-08-14): 2.575 símbolos en 11 chunks de 250 -- 200 OK
# en los 11, latencia 0.84-1.07s por chunk, sin rate-limit. No es un límite
# documentado de Tradier, es el tamaño validado empíricamente en esta sesión.
TRADIER_CHUNK_SIZE = 250

_DAYS_BY_PERIOD = {"1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "2y": 730, "5y": 1825}


def _to_quote(data: Dict[str, Any], symbol: str) -> Quote:
    last_price = data.get("last")
    if last_price is None:
        raise QuoteNotFoundError(symbol)

    epoch_ms = data.get("trade_date")
    timestamp = (
        datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc) if epoch_ms else datetime.now(timezone.utc)
    )

    volume = data.get("volume")
    average_volume = data.get("average_volume")
    relative_volume = None
    if volume is not None and average_volume:
        relative_volume = round(volume / average_volume, 4)

    return Quote(
        symbol=symbol,
        name=data.get("description"),
        last_price=last_price,
        change_percent=data.get("change_percentage"),
        volume=volume,
        open=data.get("open"),
        high=data.get("high"),
        low=data.get("low"),
        previous_close=data.get("prevclose"),
        average_volume=average_volume,
        relative_volume=relative_volume,
        timestamp=timestamp,
        source=SOURCE_NAME,
    )


class TradierProvider(DataProvider):
    """Proveedor principal basado en Tradier -- ver docstring del módulo
    para la evidencia de validación y las limitaciones reales."""

    def __init__(self, api_token: str, base_url: str = "https://api.tradier.com") -> None:
        if not api_token:
            raise ValueError("TradierProvider requiere un api_token no vacío.")
        self._api_token = api_token
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json"}

    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            response = requests.get(
                f"{self._base_url}{path}", params=params, headers=self._headers, timeout=REQUEST_TIMEOUT_SECONDS
            )
        except requests.RequestException as exc:
            raise ProviderError(f"Fallo de red al consultar Tradier {path}: {exc}") from exc
        if response.status_code != 200:
            raise ProviderError(f"Tradier devolvió HTTP {response.status_code} para {path}: {response.text[:300]}")
        return response.json()

    def get_quote(self, symbol: str) -> Quote:
        data = self._get(QUOTES_PATH, {"symbols": symbol, "greeks": "false"})
        quote_obj = data.get("quotes", {}).get("quote")
        if isinstance(quote_obj, list):
            quote_obj = quote_obj[0] if quote_obj else None
        if not quote_obj:
            raise QuoteNotFoundError(symbol)
        return _to_quote(quote_obj, quote_obj.get("symbol", symbol))

    def get_quotes(self, symbols: List[str]) -> List[Quote]:
        """Usa el endpoint de lote real de Tradier en chunks de
        `TRADIER_CHUNK_SIZE`. Símbolos no reconocidos se omiten en
        silencio (mismo criterio que `FinnhubProvider.get_quotes()`) --
        detectar cuáles faltan es responsabilidad del orquestador
        (`atlas_live/data_fusion/universe_quotes.py`), comparando el
        resultado contra la lista pedida."""
        quotes: List[Quote] = []
        for i in range(0, len(symbols), TRADIER_CHUNK_SIZE):
            chunk = [s for s in symbols[i:i + TRADIER_CHUNK_SIZE] if s]
            if not chunk:
                continue
            data = self._get(QUOTES_PATH, {"symbols": ",".join(chunk), "greeks": "false"})
            quote_field = data.get("quotes", {}).get("quote")
            if isinstance(quote_field, list):
                for item in quote_field:
                    if isinstance(item, dict) and item.get("symbol") and item.get("last") is not None:
                        quotes.append(_to_quote(item, item["symbol"]))
            elif isinstance(quote_field, dict) and quote_field.get("symbol") and quote_field.get("last") is not None:
                quotes.append(_to_quote(quote_field, quote_field["symbol"]))
        return quotes

    def get_history(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        """Barras diarias vía `/v1/markets/history`. Formato verificado en
        vivo (2026-08-14): `history.day` es una lista de
        {date, open, high, low, close, volume}."""
        days = _DAYS_BY_PERIOD.get(period, 180)
        end = datetime.now(timezone.utc).date()
        start = end - pd.Timedelta(days=days)
        tradier_interval = "daily" if interval in ("1d", "daily", "D") else interval

        data = self._get(
            HISTORY_PATH,
            {"symbol": symbol, "interval": tradier_interval, "start": start.isoformat(), "end": end.isoformat()},
        )
        rows = data.get("history", {}).get("day")
        if not rows:
            raise QuoteNotFoundError(symbol)
        if isinstance(rows, dict):
            rows = [rows]

        df = pd.DataFrame(
            {
                "Open": [r["open"] for r in rows],
                "High": [r["high"] for r in rows],
                "Low": [r["low"] for r in rows],
                "Close": [r["close"] for r in rows],
                "Volume": [r["volume"] for r in rows],
            },
            index=pd.to_datetime([r["date"] for r in rows], utc=True),
        )
        return df

    def get_intraday_timesales(
        self, symbol: str, interval: str = "1min", session_filter: str = "all",
        start: Optional[str] = None, end: Optional[str] = None,
    ) -> pd.DataFrame:
        """Velas intradía con volumen por minuto y VWAP vía
        `/v1/markets/timesales` -- formato verificado en vivo (2026-08-14).
        NO se usa todavía en ningún flujo de escaneo (CAPA 3, pendiente);
        expuesto acá para que Capa 3 lo reutilice sin reimplementar la
        llamada ni volver a validar el formato."""
        params: Dict[str, Any] = {"symbol": symbol, "interval": interval, "session_filter": session_filter}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        data = self._get(TIMESALES_PATH, params)
        rows = data.get("series", {}).get("data")
        if not rows:
            raise QuoteNotFoundError(symbol)
        if isinstance(rows, dict):
            rows = [rows]

        df = pd.DataFrame(
            {
                "Open": [r["open"] for r in rows],
                "High": [r["high"] for r in rows],
                "Low": [r["low"] for r in rows],
                "Close": [r["close"] for r in rows],
                "Volume": [r["volume"] for r in rows],
                "VWAP": [r.get("vwap") for r in rows],
            },
            index=pd.to_datetime([r["timestamp"] for r in rows], unit="s", utc=True),
        )
        return df
