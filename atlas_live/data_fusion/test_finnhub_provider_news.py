"""Tests de get_company_news()/get_earnings_calendar() (2026-08-23, Motor
de Catalizadores). Mockea requests.get -- nunca red real, mismo espíritu
que el resto de la suite (DB temporal/fakes, sin llamadas externas)."""

import requests

from atlas_live.data_fusion import finnhub_provider as fp
from atlas.data.providers.base import ProviderError


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def test_get_company_news_devuelve_lista_cruda(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(200, [
            {"id": 1, "headline": "Company X Announces Positive Phase 3 Topline Results",
             "summary": "...", "source": "PR Newswire", "url": "https://example.com/1",
             "datetime": 1755763200, "category": "company news", "related": "XYZ"},
        ])

    monkeypatch.setattr(fp.requests, "get", fake_get)
    provider = fp.FinnhubProvider("fake-key")
    result = provider.get_company_news("XYZ", "2026-08-21", "2026-08-21")

    assert captured["url"] == fp.NEWS_URL
    assert captured["params"]["symbol"] == "XYZ"
    assert captured["params"]["token"] == "fake-key"
    assert len(result) == 1
    assert result[0]["headline"] == "Company X Announces Positive Phase 3 Topline Results"


def test_get_company_news_sin_noticias_devuelve_lista_vacia_no_excepcion(monkeypatch):
    monkeypatch.setattr(fp.requests, "get", lambda *a, **k: _FakeResponse(200, []))
    provider = fp.FinnhubProvider("fake-key")
    result = provider.get_company_news("QUIETO", "2026-08-21", "2026-08-21")
    assert result == []


def test_get_company_news_http_error_lanza_provider_error(monkeypatch):
    monkeypatch.setattr(fp.requests, "get", lambda *a, **k: _FakeResponse(500, {"error": "boom"}))
    provider = fp.FinnhubProvider("fake-key")
    try:
        provider.get_company_news("XYZ", "2026-08-21", "2026-08-21")
        assert False, "debía lanzar ProviderError"
    except ProviderError:
        pass


def test_get_company_news_fallo_de_red_lanza_provider_error(monkeypatch):
    def _boom(*a, **k):
        raise requests.RequestException("timeout")

    monkeypatch.setattr(fp.requests, "get", _boom)
    provider = fp.FinnhubProvider("fake-key")
    try:
        provider.get_company_news("XYZ", "2026-08-21", "2026-08-21")
        assert False, "debía lanzar ProviderError"
    except ProviderError:
        pass


def test_get_earnings_calendar_sin_symbol_trae_todo_el_rango(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(200, {"earningsCalendar": [
            {"symbol": "NSSC", "date": "2026-08-25", "hour": "bmo",
             "epsEstimate": 0.5, "revenueEstimate": 12000000},
            {"symbol": "GRRR", "date": "2026-08-26", "hour": "amc",
             "epsEstimate": -0.1, "revenueEstimate": 8000000},
        ]})

    monkeypatch.setattr(fp.requests, "get", fake_get)
    provider = fp.FinnhubProvider("fake-key")
    result = provider.get_earnings_calendar("2026-08-24", "2026-08-28")

    assert "symbol" not in captured["params"]  # no se pidió un ticker puntual
    assert len(result) == 2
    assert {r["symbol"] for r in result} == {"NSSC", "GRRR"}


def test_get_earnings_calendar_con_symbol_lo_pasa_como_parametro(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return _FakeResponse(200, {"earningsCalendar": []})

    monkeypatch.setattr(fp.requests, "get", fake_get)
    provider = fp.FinnhubProvider("fake-key")
    provider.get_earnings_calendar("2026-08-24", "2026-08-28", symbol="NSSC")
    assert captured["params"]["symbol"] == "NSSC"


def test_get_earnings_calendar_rango_vacio_no_rompe(monkeypatch):
    monkeypatch.setattr(fp.requests, "get", lambda *a, **k: _FakeResponse(200, {"earningsCalendar": []}))
    provider = fp.FinnhubProvider("fake-key")
    result = provider.get_earnings_calendar("2026-08-24", "2026-08-28")
    assert result == []


def test_get_earnings_calendar_http_error_lanza_provider_error(monkeypatch):
    monkeypatch.setattr(fp.requests, "get", lambda *a, **k: _FakeResponse(503, {"error": "down"}))
    provider = fp.FinnhubProvider("fake-key")
    try:
        provider.get_earnings_calendar("2026-08-24", "2026-08-28")
        assert False, "debía lanzar ProviderError"
    except ProviderError:
        pass


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
            p += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
            traceback.print_exc()
            f += 1
    print(f"--- {p} passed, {f} failed ---")
