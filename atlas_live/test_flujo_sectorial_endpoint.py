"""Tests del endpoint público GET /api/flujo-sectorial (2026-08-18, cierre
de arquitectura) -- sirve el snapshot ya cacheado por scan_worker.STATE,
sin recalcular nada. Sin red, sin tocar la base real."""

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


def _client():
    return server.app.test_client()


def test_antes_del_primer_ciclo_devuelve_vacio_sin_romper():
    orig = _sw.STATE.sector_flow_snapshot
    _sw.STATE.sector_flow_snapshot = None
    try:
        r = _client().get("/api/flujo-sectorial")
        assert r.status_code == 200
        body = r.get_json()
        assert body["generated_at"] is None
        assert body["sectores"] == []
        assert body["symbol_sector_map"] == {}
        assert "cobertura" in body and body["cobertura"]
    finally:
        _sw.STATE.sector_flow_snapshot = orig


def test_sirve_el_snapshot_cacheado_tal_cual():
    orig = _sw.STATE.sector_flow_snapshot
    snapshot = {
        "generated_at": "2026-08-18T09:00:00+00:00",
        "cobertura": "Racional (watchlist escaneado por Yahoo en este ciclo)",
        "sectores": [
            {"sector": "Energy", "money_flow_score": 62.5, "stock_count": 12,
             "avg_change_percent": 1.8, "avg_relative_volume": 2.1, "top_stocks": ["XOM", "CVX"]},
        ],
        "symbol_sector_map": {"XOM": "Energy", "CVX": "Energy"},
    }
    _sw.STATE.sector_flow_snapshot = snapshot
    try:
        r = _client().get("/api/flujo-sectorial")
        assert r.status_code == 200
        body = r.get_json()
        assert body == snapshot
    finally:
        _sw.STATE.sector_flow_snapshot = orig
