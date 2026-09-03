"""Tests de `GET /api/admin/radar-worker-status` (2026-09-03, autorizado
explícitamente) -- mismo patrón sin red/sin hilos de fondo que
`test_decision_knowledge_tribunal_endpoint.py` (incluye la neutralización
de `study_worker`/`catalyst_worker`/`radar_worker`/`market_view`/
`scan_worker`, para que ninguno arranque de verdad al importar
`server.py`)."""

import os
import threading
import time

import atlas_live.backtest.seed_import as _si
import atlas_live.catalyst.catalyst_worker as _cw
import atlas_live.market_study.study_worker as _stw
import atlas_live.market_view as _mv
import atlas_live.radar.radar_worker as _rw
import atlas_live.scan_worker as _sw

_orig_seed = _si.import_all_seeds
_orig_refresh = _sw.start_background_refresh
_orig_radar = _rw.start_universe_radar
_orig_market_view = _mv.start_market_view
_orig_study = _stw.start_study_worker
_orig_catalyst = _cw.start_catalyst_worker
_si.import_all_seeds = lambda *a, **k: None
_sw.start_background_refresh = lambda *a, **k: None
_rw.start_universe_radar = lambda *a, **k: None
_mv.start_market_view = lambda *a, **k: None
_stw.start_study_worker = lambda *a, **k: None
_cw.start_catalyst_worker = lambda *a, **k: None
try:
    from atlas_live import server  # noqa: E402
finally:
    _si.import_all_seeds = _orig_seed
    _sw.start_background_refresh = _orig_refresh
    _rw.start_universe_radar = _orig_radar
    _mv.start_market_view = _orig_market_view
    _stw.start_study_worker = _orig_study
    _cw.start_catalyst_worker = _orig_catalyst

from atlas_live.radar import candidate_registry as radar_registry  # noqa: E402
from atlas_live.radar import radar_worker as rw  # noqa: E402

_ORIG_THREAD = rw._thread


def _client():
    return server.app.test_client()


def _restore_thread_state():
    rw._thread = _ORIG_THREAD
    rw._stop.clear()


# --- thread_exists / thread_alive / stop_requested (pruebas directas) ------

def test_thread_exists_false_cuando_thread_es_none():
    rw._thread = None
    try:
        thread_exists = rw._thread is not None
        assert thread_exists is False
    finally:
        _restore_thread_state()


def test_thread_alive_refleja_is_alive_con_hilo_vivo():
    ev = threading.Event()
    t = threading.Thread(target=ev.wait, daemon=True)
    t.start()
    rw._thread = t
    try:
        assert rw._thread.is_alive() is True
    finally:
        ev.set()
        t.join(timeout=2)
        _restore_thread_state()


def test_thread_alive_refleja_is_alive_con_hilo_terminado():
    t = threading.Thread(target=lambda: None, daemon=True)
    t.start()
    t.join(timeout=2)
    rw._thread = t
    try:
        assert rw._thread.is_alive() is False
    finally:
        _restore_thread_state()


def test_stop_requested_refleja_stop_is_set():
    rw._stop.clear()
    assert rw._stop.is_set() is False
    rw._stop.set()
    try:
        assert rw._stop.is_set() is True
    finally:
        _restore_thread_state()


# --- endpoint ------------------------------------------------------------

def test_endpoint_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().get("/api/admin/radar-worker-status")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_endpoint_con_token_devuelve_estado_esperado_thread_none():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    rw._thread = None
    rw._stop.clear()
    try:
        r = _client().get("/api/admin/radar-worker-status?token=secreto-real")
        assert r.status_code == 200
        body = r.get_json()
        assert body["thread_exists"] is False
        assert body["thread_alive"] is False
        assert body["stop_requested"] is False
        assert "radar_status" in body
        assert "radar_enabled" in body
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]
        _restore_thread_state()


