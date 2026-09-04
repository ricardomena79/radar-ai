"""Tests de los endpoints de Hito 3, Fase 3.6 (2026-09-03, autorizado
explícitamente en Plan Mode, revisión corregida) -- mismo patrón sin
red/sin hilos de fondo que `test_activation_endpoint.py`."""

import os

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

from atlas_live.core import continuous_evaluation_registry as cer  # noqa: E402


def _client():
    return server.app.test_client()


# --- POST /api/admin/continuous-evaluation-run ------------------------------

def test_run_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().post("/api/admin/continuous-evaluation-run")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_run_condicion_puntual_con_token(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    capturado = {}

    def _fake(direction, timing_deteccion, methodology_version, as_of_date, n_ventana, auto_revoke):
        capturado["args"] = (direction, timing_deteccion, methodology_version, n_ventana, auto_revoke)
        return {"evaluation_state": "VALIDO", "direction": direction}

    monkeypatch.setattr(cer, "evaluate_condition", _fake)
    try:
        r = _client().post(
            "/api/admin/continuous-evaluation-run"
            "?token=secreto-real&direction=ALCISTA&timing_deteccion=al_comienzo"
            "&methodology_version=v1&as_of_date=2026-08-24&n_ventana=500&auto_revoke=false"
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body["n_condiciones"] == 1
        assert body["auto_revoke"] is False
        assert capturado["args"] == ("ALCISTA", "al_comienzo", "v1", 500, False)
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_run_auto_revoke_por_defecto_es_false(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    capturado = {}

    def _fake(direction, timing_deteccion, methodology_version, as_of_date, n_ventana, auto_revoke):
        capturado["auto_revoke"] = auto_revoke
        return {"evaluation_state": "VALIDO"}

    monkeypatch.setattr(cer, "evaluate_condition", _fake)
    try:
        r = _client().post(
            "/api/admin/continuous-evaluation-run"
            "?token=secreto-real&direction=ALCISTA&timing_deteccion=al_comienzo&methodology_version=v1"
        )
        assert r.status_code == 200
        assert capturado["auto_revoke"] is False
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_run_condicion_incompleta_devuelve_400(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    try:
        r = _client().post("/api/admin/continuous-evaluation-run?token=secreto-real&direction=ALCISTA")
        assert r.status_code == 400
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_run_sin_condicion_usa_list_eligible_conditions(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    monkeypatch.setattr(cer, "list_eligible_conditions", lambda: [("ALCISTA", "al_comienzo", "v1"), ("BAJISTA", "agotamiento", "v1")])
    monkeypatch.setattr(cer, "evaluate_condition", lambda **k: {"evaluation_state": "VALIDO"})
    try:
        r = _client().post("/api/admin/continuous-evaluation-run?token=secreto-real")
        assert r.status_code == 200
        assert r.get_json()["n_condiciones"] == 2
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


# --- GET /api/admin/continuous-evaluation-report ----------------------------

def test_report_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().get("/api/admin/continuous-evaluation-report")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_report_con_token_delega_y_pasa_los_filtros(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    capturado = {}

    def _fake(market_date=None, evaluation_state=None, limit=5000):
        capturado["args"] = (market_date, evaluation_state, limit)
        return {"ok": True, "n_eventos": 0, "conteos_por_estado": {}, "eventos": []}

    monkeypatch.setattr(cer, "full_continuous_evaluation_report", _fake)
    try:
        r = _client().get(
            "/api/admin/continuous-evaluation-report?token=secreto-real&market_date=2026-08-24&evaluation_state=DEGRADADO&limit=10"
        )
        assert r.status_code == 200
        assert capturado["args"] == ("2026-08-24", "DEGRADADO", 10)
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_report_ok_false_devuelve_500(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    monkeypatch.setattr(
        cer, "full_continuous_evaluation_report",
        lambda market_date=None, evaluation_state=None, limit=5000: {"ok": False, "error": "fallo"},
    )
    try:
        r = _client().get("/api/admin/continuous-evaluation-report?token=secreto-real")
        assert r.status_code == 500
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]
