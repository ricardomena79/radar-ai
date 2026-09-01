"""Tests del endpoint admin de solo lectura /api/admin/candidate-timeline
(Fase 5, 2026-08-17) -- mismo patrón que los demás endpoints admin: sin
red, sin tocar la base real (candidate_timeline se mockea)."""

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

from atlas_live.memory import market_hours  # noqa: E402
from atlas_live.radar import candidate_registry as reg  # noqa: E402


def _client():
    return server.app.test_client()


def test_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().get("/api/admin/candidate-timeline?ticker=ZIM")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_con_token_sin_ticker_rechaza():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    try:
        r = _client().get("/api/admin/candidate-timeline?token=secreto-real")
        assert r.status_code == 400
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_con_token_y_ticker_devuelve_el_timeline_real():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    captured = {}
    orig = reg.candidate_timeline

    def _fake_timeline(ticker, market_date):
        captured["ticker"] = ticker
        captured["market_date"] = market_date
        return {"ticker": ticker, "market_date": market_date, "detection": {"price_at_detection": 28.14},
                "observaciones": [], "transiciones_alerta": [], "outcome": None, "racional_available": True}

    reg.candidate_timeline = _fake_timeline
    try:
        r = _client().get("/api/admin/candidate-timeline?token=secreto-real&ticker=zim&date=2026-08-17")
        assert r.status_code == 200
        body = r.get_json()
        assert body["detection"]["price_at_detection"] == 28.14
        assert captured["ticker"] == "ZIM"  # normalizado a mayúsculas por el endpoint
        assert captured["market_date"] == "2026-08-17"
    finally:
        reg.candidate_timeline = orig
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_sin_date_usa_la_fecha_de_mercado_actual():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    orig_timeline = reg.candidate_timeline
    orig_market_date = market_hours.market_date
    captured = {}

    reg.candidate_timeline = lambda ticker, market_date: captured.update(
        ticker=ticker, market_date=market_date) or {}
    market_hours.market_date = lambda: "2026-08-17"
    try:
        r = _client().get("/api/admin/candidate-timeline?token=secreto-real&ticker=ZIM")
        assert r.status_code == 200
        assert captured["market_date"] == "2026-08-17"
    finally:
        reg.candidate_timeline = orig_timeline
        market_hours.market_date = orig_market_date
        del os.environ["ATLAS_ADMIN_TOKEN"]
