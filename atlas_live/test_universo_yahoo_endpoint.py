"""Tests de los endpoints de Universo Yahoo (2026-09-06, Cabina):

- GET /api/universo-yahoo -- identidad completa del universo amplio,
  reutilizando fetch_broad_universe_meta() (sin red nueva, sin campos de
  Racional).
- GET /api/universo-yahoo/<symbol> -- cotización puntual vía
  YahooFinanceLiveProvider, sin pasar por AtlasScore/MomentumScore/
  MoneyFlowEngine/DecisionEngine.

Sin red real: se monkeypatchea fetch_broad_universe_meta() y
YahooFinanceLiveProvider.get_quote()."""

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
from atlas.data.providers.base import ProviderError, QuoteNotFoundError  # noqa: E402
from atlas_live.data_fusion.yahoo_finance_live_provider import YahooFinanceLiveProvider  # noqa: E402
from atlas_live.market_study import universe as broad_universe  # noqa: E402


def _client():
    return server.app.test_client()


def _fake_quote(**overrides):
    base = dict(
        symbol="AAPL", name="Apple Inc.", last_price=228.4, change_percent=0.8,
        volume=45_000_000, open=227.0, high=229.1, low=226.5, previous_close=226.6,
        market_cap=3_500_000_000_000, sector="Technology", industry="Consumer Electronics",
        float_shares=None, average_volume=50_000_000, relative_volume=0.9,
        source="yahoo_finance", price_type="regular", market_state="REGULAR",
    )
    base.update(overrides)
    return Quote(**base)


# --------------------------- /api/universo-yahoo ---------------------------

def test_universo_yahoo_lista_identidad_sin_campos_de_racional(monkeypatch):
    meta = {
        "AAPL": {"type": "EQUITY", "name": "Apple Inc.", "exchange": "NASDAQ"},
        "SPY": {"type": "ETF", "name": "SPDR S&P 500", "exchange": "ARCA"},
    }
    monkeypatch.setattr(broad_universe, "fetch_broad_universe_meta", lambda use_cache=True: meta)
    r = _client().get("/api/universo-yahoo")
    assert r.status_code == 200
    body = r.get_json()
    assert body["total"] == 2
    assert body["instrumentos"] == [
        {"symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ", "type": "EQUITY"},
        {"symbol": "SPY", "name": "SPDR S&P 500", "exchange": "ARCA", "type": "ETF"},
    ]
    for row in body["instrumentos"]:
        assert "racional_available" not in row
        assert "racional" not in row


def test_universo_yahoo_vacio_no_rompe(monkeypatch):
    monkeypatch.setattr(broad_universe, "fetch_broad_universe_meta", lambda use_cache=True: {})
    r = _client().get("/api/universo-yahoo")
    assert r.status_code == 200
    body = r.get_json()
    assert body["total"] == 0
    assert body["instrumentos"] == []


# ----------------------- /api/universo-yahoo/<symbol> -----------------------

def test_universo_yahoo_detalle_devuelve_campos_reales(monkeypatch):
    monkeypatch.setattr(YahooFinanceLiveProvider, "get_quote", lambda self, symbol: _fake_quote(symbol=symbol))
    r = _client().get("/api/universo-yahoo/AAPL")
    assert r.status_code == 200
    body = r.get_json()
    assert body["symbol"] == "AAPL"
    for campo in (
        "last_price", "change_percent", "volume", "open", "high", "low", "previous_close",
        "market_cap", "sector", "industry", "average_volume", "relative_volume",
        "price_type", "market_state",
    ):
        assert campo in body
    assert "racional_available" not in body


def test_universo_yahoo_detalle_normaliza_symbol_a_mayusculas(monkeypatch):
    captured = {}

    def fake_get_quote(self, symbol):
        captured["symbol"] = symbol
        return _fake_quote(symbol=symbol)

    monkeypatch.setattr(YahooFinanceLiveProvider, "get_quote", fake_get_quote)
    r = _client().get("/api/universo-yahoo/aapl")
    assert r.status_code == 200
    assert captured["symbol"] == "AAPL"


def test_universo_yahoo_detalle_sin_dato_devuelve_404(monkeypatch):
    def fake_get_quote(self, symbol):
        raise QuoteNotFoundError(symbol)

    monkeypatch.setattr(YahooFinanceLiveProvider, "get_quote", fake_get_quote)
    r = _client().get("/api/universo-yahoo/ZZZNOEXISTE")
    assert r.status_code == 404
    body = r.get_json()
    assert body["error"] == "sin_dato"
    assert body["symbol"] == "ZZZNOEXISTE"


def test_universo_yahoo_detalle_error_proveedor_devuelve_502(monkeypatch):
    def fake_get_quote(self, symbol):
        raise ProviderError("timeout simulado")

    monkeypatch.setattr(YahooFinanceLiveProvider, "get_quote", fake_get_quote)
    r = _client().get("/api/universo-yahoo/AAPL")
    assert r.status_code == 502
    body = r.get_json()
    assert body["error"] == "proveedor_no_disponible"


def test_universo_yahoo_detalle_una_sola_llamada_al_proveedor(monkeypatch):
    """Confirma que UN request al endpoint dispara EXACTAMENTE una llamada
    al proveedor -- nunca un barrido/lote por detrás."""
    calls = []

    def fake_get_quote(self, symbol):
        calls.append(symbol)
        return _fake_quote(symbol=symbol)

    monkeypatch.setattr(YahooFinanceLiveProvider, "get_quote", fake_get_quote)
    r = _client().get("/api/universo-yahoo/MSFT")
    assert r.status_code == 200
    assert calls == ["MSFT"]
