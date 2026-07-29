"""Proveedor de datos de mercado basado en Yahoo Finance (vía yfinance)."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pandas as pd
import yfinance as yf

from atlas.data.models.quote import Quote
from atlas.data.providers.base import DataProvider, ProviderError, QuoteNotFoundError


class YahooFinanceProvider(DataProvider):
    """Obtiene cotizaciones consultando la API pública de Yahoo Finance."""

    def get_quote(self, symbol: str) -> Quote:
        """Consulta yfinance y normaliza la respuesta a un Quote."""
        info = self._fetch_info(symbol)

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
            float_shares=info.get("floatShares"),
            average_volume=average_volume,
            relative_volume=self._calculate_relative_volume(volume, average_volume),
            timestamp=self._resolve_timestamp(info),
        )

    def get_history(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        """Descarga barras OHLCV históricas de Yahoo Finance."""
        try:
            history = yf.Ticker(symbol).history(period=period, interval=interval)
        except Exception as exc:
            raise ProviderError(f"Fallo al consultar historial de '{symbol}': {exc}") from exc

        if history.empty:
            raise QuoteNotFoundError(symbol)

        return history[["Open", "High", "Low", "Close", "Volume"]]

    def _fetch_info(self, symbol: str) -> Dict[str, Any]:
        """Descarga el diccionario `info` de yfinance para un símbolo."""
        try:
            info = yf.Ticker(symbol).info
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
