"""Tests del disparo en segundo plano (2026-08-16) -- no-reentrancia, estado
persistido, manejo de error. Sin red: `run_batch` se reemplaza por un stub
controlado. DB temporal, nunca toca `historical_reference.db` real.

Cada test espera el hilo de fondo con `.join()` explícito (no solo
polling) antes de terminar, para que el siguiente test arranque limpio --
`reference_registry` comparte estado de módulo (DB_PATH, cache de schema)
y dos hilos de tests distintos escribiendo a la vez causan "database is
locked"/"no such table" por una carrera real, no por un bug de producción
(en producción nunca hay dos construcciones concurrentes -- por diseño,
justamente lo que este archivo prueba)."""

import tempfile
import threading
import uuid as _uuid
from pathlib import Path

from atlas_live.reference import reference_registry as reg
from scripts import build_historical_reference as bhr

_ORIG_DB_PATH = reg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_bhr_bg_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None


def _restore():
    reg.DB_PATH = _ORIG_DB_PATH
    bhr._build_thread = None


def _wait_for_current_thread(timeout=5):
    """Espera (join real, no polling) a que el hilo de fondo ACTUAL termine."""
    with bhr._build_lock:
        t = bhr._build_thread
    if t is not None:
        t.join(timeout=timeout)


def test_no_reentrante_segunda_llamada_mientras_la_primera_corre():
    _fresh()
    gate = threading.Event()

    def _lento(*args, **kwargs):
        gate.wait(timeout=5)
        return {"ok": 1, "sin_datos": 0, "errores": 0}

    orig_run_batch = bhr.run_batch
    bhr.run_batch = _lento
    try:
        r1 = bhr.start_background_build(limit=1)
        assert r1["started"] is True

        r2 = bhr.start_background_build(limit=1)
        assert r2["started"] is False
        assert "en curso" in r2["reason"]

        gate.set()
        _wait_for_current_thread()
        assert bhr.build_status()["build_state"] == "COMPLETED"
    finally:
        bhr.run_batch = orig_run_batch
        gate.set()
        _wait_for_current_thread()
        _restore()


def test_una_nueva_construccion_puede_arrancar_despues_de_que_termine_la_anterior():
    _fresh()
    orig_run_batch = bhr.run_batch
    bhr.run_batch = lambda *a, **k: {"ok": 1, "sin_datos": 0, "errores": 0}
    try:
        r1 = bhr.start_background_build(limit=1)
        assert r1["started"] is True
        _wait_for_current_thread()

        r2 = bhr.start_background_build(limit=1)
        assert r2["started"] is True
        _wait_for_current_thread()
        assert bhr.build_status()["build_state"] == "COMPLETED"
    finally:
        bhr.run_batch = orig_run_batch
        _wait_for_current_thread()
        _restore()


def test_estado_persiste_via_meta_y_sobrevive_reinicio_simulado():
    """Simula un 'reinicio': el lock en memoria se resetea (proceso nuevo),
    pero el estado y los conteos siguen en la base -- el hilo viejo nunca
    llegó a poner build_state=COMPLETED."""
    _fresh()
    reg.set_meta(build_state="RUNNING", build_started_at="2026-08-16T00:00:00Z", build_finished_at=None, build_error=None)
    bhr._build_thread = None  # "reinicio": el proceso nuevo no tiene memoria del hilo viejo

    status = bhr.build_status()
    assert status["build_state"] == "RUNNING"  # informativo, quedó de la corrida cortada
    assert status["corriendo_en_este_proceso"] is False  # pero el lock actual está libre

    # una nueva llamada puede arrancar sin problema (no queda bloqueada para siempre)
    orig_run_batch = bhr.run_batch
    bhr.run_batch = lambda *a, **k: {"ok": 1, "sin_datos": 0, "errores": 0}
    try:
        r = bhr.start_background_build(limit=1)
        assert r["started"] is True
        _wait_for_current_thread()
        assert bhr.build_status()["build_state"] == "COMPLETED"
    finally:
        bhr.run_batch = orig_run_batch
        _wait_for_current_thread()
        _restore()


def test_error_de_run_batch_queda_registrado_no_silenciado():
    _fresh()
    orig_run_batch = bhr.run_batch
    bhr.run_batch = lambda *a, **k: {"error": "TRADIER_API_TOKEN no configurado"}
    try:
        r = bhr.start_background_build(limit=1)
        assert r["started"] is True
        _wait_for_current_thread()
        status = bhr.build_status()
        assert status["build_state"] == "ERROR"
        assert "TRADIER_API_TOKEN" in status["build_error"]
    finally:
        bhr.run_batch = orig_run_batch
        _wait_for_current_thread()
        _restore()


def test_excepcion_no_capturada_tambien_queda_registrada():
    _fresh()
    orig_run_batch = bhr.run_batch

    def _explota(*a, **k):
        raise RuntimeError("fallo simulado")

    bhr.run_batch = _explota
    try:
        r = bhr.start_background_build(limit=1)
        assert r["started"] is True
        _wait_for_current_thread()
        status = bhr.build_status()
        assert status["build_state"] == "ERROR"
        assert "fallo simulado" in status["build_error"]
    finally:
        bhr.run_batch = orig_run_batch
        _wait_for_current_thread()
        _restore()


def test_build_status_sin_haber_corrido_nunca():
    _fresh()
    try:
        status = bhr.build_status()
        assert status["build_state"] == "NUNCA_INICIADO"
        assert status["corriendo_en_este_proceso"] is False
    finally:
        _restore()


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
            p += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
            traceback.print_exc()
            f += 1
    print(f"--- {p} passed, {f} failed ---")
