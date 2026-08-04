"""Punto de entrada único de adquisición de datos para el resto de Atlas."""

from typing import Dict, List, Optional

import pandas as pd

from atlas.data.models.quote import Quote
from atlas.data.providers import get_default_provider
from atlas.data.providers.base import DataProvider
from atlas.storage import MemoryCache


class DataCollector:
    """Envuelve un DataProvider; el resto del sistema depende solo de esta clase.

    Cachea internamente en memoria (MemoryCache) para evitar volver a consultar
    al proveedor el mismo símbolo dentro de la ventana de `cache_ttl`. La
    API pública (get_quote, get_quotes, get_history) no cambia: el caché es
    un detalle de implementación transparente para quien la use.

    Si no se pasa `provider`, se usa el proveedor por defecto configurado en
    atlas.data.providers (registro + ATLAS_DATA_PROVIDER, hoy Yahoo
    Finance). Ningún módulo que construya `DataCollector()` sin argumentos
    necesita cambiar cuando se reemplace el proveedor.
    """

    def __init__(
        self,
        provider: Optional[DataProvider] = None,
        cache: Optional[MemoryCache] = None,
        cache_ttl: float = 300.0,
    ) -> None:
        self._provider = provider or get_default_provider()
        self._cache = cache if cache is not None else MemoryCache()
        self._cache_ttl = cache_ttl

    @staticmethod
    def _quote_key(symbol: str) -> str:
        return f"quote:{symbol.upper()}"

    @staticmethod
    def _history_key(symbol: str, period: str, interval: str) -> str:
        return f"history:{symbol.upper()}:{period}:{interval}"

    def get_quote(self, symbol: str) -> Quote:
        """Obtiene la cotización de un símbolo, sirviendo desde caché si está vigente."""
        key = self._quote_key(symbol)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        quote = self._provider.get_quote(symbol)
        self._cache.set(key, quote, ttl=self._cache_ttl)
        return quote

    def get_quotes(self, symbols: List[str]) -> List[Quote]:
        """Obtiene la cotización de varios símbolos.

        Sirve desde caché lo que ya esté vigente y consulta al proveedor,
        en un único lote, solo los símbolos faltantes.
        """
        quotes: Dict[str, Quote] = {}
        missing: List[str] = []

        for symbol in symbols:
            cached = self._cache.get(self._quote_key(symbol))
            if cached is not None:
                quotes[symbol] = cached
            else:
                missing.append(symbol)

        if missing:
            for quote in self._provider.get_quotes(missing):
                quotes[quote.symbol] = quote
                self._cache.set(self._quote_key(quote.symbol), quote, ttl=self._cache_ttl)

        return [quotes[symbol] for symbol in symbols if symbol in quotes]

    def get_history(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        """Obtiene barras OHLCV históricas, sirviendo desde caché si está vigente."""
        key = self._history_key(symbol, period, interval)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        history = self._provider.get_history(symbol, period=period, interval=interval)
        self._cache.set(key, history, ttl=self._cache_ttl)
        return history
