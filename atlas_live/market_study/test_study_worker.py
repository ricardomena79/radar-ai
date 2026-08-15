"""Tests del worker de fondo del estudio (2026-08-10). Con universo y fetch
FALSOS (sin red), DB temporal. Verifica: procesa el universo, checkpoint,
reanudación tras "reinicio" (no reprocesa ni duplica), estado, y que un
símbolo con error no detiene al resto.
"""

import tempfile
import threading
import time
import uuid as _uuid
from pathlib import Path

from atlas_live.market_study import explosion_scan
from atlas_live.market_study import study_registry as reg
from atlas_live.market_study import study_worker as w
from atlas_live.market_study import universe

_ORIG = reg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_study_worker_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None
    reg._connect().close()  # crea el esquema una vez, antes de lanzar hilos (evita carrera de DDL)


def _restore():
    reg.DB_PATH = _ORIG


def _install_fakes(monkey_symbols, scan_fn):
    # FASE 11 (2026-08-10, sesión paralela): study_loop() ahora llama
    # fetch_broad_universe_meta() (identidad exchange+nombre), no
    # fetch_broad_universe() -- parchear la función vieja ya no intercepta
    # nada real, el worker terminaba golpeando la red de verdad. Corregido
    # acá para que el test vuelva a ser 100% offline/determinístico.
    orig_u = universe.fetch_broad_universe_meta
    orig_r = universe.racional_symbols
    orig_s = explosion_scan.scan_symbol
    orig_yield = w._operational_scanning
    universe.fetch_broad_universe_meta = lambda use_cache=True: {s: {"exchange": "TEST", "name": s} for s in monkey_symbols}
    universe.racional_symbols = lambda: set()
    explosion_scan.scan_symbol = scan_fn
    w._operational_scanning = lambda: False  # nunca cede en el test
    return (orig_u, orig_r, orig_s, orig_yield)


def _uninstall_fakes(saved):
    universe.fetch_broad_universe_meta, universe.racional_symbols, explosion_scan.scan_symbol, w._operational_scanning = saved


def _run_until_processed(symbols, timeout=8.0):
    w._stop.clear()
    # delay realista-pero-rápido para el test (no 0.01: evita martillar la DB)
    orig_delay = w.STUDY_DELAY_SECONDS
    w.STUDY_DELAY_SECONDS = 0.1
    t = threading.Thread(target=w.study_loop, daemon=True)
    t.start()
    t0 = time.time()
    while time.time() - t0 < timeout:
        if reg.processed_symbols() >= set(symbols):
            break
        time.sleep(0.25)
    w.request_stop()
    t.join(timeout=3)
    w.STUDY_DELAY_SECONDS = orig_delay
    return t


def test_worker_procesa_universo_y_checkpoint():
    _fresh()
    calls = []
    def fake(sym, rac, period="6mo"):
        calls.append(sym)
        return {"status": "ok", "explosions": 1, "nuevas": 1}
    saved = _install_fakes(["AAA", "BBB", "CCC"], fake)
    try:
        _run_until_processed(["AAA", "BBB", "CCC"])
        assert reg.processed_symbols() == {"AAA", "BBB", "CCC"}
        st = reg.study_status()
        assert st["universe_total"] == 3
        assert st["procesados"] == 3
        assert st["ultimo_avance_at"] is not None  # evidencia de progreso
    finally:
        _uninstall_fakes(saved)
        _restore()


def test_worker_reanuda_sin_reprocesar():
    _fresh()
    calls = []
    def fake(sym, rac, period="6mo"):
        calls.append(sym)
        return {"status": "ok", "explosions": 0, "nuevas": 0}
    saved = _install_fakes(["AAA", "BBB"], fake)
    try:
        _run_until_processed(["AAA", "BBB"])
        assert set(calls) == {"AAA", "BBB"}
        n_antes = len(calls)
        # "reinicio": nuevo study_loop sobre la MISMA base -> no reprocesa
        _run_until_processed(["AAA", "BBB"], timeout=2.0)
        assert len(calls) == n_antes, f"reprocesó: {calls}"  # no duplica trabajo
    finally:
        _uninstall_fakes(saved)
        _restore()


def test_worker_un_error_no_detiene_al_resto():
    _fresh()
    def fake(sym, rac, period="6mo"):
        if sym == "BOOMBAD":
            return {"status": "error", "explosions": 0, "note": "fallo simulado"}
        return {"status": "ok", "explosions": 1, "nuevas": 1}
    saved = _install_fakes(["AAA", "BOOMBAD", "CCC"], fake)
    try:
        # menos retries para acelerar el test
        orig_retries = w.STUDY_MAX_RETRIES
        w.STUDY_MAX_RETRIES = 1
        _run_until_processed(["AAA", "BOOMBAD", "CCC"], timeout=8.0)
        w.STUDY_MAX_RETRIES = orig_retries
        assert reg.processed_symbols() == {"AAA", "BOOMBAD", "CCC"}  # continuó con todos
        assert reg.study_status()["errores"] >= 1  # registró el error, no lo ocultó
    finally:
        _uninstall_fakes(saved)
        _restore()


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
