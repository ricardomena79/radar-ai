"""Tests de `GET /api/admin/shadow-observation-report` (2026-09-03,
Hito 3, Fase 3.4, autorizado explícitamente en Plan Mode) -- mismo patrón
sin red/sin hilos de fondo que `test_knowledge_eligibility_endpoint.py`."""

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

from atlas_live.core import shadow_observation_registry as sor  # noqa: E402


def _client():
    return server.app.test_client()


def test_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().get("/api/admin/shadow-observation-report")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_con_token_delega_y_pasa_los_filtros(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    capturado = {}

    def _fake(market_date=None, eligibility_state=None, limit=5000):
        capturado["args"] = (market_date, eligibility_state, limit)
        return {"ok": True, "n_observaciones": 0, "eventos": [], "agregado_por_elegibilidad": {}}

    monkeypatch.setattr(sor, "full_shadow_observation_report", _fake)
    try:
        r = _client().get(
            "/api/admin/shadow-observation-report"
            "?token=secreto-real&market_date=2026-08-24&eligibility_state=ELEGIBLE&limit=10"
        )
        assert r.status_code == 200
        assert r.get_json()["ok"] is True
        assert capturado["args"] == ("2026-08-24", "ELEGIBLE", 10)
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_ok_false_devuelve_500(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    monkeypatch.setattr(
        sor, "full_shadow_observation_report",
        lambda market_date=None, eligibility_state=None, limit=5000: {"ok": False, "error": "fallo"},
    )
    try:
        r = _client().get("/api/admin/shadow-observation-report?token=secreto-real")
        assert r.status_code == 500
        assert r.get_json()["ok"] is False
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_sin_filtros_usa_defaults_none_y_limit_5000(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    capturado = {}

    def _fake(market_date=None, eligibility_state=None, limit=5000):
        capturado["args"] = (market_date, eligibility_state, limit)
        return {"ok": True}

    monkeypatch.setattr(sor, "full_shadow_observation_report", _fake)
    try:
        r = _client().get("/api/admin/shadow-observation-report?token=secreto-real")
        assert r.status_code == 200
        assert capturado["args"] == (None, None, 5000)
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]
