"""Tests de `GET /api/admin/capacity` (protegido) y `GET /api/capacidad-resumen`
(público) -- Capacity Monitor, 2026-09-07, autorizado explícitamente.
Mismo patrón sin red/sin hilos de fondo que `test_knowledge_eligibility_endpoint.py`."""

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

from atlas_live import capacity_monitor as cm  # noqa: E402


def _client():
    return server.app.test_client()


_SINTETICO = {
    "generated_at": "2026-09-07T00:00:00+00:00",
    "total_bytes": 10_000_000_000,
    "used_bytes": 6_000_000_000,
    "free_bytes": 4_000_000_000,
    "used_pct": 60.0,
    "free_pct": 40.0,
    "top_consumers": [{"path": "radar_candidates.db", "size_bytes": 500_000}],
    "growth": {"mb_per_hour": 4.0, "mb_per_day": 96.0, "history_status": "ok"},
    "projection": {"days_to_80_pct": 20.8, "days_to_90_pct": 31.25, "days_to_95_pct": 36.5, "days_to_100_pct": 41.6},
    "status": "OK",
}


# --- /api/admin/capacity (protegido) ---------------------------------------

def test_admin_capacity_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().get("/api/admin/capacity")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_admin_capacity_con_token_delega_en_capacity_report(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    try:
        monkeypatch.setattr(cm, "capacity_report", lambda: _SINTETICO)
        r = _client().get("/api/admin/capacity", headers={"X-Admin-Token": "secreto-real"})
        assert r.status_code == 200
        assert r.get_json() == _SINTETICO
        assert "top_consumers" in r.get_json()
    finally:
        os.environ.pop("ATLAS_ADMIN_TOKEN", None)


def test_admin_capacity_token_incorrecto_rechaza():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    try:
        r = _client().get("/api/admin/capacity", headers={"X-Admin-Token": "otro-valor"})
        assert r.status_code == 403
    finally:
        os.environ.pop("ATLAS_ADMIN_TOKEN", None)


# --- /api/capacidad-resumen (público) ---------------------------------------

def test_capacidad_resumen_responde_200_sin_token():
    r = _client().get("/api/capacidad-resumen")
    assert r.status_code == 200


def test_capacidad_resumen_delega_en_capacity_summary_public(monkeypatch):
    publico = {k: v for k, v in _SINTETICO.items() if k != "top_consumers"}
    monkeypatch.setattr(cm, "capacity_summary_public", lambda: publico)
    r = _client().get("/api/capacidad-resumen")
    assert r.status_code == 200
    assert r.get_json() == publico


def test_capacidad_resumen_nunca_expone_top_consumers():
    r = _client().get("/api/capacidad-resumen")
    assert r.status_code == 200
    assert "top_consumers" not in r.get_json()
