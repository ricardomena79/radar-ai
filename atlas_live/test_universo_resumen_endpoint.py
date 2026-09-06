"""Tests del endpoint público GET /api/universo-resumen (2026-09-05, Cabina
nueva) -- confirma que reutiliza exclusivamente datos ya calculados
(clasificación cacheada del universo + últimas quotes en memoria del
radar), nunca dispara una consulta nueva a Tradier. Sin red, sin tocar la
base real."""

import types

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

from atlas_live.market_study import universe as broad_universe  # noqa: E402


def _client():
    return server.app.test_client()


def _fake_quote(volume, relative_volume):
    return types.SimpleNamespace(volume=volume, relative_volume=relative_volume)


def _patch_universe(monkeypatch, meta, racional, quotes):
    monkeypatch.setattr(broad_universe, "fetch_broad_universe_meta", lambda use_cache=True: meta)
    monkeypatch.setattr(
        broad_universe, "is_leveraged_etf_name",
        lambda name: bool(name) and "3X" in name.upper(),
    )
    monkeypatch.setattr(broad_universe, "racional_symbols", lambda: racional)
    monkeypatch.setattr(_rw, "get_last_quotes", lambda: quotes)


def test_universo_total_cuenta_equity_y_etf_apalancado_no_el_resto(monkeypatch):
    meta = {
        "AAA": {"type": "EQUITY", "name": "Alpha Corp"},
        "BBB": {"type": "EQUITY", "name": "Beta Corp"},
        "SOXL": {"type": "ETF", "name": "Direxion 3X Semiconductor"},
        "SPY": {"type": "ETF", "name": "SPDR S&P 500"},  # ETF no apalancado -- excluido
        "WNT": {"type": "WARRANT", "name": "Some Warrant"},
    }
    _patch_universe(monkeypatch, meta, racional={"AAA"}, quotes={})
    r = _client().get("/api/universo-resumen")
    assert r.status_code == 200
    body = r.get_json()
    assert body["universo_total"] == 3  # AAA, BBB, SOXL -- nunca SPY ni WNT
    assert body["disponibles_racional"] == 1  # solo AAA


def test_sin_quotes_todavia_top_volumen_vacio_sin_romper(monkeypatch):
    meta = {"AAA": {"type": "EQUITY", "name": "Alpha Corp"}}
    _patch_universe(monkeypatch, meta, racional=set(), quotes={})
    r = _client().get("/api/universo-resumen")
    assert r.status_code == 200
    body = r.get_json()
    assert body["top_volumen"] == []
    assert body["con_dato_de_volumen_ahora"] == 0


def test_top_volumen_ordenado_descendente_y_acotado_a_10(monkeypatch):
    meta = {f"T{i}": {"type": "EQUITY", "name": f"Ticker {i}"} for i in range(15)}
    quotes = {f"T{i}": _fake_quote(volume=i * 1000, relative_volume=1.0 + i * 0.1) for i in range(15)}
    _patch_universe(monkeypatch, meta, racional={"T14"}, quotes=quotes)
    r = _client().get("/api/universo-resumen")
    body = r.get_json()
    top = body["top_volumen"]
    assert len(top) == 10
    volumes = [row["volume"] for row in top]
    assert volumes == sorted(volumes, reverse=True)
    assert top[0]["ticker"] == "T14"
    assert top[0]["racional_available"] is True
    assert top[1]["racional_available"] is False
    assert body["con_dato_de_volumen_ahora"] == 15


def test_quote_sin_volumen_no_entra_en_top_volumen(monkeypatch):
    meta = {"AAA": {"type": "EQUITY", "name": "Alpha"}, "BBB": {"type": "EQUITY", "name": "Beta"}}
    quotes = {
        "AAA": _fake_quote(volume=None, relative_volume=None),
        "BBB": _fake_quote(volume=500, relative_volume=1.2),
    }
    _patch_universe(monkeypatch, meta, racional=set(), quotes=quotes)
    r = _client().get("/api/universo-resumen")
    body = r.get_json()
    assert [row["ticker"] for row in body["top_volumen"]] == ["BBB"]
    assert body["con_dato_de_volumen_ahora"] == 2  # sigue contando ambas quotes recibidas
