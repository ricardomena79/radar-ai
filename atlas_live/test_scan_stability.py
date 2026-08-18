"""Tests de estabilidad del scanner (2026-08-09): guard de no-reentrancia y
heartbeat de ciclos. Sin red, sin DB real -- solo la lógica de control.
"""

import threading
import time

import atlas_live.scan_worker as sw


def test_no_reentrante_no_solapa_ciclos():
    # Dos llamadas concurrentes: solo una debe ejecutar el ciclo; la otra se
    # saltea (no se solapan) -- evita duplicar carga al proveedor y la carrera
    # sobre STATE.
    ejecuciones = {"n": 0}
    orig = sw._run_scan_once_locked

    def _slow():
        ejecuciones["n"] += 1
        time.sleep(0.4)

    sw._run_scan_once_locked = _slow
    try:
        t1 = threading.Thread(target=sw.run_scan_once)
        t2 = threading.Thread(target=sw.run_scan_once)
        t1.start(); time.sleep(0.05); t2.start()
        t1.join(); t2.join()
        assert ejecuciones["n"] == 1, ejecuciones
    finally:
        sw._run_scan_once_locked = orig


def test_ciclo_siguiente_arranca_despues_de_uno_fallido():
    # Tras un ciclo que lanzó (aislado), el lock queda liberado y el siguiente
    # puede correr.
    llamadas = {"n": 0}
    orig = sw._run_scan_once_locked

    def _boom():
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise RuntimeError("fallo simulado del primer ciclo")

    sw._run_scan_once_locked = _boom
    try:
        # run_scan_once NO propaga (el llamador -- el loop -- no debe morir).
        # Aquí _run_scan_once_locked sí lanza, pero run_scan_once solo garantiza
        # liberar el lock; el loop real envuelve en try/except. Simulamos ambos.
        try:
            sw.run_scan_once()
        except RuntimeError:
            pass
        # el lock quedó libre -> el segundo ciclo corre
        try:
            sw.run_scan_once()
        except RuntimeError:
            pass
        assert llamadas["n"] == 2
        assert not sw._scan_lock.locked()
    finally:
        sw._run_scan_once_locked = orig


def test_record_cycle_outcome_contadores():
    st = sw._State()
    st.record_cycle_outcome("ok", "2026-08-09T10:00:00+00:00")
    st.record_cycle_outcome("sin_datos", "2026-08-09T10:05:00+00:00", reason="proveedor caído")
    st.record_cycle_outcome("error", "2026-08-09T10:10:00+00:00", reason="ValueError: x")
    snap = st.snapshot()
    assert snap["cycles_total"] == 3
    assert snap["cycles_ok"] == 1 and snap["cycles_sin_datos"] == 1 and snap["cycles_error"] == 1
    # last_success_at solo avanza con 'ok'
    assert snap["last_success_at"] == "2026-08-09T10:00:00+00:00"
    assert snap["last_cycle_status"] == "error"
    assert snap["last_cycle_finished_at"] == "2026-08-09T10:10:00+00:00"
    assert snap["last_failure_reason"] == "ValueError: x"


def test_record_ok_limpia_last_failure_reason():
    st = sw._State()
    st.record_cycle_outcome("error", "t1", reason="algo")
    assert st.snapshot()["last_failure_reason"] == "algo"
    st.record_cycle_outcome("ok", "t2")
    assert st.snapshot()["last_failure_reason"] is None
    assert st.snapshot()["last_success_at"] == "t2"


# --- Diagnóstico real de _score_symbol() (2026-08-18, caso "0 ciclos con datos") ---

def test_state_snapshot_incluye_last_score_symbol_error_default_none():
    st = sw._State()
    assert st.snapshot()["last_score_symbol_error"] is None


def test_score_symbol_captura_el_error_real_con_simbolo_y_tipo():
    from atlas.data.universe import Asset

    class _CollectorRoto:
        def get_quote(self, symbol):
            raise ValueError("boom -- fallo simulado de proveedor")

    orig = sw.STATE.last_score_symbol_error
    sw.STATE.update(last_score_symbol_error=None)
    try:
        asset = Asset(symbol="ZZZZ", name="Zzzz Test", type="EQUITY")
        result = sw._score_symbol(asset, _CollectorRoto(), money_flow_engine=None,
                                   recorder=None, context=None, explosive_cfg={})
        assert result is None  # comportamiento sin cambios: sigue devolviendo None
        captured = sw.STATE.snapshot()["last_score_symbol_error"]
        assert captured is not None
        assert "ZZZZ" in captured
        assert "ValueError" in captured
        assert "boom" in captured
    finally:
        sw.STATE.update(last_score_symbol_error=orig)


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
