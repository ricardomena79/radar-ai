"""Tests del endpoint público GET /api/probabilidad-umbrales -- solo lectura,
sin red ni base real (el loader de experiencia se reemplaza)."""

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


import atlas_live.radar.threshold_probability as _tp


def _client():
    return server.app.test_client()


def _fake_rows(as_of):
    rows = []
    for v in [0.0, 1.0, 3.0, 6.0, 11.0]:
        rows.append({"direction": "ALCISTA", "timing_deteccion": "INICIO", "max_advance_pct": v})
    rows.append({"direction": "BAJISTA", "timing_deteccion": "FLUJO_VENDEDOR", "max_advance_pct": 0.2})
    return rows


def test_endpoint_devuelve_grupos_con_umbrales(monkeypatch):
    _tp._reset_cache_for_tests()
    monkeypatch.setattr(_tp.les, "_load_rows_from_db_by_stage", _fake_rows)
    r = _client().get("/api/probabilidad-umbrales")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["umbrales_pct"] == [2.0, 5.0, 10.0]
    g = body["grupos"][0]  # ordenado por n descendente
    assert (g["direction"], g["stage"], g["n"]) == ("ALCISTA", "INICIO", 5)
    assert g["umbrales"]["2"]["aciertos"] == 3
    assert g["umbrales"]["10"]["aciertos"] == 1
    assert g["validation_state"] == "MUESTRA_INSUFICIENTE"
    _tp._reset_cache_for_tests()


def test_endpoint_no_rompe_si_falla_la_fuente(monkeypatch):
    _tp._reset_cache_for_tests()

    def boom(as_of):
        raise RuntimeError("db caida")

    monkeypatch.setattr(_tp.les, "_load_rows_from_db_by_stage", boom)
    r = _client().get("/api/probabilidad-umbrales")
    assert r.status_code == 200
    assert r.get_json()["ok"] is False
    _tp._reset_cache_for_tests()
