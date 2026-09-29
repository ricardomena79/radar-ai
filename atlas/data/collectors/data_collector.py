"""Punto de entrada único de adquisición de datos para el resto de Atlas."""

from typing import Dict, List, Optional

import pandas as pd

from atlas.data.models.quote import Quote
from atlas.data.providers.base import DataProvider, ProviderError
from atlas.storage import MemoryCache

# Caché NEGATIVO de get_history() (2026-09-29, autorizado explícitamente --
# investigación real: con Yahoo rate-limitando de forma sostenida,
# `_score_symbol()` en `atlas_live/scan_worker.py` pide historial del MISMO
# símbolo hasta 5 veces por ciclo (atlas_score.py + momentum_engine.py +
# decision_engine.py, cada uno con su propia llamada daily/intradía) -- como
# el caché de ÉXITO nunca se alcanzaba a escribir (la excepción se propaga
# ANTES de esa línea), cada una de esas 5 llamadas repetía el mismo
# round-trip de red que ya había fallado segundos antes. TTL deliberadamente
# corto (mucho menor al de éxito, 300s) -- alcanza para cubrir las llamadas
# redundantes de un mismo símbolo dentro del mismo ciclo, sin ocultar una
# recuperación real de Yahoo por mucho tiempo.
DEFAULT_HISTORY_ERROR_TTL = 90.0

# Caché NEGATIVO de get_quote() (2026-09-29, autorizado explícitamente --
# mismo día, extensión del fix de arriba tras confirmar en producción que
# Yahoo Y Finnhub pueden rate-limitar cotizaciones al mismo tiempo, dejando
# la app entera sin threads libres -- ver docstring de
# `atlas_live/data_fusion/multi_provider.py`). Mismo criterio que el
# histórico: un fallo reciente para el mismo símbolo se re-lanza desde
# memoria, sin volver a golpear la red.
DEFAULT_QUOTE_ERROR_TTL = 60.0


class DataCollector:
    """Envuelve un DataProvider; el resto del sistema depende solo de esta clase.

    Cachea internamente en memoria (MemoryCache) para evitar volver a consultar
    a Yahoo Finance el mismo símbolo dentro de la ventana de `cache_ttl`. La
    API pública (get_quote, get_quotes, get_history) no cambia: el caché es
    un detalle de implementación transparente para quien la use.
    """

    def __init__(
        self,
        provider: DataProvider,
        cache: Optional[MemoryCache] = None,
        cache_ttl: float = 300.0,
        history_error_ttl: float = DEFAULT_HISTORY_ERROR_TTL,
        quote_error_ttl: float = DEFAULT_QUOTE_ERROR_TTL,
    ) -> None:
        self._provider = provider
        self._cache = cache if cache is not None else MemoryCache()
        self._cache_ttl = cache_ttl
        self._history_error_ttl = history_error_ttl
        self._quote_error_ttl = quote_error_ttl

    @staticmethod
    def _quote_key(symbol: str) -> str:
        return f"quote:{symbol.upper()}"

    @staticmethod
    def _quote_error_key(symbol: str) -> str:
        return f"quote_error:{symbol.upper()}"

    @staticmethod
    def _history_key(symbol: str, period: str, interval: str) -> str:
        return f"history:{symbol.upper()}:{period}:{interval}"

    @staticmethod
    def _history_error_key(symbol: str, period: str, interval: str) -> str:
        return f"history_error:{symbol.upper()}:{period}:{interval}"

    def get_quote(self, symbol: str) -> Quote:
        """Obtiene la cotización de un símbolo, sirviendo desde caché si está vigente.

        Caché negativo (2026-09-29): un fallo reciente del proveedor para
        el mismo símbolo se re-lanza directo desde memoria -- ver
        `DEFAULT_QUOTE_ERROR_TTL` arriba."""
        key = self._quote_key(symbol)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        error_key = self._quote_error_key(symbol)
        cached_error = self._cache.get(error_key)
        if cached_error is not None:
            raise cached_error

        try:
            quote = self._provider.get_quote(symbol)
        except ProviderError as exc:
            self._cache.set(error_key, exc, ttl=self._quote_error_ttl)
            raise
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
        """Obtiene barras OHLCV históricas, sirviendo desde caché si está vigente.

        Caché negativo (2026-09-29): un fallo reciente del proveedor para
        el mismo símbolo/period/interval se re-lanza directo desde memoria,
        sin volver a golpear la red -- ver `DEFAULT_HISTORY_ERROR_TTL`
        arriba para la evidencia real que motivó esto."""
        key = self._history_key(symbol, period, interval)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        error_key = self._history_error_key(symbol, period, interval)
        cached_error = self._cache.get(error_key)
        if cached_error is not None:
            raise cached_error

        try:
            history = self._provider.get_history(symbol, period=period, interval=interval)
        except ProviderError as exc:
            self._cache.set(error_key, exc, ttl=self._history_error_ttl)
            raise
        self._cache.set(key, history, ttl=self._cache_ttl)
        return history
