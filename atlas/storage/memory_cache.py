"""Caché en memoria RAM, simple y reutilizable, con soporte opcional de TTL.

Sin Redis, sin base de datos, sin archivos: solo un diccionario en memoria
del proceso. Pensada para que el Data Collector la use en una fase futura
y evite consultas repetidas a Yahoo Finance, sin cambiar su API pública.
"""

import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Dict, Optional


@dataclass
class _CacheEntry:
    """Valor almacenado junto con su instante de expiración (reloj monotónico)."""

    value: Any
    expires_at: Optional[float]

    def is_expired(self) -> bool:
        return self.expires_at is not None and time.monotonic() >= self.expires_at


class MemoryCache:
    """Caché clave-valor en memoria, con expiración perezosa por TTL."""

    def __init__(self) -> None:
        self._store: Dict[str, _CacheEntry] = {}
        self._lock = Lock()

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Guarda `value` bajo `key`. `ttl` en segundos es opcional; None = sin expiración."""
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl debe ser mayor que 0")

        expires_at = time.monotonic() + ttl if ttl is not None else None
        with self._lock:
            self._store[key] = _CacheEntry(value=value, expires_at=expires_at)

    def get(self, key: str) -> Optional[Any]:
        """Devuelve el valor de `key`, o None si no existe o ya expiró."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.is_expired():
                del self._store[key]
                return None
            return entry.value

    def has(self, key: str) -> bool:
        """Indica si `key` existe y sigue vigente (no expiró)."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False
            if entry.is_expired():
                del self._store[key]
                return False
            return True

    def remove(self, key: str) -> None:
        """Elimina `key` de la caché, si existe."""
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        """Vacía la caché por completo."""
        with self._lock:
            self._store.clear()

    def size(self) -> int:
        """Número de elementos vigentes almacenados (purga expirados antes de contar)."""
        with self._lock:
            expired_keys = [key for key, entry in self._store.items() if entry.is_expired()]
            for key in expired_keys:
                del self._store[key]
            return len(self._store)
