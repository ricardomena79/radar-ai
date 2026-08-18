"""Tests de la configuración por entorno del escaneo (2026-08-09).

Verifica que:
- sin variables de entorno, los defaults son los actuales (documentados,
  no necesariamente "sin cambio" -- SCAN_REQUEST_DELAY_MS pasó de 0 a 150
  el 2026-08-18, ver scan_worker.py, caso real "0 ciclos con datos");
- una variable de entorno válida sobreescribe el valor;
- un valor inválido cae al default (no rompe el arranque).
"""

import importlib


def test_defaults_sin_env():
    import atlas_live.scan_worker as sw
    importlib.reload(sw)
    assert sw.WATCHLIST_EQUITIES == 150
    assert sw.WATCHLIST_ETFS == 50
    assert sw.MAX_WORKERS == 10
    assert sw.SCAN_REQUEST_DELAY_MS == 150
    assert sw.REFRESH_INTERVAL_SECONDS == 300
    assert sw.PREFILTER_CHUNK_SIZE == 400
    assert sw.PREFILTER_WORKERS == 30


def test_env_override(monkeypatch=None):
    import os
    import atlas_live.scan_worker as sw
    os.environ["ATLAS_SCAN_WATCHLIST_EQUITIES"] = "40"
    os.environ["ATLAS_SCAN_MAX_WORKERS"] = "4"
    os.environ["ATLAS_SCAN_REQUEST_DELAY_MS"] = "50"
    try:
        importlib.reload(sw)
        assert sw.WATCHLIST_EQUITIES == 40
        assert sw.MAX_WORKERS == 4
        assert sw.SCAN_REQUEST_DELAY_MS == 50
    finally:
        for k in ("ATLAS_SCAN_WATCHLIST_EQUITIES", "ATLAS_SCAN_MAX_WORKERS", "ATLAS_SCAN_REQUEST_DELAY_MS"):
            os.environ.pop(k, None)
        importlib.reload(sw)  # restaura defaults para el resto de la suite


def test_env_invalido_cae_al_default():
    import os
    import atlas_live.scan_worker as sw
    os.environ["ATLAS_SCAN_MAX_WORKERS"] = "no-es-un-numero"
    try:
        importlib.reload(sw)
        assert sw.MAX_WORKERS == 10  # inválido -> default, no rompe
    finally:
        os.environ.pop("ATLAS_SCAN_MAX_WORKERS", None)
        importlib.reload(sw)


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__); p += 1
        except Exception as e:
            print("FAIL", fn.__name__, e); traceback.print_exc(); f += 1
    print(f"--- {p} passed, {f} failed ---")
