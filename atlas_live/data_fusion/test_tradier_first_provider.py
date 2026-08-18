"""Tests de TradierFirstProvider (Fase 5, 2026-08-17). Con fakes -- sin red real."""

import pandas as pd
import pytest

from atlas.data.models.quote import Quote
from atlas.data.providers.base import ProviderError, QuoteNotFoundError
from atlas_live.data_fusion import tradier_first_provider as tfp


def _quote(symbol):
    return Quote(symbol=symbol, name=symbol, last_price=10.0, change_percent=1.0,
                 volume=1000, open=10, high=10, low=10, previous_close=9.9,
                 average_volume=500, relative_volume=2.0)


class _FakeUniverseResult:
    def __init__(self, quotes):
        self.quotes = quotes


def _install_fake_fetch(monkeypatch, quotes_by_symbol, captured=None):
    def _fake_fetch(symbols, tradier_provider=None, fallback_provider=None):
        if captured is not None:
            captured["symbols"] = symbols
            captured["tradier_provider"] = tradier_provider
            captured["fallback_provider"] = fallback_provider
        return _FakeUniverseResult({s: quotes_by_symbol[s] for s in symbols if s in quotes_by_symbol})

    monkeypatch.setattr(tfp, "fetch_universe_quotes", _fake_fetch)


def _provider(tradier_provider=None, fallback_provider=None):
    # evita que __init__ arme proveedores reales (build_tradier_provider()/get_default_provider())
    p = tfp.TradierFirstProvider.__new__(tfp.TradierFirstProvider)
    p._tradier_provider = tradier_provider
    p._fallback_provider = fallback_provider
    return p


def test_get_quote_devuelve_la_cotizacion_cuando_existe(monkeypatch):
    _install_fake_fetch(monkeypatch, {"AAPL": _quote("AAPL")})
    provider = _provider()
    assert provider.get_quote("AAPL").symbol == "AAPL"


def test_get_quote_lanza_quotenotfound_cuando_no_hay_datos(monkeypatch):
    _install_fake_fetch(monkeypatch, {})
    provider = _provider()
    with pytest.raises(QuoteNotFoundError):
        provider.get_quote("ZZZZ")


def test_get_quotes_respeta_orden_y_omite_lo_no_resuelto(monkeypatch):
    _install_fake_fetch(monkeypatch, {"AAPL": _quote("AAPL"), "MSFT": _quote("MSFT")})
    provider = _provider()
    result = provider.get_quotes(["MSFT", "ZZZZ", "AAPL"])
    assert [q.symbol for q in result] == ["MSFT", "AAPL"]


def test_get_quotes_pasa_los_proveedores_inyectados(monkeypatch):
    captured = {}
    _install_fake_fetch(monkeypatch, {"AAPL": _quote("AAPL")}, captured=captured)
    tradier_sentinel = object()
    fallback_sentinel = object()
    provider = _provider(tradier_provider=tradier_sentinel, fallback_provider=fallback_sentinel)
    provider.get_quotes(["AAPL"])
    assert captured["tradier_provider"] is tradier_sentinel
    assert captured["fallback_provider"] is fallback_sentinel


def test_get_history_diario_usa_tradier_primero():
    calls = {"tradier": 0, "fallback": 0}

    class _Tradier:
        def get_history(self, symbol, period="6mo", interval="1d"):
            calls["tradier"] += 1
            return pd.DataFrame({"Close": [1.0]})

    class _Fallback:
        def get_history(self, symbol, period="6mo", interval="1d"):
            calls["fallback"] += 1
            return pd.DataFrame({"Close": [2.0]})

    provider = _provider(tradier_provider=_Tradier(), fallback_provider=_Fallback())
    df = provider.get_history("AAPL", period="6mo", interval="1d")
    assert calls == {"tradier": 1, "fallback": 0}
    assert df["Close"].iloc[0] == 1.0


def test_get_history_diario_cae_a_fallback_si_tradier_falla():
    class _Tradier:
        def get_history(self, symbol, period="6mo", interval="1d"):
            raise ProviderError("tradier caído")

    class _Fallback:
        def get_history(self, symbol, period="6mo", interval="1d"):
            return pd.DataFrame({"Close": [2.0]})

    provider = _provider(tradier_provider=_Tradier(), fallback_provider=_Fallback())
    df = provider.get_history("AAPL", period="6mo", interval="1d")
    assert df["Close"].iloc[0] == 2.0


def test_get_history_diario_cae_a_fallback_si_tradier_no_tiene_historial():
    class _Tradier:
        def get_history(self, symbol, period="6mo", interval="1d"):
            raise QuoteNotFoundError(symbol)

    class _Fallback:
        def get_history(self, symbol, period="6mo", interval="1d"):
            return pd.DataFrame({"Close": [2.0]})

    provider = _provider(tradier_provider=_Tradier(), fallback_provider=_Fallback())
    df = provider.get_history("AAPL", period="6mo", interval="1d")
    assert df["Close"].iloc[0] == 2.0


def test_get_history_sin_tradier_configurado_va_directo_a_fallback():
    class _Fallback:
        def get_history(self, symbol, period="6mo", interval="1d"):
            return pd.DataFrame({"Close": [3.0]})

    provider = _provider(tradier_provider=None, fallback_provider=_Fallback())
    df = provider.get_history("AAPL", period="6mo", interval="1d")
    assert df["Close"].iloc[0] == 3.0


def test_get_history_intradia_va_directo_a_fallback_sin_tocar_tradier():
    calls = {"tradier": 0, "fallback": 0}

    class _Tradier:
        def get_history(self, symbol, period="6mo", interval="1d"):
            calls["tradier"] += 1
            return pd.DataFrame({"Close": [1.0]})

    class _Fallback:
        def get_history(self, symbol, period="1d", interval="5m"):
            calls["fallback"] += 1
            return pd.DataFrame({"Close": [2.0]})

    provider = _provider(tradier_provider=_Tradier(), fallback_provider=_Fallback())
    df = provider.get_history("AAPL", period="1d", interval="5m")
    assert calls == {"tradier": 0, "fallback": 1}
    assert df["Close"].iloc[0] == 2.0