def test_endpoint_con_token_devuelve_estado_esperado_thread_vivo():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    ev = threading.Event()
    t = threading.Thread(target=ev.wait, daemon=True)
    t.start()
    rw._thread = t
    rw._stop.set()
    try:
        r = _client().get("/api/admin/radar-worker-status?token=secreto-real")
        assert r.status_code == 200
        body = r.get_json()
        assert body["thread_exists"] is True
        assert body["thread_alive"] is True
        assert body["stop_requested"] is True
    finally:
        ev.set()
        t.join(timeout=2)
        del os.environ["ATLAS_ADMIN_TOKEN"]
        _restore_thread_state()


def test_endpoint_incluye_radar_status_real():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    try:
        r = _client().get("/api/admin/radar-worker-status?token=secreto-real")
        assert r.status_code == 200
        body = r.get_json()
        assert body["radar_status"] == radar_registry.radar_status()
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]
        _restore_thread_state()


# --- lock_locked / thread_ident / stack_summary (Fase 2, 2026-09-03) -------

def test_endpoint_thread_none_campos_nuevos_en_estado_vacio():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    rw._thread = None
    rw._stop.clear()
    try:
        r = _client().get("/api/admin/radar-worker-status?token=secreto-real")
        assert r.status_code == 200
        body = r.get_json()
        assert body["lock_locked"] is False
        assert body["thread_ident"] is None
        assert body["stack_summary"] is None
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]
        _restore_thread_state()


def test_lock_locked_true_cuando_el_lock_esta_tomado():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    ev = threading.Event()

    def _hold_lock():
        rw._lock.acquire()
        try:
            ev.wait()
        finally:
            rw._lock.release()

    t = threading.Thread(target=_hold_lock, daemon=True)
    t.start()
    deadline = time.time() + 2
    while not rw._lock.locked() and time.time() < deadline:  # espera acotada a que el hilo tome el lock
        time.sleep(0.005)
    assert rw._lock.locked() is True  # confirma que el setup del test funcionó antes de seguir
    rw._thread = t
    try:
        r = _client().get("/api/admin/radar-worker-status?token=secreto-real")
        assert r.status_code == 200
        body = r.get_json()
        assert body["lock_locked"] is True
    finally:
        ev.set()
        t.join(timeout=2)
        del os.environ["ATLAS_ADMIN_TOKEN"]
        _restore_thread_state()


def test_lock_locked_false_cuando_el_lock_esta_libre():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    assert rw._lock.locked() is False
    try:
        r = _client().get("/api/admin/radar-worker-status?token=secreto-real")
        assert r.status_code == 200
        assert r.get_json()["lock_locked"] is False
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]
        _restore_thread_state()


def test_thread_ident_coincide_con_el_hilo_real():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    ev = threading.Event()
    t = threading.Thread(target=ev.wait, daemon=True)
    t.start()
    rw._thread = t
    try:
        r = _client().get("/api/admin/radar-worker-status?token=secreto-real")
        assert r.status_code == 200
        assert r.get_json()["thread_ident"] == t.ident
    finally:
        ev.set()
        t.join(timeout=2)
        del os.environ["ATLAS_ADMIN_TOKEN"]
        _restore_thread_state()


def _funcion_donde_el_hilo_de_prueba_queda_esperando(ev):
    ev.wait()


def test_stack_summary_muestra_la_funcion_real_donde_esta_el_hilo():
    """Confirma que sys._current_frames()+traceback.format_stack() captura
    el stack del hilo CORRECTO -- no un placeholder ni el stack del hilo
    principal que atiende el request."""
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    ev = threading.Event()
    t = threading.Thread(target=_funcion_donde_el_hilo_de_prueba_queda_esperando, args=(ev,), daemon=True)
    t.start()
    rw._thread = t
    try:
        r = _client().get("/api/admin/radar-worker-status?token=secreto-real")
        assert r.status_code == 200
        stack = r.get_json()["stack_summary"]
        assert stack is not None
        assert "_funcion_donde_el_hilo_de_prueba_queda_esperando" in stack
    finally:
        ev.set()
        t.join(timeout=2)
        del os.environ["ATLAS_ADMIN_TOKEN"]
        _restore_thread_state()
        _restore_thread_state()
