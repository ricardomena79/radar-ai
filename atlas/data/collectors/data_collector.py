"""Punto de entrada único de adquisición de datos para el resto de Atlas."""

from typing import List

import pandas as pd

from atlas.data.models.quote import Quote
from atlas.data.providers.base import DataProvider


class DataCollector:
    """Envuelve un DataProvider; el resto del sistema depende solo de esta clase."""

    def __init__(self, provider: DataProvider) -> None:
        self._provider = provider

    def get_quote(self, symbol: str) -> Quote:
        """Obtiene la cotización de un símbolo a través del proveedor configurado."""
        return self._provider.get_quote(symbol)

    def get_quotes(self, symbols: List[str]) -> List[Quote]:
        """Obtiene la cotización de varios símbolos a través del proveedor configurado."""
        return self._provider.get_quotes(symbols)

    def get_history(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        """Obtiene barras OHLCV históricas a través del proveedor configurado."""
        return self._provider.get_history(symbol, period=period, interval=interval)
