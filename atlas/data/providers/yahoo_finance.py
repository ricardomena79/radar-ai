"""Proveedor de datos de mercado basado en Yahoo Finance (vía yfinance).

Responsabilidad única: obtener datos crudos de Yahoo Finance y normalizarlos
a Quote. No calcula indicadores, no aplica scoring ni filtra símbolos.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from atlas.data.models.quote import Quote
from atlas.data.providers.base import DataProvider, ProviderError, QuoteNotFoundError, RateLimitError
from atlas.data.providers.base import call_with_timeout as _call_with_timeout

NETWORK_TIMEOUT_SECONDS = 15.0

logger = logging.getLogger(__name__)


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

        Excepción: un rate-limit de Yahoo (YFRateLimitError) no es un
        problema de ESE símbolo -- es del proveedor completo, y el resto
        del lote fallaría igual. Se convierte en RateLimitError y se
        relanza de inmediato (Investigación 6) en vez de seguir el lote,
        para que MultiProvider pueda intentar con el siguiente proveedor
        configurado. Ningún otro tipo de excepción cambia de
        comportamiento: sigue absorbiéndose por símbolo, como siempre.
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
                info = _call_with_timeout(
                    lambda t=ticker: t.info, symbol, "cotización",
                    timeout_seconds=NETWORK_TIMEOUT_SECONDS, provider_name="Yahoo Finance",
                )
                if not info:
                    continue
                quotes.append(self._quote_from_info(symbol, info))
            except YFRateLimitError as exc:
                raise RateLimitError(
                    f"Yahoo Finance devolvió rate-limit consultando '{symbol}': {exc}"
                ) from exc
            except (ProviderError, QuoteNotFoundError) as exc:
                logger.warning("Yahoo Finance falló para '%s' (%s) -- se omite, continúa el resto del lote.", symbol, exc)
                continue

        return quotes

    def _quote_from_info(self, symbol: str, info: Dict[str, Any]) -> Quote:
        """Normaliza el diccionario `info` de yfinance a un Quote."""
        last_price = info.get("regularMarketPrice") or info.get("currentPrice")
        previous_close = info.get("regularMarketPreviousClose") or info.get("previousClose")

        if last_price is None or previous_close is None:
            raise QuoteNotFoundError(symbol)

        volume = info.get("regularMarketVolume") or info.get("volume")
        average_volume = info.get("averageVolume") or info.get("averageDailyVolume10Day")

        return Quote(
            symbol=symbol,
            name=info.get("longName") or info.get("shortName"),
            last_price=last_price,
            change_percent=self._calculate_change_percent(last_price, previous_close),
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
        )

    def get_history(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        """Descarga barras OHLCV históricas de Yahoo Finance."""
        try:
            history = _call_with_timeout(
                lambda: yf.Ticker(symbol).history(period=period, interval=interval),
                symbol, "historial", timeout_seconds=NETWORK_TIMEOUT_SECONDS, provider_name="Yahoo Finance",
            )
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
            info = _call_with_timeout(
                lambda: yf.Ticker(symbol).info, symbol, "cotización",
                timeout_seconds=NETWORK_TIMEOUT_SECONDS, provider_name="Yahoo Finance",
            )
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
