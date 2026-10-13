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
    monkeypatch.setattr(mv.yahoo_shared_pause, "_blocked_until", time.monotonic() - 1)
    _FakeYahoo.raise_rate_limit = False
    assert set(mv._fetch_yahoo_chunk(["AAA"])) == {"AAA"}


# --- pre-filtro de scan_worker: usa la misma pausa y se apaga sin mercado ---

def _assets(n):
    from atlas.data.universe import Asset
    return [Asset(symbol=f"S{i}", name="x", type="EQUITY") for i in range(n)]


def test_prefilter_no_sale_a_yahoo_si_esta_en_pausa(monkeypatch):
    import atlas_live.scan_worker as sw

    def boom(symbol):
        raise AssertionError("no debe consultar Yahoo en pausa")

    monkeypatch.setattr(sw.yf, "Ticker", boom)
    mv.yahoo_shared_pause.trigger()
    assert sw._lightweight_change_pct(_assets(1)[0]) is None


def test_un_rate_limit_del_prefiltro_pausa_a_las_vistas(monkeypatch):
    import atlas_live.scan_worker as sw

    class _Boom:
        def __init__(self, symbol):
            pass

        @property
        def fast_info(self):
            raise RuntimeError("Too Many Requests. Rate limited.")

    monkeypatch.setattr(sw.yf, "Ticker", _Boom)
    assert not mv._yahoo_blocked()
    sw._lightweight_change_pct(_assets(1)[0])
    assert mv._yahoo_blocked()


def test_prefiltro_se_apaga_en_overnight_y_fin_de_semana(monkeypatch):
    import atlas_live.memory.market_hours as mh
    import atlas_live.scan_worker as sw

    llamadas = []
    monkeypatch.setattr(sw, "_lightweight_change_pct", lambda a: llamadas.append(a) or (a, 1.0))
    chunk = _assets(5)
    for sesion in ("overnight", "closed"):
        monkeypatch.setattr(mh, "get_session", lambda now=None, s=sesion: s)
        assert sw._prefilter_movers(chunk) == []
    assert llamadas == []
    monkeypatch.setattr(mh, "get_session", lambda now=None: "regular")
    assert sw._prefilter_movers(chunk) != [] and len(llamadas) == 5


def test_bloque_grande_sin_datos_pausa_yahoo(monkeypatch):
    import atlas_live.memory.market_hours as mh
    import atlas_live.scan_worker as sw

    monkeypatch.setattr(mh, "get_session", lambda now=None: "regular")
    monkeypatch.setattr(sw, "_lightweight_change_pct", lambda a: None)
    assert sw._prefilter_movers(_assets(60)) == []
    assert mv._yahoo_blocked()
