"""Tests del guard de reentrancia de GET /api/radar-oportunidades
(2026-09-03, fix operativo post-deploy de Hito 3.5, autorizado
explícitamente) -- ver `_oportunidades_lock` en `server.py`.

Incidente real que motiva este fix: dos ejecuciones concurrentes de este
endpoint (cada una recorriendo ~1.500 candidatas, cada una abriendo varias
conexiones SQLite por candidata vía Fases 3.0/3.3/3.4) contendieron por los
mismos archivos en modo WAL y, en conjunto, agotaron los 8 threads de
gunicorn -- dejando sin capacidad incluso a `/api/mercado`, un endpoint
completamente ajeno. El fix es exclusivamente un `threading.Lock()` de
exclusión mutua alrededor del handler completo -- nunca toca baseline,
shadow, elegibilidad (3.3) ni activación (3.5)."""

import os
import threading

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

from atlas_live.radar import candidate_registry as reg  # noqa: E402


def _client():
    return server.app.test_client()


def _mock_una_candidata(monkeypatch):
    monkeypatch.setattr(reg, "live_opportunities", lambda market_date: [
        {"ticker": "AAA", "price_at_detection": 10.0, "stage": "PREPARACION", "racional_available": True},
    ])
    monkeypatch.setattr(_rw, "get_last_quotes", lambda: {})


# --- 1) un request normal entra correctamente -------------------------------

def test_request_normal_entra_y_libera_el_lock(monkeypatch):
    _mock_una_candidata(monkeypatch)
    assert not server._oportunidades_lock.locked()
    r = _client().get("/api/radar-oportunidades")
    assert r.status_code == 200
    assert r.get_json()["oportunidades"][0]["ticker"] == "AAA"
    # el lock queda liberado despues de un request exitoso -- no se filtra
    assert not server._oportunidades_lock.locked()


# --- 2/3) segundo request concurrente es rechazado de inmediato, sin correr el loop pesado --

def test_segundo_request_con_lock_tomado_es_rechazado_de_inmediato(monkeypatch):
    """Simula un ciclo real ya en curso reteniendo el lock manualmente --
    mismo patrón exacto que `test_market_view.py`/`test_radar_worker.py`
    para sus propios locks de reentrancia."""
    llamado = {"impl": False}

    def _impl_nunca_debe_correr():
        llamado["impl"] = True
        return server.jsonify({"deberia": "no llegar aca"})

    monkeypatch.setattr(server, "_api_radar_oportunidades_impl", _impl_nunca_debe_correr)
    assert server._oportunidades_lock.acquire(blocking=False)  # simula ejecucion pesada en curso
    try:
        r = _client().get("/api/radar-oportunidades")
        assert r.status_code == 429
        body = r.get_json()
        assert body["error"] == "ciclo_ya_en_curso"
        assert llamado["impl"] is False  # el loop pesado NUNCA se ejecuto
    finally:
        server._oportunidades_lock.release()


def test_rechazo_es_inmediato_no_espera(monkeypatch):
    """El rechazo no debe involucrar ningun sleep/poll -- se mide que la
    respuesta llega en milisegundos, no que "eventualmente" llegue."""
    import time

    assert server._oportunidades_lock.acquire(blocking=False)
    try:
        inicio = time.monotonic()
        r = _client().get("/api/radar-oportunidades")
        duracion = time.monotonic() - inicio
        assert r.status_code == 429
        assert duracion < 1.0  # muy por debajo de cualquier timeout real
    finally:
        server._oportunidades_lock.release()


# --- 4) al terminar el primero, un nuevo request puede entrar --------------

def test_tras_liberar_el_lock_un_nuevo_request_entra_normalmente(monkeypatch):
    _mock_una_candidata(monkeypatch)
    assert server._oportunidades_lock.acquire(blocking=False)
    server._oportunidades_lock.release()  # simula que el primer request ya termino

    r = _client().get("/api/radar-oportunidades")
    assert r.status_code == 200
    assert not server._oportunidades_lock.locked()


# --- 5) no existe deadlock -- concurrencia real con threads -----------------

def test_dos_threads_reales_concurrentes_uno_entra_otro_es_rechazado(monkeypatch):
    """Concurrencia real (no simulada con acquire manual): dos threads
    llaman al endpoint al mismo tiempo sobre una candidata que tarda un
    poco en resolverse -- exactamente uno debe entrar al loop pesado, el
    otro debe recibir 429 sin bloquearse. Demuestra ausencia de deadlock:
    ambos threads siempre terminan (join con timeout real)."""
    entro_al_impl = threading.Event()
    puede_continuar = threading.Event()
    orig_impl = server._api_radar_oportunidades_impl

    def _impl_lento():
        entro_al_impl.set()
        puede_continuar.wait(timeout=5)
        return server.jsonify({"ok": True})

    monkeypatch.setattr(server, "_api_radar_oportunidades_impl", _impl_lento)

    resultados = {}

    def _req_primero():
        resultados["primero"] = _client().get("/api/radar-oportunidades").status_code

    def _req_segundo():
        entro_al_impl.wait(timeout=5)  # esperar a que el primero ya haya tomado el lock
        resultados["segundo"] = _client().get("/api/radar-oportunidades").status_code

    t1 = threading.Thread(target=_req_primero)
    t2 = threading.Thread(target=_req_segundo)
    t1.start()
    t2.start()
    t2.join(timeout=6)
    assert "segundo" in resultados, "el segundo request nunca respondio -- posible bloqueo"
    assert resultados["segundo"] == 429
    puede_continuar.set()
    t1.join(timeout=6)
    assert "primero" in resultados, "el primer request nunca respondio -- posible deadlock"
    assert resultados["primero"] == 200
    assert not server._oportunidades_lock.locked()  # liberado limpio al final


