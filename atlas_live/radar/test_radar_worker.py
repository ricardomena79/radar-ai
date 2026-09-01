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


def test_sweep_usa_universo_completo_equity_mas_etfs_apalancados():
    """Fase 5 (2026-08-17) -- el barrido ya NO se limita al universo
    Racional: usa fetch_broad_universe_meta() filtrado a EQUITY (misma
    clasificación ya aprobada en build_historical_reference.py). ETFs y
    derivados quedan afuera de la detección, CON UNA EXCEPCIÓN (2026-08-20,
    caso real MSTU): los ETFs apalancados (1x/2x/3x sobre una sola acción/
    cripto) sí se incluyen -- amplifican directamente su subyacente. Un
    ETF sin ese patrón en el nombre (QQQ) sigue excluido, sin cambios."""
    _fresh()
    saved = _install_fakes(session="regular", quotes={})
    orig_meta = w.broad_universe.fetch_broad_universe_meta
    captured = {}
    w.broad_universe.fetch_broad_universe_meta = lambda: {
        "AAPL": {"type": "EQUITY", "name": "Apple Inc."},
        "ZZZZ": {"type": "EQUITY", "name": "ZZZZ Corp"},
        "QQQ": {"type": "ETF", "name": "Invesco QQQ Trust Series 1"},
        "MSTU": {"type": "ETF", "name": "T-Rex 2X Long MSTR Daily Target ETF"},
        "XYZW": {"type": "WARRANT", "name": "XYZ Corp Warrants"},
    }
    orig_fetch = w.fetch_universe_quotes

    def _capturing_fetch(symbols, tradier_provider=None, fallback_provider=None):
        captured["symbols"] = symbols
        return orig_fetch(symbols, tradier_provider=tradier_provider, fallback_provider=fallback_provider)

    w.fetch_universe_quotes = _capturing_fetch
    try:
        w.run_sweep_once()
        # EQUITY + el ETF apalancado (MSTU), ordenado -- QQQ (ETF normal) y
        # XYZW (warrant) siguen afuera.
        assert captured["symbols"] == ["AAPL", "MSTU", "ZZZZ"]
    finally:
        w.broad_universe.fetch_broad_universe_meta = orig_meta
        w.fetch_universe_quotes = orig_fetch
        _uninstall_fakes(saved)
        _restore()


# ---------------------------------------------------------------------------
# Fase 3/5 (2026-08-25) -- Caso E: una falla en la generación de conocimiento
# NO debe tumbar el radar. `_maybe_generate_experience_knowledge()` está
# testeado de forma aislada acá (no requiere simular todo el EOD real).
# ---------------------------------------------------------------------------

def test_E_falla_en_generacion_de_conocimiento_no_tumba_el_radar():
    _fresh()
    try:
        from atlas_live.learning import live_experience_pipeline as lep

        orig_run = lep.run_experience_learning_cycle

        def _boom(as_of_date, **kwargs):
            raise RuntimeError("fallo simulado -- SQLite inaccesible")

        lep.run_experience_learning_cycle = _boom
        try:
            # No debe lanzar NINGUNA excepción -- ese es exactamente el punto.
            w._maybe_generate_experience_knowledge("2026-08-24")
        finally:
            lep.run_experience_learning_cycle = orig_run

        meta = reg.get_meta()
        assert "RuntimeError" in (meta.get("conocimiento_ultimo_error") or "")
        # El marcador de "ya se generó" NUNCA se puso -- la falla no se
        # disfraza de éxito.
        assert meta.get("conocimiento_generado_para") != "2026-08-24"
    finally:
        _restore()


def test_E_no_se_re_ejecuta_el_mismo_dia_si_ya_se_genero():
    _fresh()
    try:
        reg.set_meta(conocimiento_generado_para="2026-08-24")
        from atlas_live.learning import live_experience_pipeline as lep

        orig_run = lep.run_experience_learning_cycle
        llamadas = []
        lep.run_experience_learning_cycle = lambda as_of_date, **k: llamadas.append(as_of_date)
        try:
            w._maybe_generate_experience_knowledge("2026-08-24")
        finally:
            lep.run_experience_learning_cycle = orig_run
        assert llamadas == []  # nunca se volvió a llamar -- ya estaba marcado para esa fecha
    finally:
        _restore()


