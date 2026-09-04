"""HITO 4 -- Fase 4.2 (2026-09-04, autorizado explícitamente en Plan Mode):
tests del endpoint `/api/admin/hito3-integrity-check` -- mismo patrón
sin red/sin hilos de fondo que `test_continuous_evaluation_endpoint.py`."""

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

from atlas_live.core import hito3_integrity_check as hic  # noqa: E402


def _client():
    return server.app.test_client()


def test_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().get("/api/admin/hito3-integrity-check")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_con_token_delega_a_run_all_checks(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    monkeypatch.setattr(hic, "run_all_checks", lambda: {"ok": True, "checks": {}})
    try:
        r = _client().get("/api/admin/hito3-integrity-check?token=secreto-real")
        assert r.status_code == 200
        assert r.get_json() == {"ok": True, "checks": {}}
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_ok_false_devuelve_500(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    monkeypatch.setattr(hic, "run_all_checks", lambda: {"ok": False, "checks": {"x": {"ok": False}}})
    try:
        r = _client().get("/api/admin/hito3-integrity-check?token=secreto-real")
        assert r.status_code == 500
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_contra_el_repo_real_con_token_da_200_ok_true():
    """Sin mockear -- confirma, end-to-end vía el endpoint real, que Hito 3
    sigue íntegro HOY."""
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    try:
        r = _client().get("/api/admin/hito3-integrity-check?token=secreto-real")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]