# --- 6/7) baseline y shadow permanecen identicos -----------------------------

def test_baseline_identico_con_y_sin_contencion(monkeypatch):
    """El wrapper no debe alterar ningun campo de la respuesta real -- se
    compara la respuesta de un request normal contra la misma llamada
    directa a la implementacion."""
    _mock_una_candidata(monkeypatch)
    r_wrapper = _client().get("/api/radar-oportunidades")
    with server.app.test_request_context("/api/radar-oportunidades"):
        r_directo = server._api_radar_oportunidades_impl()
    assert r_wrapper.get_json()["oportunidades"] == r_directo.get_json()["oportunidades"]
    assert r_wrapper.get_json()["oportunidades"][0]["estado_final"] == r_directo.get_json()["oportunidades"][0]["estado_final"]


def test_shadow_decision_shadow_identico_con_el_wrapper(monkeypatch):
    """`decision_shadow` (Fase 3.4) debe seguir viajando igual -- el guard
    de reentrancia no toca ningun campo de la respuesta."""
    monkeypatch.setattr(reg, "live_opportunities", lambda market_date: [
        {"ticker": "AAA", "price_at_detection": 10.0, "stage": "INICIO",
         "direction": "ALCISTA", "timing_deteccion_hoy": "al_comienzo", "racional_available": True},
    ])
    monkeypatch.setattr(_rw, "get_last_quotes", lambda: {})
    r = _client().get("/api/radar-oportunidades")
    body = r.get_json()["oportunidades"][0]
    assert "decision_shadow" in body
    assert "shadow_differs" in body


# --- 8) 3.3/3.4/3.5 siguen funcionando dentro del wrapper -------------------

def test_bloques_de_3_3_3_4_3_5_siguen_corriendo_dentro_del_wrapper(monkeypatch):
    """El wrapper envuelve la funcion completa -- confirma que las
    escrituras de elegibilidad (3.3)/observacion (3.4)/activacion (3.5)
    siguen intentandose (sus propios try/except internos ya las protegen
    de fallos reales de DB, esto solo confirma que el codigo se alcanza)."""
    from atlas_live.core import knowledge_eligibility_registry as ker

    llamado = {"eligibility": False}
    orig = ker.record_eligibility_snapshot

    def _spy(*a, **k):
        llamado["eligibility"] = True
        return orig(*a, **k)

    monkeypatch.setattr(ker, "record_eligibility_snapshot", _spy)
    monkeypatch.setattr(reg, "live_opportunities", lambda market_date: [
        {"ticker": "AAA", "price_at_detection": 10.0, "stage": "PREPARACION", "racional_available": True},
    ])
    monkeypatch.setattr(_rw, "get_last_quotes", lambda: {})
    r = _client().get("/api/radar-oportunidades")
    assert r.status_code == 200
    assert llamado["eligibility"] is True


def test_activation_off_permanece_off_con_el_wrapper(monkeypatch):
    from atlas_live.core import activation_registry as areg

    orig_db = areg.DB_PATH
    import tempfile
    import uuid as _uuid
    from pathlib import Path
    areg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_areg_wrapper_{_uuid.uuid4().hex}.db"
    try:
        assert areg.get_mechanism_state() == "OFF"
        _mock_una_candidata(monkeypatch)
        r = _client().get("/api/radar-oportunidades")
        assert r.status_code == 200
        # el request no debio activar nada -- sigue OFF, sin ninguna fila nueva
        assert areg.get_mechanism_state() == "OFF"
        assert areg.list_activation_states() == []
    finally:
        areg.DB_PATH = orig_db


# --- escaneo estático: fail-safe / sin sleeps ------------------------------

def test_wrapper_usa_acquire_no_bloqueante_sin_sleeps():
    import inspect

    src = inspect.getsource(server.api_radar_oportunidades)
    assert "blocking=False" in src
    assert "time.sleep" not in src
    assert "sleep(" not in src


def test_lock_siempre_se_libera_incluso_si_el_impl_lanza(monkeypatch):
    def _impl_que_lanza():
        raise RuntimeError("boom")

    monkeypatch.setattr(server, "_api_radar_oportunidades_impl", _impl_que_lanza)
    try:
        _client().get("/api/radar-oportunidades")
    except Exception:
        pass
    # pase lo que pase adentro, el lock debe quedar libre para el siguiente request
    assert not server._oportunidades_lock.locked()
