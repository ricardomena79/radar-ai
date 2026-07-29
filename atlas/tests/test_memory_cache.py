"""Prueba manual de atlas.storage.MemoryCache."""

import time

from atlas.storage import MemoryCache


def test_memory_cache() -> None:
    cache = MemoryCache()

    print("=" * 40)
    print("ATLAS - PRUEBA DE MEMORY CACHE")
    print("=" * 40)

    # 1. Guardar un valor
    cache.set("AAPL", {"price": 338.19})
    assert cache.has("AAPL")
    print("Guardado           : AAPL -> {'price': 338.19}")

    # 2. Recuperarlo
    value = cache.get("AAPL")
    assert value == {"price": 338.19}
    print(f"Recuperado          : AAPL -> {value}")

    # 3. Eliminarlo
    cache.remove("AAPL")
    assert not cache.has("AAPL")
    assert cache.get("AAPL") is None
    print("Eliminado           : AAPL ya no está en la caché")

    # 4. Expiración por TTL
    cache.set("NVDA", {"price": 190.01}, ttl=0.2)
    assert cache.has("NVDA")
    print("TTL                 : NVDA guardado con ttl=0.2s")
    time.sleep(0.3)
    assert not cache.has("NVDA")
    assert cache.get("NVDA") is None
    print("TTL                 : NVDA expiró y se eliminó automáticamente")

    # 5. Tamaño y limpieza
    cache.set("PLTR", {"price": 123.0})
    cache.set("SOXL", {"price": 91.99})
    print(f"Tamaño antes de clear(): {cache.size()}")
    assert cache.size() == 2

    cache.clear()
    print(f"Tamaño después de clear(): {cache.size()}")
    assert cache.size() == 0

    print("=" * 40)
    print("OK: MemoryCache funciona correctamente (set/get/has/remove/clear/size/TTL).")


if __name__ == "__main__":
    test_memory_cache()
