"""MultiProvider -- failover entre proveedores, según el mecanismo ya
diseñado y aprobado en DATA_FUSION_ENGINE_PROPUESTA.md, sección
"MECANISMO DE FAILOVER" (2026-08-02). Primer proveedor real que lo
implementa (2026-08-04) -- antes solo existía el diseño.

Implementa `DataProvider` -- por fuera es indistinguible de cualquier
otro proveedor, así que `DataCollector` (y todo lo que ya depende de él,
según la regla de arquitectura declarada oficialmente el 2026-08-02) no
necesita ningún cambio para usarlo.

Algoritmo (idéntico al ya documentado, no reinventado acá):
  1. Intenta con el proveedor de mayor prioridad de la lista.
  2. Si lanza `ProviderError` (falla real del proveedor -- red, auth,
     HTTP no-200), se registra el fallo y se reintenta con el siguiente.
  3. Si lanza `QuoteNotFoundError` (el símbolo específico no existe para
     ESE proveedor -- no es una falla del proveedor en sí), se propaga
     tal cual, sin intentar el siguiente proveedor -- mismo criterio ya
     definido en la propuesta.
  4. Si todos los proveedores fallan con `ProviderError`, se propaga el
     último `ProviderError` hacia arriba.
  5. Sin recuperación automática al proveedor primario dentro de una
     misma llamada -- cada llamada nueva vuelve a intentar desde el
     proveedor de mayor prioridad (deliberadamente simple, sin circuit
     breaker todavía -- mismo criterio ya documentado).
"""

import logging
from typing import Any, Dict, List

import pandas as pd

from atlas.data.models.quote import Quote
from atlas.data.providers.base import DataProvider, ProviderError, QuoteNotFoundError

logger = logging.getLogger(__name__)


class MultiProvider(DataProvider):
    """Envuelve una lista ORDENADA de proveedores -- el primero es el de
    mayor prioridad, se usa en condiciones normales. Los siguientes solo
    se consultan si el anterior falla con `ProviderError`."""

    def __init__(self, providers: List[DataProvider]) -> None:
        if not providers:
            raise ValueError("MultiProvider requiere al menos un proveedor.")
        self._providers = providers

    def get_quote(self, symbol: str) -> Quote:
        last_error: ProviderError = None
        for provider in self._providers:
            try:
                return provider.get_quote(symbol)
            except QuoteNotFoundError:
                raise
            except ProviderError as exc:
                logger.warning(
                    "MultiProvider: %s falló para '%s' (%s) -- probando siguiente proveedor.",
                    type(provider).__name__, symbol, exc,
                )
                last_error = exc
                continue
        raise last_error

    def get_quotes(self, symbols: List[str]) -> List[Quote]:
        """Mismo failover que get_quote(), pero por lote completo: si el
        proveedor primario falla con ProviderError para el LOTE, se
        reintenta el lote completo con el siguiente -- no se mezclan
        símbolos de distintos proveedores en una misma respuesta, para
        que el origen del dato sea siempre trazable de punta a punta."""
        last_error: ProviderError = None
        for provider in self._providers:
            try:
                return provider.get_quotes(symbols)
            except ProviderError as exc:
                logger.warning(
                    "MultiProvider: %s falló para el lote de %d símbolos (%s) -- probando siguiente proveedor.",
                    type(provider).__name__, len(symbols), exc,
                )
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        return []

    def get_history(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        last_error: ProviderError = None
        for provider in self._providers:
            try:
                return provider.get_history(symbol, period=period, interval=interval)
            except QuoteNotFoundError:
                raise
            except ProviderError as exc:
                logger.warning(
                    "MultiProvider: %s falló historial de '%s' (%s) -- probando siguiente proveedor.",
                    type(provider).__name__, symbol, exc,
                )
                last_error = exc
                continue
        raise last_error
