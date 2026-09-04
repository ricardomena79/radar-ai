"""Tests de los endpoints de Hito 3, Fase 3.5 (2026-09-03, autorizado
explícitamente en Plan Mode) -- mismo patrón sin red/sin hilos de fondo
que `test_shadow_observation_endpoint.py`."""

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

from atlas_live.core import activation_registry as areg  # noqa: E402


def _client():
    return server.app.test_client()


# --- GET/POST /api/admin/activation-mechanism-state -----------------------

def test_mechanism_state_get_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().get("/api/admin/activation-mechanism-state")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_mechanism_state_post_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().post("/api/admin/activation-mechanism-state?state=ON_CONTROLADO&reason=x")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_mechanism_state_get_devuelve_off_por_defecto(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    monkeypatch.setattr(areg, "get_mechanism_state", lambda: "OFF")
    monkeypatch.setattr(areg, "get_mechanism_history", lambda: [])
    monkeypatch.setattr(areg, "list_revocations", lambda: [])
    try:
        r = _client().get("/api/admin/activation-mechanism-state?token=secreto-real")
        assert r.status_code == 200
        assert r.get_json()["mechanism_state"] == "OFF"
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_mechanism_state_post_aplica_y_confirma(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    capturado = {}

    def _fake_set(state, reason):
        capturado["args"] = (state, reason)
        return True

    monkeypatch.setattr(areg, "set_mechanism_state", _fake_set)
    monkeypatch.setattr(areg, "get_mechanism_state", lambda: "ON_CONTROLADO")
    try:
        r = _client().post("/api/admin/activation-mechanism-state?token=secreto-real&state=ON_CONTROLADO&reason=prueba+controlada")
        assert r.status_code == 200
        assert r.get_json()["ok"] is True
        assert capturado["args"] == ("ON_CONTROLADO", "prueba controlada")
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_mechanism_state_post_valor_invalido_devuelve_400(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"

    def _fake_set(state, reason):
        raise ValueError(f"state debe ser uno de ('OFF', 'ON_CONTROLADO'), recibido: {state!r}")

    monkeypatch.setattr(areg, "set_mechanism_state", _fake_set)
    try:
        r = _client().post("/api/admin/activation-mechanism-state?token=secreto-real&state=ALGO_RARO&reason=x")
        assert r.status_code == 400
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


# --- POST /api/admin/activation-revoke -------------------------------------

def test_revoke_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().post("/api/admin/activation-revoke?scope=GLOBAL&reason=x")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_revoke_global_con_token(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    capturado = {}

    def _fake_revoke(scope, reason, direction=None, timing_deteccion=None, methodology_version=None):
        capturado["args"] = (scope, reason, direction, timing_deteccion, methodology_version)
        return True

    monkeypatch.setattr(areg, "revoke", _fake_revoke)
    try:
        r = _client().post("/api/admin/activation-revoke?token=secreto-real&scope=GLOBAL&reason=incidente+real")
        assert r.status_code == 200
        assert r.get_json()["ok"] is True
        assert capturado["args"] == ("GLOBAL", "incidente real", None, None, None)
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_revoke_condicion_incompleta_devuelve_400(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"

    def _fake_revoke(scope, reason, direction=None, timing_deteccion=None, methodology_version=None):
        raise ValueError("scope=CONDICION requiere direction, timing_deteccion y methodology_version")

    monkeypatch.setattr(areg, "revoke", _fake_revoke)
    try:
        r = _client().post("/api/admin/activation-revoke?token=secreto-real&scope=CONDICION&reason=x&direction=ALCISTA")
        assert r.status_code == 400
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


# --- GET /api/admin/activation-report ---------------------------------------

def test_report_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().get("/api/admin/activation-report")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_report_con_token_delega_y_pasa_los_filtros(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    capturado = {}

    def _fake(market_date=None, activation_state=None, limit=5000):
        capturado["args"] = (market_date, activation_state, limit)
        return {"ok": True, "n_eventos": 0, "conteos_por_estado": {}, "eventos": []}

    monkeypatch.setattr(areg, "full_activation_report", _fake)
    try:
        r = _client().get(
            "/api/admin/activation-report?token=secreto-real&market_date=2026-08-24&activation_state=ACTIVADO&limit=10"
        )
        assert r.status_code == 200
        assert r.get_json()["ok"] is True
        assert capturado["args"] == ("2026-08-24", "ACTIVADO", 10)
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_report_ok_false_devuelve_500(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    monkeypatch.setattr(
        areg, "full_activation_report",
        lambda market_date=None, activation_state=None, limit=5000: {"ok": False, "error": "fallo"},
    )
    try:
        r = _client().get("/api/admin/activation-report?token=secreto-real")
        assert r.status_code == 500
        assert r.get_json()["ok"] is False
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]
