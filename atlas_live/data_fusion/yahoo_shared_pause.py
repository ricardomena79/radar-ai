"""Pausa compartida de Yahoo tras un rate-limit.

Todo lo que consulta Yahoo desde este servidor (las vistas de Mercado y el
pre-filtro de `scan_worker`) sale por la misma IP: si Yahoo responde "Too Many
Requests", seguir consultando desde otro punto mantiene el bloqueo. Quien
detecte el rate-limit llama a `trigger()` y todos los demas dejan de consultar
hasta que termine la pausa.
"""

import os
import threading
import time

COOLDOWN_SECONDS = float(os.environ.get("ATLAS_YAHOO_RATE_LIMIT_COOLDOWN_SECONDS", "600"))

_lock = threading.Lock()
_blocked_until = 0.0


def is_paused() -> bool:
    return time.monotonic() < _blocked_until


def trigger() -> None:
    global _blocked_until
    with _lock:
        _blocked_until = time.monotonic() + COOLDOWN_SECONDS


def reset() -> None:
    global _blocked_until
    with _lock:
        _blocked_until = 0.0


def looks_like_rate_limit(exc: BaseException) -> bool:
    return type(exc).__name__ in ("YFRateLimitError", "RateLimitError") or "Too Many Requests" in str(exc)
