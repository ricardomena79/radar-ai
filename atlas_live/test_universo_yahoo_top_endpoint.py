"""Tests de GET /api/universo-yahoo/top (2026-09-06) -- TOP 100 EN
MOVIMIENTO. Confirma: sirve el ranking mecánico sobre
radar_worker.get_last_quotes() (sin red), la ruta estática /top nunca es
capturada por /<symbol>, radar caído/sin quotes no fabrica nada, y un
clic en el detalle sigue usando exclusivamente el endpoint puntual ya
existente (1 sola llamada)."""

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

from atlas.data.models.quote import Quote  # noqa: E402
from atlas_live.data_fusion.yahoo_finance_live_provider import YahooFinanceLiveProvider  # noqa: E402


def _client():
    return server.app.test_client()


def _fake_quote(change_percent, relative_volume, last_price=10.0, volume=100_000, name="Fake Corp"):
    return types.SimpleNamespace(
        change_percent=change_percent, relative_volume=relative_volume,
        last_price=last_price, volume=volume, name=name,
    )


def test_top_sirve_ranking_real_sobre_quotes_existentes(monkeypatch):
    quotes = {
        "AAA": _fake_quote(8.0, 4.0, last_price=50, volume=2_000_000),
        "EEE": _fake_quote(-3.0, 5.0, last_price=5, volume=50_000),
    }
    monkeypatch.setattr(_rw, "get_last_quotes", lambda: quotes)
    r = _client().get("/api/universo-yahoo/top")
    assert r.status_code == 200
    body = r.get_json()
    assert body["quotes_disponibles"] == 2
    assert body["candidatos_tras_filtros"] == 1
    assert len(body["top"]) == 1
    assert body["top"][0]["symbol"] == "AAA"
    assert body["top"][0]["score"] == 32.0
    assert "ultimo_sweep_at" in body


def test_top_sin_quotes_no_fabrica_nada(monkeypatch):
    monkeypatch.setattr(_rw, "get_last_quotes", lambda: {})
    r = _client().get("/api/universo-yahoo/top")
    assert r.status_code == 200
    body = r.get_json()
    assert body["quotes_disponibles"] == 0
    assert body["candidatos_tras_filtros"] == 0
    assert body["top"] == []


def test_top_nunca_capturado_como_symbol_detalle(monkeypatch):
    """La ruta estática /api/universo-yahoo/top debe resolver al handler
    del ranking, NUNCA al de detalle interpretando 'top' como ticker."""
    llamado_detalle = {"veces": 0}

    def fake_get_quote(self, symbol):
        llamado_detalle["veces"] += 1
        return _fake_quote(1.0, 1.0)

    monkeypatch.setattr(YahooFinanceLiveProvider, "get_quote", fake_get_quote)
    monkeypatch.setattr(_rw, "get_last_quotes", lambda: {})
    r = _client().get("/api/universo-yahoo/top")
    assert r.status_code == 200
    body = r.get_json()
    assert "top" in body and "quotes_disponibles" in body  # forma de la respuesta del ranking, no del detalle
    assert llamado_detalle["veces"] == 0  # jamás se llamó a Yahoo por "top"


def test_top_no_dispara_ninguna_llamada_a_proveedores(monkeypatch):
    """Confirma 'cero llamadas nuevas a proveedores para construir el
    ranking': se espía tanto Tradier (build_tradier_provider) como Yahoo
    (YahooFinanceLiveProvider.get_quote) y ninguno debe invocarse."""
    from atlas_live.data_fusion import universe_quotes as uq

    llamadas = {"tradier": 0, "yahoo": 0}
    monkeypatch.setattr(uq, "build_tradier_provider", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debia llamarse")))

    def fake_get_quote(self, symbol):
        llamadas["yahoo"] += 1
        return _fake_quote(1.0, 1.0)

    monkeypatch.setattr(YahooFinanceLiveProvider, "get_quote", fake_get_quote)
    monkeypatch.setattr(_rw, "get_last_quotes", lambda: {
        "AAA": _fake_quote(8.0, 4.0, last_price=50, volume=2_000_000),
    })
    r = _client().get("/api/universo-yahoo/top")
    assert r.status_code == 200
    assert llamadas["yahoo"] == 0


def test_clic_en_fila_del_top_reutiliza_endpoint_puntual_existente(monkeypatch):
    """El detalle de una fila del Top 100 usa el MISMO endpoint ya
    aprobado (/api/universo-yahoo/<symbol>), con exactamente 1 llamada
    real al proveedor por click -- no un mecanismo nuevo/paralelo."""
    calls = []

    def fake_get_quote(self, symbol):
        calls.append(symbol)
        return Quote(
            symbol=symbol, name="Fake Corp", last_price=10.0, change_percent=8.0,
            volume=100_000, open=None, high=None, low=None, previous_close=None,
        )

    monkeypatch.setattr(YahooFinanceLiveProvider, "get_quote", fake_get_quote)
    r = _client().get("/api/universo-yahoo/AAA")
    assert r.status_code == 200
    assert calls == ["AAA"]
