"""Tests de `GET /api/admin/u3c3-exclusive-diagnostics` (2026-09-02,
autorizado explícitamente) -- mismo patrón sin red/sin hilos de fondo que
`test_u3c3_quality_report_endpoint.py`. Confirma: token obligatorio, y que
con token responde con el reporte real (mockeado acá para no depender de
datos)."""

import os

import atlas_live.backtest.seed_import as _si
import atlas_live.market_view as _mv
import atlas_live.radar.radar_worker as _rw
import atlas_live.scan_worker as _sw

_orig_seed = _si.import_all_seeds
_orig_refresh = _sw.start_background_refresh
_orig_radar = _rw.start_universe_radar
_orig_market_view = _mv.start_market_view
_si.import_all_seeds = lambda *a, **k: None
_sw.start_background_refresh = lambda *a, **k: None
_rw.start_universe_radar = lambda *a, **k: None
_mv.start_market_view = lambda *a, **k: None
try:
    from atlas_live import server  # noqa: E402
finally:
    _si.import_all_seeds = _orig_seed
    _sw.start_background_refresh = _orig_refresh
    _rw.start_universe_radar = _orig_radar
    _mv.start_market_view = _orig_market_view

from atlas_live.radar import u3c3_exclusive_diagnostics as u3d  # noqa: E402


def _client():
    return server.app.test_client()


def test_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().get("/api/admin/u3c3-exclusive-diagnostics")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_con_token_devuelve_el_reporte(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    monkeypatch.setattr(u3d, "full_report", lambda: {"market_dates": list(u3d.DIAGNOSTIC_MARKET_DATES)})
    try:
        r = _client().get("/api/admin/u3c3-exclusive-diagnostics?token=secreto-real")
        assert r.status_code == 200
        assert r.get_json()["market_dates"] == ["2026-08-26", "2026-08-27", "2026-08-28", "2026-08-31"]
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]
