"""Tests del Hilo A (2026-08-14). Con fakes (sin red real), DB temporal.

Sigue el mismo patrón ya usado y probado en `test_study_worker.py`: fakes
para `market_hours.get_session`, `build_tradier_provider` y
`fetch_universe_quotes`, DB temporal para no pisar datos reales.
"""

import tempfile
import threading
import time
import uuid as _uuid
from pathlib import Path
from types import SimpleNamespace

from atlas.data.models.quote import Quote
from atlas_live.data_fusion import universe_quotes as uq
from atlas_live.memory import market_hours
from atlas_live.radar import candidate_registry as reg
from atlas_live.radar import radar_worker as w

_ORIG_DB = reg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_radar_worker_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None
    w._history.reset_for_new_day("__reset__")
    w._last_quotes = {}


def _restore():
    reg.DB_PATH = _ORIG_DB


def _fake_quote(symbol, change_pct):
    return Quote(symbol=symbol, name=symbol, last_price=10.0, change_percent=change_pct,
                 volume=1000, open=10, high=10, low=10, previous_close=9.7,
                 average_volume=500, relative_volume=2.0)


def _install_fakes(session="regular", quotes=None):
    # OJO: radar_worker.py hace `from ...universe_quotes import fetch_universe_quotes`
    # -- eso vincula el nombre en el namespace de `w`, no en `uq`. Parchear
    # `uq.fetch_universe_quotes` NO intercepta la llamada real; hay que
    # parchear `w.fetch_universe_quotes` directamente.
    orig_session = market_hours.get_session
    orig_build_tradier = w.build_tradier_provider
    orig_fetch = w.fetch_universe_quotes

    market_hours.get_session = lambda now=None: session
    w.build_tradier_provider = lambda: SimpleNamespace(get_quotes=lambda syms: [])  # objeto no-None basta

    def fake_fetch(symbols, tradier_provider=None, fallback_provider=None):
        diag = SimpleNamespace(tradier_error=None)
        return SimpleNamespace(quotes=quotes or {}, states={}, diagnostics=diag)

    w.fetch_universe_quotes = fake_fetch
    return (orig_session, orig_build_tradier, orig_fetch)


def _uninstall_fakes(saved):
    market_hours.get_session, w.build_tradier_provider, w.fetch_universe_quotes = saved


def test_sweep_no_corre_fuera_de_ventana():
    _fresh()
    saved = _install_fakes(session="afterhours")
    try:
        result = w.run_sweep_once()
        assert result is None
        assert reg.count_candidates_for_date(market_hours.market_date()) == 0
    finally:
        _uninstall_fakes(saved)
        _restore()


def test_sweep_procesa_y_detecta_candidatas():
    _fresh()
    quotes = {"AAPL": _fake_quote("AAPL", 6.0), "MSFT": _fake_quote("MSFT", 0.1)}
    saved = _install_fakes(session="regular", quotes=quotes)
    try:
        duration = w.run_sweep_once()
        assert duration is not None
        market_date = market_hours.market_date()
        candidatas = reg.list_candidates_for_date(market_date)
        tickers = {c["ticker"] for c in candidatas}
        assert "AAPL" in tickers  # 6% dispara gate_price_change
        status = reg.radar_status()
        assert status["state"] == "RUNNING"
        assert status["sweeps_ok"] == 1
    finally:
        _uninstall_fakes(saved)
        _restore()


def test_no_reentrante_bajo_llamadas_simultaneas():
    _fresh()
    quotes = {"AAPL": _fake_quote("AAPL", 6.0)}
    saved = _install_fakes(session="regular", quotes=quotes)
    try:
        results = []

        def _worker():
            results.append(w.run_sweep_once())

        # el lock ya está tomado por este hilo principal -- simula solapamiento real
        acquired = w._lock.acquire(blocking=False)
        assert acquired
        t = threading.Thread(target=_worker)
        t.start()
        t.join(timeout=3)
        w._lock.release()
        assert results[0] is None  # el segundo intento no pudo entrar -- no hubo solapamiento
    finally:
        _uninstall_fakes(saved)
        _restore()


def test_un_barrido_roto_no_tumba_el_mecanismo():
    _fresh()
    saved = _install_fakes(session="regular", quotes={})
    orig_process = None
    try:
        from atlas_live.radar import candidate_tracker as tracker
        orig_process = tracker.process_sweep

        def _boom(*a, **kw):
            raise RuntimeError("fallo simulado")

        tracker.process_sweep = _boom
        result = w.run_sweep_once()
        assert result is None
        status = reg.radar_status()
        assert status["sweeps_error"] == 1
        assert status["state"] == "ERROR"
    finally:
        if orig_process:
            from atlas_live.radar import candidate_tracker as tracker
            tracker.process_sweep = orig_process
        _uninstall_fakes(saved)
        _restore()


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