# ---------------------------------------------------------------------------
# Blindaje del loop (2026-08-31) -- reconstrucción del incidente real de
# producción: el 31/08 una excepción original en process_sweep() fue
# atrapada por run_sweep_once(), pero el propio manejador de error
# (reg.set_meta) lanzó una SEGUNDA excepción que escapó hasta _loop() y
# terminó el hilo -- por eso el EOD/aprendizaje de ese día nunca corrió.
# ---------------------------------------------------------------------------

def test_doble_fallo_no_escapa_de_run_sweep_once():
    """Reconstrucción exacta del incidente del 31/08: excepción original
    en process_sweep() (línea real ~189/antes 131) + una segunda excepción
    dentro del propio manejador de error (reg.set_meta, línea real ~222/
    antes 153) -- ninguna de las dos, ni juntas, puede escapar de
    run_sweep_once(). Debe seguir devolviendo None sin lanzar, y el lock
    debe quedar liberado."""
    _fresh()
    saved = _install_fakes(session="regular", quotes={})
    from atlas_live.radar import candidate_tracker as tracker

    orig_process = tracker.process_sweep
    orig_set_meta = reg.set_meta
    calls = {"n": 0}

    def _boom_process(*a, **kw):
        raise RuntimeError("fallo original simulado -- caso real 31/08")

    def _boom_set_meta(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("fallo secundario simulado -- database is locked (sintético)")
        return orig_set_meta(**kwargs)

    tracker.process_sweep = _boom_process
    reg.set_meta = _boom_set_meta
    try:
        result = w.run_sweep_once()  # NO debe lanzar
        assert result is None
        assert w._lock.acquire(blocking=False)  # el lock quedó liberado igual
        w._lock.release()
    finally:
        tracker.process_sweep = orig_process
        reg.set_meta = orig_set_meta
        _uninstall_fakes(saved)
        _restore()


def test_manejador_de_eod_tambien_resiliente_a_doble_fallo():
    """Mismo patrón que arriba, pero para el manejador de error de
    maybe_run_eod_evaluation() -- nunca falló en producción, pero
    comparte el mismo riesgo estructural (reg.set_meta sin protección
    propia dentro del except); se blinda simétricamente."""
    _fresh()
    market_date = market_hours.market_date()
    reg.set_meta(current_market_date=market_date)
    saved = _install_fakes(session="closed")
    from atlas_live.radar import eod_report as eod

    orig_build = w.build_tradier_provider
    w.build_tradier_provider = lambda: SimpleNamespace(get_quotes=lambda syms: [])
    orig_run_eod = eod.run_eod_evaluation
    orig_set_meta = reg.set_meta
    calls = {"n": 0}

    def _boom_run_eod(*a, **kw):
        raise RuntimeError("fallo original simulado en EOD")

    def _boom_set_meta(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("fallo secundario simulado en el manejador de EOD")
        return orig_set_meta(**kwargs)

    eod.run_eod_evaluation = _boom_run_eod
    reg.set_meta = _boom_set_meta
    try:
        resultado = w.maybe_run_eod_evaluation()  # NO debe lanzar
        assert resultado is False
    finally:
        eod.run_eod_evaluation = orig_run_eod
        reg.set_meta = orig_set_meta
        w.build_tradier_provider = orig_build
        _uninstall_fakes(saved)
        _restore()


def test_loop_exterior_no_muere_ante_excepcion_no_capturada_por_dentro():
    """Blindaje exterior de _loop() -- incluso una excepción que ocurra
    directamente en la primera línea del propio loop (market_hours.get_session,
    fuera de la protección interna de run_sweep_once()) no debe terminar
    el hilo: la iteración siguiente debe poder seguir corriendo con
    normalidad, y el hilo solo debe terminar cuando se pide explícitamente
    (_stop.set()), nunca por la excepción."""
    _fresh()
    quotes = {"AAPL": _fake_quote("AAPL", 6.0)}
    saved = _install_fakes(session="regular", quotes=quotes)
    orig_get_session = market_hours.get_session
    orig_idle = w.IDLE_RECHECK_SECONDS
    calls = {"n": 0}

    def _flaky_session(now=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("fallo sintético directamente en _loop()")
        return "regular"

    market_hours.get_session = _flaky_session
    w.IDLE_RECHECK_SECONDS = 0.05
    w._stop.clear()
    try:
        t = threading.Thread(target=w._loop, daemon=True)
        t.start()
        time.sleep(0.5)
        w._stop.set()
        t.join(timeout=3)
        assert not t.is_alive()  # terminó LIMPIO (por _stop), no por la excepción
        assert calls["n"] >= 2  # sobrevivió la primera excepción y siguió iterando
        meta = reg.get_meta()
        assert meta.get("ultimo_error_etapa") == "loop_outer"
        assert meta.get("ultimo_error_tipo") == "RuntimeError"
    finally:
        w._stop.clear()
        market_hours.get_session = orig_get_session
        w.IDLE_RECHECK_SECONDS = orig_idle
        _uninstall_fakes(saved)
        _restore()


def test_eod_se_ejecuta_aunque_hubo_fallos_de_sweep_antes_ese_dia():
    """El sweep que falla a media mañana no debe impedir que, más tarde,
    el mismo _loop() (blindado) llegue a disparar el EOD -- la
    precondición real (current_market_date == market_date) solo exige que
    UN sweep haya tenido éxito ese día, no el último (mismo escenario real
    del 31/08: 3.350 sweeps ok antes del que murió)."""
    _fresh()
    market_date = market_hours.market_date()
    reg.set_meta(current_market_date=market_date, state="RUNNING")
    saved = _install_fakes(session="closed")
    from atlas_live.radar import eod_report as eod

    orig_build = w.build_tradier_provider
    w.build_tradier_provider = lambda: SimpleNamespace(get_quotes=lambda syms: [])
    orig_run_eod = eod.run_eod_evaluation
    llamado = {"n": 0}

    def _fake_run_eod(market_date_arg, provider, **kw):
        llamado["n"] += 1
        return SimpleNamespace(
            market_date=market_date_arg, n_estudiadas=0, n_candidatas=0, n_senales=0,
            n_evaluadas=0, n_aciertos=0, n_reached_20=0, n_reached_50=0, n_reached_100=0,
            n_falsas_senales=0, n_deteccion_tardia=0, n_direccion_correcta=0,
            n_direccion_incorrecta=0, mejores_oportunidades=[], posibles_no_detectadas=[],
        )

    eod.run_eod_evaluation = _fake_run_eod
    try:
        resultado = w.maybe_run_eod_evaluation()
        assert resultado is True
        assert llamado["n"] == 1
    finally:
        eod.run_eod_evaluation = orig_run_eod
        w.build_tradier_provider = orig_build
        _uninstall_fakes(saved)
        _restore()


def test_maybe_run_eod_evaluation_no_se_duplica():
    """Nivel radar_worker.py -- distinto de la idempotencia interna ya
    cubierta en test_eod_report.py::test_run_eod_evaluation_completo_e_idempotente
    (esa prueba corre eod.run_eod_evaluation() dos veces y confirma que no
    duplica filas; ésta prueba el gate de MÁS AFUERA, en radar_worker.py,
    que ni siquiera debe volver a LLAMAR a eod.run_eod_evaluation() una
    segunda vez para el mismo market_date)."""
    _fresh()
    market_date = market_hours.market_date()
    reg.set_meta(eod_ejecutado_para=market_date, current_market_date=market_date)
    saved = _install_fakes(session="closed")
    from atlas_live.radar import eod_report as eod

    orig_build = w.build_tradier_provider
    w.build_tradier_provider = lambda: SimpleNamespace(get_quotes=lambda syms: [])
    orig_run_eod = eod.run_eod_evaluation
    llamado = {"n": 0}
    eod.run_eod_evaluation = lambda *a, **k: llamado.__setitem__("n", llamado["n"] + 1)
    try:
        resultado = w.maybe_run_eod_evaluation()
        assert resultado is False
        assert llamado["n"] == 0
    finally:
        eod.run_eod_evaluation = orig_run_eod
        w.build_tradier_provider = orig_build
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
