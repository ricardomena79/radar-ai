"""Caché y pausa compartidas de Yahoo (`market_view._fetch_yahoo_chunk`),
usadas por todas las vistas. Sin red: el proveedor se reemplaza."""

import time
from types import SimpleNamespace

import pytest

import atlas_live.market_view as mv
from atlas.data.providers.base import RateLimitError


class _FakeYahoo:
    calls = []
    raise_rate_limit = False

    def get_quotes(self, symbols):
        _FakeYahoo.calls.append(list(symbols))
        if _FakeYahoo.raise_rate_limit:
            raise RateLimitError("Too Many Requests")
        return [SimpleNamespace(symbol=s, last_price=1.0) for s in symbols]


@pytest.fixture(autouse=True)
def _estado_limpio(monkeypatch):
    _FakeYahoo.calls = []
    _FakeYahoo.raise_rate_limit = False
    mv._reset_yahoo_shared_state()
    monkeypatch.setattr(mv, "YahooFinanceLiveProvider", _FakeYahoo)
    yield
    mv._reset_yahoo_shared_state()


def test_un_simbolo_pedido_dos_veces_se_consulta_una_sola_vez():
    mv._fetch_yahoo_chunk(["AAA", "BBB"])
    r = mv._fetch_yahoo_chunk(["AAA", "BBB"])
    assert set(r) == {"AAA", "BBB"}
    assert _FakeYahoo.calls == [["AAA", "BBB"]]


def test_solo_se_piden_los_simbolos_que_faltan():
    mv._fetch_yahoo_chunk(["AAA"])
    r = mv._fetch_yahoo_chunk(["AAA", "BBB"])
    assert set(r) == {"AAA", "BBB"}
    assert _FakeYahoo.calls == [["AAA"], ["BBB"]]


def test_vencido_el_ttl_se_vuelve_a_consultar(monkeypatch):
    mv._fetch_yahoo_chunk(["AAA"])
    monkeypatch.setattr(mv, "YAHOO_QUOTE_TTL_SECONDS", 0.0)
    mv._fetch_yahoo_chunk(["AAA"])
    assert len(_FakeYahoo.calls) == 2


def test_rate_limit_pausa_a_todas_las_vistas_sin_salir_a_la_red():
    _FakeYahoo.raise_rate_limit = True
    with pytest.raises(RateLimitError):
        mv._fetch_yahoo_chunk(["AAA"])
    assert mv._yahoo_blocked()
    with pytest.raises(RateLimitError):
        mv._fetch_yahoo_chunk(["BBB", "CCC"])
    assert len(_FakeYahoo.calls) == 1  # el segundo pedido no llegó a Yahoo


def test_en_pausa_devuelve_lo_que_hay_en_cache():
    mv._fetch_yahoo_chunk(["AAA"])
    _FakeYahoo.raise_rate_limit = True
    r = mv._fetch_yahoo_chunk(["AAA", "BBB"])  # BBB falta -> rate limit, pero AAA estaba en cache
    assert set(r) == {"AAA"} and mv._yahoo_blocked()
    r2 = mv._fetch_yahoo_chunk(["AAA", "CCC"])  # ya en pausa: solo cache
    assert set(r2) == {"AAA"}


def test_termina_la_pausa_y_se_vuelve_a_consultar(monkeypatch):
    _FakeYahoo.raise_rate_limit = True
    with pytest.raises(RateLimitError):
        mv._fetch_yahoo_chunk(["AAA"])
    monkeypatch.setattr(mv, "_yahoo_blocked_until", time.monotonic() - 1)
    _FakeYahoo.raise_rate_limit = False
    assert set(mv._fetch_yahoo_chunk(["AAA"])) == {"AAA"}
