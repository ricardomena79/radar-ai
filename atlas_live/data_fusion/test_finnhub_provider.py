"""Tests de `FinnhubProvider.get_quote()` (2026-09-14, autorizado
explícitamente): distinguir HTTP 429 (`RateLimitError`) del resto de
errores HTTP (`ProviderError` genérico, comportamiento preexistente sin
cambios). Mockea `requests.get` -- nunca red real, mismo estilo que
`test_finnhub_provider_news.py`."""

from atlas.data.providers.base import ProviderError, QuoteNotFoundError, RateLimitError
from atlas_live.data_fusion import finnhub_provider as fp


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def test_get_quote_200_devuelve_quote_real(monkeypatch):
    monkeypatch.setattr(fp.requests, "get", lambda *a, **k: _FakeResponse(
        200, {"c": 71.44, "o": 68.0, "h": 72.0, "l": 67.5, "pc": 68.0, "dp": 5.07, "t": 1757620800},
    ))
    provider = fp.FinnhubProvider("fake-key")
    q = provider.get_quote("AFRM")
    assert q.last_price == 71.44
    assert q.source == "finnhub"


def test_get_quote_429_lanza_rate_limit_error_no_provider_error_generico(monkeypatch):
    monkeypatch.setattr(fp.requests, "get", lambda *a, **k: _FakeResponse(
        429, {"error": "Too many requests. Please try again later."},
    ))
    provider = fp.FinnhubProvider("fake-key")
    try:
        provider.get_quote("AFRM")
        assert False, "debía lanzar RateLimitError"
    except RateLimitError:
        pass


def test_get_quote_429_rate_limit_error_es_subclase_de_provider_error():
    # Garantiza que ningún `except ProviderError` existente (hot_quote.py,
    # catalyst_worker.py) deja de capturar el 429 tras este cambio.
    assert issubclass(RateLimitError, ProviderError)


def test_get_quote_500_sigue_lanzando_provider_error_generico(monkeypatch):
    monkeypatch.setattr(fp.requests, "get", lambda *a, **k: _FakeResponse(500, {"error": "boom"}))
    provider = fp.FinnhubProvider("fake-key")
    try:
        provider.get_quote("AFRM")
        assert False, "debía lanzar ProviderError"
    except RateLimitError:
        assert False, "500 nunca debe clasificarse como RateLimitError"
    except ProviderError:
        pass


def test_get_quote_404_sigue_lanzando_provider_error_generico(monkeypatch):
    monkeypatch.setattr(fp.requests, "get", lambda *a, **k: _FakeResponse(404, {"error": "not found"}))
    provider = fp.FinnhubProvider("fake-key")
    try:
        provider.get_quote("AFRM")
        assert False, "debía lanzar ProviderError"
    except RateLimitError:
        assert False, "404 nunca debe clasificarse como RateLimitError"
    except ProviderError:
        pass


def test_get_quote_simbolo_inexistente_sigue_dando_quote_not_found(monkeypatch):
    # Comportamiento preexistente (200 con todos los campos en 0) -- sin
    # cambios por este fix, confirmado explícitamente.
    monkeypatch.setattr(fp.requests, "get", lambda *a, **k: _FakeResponse(
        200, {"c": 0, "d": None, "dp": None, "h": 0, "l": 0, "o": 0, "pc": 0, "t": 0},
    ))
    provider = fp.FinnhubProvider("fake-key")
    try:
        provider.get_quote("NOEXISTE")
        assert False, "debía lanzar QuoteNotFoundError"
    except QuoteNotFoundError:
        pass
