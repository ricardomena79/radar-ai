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
import time
from threading import Lock
from typing import Any, Dict, List

import pandas as pd

from atlas.data.models.quote import Quote
from atlas.data.providers.base import DataProvider, ProviderError, QuoteNotFoundError

logger = logging.getLogger(__name__)

# Circuit breaker (2026-09-29, autorizado explícitamente -- investigación
# real: Yahoo rate-limitando de forma SOSTENIDA, no un pico pasajero --
# "Too Many Requests" repetido símbolo tras símbolo en producción,
# compitiendo por los mismos threads de gunicorn que el barrido real del
# radar y atrasándolo). Extendido el mismo día (autorizado explícitamente
# de nuevo) a get_quote()/get_quotes() tras confirmar en logs de
# producción que AMBOS proveedores (Yahoo Y Finnhub) pueden rate-limitar
# al mismo tiempo en ese camino también -- dejando la app entera sin
# responder (hasta /api/capacidad-resumen, que ni depende de Yahoo, sin
# threads libres). Tras `CIRCUIT_BREAKER_FAILURES` fallos SEGUIDOS de un
# proveedor en un método dado, se deja de intentar ESE proveedor para ESE
# método por `CIRCUIT_BREAKER_COOLDOWN_SECONDS` -- mismo patrón de
# cooldown ya usado en este repo (`catalyst_worker.py` ante 401/429,
# `storage_guard.py` ante disco lleno). Cada método (history/quote/quotes)
# lleva su propio contador -- un proveedor rate-limitado en historial no
# necesariamente lo está también en cotizaciones, y viceversa.
#
# Estado a nivel de MÓDULO (no de instancia) porque
# `get_default_provider()`/`TradierFirstProvider()` construyen un
# `MultiProvider` NUEVO en cada ciclo de escaneo ("barato, no abre
# conexiones hasta que se usa", ver `registry.py`) -- si el estado viviera
# en la instancia, el breaker se reiniciaría en cada ciclo y nunca
# protegería más allá del primer puñado de símbolos de cada barrido.
CIRCUIT_BREAKER_FAILURES = 5
CIRCUIT_BREAKER_COOLDOWN_SECONDS = 120.0

_breaker_lock = Lock()
_breaker_consecutive_failures: Dict[str, int] = {}
_breaker_open_until: Dict[str, float] = {}


def _breaker_key(provider: DataProvider, kind: str) -> str:
    return f"{type(provider).__name__}:{kind}"


def _breaker_is_open(provider: DataProvider, kind: str, now: float) -> bool:
    with _breaker_lock:
        open_until = _breaker_open_until.get(_breaker_key(provider, kind))
        return open_until is not None and now < open_until


def _breaker_record_success(provider: DataProvider, kind: str) -> None:
    key = _breaker_key(provider, kind)
    with _breaker_lock:
        _breaker_consecutive_failures[key] = 0
        _breaker_open_until.pop(key, None)


def _breaker_record_failure(provider: DataProvider, kind: str, now: float) -> None:
    key = _breaker_key(provider, kind)
    with _breaker_lock:
        count = _breaker_consecutive_failures.get(key, 0) + 1
        _breaker_consecutive_failures[key] = count
        if count >= CIRCUIT_BREAKER_FAILURES:
            _breaker_open_until[key] = now + CIRCUIT_BREAKER_COOLDOWN_SECONDS
            logger.warning(
                "MultiProvider: circuit breaker de %s() ABIERTO para %s por %.0fs "
                "tras %d fallos seguidos.",
                kind, type(provider).__name__, CIRCUIT_BREAKER_COOLDOWN_SECONDS, count,
            )


