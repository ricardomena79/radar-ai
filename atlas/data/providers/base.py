"""Interfaz común que debe implementar cualquier proveedor de datos de mercado."""

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Callable, List, TypeVar

import pandas as pd

from atlas.data.models.quote import Quote


class ProviderError(Exception):
    """Error genérico originado por un DataProvider."""


class QuoteNotFoundError(ProviderError):
    """El proveedor no encontró datos para el símbolo solicitado."""

    def __init__(self, symbol: str) -> None:
        super().__init__(f"No se encontró cotización para el símbolo '{symbol}'")
        self.symbol = symbol


# --- Timeout de red compartido por cualquier proveedor ---
# Bajo contención (ej. el escaneo de fondo de Atlas Live compitiendo con
# una consulta puntual), yfinance puede dejar una conexión colgada sin
# devolver error ni éxito -- sin este límite esa espera es indefinida.
# `FinnhubProvider` ya pasa su propio `timeout=` a `requests.get()`
# directamente (no lo necesita); esto es específicamente para
# `YahooFinanceProvider`, cuya API (`yfinance`) no acepta un timeout
# propio para `.info`/`.history()`.
DEFAULT_NETWORK_TIMEOUT_SECONDS = 15.0

_T = TypeVar("_T")
_NETWORK_EXECUTOR = ThreadPoolExecutor(max_workers=40, thread_name_prefix="dataprovider-network")


def call_with_timeout(
    func: Callable[[], _T],
    symbol: str,
    what: str,
    timeout_seconds: float = DEFAULT_NETWORK_TIMEOUT_SECONDS,
    provider_name: str = "el proveedor",
) -> _T:
    """Ejecuta `func` con un límite duro de `timeout_seconds`.

    Si el proveedor no responde a tiempo, levanta ProviderError -- el
    mismo tipo de error que ya maneja cada símbolo individualmente en los
    escaneos, así que un timeout no detiene el resto del trabajo.
    """
    future = _NETWORK_EXECUTOR.submit(func)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError:
        raise ProviderError(
            f"Tiempo de espera agotado ({timeout_seconds:.0f}s) "
            f"consultando {what} de '{symbol}' en {provider_name}"
        )


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
