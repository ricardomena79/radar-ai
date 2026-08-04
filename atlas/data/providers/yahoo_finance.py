"""Proveedor de datos de mercado basado en Yahoo Finance (vía yfinance).

Responsabilidad única: obtener datos crudos de Yahoo Finance y normalizarlos
a Quote. No calcula indicadores, no aplica scoring ni filtra símbolos.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from atlas.data.models.quote import (
    SESSION_AFTERHOURS,
    SESSION_CLOSED,
    SESSION_PREMARKET,
    SESSION_REGULAR,
    Quote,
)
from atlas.data.providers.base import (
    DataProvider,
    ProviderError,
    QuoteNotFoundError,
    RateLimitError,
)
from atlas.data.providers.base import call_with_timeout as _shared_call_with_timeout

# Yahoo Finance reporta el estado de la sesión en `marketState`. Fuera de
# la sesión regular, `regularMarketPrice` queda congelado en el último
# precio de la sesión regular anterior -- no se actualiza con el
# premarket ni el afterhours. Este mapa normaliza esos valores crudos al
# vocabulario propio de Atlas (ver atlas.data.models.quote).
_MARKET_STATE_TO_SESSION = {
    "PRE": SESSION_PREMARKET,
    "PREPRE": SESSION_PREMARKET,
    "REGULAR": SESSION_REGULAR,
    "POST": SESSION_AFTERHOURS,
    "POSTPOST": SESSION_AFTERHOURS,
    "CLOSED": SESSION_CLOSED,
}

# Tiempo máximo de espera para una sola llamada de red a Yahoo Finance
# (.info o .history). Bajo contención -- ej. el escaneo de fondo de Atlas
# Live compitiendo con una consulta puntual -- Yahoo puede dejar una
# conexión colgada sin devolver error ni éxito; sin este límite esa espera
# es indefinida. Propio de este proveedor (independiente del de cualquier
# otro), aunque reutiliza el mecanismo compartido de atlas.data.providers.base.
NETWORK_TIMEOUT_SECONDS = 15.0


def _call_with_timeout(func, symbol: str, what: str):
    return _shared_call_with_timeout(
        func, symbol, what, timeout_seconds=NETWORK_TIMEOUT_SECONDS, provider_name="Yahoo Finance"
    )


class YahooFinanceProvider(DataProvider):
    """Obtiene cotizaciones consultando la API pública de Yahoo Finance."""

    def get_quote(self, symbol: str) -> Quote:
        """Consulta yfinance y normaliza la respuesta a un Quote."""
        info = self._fetch_info(symbol)
        return self._quote_from_info(symbol, info)

    def get_quotes(self, symbols: List[str]) -> List[Quote]:
        """Obtiene cotizaciones para varios símbolos compartiendo una única sesión HTTP.

        Pensado para escanear listas grandes de símbolos (el Universo Racional
        completo): un símbolo inválido o sin datos no interrumpe al resto.
        """
        if not symbols:
            return []

        tickers = yf.Tickers(" ".join(symbols)).tickers

        quotes: List[Quote] = []
        for symbol in symbols:
            ticker = tickers.get(symbol) or tickers.get(symbol.upper())
            if ticker is None:
                continue
            try:
                info = _call_with_timeout(lambda t=ticker: t.info, symbol, "cotización")
                if not info:
                    continue
                quotes.append(self._quote_from_info(symbol, info))
            except YFRateLimitError as exc:
                raise RateLimitError(f"Yahoo Finance limitó la tasa de consultas: {exc}") from exc
            except (ProviderError, QuoteNotFoundError):
                continue

        return quotes

    def _quote_from_info(self, symbol: str, info: Dict[str, Any]) -> Quote:
        """Normaliza el diccionario `info` de yfinance a un Quote.

        Fuera de la sesión regular, `regularMarketPrice` queda congelado en
        el cierre de la sesión regular anterior: usarlo tal cual durante el
        premarket o el afterhours haría ver un precio y un cambio % que no
        corresponden al momento actual. Por eso se elige el precio y la
        base de comparación según `marketState`.

        Importante: fuera de la sesión regular, `regularMarketPrice` ES el
        cierre regular más reciente (el de "ayer" visto desde el
        premarket). `regularMarketPreviousClose` es el cierre de la
        sesión anterior a esa -- un día más atrás. Por eso el premarket se
        compara contra `regularMarketPrice`, no contra
        `regularMarketPreviousClose` (verificado contra el
        `preMarketChangePercent` que reporta el propio Yahoo Finance).
        """
        regular_price = info.get("regularMarketPrice") or info.get("currentPrice")
        previous_close = info.get("regularMarketPreviousClose") or info.get("previousClose")

        if regular_price is None or previous_close is None:
            raise QuoteNotFoundError(symbol)

        session = _MARKET_STATE_TO_SESSION.get(info.get("marketState"), SESSION_REGULAR)
        last_price = regular_price
        baseline_price = previous_close

        if session == SESSION_PREMARKET and info.get("preMarketPrice") is not None:
            last_price = info["preMarketPrice"]
            baseline_price = regular_price  # el cierre regular más reciente
        elif session == SESSION_AFTERHOURS and info.get("postMarketPrice") is not None:
            last_price = info["postMarketPrice"]
            baseline_price = regular_price  # el cierre oficial de hoy

        volume = info.get("regularMarketVolume") or info.get("volume")
        average_volume = info.get("averageVolume") or info.get("averageDailyVolume10Day")

        return Quote(
            symbol=symbol,
            name=info.get("longName") or info.get("shortName"),
            last_price=last_price,
            change_percent=self._calculate_change_percent(last_price, baseline_price),
            volume=volume,
            open=info.get("regularMarketOpen"),
            high=info.get("regularMarketDayHigh"),
            low=info.get("regularMarketDayLow"),
            previous_close=previous_close,
            market_cap=info.get("marketCap"),
            sector=info.get("sector"),
            industry=info.get("industry"),
            float_shares=info.get("floatShares"),
            average_volume=average_volume,
            relative_volume=self._calculate_relative_volume(volume, average_volume),
            timestamp=self._resolve_timestamp(info),
            session=session,
        )

    def get_history(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        """Descarga barras OHLCV históricas de Yahoo Finance."""
        try:
            history = _call_with_timeout(
                lambda: yf.Ticker(symbol).history(period=period, interval=interval),
                symbol,
                "historial",
            )
        except YFRateLimitError as exc:
            raise RateLimitError(f"Yahoo Finance limitó la tasa de consultas: {exc}") from exc
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"Fallo al consultar historial de '{symbol}': {exc}") from exc

        if history.empty:
            raise QuoteNotFoundError(symbol)

        return history[["Open", "High", "Low", "Close", "Volume"]]

    def _fetch_info(self, symbol: str) -> Dict[str, Any]:
        """Descarga el diccionario `info` de yfinance para un símbolo."""
        try:
            info = _call_with_timeout(lambda: yf.Ticker(symbol).info, symbol, "cotización")
        except YFRateLimitError as exc:
            raise RateLimitError(f"Yahoo Finance limitó la tasa de consultas: {exc}") from exc
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"Fallo al consultar Yahoo Finance para '{symbol}': {exc}") from exc

        if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
            raise QuoteNotFoundError(symbol)

        return info

    @staticmethod
    def _calculate_change_percent(last_price: float, previous_close: float) -> Optional[float]:
        """Calcula el cambio porcentual localmente en vez de confiar en el campo del proveedor."""
        if not previous_close:
            return None
        return ((last_price - previous_close) / previous_close) * 100

    @staticmethod
    def _calculate_relative_volume(
        volume: Optional[int], average_volume: Optional[int]
    ) -> Optional[float]:
        """Volumen relativo = volumen actual / volumen promedio."""
        if not volume or not average_volume:
            return None
        return volume / average_volume

    @staticmethod
    def _resolve_timestamp(info: Dict[str, Any]) -> datetime:
        """Convierte el epoch de Yahoo Finance a datetime UTC; usa la hora actual como respaldo."""
        epoch = info.get("regularMarketTime")
        if epoch:
            return datetime.fromtimestamp(epoch, tz=timezone.utc)
        return datetime.now(timezone.utc)