class MultiProvider(DataProvider):
    """Envuelve una lista ORDENADA de proveedores -- el primero es el de
    mayor prioridad, se usa en condiciones normales. Los siguientes solo
    se consultan si el anterior falla con `ProviderError`."""

    def __init__(self, providers: List[DataProvider]) -> None:
        if not providers:
            raise ValueError("MultiProvider requiere al menos un proveedor.")
        self._providers = providers

    def get_quote(self, symbol: str) -> Quote:
        """Failover con circuit breaker (2026-09-29, kind='quote' -- ver
        `_breaker_*` al inicio del módulo): un proveedor con
        `CIRCUIT_BREAKER_FAILURES` fallos seguidos se salta durante
        `CIRCUIT_BREAKER_COOLDOWN_SECONDS`, en vez de repetir un
        round-trip que ya se sabe que va a fallar."""
        last_error: ProviderError = None
        any_attempted = False
        now = time.monotonic()
        for provider in self._providers:
            if _breaker_is_open(provider, "quote", now):
                logger.debug(
                    "MultiProvider: %s en cooldown (circuit breaker) -- se salta para cotización de '%s'.",
                    type(provider).__name__, symbol,
                )
                continue
            any_attempted = True
            try:
                result = provider.get_quote(symbol)
                _breaker_record_success(provider, "quote")
                return result
            except QuoteNotFoundError:
                raise
            except ProviderError as exc:
                logger.warning(
                    "MultiProvider: %s falló para '%s' (%s) -- probando siguiente proveedor.",
                    type(provider).__name__, symbol, exc,
                )
                last_error = exc
                _breaker_record_failure(provider, "quote", now)
                continue
        if last_error is not None:
            raise last_error
        if not any_attempted:
            raise ProviderError(
                f"Todos los proveedores de cotización están en cooldown (circuit breaker) para '{symbol}'."
            )
        raise ProviderError(f"Ningún proveedor de cotización disponible para '{symbol}'.")

    def get_quotes(self, symbols: List[str]) -> List[Quote]:
        """Mismo failover que get_quote(), pero por lote completo: si el
        proveedor primario falla con ProviderError para el LOTE, se
        reintenta el lote completo con el siguiente -- no se mezclan
        símbolos de distintos proveedores en una misma respuesta, para
        que el origen del dato sea siempre trazable de punta a punta.
        Circuit breaker (2026-09-29, kind='quotes'): mismo criterio que
        get_quote()/get_history()."""
        last_error: ProviderError = None
        any_attempted = False
        now = time.monotonic()
        for provider in self._providers:
            if _breaker_is_open(provider, "quotes", now):
                logger.debug(
                    "MultiProvider: %s en cooldown (circuit breaker) -- se salta para el lote de %d símbolos.",
                    type(provider).__name__, len(symbols),
                )
                continue
            any_attempted = True
            try:
                result = provider.get_quotes(symbols)
                _breaker_record_success(provider, "quotes")
                return result
            except ProviderError as exc:
                logger.warning(
                    "MultiProvider: %s falló para el lote de %d símbolos (%s) -- probando siguiente proveedor.",
                    type(provider).__name__, len(symbols), exc,
                )
                last_error = exc
                _breaker_record_failure(provider, "quotes", now)
                continue
        if last_error is not None:
            raise last_error
        if not any_attempted:
            raise ProviderError(
                f"Todos los proveedores de cotización están en cooldown (circuit breaker) para el lote de {len(symbols)} símbolos."
            )
        return []

    def get_history(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        """Mismo failover de arriba, más un circuit breaker (2026-09-29,
        kind='history' -- ver constantes/funciones `_breaker_*` al inicio
        del módulo): un proveedor con `CIRCUIT_BREAKER_FAILURES` fallos
        seguidos se salta (sin siquiera intentarlo) durante
        `CIRCUIT_BREAKER_COOLDOWN_SECONDS`, en vez de repetir un
        round-trip de red que ya se sabe que va a fallar."""
        last_error: ProviderError = None
        any_attempted = False
        now = time.monotonic()
        for provider in self._providers:
            if _breaker_is_open(provider, "history", now):
                logger.debug(
                    "MultiProvider: %s en cooldown (circuit breaker) -- se salta para historial de '%s'.",
                    type(provider).__name__, symbol,
                )
                continue
            any_attempted = True
            try:
                result = provider.get_history(symbol, period=period, interval=interval)
                _breaker_record_success(provider, "history")
                return result
            except QuoteNotFoundError:
                raise
            except ProviderError as exc:
                logger.warning(
                    "MultiProvider: %s falló historial de '%s' (%s) -- probando siguiente proveedor.",
                    type(provider).__name__, symbol, exc,
                )
                last_error = exc
                _breaker_record_failure(provider, "history", now)
                continue
        if last_error is not None:
            raise last_error
        if not any_attempted:
            raise ProviderError(
                f"Todos los proveedores de historial están en cooldown (circuit breaker) para '{symbol}'."
            )
        raise ProviderError(f"Ningún proveedor de historial disponible para '{symbol}'.")
