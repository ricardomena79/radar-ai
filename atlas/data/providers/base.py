"""Interfaz común que debe implementar cualquier proveedor de datos de mercado."""

from abc import ABC, abstractmethod
from typing import List

import pandas as pd

from atlas.data.models.quote import Quote


class ProviderError(Exception):
    """Error genérico originado por un DataProvider."""


class QuoteNotFoundError(ProviderError):
    """El proveedor no encontró datos para el símbolo solicitado."""

    def __init__(self, symbol: str) -> None:
        super().__init__(f"No se encontró cotización para el símbolo '{symbol}'")
        self.symbol = symbol


class DataProvider(ABC):
    """Contrato que todo proveedor de datos (Yahoo Finance, Polygon, Finnhub, Alpaca, ...) debe cumplir."""

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        """Devuelve la cotización actual de un símbolo."""
        raise NotImplementedError

    def get_quotes(self, symbols: List[str]) -> List[Quote]:
        """Devuelve la cotización de varios símbolos; por defecto itera get_quote()."""
        return [self.get_quote(symbol) for symbol in symbols]

    @abstractmethod
    def get_history(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        """Devuelve barras históricas OHLCV indexadas por fecha/hora.

        Columnas esperadas: Open, High, Low, Close, Volume.
        Es la fuente de datos que consume atlas.indicators.
        """
        raise NotImplementedError
