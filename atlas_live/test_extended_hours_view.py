"""Tests de la vista Horario extendido: solo funciona entre el cierre y la
apertura, usa solo el listado habilitado y nunca toca la red."""

from datetime import datetime, timezone
from types import SimpleNamespace

import atlas_live.extended_hours_view as ev
from atlas.data.universe import Asset


class _Q:
    def __init__(self, symbol, last_price, change_percent, previous_close):
        self.symbol, self.last_price, self.change_percent = symbol, last_price, change_percent
        self.timestamp = datetime.now(timezone.utc)
        self.price_is_stale = False
        self.previous_close = previous_close


class _Provider:
    def __init__(self, quotes):
        self.quotes = quotes
        self.calls = []

    def get_quotes(self, symbols):
        self.calls.append(list(symbols))
        return [self.quotes[s] for s in symbols if s in self.quotes]


def _reset():
    with ev._state_lock:
        ev._snapshot.update(rows=[], generated_at=None, cycles_total=0, cycles_ok=0, cycles_error=0, ultimo_error=None)
    ev._sparkline_by_symbol.clear()
    ev._last_known_by_symbol.clear()


def _patch(monkeypatch, symbols, quotes, session):
    assets = [Asset(symbol=s, name=f"{s} Inc.", type="ETF" if s == "SPY" else "EQUITY") for s in symbols]
    provider = _Provider(quotes)
    monkeypatch.setattr(ev, "get_extended_hours_assets", lambda: assets)
    monkeypatch.setattr(ev, "build_tradier_provider", lambda: provider)
    monkeypatch.setattr(ev, "normalize", lambda s: SimpleNamespace(query_symbol=s, state="ACTIVE"))
    monkeypatch.setattr(ev.market_hours, "get_session", lambda now=None: session)
    monkeypatch.setattr(ev, "_fetch_yahoo_batch", lambda syms: ({}, {"attempted": 0, "success": 0, "errors": 0, "aborted": 0, "budget_rejected": 0}))
    return provider


def test_sesiones_en_las_que_corre():
    assert ev._should_run("premarket", True) and ev._should_run("afterhours", True) and ev._should_run("overnight", True)
    assert not ev._should_run("regular", False)
    assert not ev._should_run("closed", True)  # fin de semana con datos: se queda con el último
    assert ev._should_run("closed", False)     # fin de semana sin ningún dato todavía: un ciclo
    assert not ev._should_run(None, False)


def test_snapshot_activa_solo_fuera_de_la_sesion_regular(monkeypatch):
    for sesion, esperado in (("regular", False), ("premarket", True), ("afterhours", True), ("overnight", True), ("closed", True)):
        monkeypatch.setattr(ev.market_hours, "get_session", lambda now=None, s=sesion: s)
        snap = ev.get_extended_hours_snapshot()
        assert snap["activa"] is esperado and snap["session_actual"] == sesion


def test_un_ciclo_solo_consulta_el_listado_y_ordena_por_cambio(monkeypatch):
    _reset()
    quotes = {"AAA": _Q("AAA", 10.0, 1.0, 9.9), "BBB": _Q("BBB", 20.0, 5.0, 19.0), "SPY": _Q("SPY", 700.0, -0.5, 703.0)}
    provider = _patch(monkeypatch, ["AAA", "BBB", "SPY"], quotes, "afterhours")
    assert ev.run_extended_hours_cycle_once() is not None
    snap = ev.get_extended_hours_snapshot()
    assert [r["symbol"] for r in snap["rows"]] == ["BBB", "AAA", "SPY"]
    assert [r["rank"] for r in snap["rows"]] == [1, 2, 3]
    assert snap["total_universe"] == 3 and snap["frescos"] == 3
    assert {r["symbol"]: r["tipo"] for r in snap["rows"]}["SPY"] == "ETF"
    assert sorted(s for call in provider.calls for s in call) == ["AAA", "BBB", "SPY"]
    _reset()


def test_overnight_no_llama_a_tradier(monkeypatch):
    _reset()
    provider = _patch(monkeypatch, ["AAA"], {}, "overnight")
    ev.run_extended_hours_cycle_once()
    assert provider.calls == []
    _reset()


def test_sin_dato_no_se_lista_pero_se_cuenta(monkeypatch):
    _reset()
    _patch(monkeypatch, ["AAA", "BBB"], {"AAA": _Q("AAA", 10.0, 1.0, 9.9)}, "premarket")
    ev.run_extended_hours_cycle_once()
    snap = ev.get_extended_hours_snapshot()
    assert [r["symbol"] for r in snap["rows"]] == ["AAA"]
    assert snap["sin_datos"] == 1
    _reset()


def test_no_toca_decision_ni_aprendizaje():
    import inspect
    src = inspect.getsource(ev)
    for prohibido in ("apply_recalibration", "atlas_decision_core", "candidate_gates", "radar_worker", "activation_registry"):
        assert prohibido not in src.replace("atlas_decision_core.py", "").replace("candidate_gates.py", "").replace("radar_worker.py", "")
