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
    orig_racional = w.broad_universe.racional_symbols
    captured = {}
    w.broad_universe.fetch_broad_universe_meta = lambda: {
        "AAPL": {"type": "EQUITY", "name": "Apple Inc."},
        "ZZZZ": {"type": "EQUITY", "name": "ZZZZ Corp"},
        "QQQ": {"type": "ETF", "name": "Invesco QQQ Trust Series 1"},
        "MSTU": {"type": "ETF", "name": "T-Rex 2X Long MSTR Daily Target ETF"},
        "XYZW": {"type": "WARRANT", "name": "XYZ Corp Warrants"},
    }
    # Fase 1 de ampliación (2026-09-07): run_sweep_once() ahora también
    # llama a racional_symbols() por dentro de build_expanded_universe() --
    # se mockea vacío acá para que esta prueba siga aislada y siga
    # verificando EXCLUSIVAMENTE el filtro de tipo del universo base
    # (QQQ/XYZW afuera, MSTU adentro por apalancado). El comportamiento de
    # unión con Racional se prueba aparte, en los tests de abajo.
    w.broad_universe.racional_symbols = lambda: set()
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
        w.broad_universe.racional_symbols = orig_racional
        w.fetch_universe_quotes = orig_fetch
        _uninstall_fakes(saved)
        _restore()


def test_sweep_amplia_universo_con_racional_sin_filtrar_por_tipo():
    """Fase 1 de ampliación hacia Racional (2026-09-07, autorizada
    explícitamente): un ETF normal, un UNIT genuino y un símbolo Racional
    sin identidad en absoluto deben llegar igual al barrido -- el filtro de
    tipo (EQUITY + ETF apalancado) sigue aplicando SOLO al universo base,
    nunca a lo que se agrega desde Racional. Sin duplicados: AAPL está en
    ambos lados y aparece una sola vez."""
    _fresh()
    saved = _install_fakes(session="regular", quotes={})
    orig_meta = w.broad_universe.fetch_broad_universe_meta
    orig_racional = w.broad_universe.racional_symbols
    captured = {}
    w.broad_universe.fetch_broad_universe_meta = lambda: {
        "AAPL": {"type": "EQUITY", "name": "Apple Inc."},
        "QQQ": {"type": "ETF", "name": "Invesco QQQ Trust Series 1"},  # ETF normal, Racional
        "BEP": {"type": "UNIT", "name": "Brookfield Renewable Partners L.P. Limited Partnership Units"},
    }
    w.broad_universe.racional_symbols = lambda: {"AAPL", "QQQ", "BEP", "GHOSTRAC"}
    orig_fetch = w.fetch_universe_quotes

    def _capturing_fetch(symbols, tradier_provider=None, fallback_provider=None):
        captured["symbols"] = symbols
        return orig_fetch(symbols, tradier_provider=tradier_provider, fallback_provider=fallback_provider)

    w.fetch_universe_quotes = _capturing_fetch
    try:
        w.run_sweep_once()
        assert captured["symbols"] == ["AAPL", "BEP", "GHOSTRAC", "QQQ"]
        assert len(captured["symbols"]) == len(set(captured["symbols"]))  # sin duplicados
    finally:
        w.broad_universe.fetch_broad_universe_meta = orig_meta
        w.broad_universe.racional_symbols = orig_racional
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
# FIX 2026-09-12 (misión "RESOLVER LA DESCONEXIÓN ENTRE APRENDIZAJE Y
# DECISIÓN") -- v2 corre en paralelo a v1, aislado, sin afectarla ni ser
# afectado por ella.
# ---------------------------------------------------------------------------

def test_v2_corre_junto_a_v1_ambos_marcadores_quedan_puestos():
    _fresh()
    try:
        from atlas_live.learning import live_experience_pipeline as lep

        orig_v1 = lep.run_experience_learning_cycle
        orig_v2 = lep.run_experience_learning_cycle_by_stage
        llamadas_v1, llamadas_v2 = [], []
        lep.run_experience_learning_cycle = lambda as_of_date, **k: (
            llamadas_v1.append(as_of_date) or {"ejecutado_at": "t1", "ok": True}
        )
        lep.run_experience_learning_cycle_by_stage = lambda as_of_date, **k: (
            llamadas_v2.append(as_of_date) or {"ejecutado_at": "t2", "ok": True}
        )
        try:
            w._maybe_generate_experience_knowledge("2026-08-24")
        finally:
            lep.run_experience_learning_cycle = orig_v1
            lep.run_experience_learning_cycle_by_stage = orig_v2

        assert llamadas_v1 == ["2026-08-24"]
        assert llamadas_v2 == ["2026-08-24"]
        meta = reg.get_meta()
        assert meta.get("conocimiento_generado_para") == "2026-08-24"
        assert meta.get("conocimiento_v2_generado_para") == "2026-08-24"
    finally:
        _restore()


def test_v2_falla_no_afecta_a_v1():
    _fresh()
    try:
        from atlas_live.learning import live_experience_pipeline as lep

        orig_v1 = lep.run_experience_learning_cycle
        orig_v2 = lep.run_experience_learning_cycle_by_stage
        lep.run_experience_learning_cycle = lambda as_of_date, **k: {"ejecutado_at": "t1", "ok": True}
        lep.run_experience_learning_cycle_by_stage = lambda as_of_date, **k: (_ for _ in ()).throw(
            RuntimeError("fallo simulado v2")
        )
        try:
            w._maybe_generate_experience_knowledge("2026-08-24")  # NO debe lanzar
        finally:
            lep.run_experience_learning_cycle = orig_v1
            lep.run_experience_learning_cycle_by_stage = orig_v2

        meta = reg.get_meta()
        assert meta.get("conocimiento_generado_para") == "2026-08-24"  # v1 sigue OK
        assert meta.get("conocimiento_v2_generado_para") != "2026-08-24"  # v2 nunca se marcó
        assert "RuntimeError" in (meta.get("conocimiento_v2_ultimo_error") or "")
    finally:
        _restore()


def test_v1_falla_no_afecta_a_v2():
    _fresh()
    try:
        from atlas_live.learning import live_experience_pipeline as lep

        orig_v1 = lep.run_experience_learning_cycle
        orig_v2 = lep.run_experience_learning_cycle_by_stage
        lep.run_experience_learning_cycle = lambda as_of_date, **k: (_ for _ in ()).throw(
            RuntimeError("fallo simulado v1")
        )
        lep.run_experience_learning_cycle_by_stage = lambda as_of_date, **k: {"ejecutado_at": "t2", "ok": True}
        try:
            w._maybe_generate_experience_knowledge("2026-08-24")
        finally:
            lep.run_experience_learning_cycle = orig_v1
            lep.run_experience_learning_cycle_by_stage = orig_v2

        meta = reg.get_meta()
        assert meta.get("conocimiento_generado_para") != "2026-08-24"
        assert meta.get("conocimiento_v2_generado_para") == "2026-08-24"
    finally:
        _restore()


def test_v2_no_se_re_ejecuta_el_mismo_dia_si_ya_se_genero():
    _fresh()
    try:
        reg.set_meta(conocimiento_generado_para="2026-08-24", conocimiento_v2_generado_para="2026-08-24")
        from atlas_live.learning import live_experience_pipeline as lep

        orig_v1 = lep.run_experience_learning_cycle
        orig_v2 = lep.run_experience_learning_cycle_by_stage
        llamadas_v1, llamadas_v2 = [], []
        lep.run_experience_learning_cycle = lambda as_of_date, **k: llamadas_v1.append(as_of_date)
        lep.run_experience_learning_cycle_by_stage = lambda as_of_date, **k: llamadas_v2.append(as_of_date)
        try:
            w._maybe_generate_experience_knowledge("2026-08-24")
        finally:
            lep.run_experience_learning_cycle = orig_v1
            lep.run_experience_learning_cycle_by_stage = orig_v2
        assert llamadas_v1 == []
        assert llamadas_v2 == []
    finally:
        _restore()


# ---------------------------------------------------------------------------
# Compaction automática de candidate_observation tras el EOD (2026-09-07,
# autorizado explícitamente -- SOLO conecta el mecanismo ya existente de
# candidate_observation_compaction.py, mismo patrón exacto que
# _maybe_generate_experience_knowledge de arriba: marcador propio +
# try/except aislado que nunca puede tumbar el radar).
# ---------------------------------------------------------------------------

import tempfile as _tempfile
import uuid as _uuid2
from datetime import date as _date, timedelta as _timedelta

from atlas_live.radar import candidate_observation_compaction as _coc
from atlas_live.radar import raw_data_consolidation_registry as _rdc_registry

_ORIG_RDC_DB = _rdc_registry.DB_PATH


def _fresh_compaction():
    _rdc_registry.DB_PATH = Path(_tempfile.gettempdir()) / f"atlas_test_radar_worker_rdc_{_uuid2.uuid4().hex}.db"


def _restore_compaction():
    _rdc_registry.DB_PATH = _ORIG_RDC_DB


def _seed_obs(market_date, ticker="AAA", n=2):
    with reg._connect() as conn:
        for i in range(n):
            conn.execute(
                """INSERT INTO candidate_observation
                   (ticker, market_date, observed_at, sweep_id, price, change_pct, volume,
                    relative_volume, gates_fired_now, vwap, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (ticker, market_date, f"{market_date}T10:{i:02d}:00+00:00", f"s{i}",
                 10.0 + i, 5.0, 1000, 2.0, "[]", None, f"{market_date}T10:{i:02d}:00+00:00"),
            )
        conn.commit()


TODAY_W = "2026-09-07"
OBJETIVO_90D = (_date.fromisoformat(TODAY_W) - _timedelta(days=_coc.RETENTION_DAYS)).isoformat()


def test_F_compaction_exitosa_de_punta_a_punta_desde_cero():
    _fresh()
    _fresh_compaction()
    try:
        _seed_obs(OBJETIVO_90D, n=3)
        w._maybe_run_observation_compaction(TODAY_W)

        meta = reg.get_meta()
        assert meta.get("observation_compaction_ejecutada_para") == TODAY_W
        resultado = meta.get("observation_compaction_ultimo_resultado")
        assert resultado["objetivo"] == OBJETIVO_90D
        assert resultado["compacted"]["ok"] is True
        assert resultado["compacted"]["deleted_row_count"] == 3

        with reg._connect() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM candidate_observation WHERE market_date=?", (OBJETIVO_90D,)
            ).fetchone()[0]
        assert n == 0
    finally:
        _restore_compaction()
        _restore()


def test_F_falla_de_compaction_no_tumba_el_radar_ni_marca_exito():
    """Simula un error real dentro del pipeline de compaction -- el radar
    (esta función, y por extensión maybe_run_eod_evaluation) NUNCA debe
    lanzar, y el marcador de 'ya se ejecutó' NUNCA debe ponerse cuando en
    realidad falló (misma garantía que el test E de conocimiento)."""
    _fresh()
    _fresh_compaction()
    try:
        _seed_obs(OBJETIVO_90D, n=1)
        orig = _coc.run_provisional_for_date

        def _boom(*a, **k):
            raise RuntimeError("fallo simulado -- SQLite inaccesible durante compaction")

        _coc.run_provisional_for_date = _boom
        try:
            w._maybe_run_observation_compaction(TODAY_W)  # NO debe lanzar
        finally:
            _coc.run_provisional_for_date = orig

        meta = reg.get_meta()
        assert "RuntimeError" in (meta.get("observation_compaction_ultimo_error") or "")
        assert meta.get("observation_compaction_ejecutada_para") != TODAY_W

        # los datos crudos siguen intactos -- la falla no borró nada a medias
        with reg._connect() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM candidate_observation WHERE market_date=?", (OBJETIVO_90D,)
            ).fetchone()[0]
        assert n == 1
    finally:
        _restore_compaction()
        _restore()


def test_F_no_se_re_ejecuta_el_mismo_dia_si_ya_corrio():
    _fresh()
    _fresh_compaction()
    try:
        reg.set_meta(observation_compaction_ejecutada_para=TODAY_W)
        llamadas = []
        orig = _coc.run_provisional_for_date
        _coc.run_provisional_for_date = lambda *a, **k: llamadas.append(a) or {"ok": True}
        try:
            w._maybe_run_observation_compaction(TODAY_W)
        finally:
            _coc.run_provisional_for_date = orig
        assert llamadas == []
    finally:
        _restore_compaction()
        _restore()


def test_F_dia_actual_y_menos_de_90_dias_nunca_se_compactan():
    """Confirma, a través del hook completo, que ni el día actual ni un
    día reciente (<90d) se compactan -- los guards de
    candidate_observation_compaction.py deciden esto, este test confirma
    que el hook los respeta sin intentar saltárselos."""
    _fresh()
    _fresh_compaction()
    try:
        reciente = (_date.fromisoformat(TODAY_W) - _timedelta(days=30)).isoformat()
        _seed_obs(TODAY_W, ticker="HOY", n=2)
        _seed_obs(reciente, ticker="RECIENTE", n=2)

        # target de este ciclo es OBJETIVO_90D, no TODAY_W/reciente -- pero
        # confirmamos ademas que llamar directo al modulo de compaction
        # sobre esas 2 fechas efectivamente las bloquea (garantía de fondo).
        r_hoy = _coc.run_provisional_for_date(TODAY_W, today=TODAY_W)
        r_reciente = _coc.run_provisional_for_date(reciente, today=TODAY_W)
        assert r_hoy["ok"] is False
        assert r_reciente["ok"] is False

        w._maybe_run_observation_compaction(TODAY_W)
        with reg._connect() as conn:
            n_hoy = conn.execute("SELECT COUNT(*) FROM candidate_observation WHERE market_date=?", (TODAY_W,)).fetchone()[0]
            n_reciente = conn.execute("SELECT COUNT(*) FROM candidate_observation WHERE market_date=?", (reciente,)).fetchone()[0]
        assert n_hoy == 2
        assert n_reciente == 2
    finally:
        _restore_compaction()
        _restore()


def test_F_candidate_detection_y_outcome_intactas_tras_el_hook():
    _fresh()
    _fresh_compaction()
    try:
        _seed_obs(OBJETIVO_90D, n=2)
        reg.record_detection(
            "AAA", OBJETIVO_90D, "regular", f"{OBJETIVO_90D}T09:31:00+00:00", "sweep0",
            10.0, 5.0, 1000, 500, 2.0, 10000.0, [{"name": "gate_x", "reason": "r", "value": 1.0}],
        )
        reg.record_outcome(
            ticker="AAA", market_date=OBJETIVO_90D, run_up_before_detection_pct=None,
            max_price_after_detection=12.0, max_return_after_detection_pct=20.0, minutes_to_max=30.0,
            reached_20=True, reached_50=False, reached_100=False, category="FINAL", is_final=True,
        )
        w._maybe_run_observation_compaction(TODAY_W)

        with reg._connect() as conn:
            det = conn.execute("SELECT COUNT(*) FROM candidate_detection WHERE market_date=?", (OBJETIVO_90D,)).fetchone()[0]
            out = conn.execute("SELECT COUNT(*) FROM candidate_outcome WHERE market_date=?", (OBJETIVO_90D,)).fetchone()[0]
            obs = conn.execute("SELECT COUNT(*) FROM candidate_observation WHERE market_date=?", (OBJETIVO_90D,)).fetchone()[0]
        assert det == 1
        assert out == 1
        assert obs == 0  # esta si se compacto
    finally:
        _restore_compaction()
        _restore()


def test_F_reinicio_desde_estado_intermedio_verified_continua_seguro():
    """El caso central de esta tarea: un intento anterior (proceso que
    murio, o una corrida previa) dejo el bloque en 'verified' pero nunca
    llego a autorizar/compactar. El hook debe DETECTAR eso y terminar el
    trabajo, sin repetir provisional/verificacion desde cero ni saltarse
    la autorizacion."""
    _fresh()
    _fresh_compaction()
    try:
        _seed_obs(OBJETIVO_90D, n=4)
        _coc.run_provisional_for_date(OBJETIVO_90D, today=TODAY_W)
        _coc.run_verification_for_date(OBJETIVO_90D)
        # simula el "reinicio" -- nada en memoria, solo lo que ya quedo en
        # el manifiesto persistido (verified, sin autorizar ni compactar).

        w._maybe_run_observation_compaction(TODAY_W)

        meta = reg.get_meta()
        resultado = meta["observation_compaction_ultimo_resultado"]
        assert resultado["estado_inicial"] == "verified"
        assert "provisional" not in resultado  # no se repitio, ya existia
        assert resultado["authorized"]["ok"] is True
        assert resultado["compacted"]["ok"] is True
        assert resultado["compacted"]["deleted_row_count"] == 4
    finally:
        _restore_compaction()
        _restore()


def test_F_reinicio_desde_compaction_authorized_termina_el_delete():
    _fresh()
    _fresh_compaction()
    try:
        _seed_obs(OBJETIVO_90D, n=5)
        _coc.run_provisional_for_date(OBJETIVO_90D, today=TODAY_W)
        _coc.run_verification_for_date(OBJETIVO_90D)
        _coc.authorize_compaction_for_date(OBJETIVO_90D)
        # "reinicio" -- quedo compaction_authorized, el DELETE nunca corrio.

        w._maybe_run_observation_compaction(TODAY_W)

        meta = reg.get_meta()
        resultado = meta["observation_compaction_ultimo_resultado"]
        assert resultado["estado_inicial"] == "compaction_authorized"
        assert "provisional" not in resultado
        assert "verified" not in resultado
        assert "authorized" not in resultado
        assert resultado["compacted"]["ok"] is True
        assert resultado["compacted"]["deleted_row_count"] == 5
    finally:
        _restore_compaction()
        _restore()


def test_F_bloque_ya_compactado_es_no_op_total():
    """Idempotencia extrema: si el bloque ya llego a 'compacted' en un
    ciclo EOD anterior (marcador de fecha distinto, ej. el proceso corrio
    ayer y hoy el objetivo es otro dia mas viejo) y por algun motivo se
    volviera a apuntar al mismo objetivo, el hook no debe intentar NINGUNA
    etapa nueva."""
    _fresh()
    _fresh_compaction()
    try:
        _seed_obs(OBJETIVO_90D, n=2)
        _coc.run_provisional_for_date(OBJETIVO_90D, today=TODAY_W)
        _coc.run_verification_for_date(OBJETIVO_90D)
        _coc.authorize_compaction_for_date(OBJETIVO_90D)
        _coc.compact_block(OBJETIVO_90D, today=TODAY_W)

        # forzar que el hook vuelva a intentar el MISMO objetivo (normalmente
        # no pasaria el mismo dia real, pero prueba la garantia de fondo).
        reg.set_meta(observation_compaction_ejecutada_para=None)
        w._maybe_run_observation_compaction(TODAY_W)

        meta = reg.get_meta()
        resultado = meta["observation_compaction_ultimo_resultado"]
        assert resultado["estado_inicial"] == "compacted"
        assert "provisional" not in resultado
        assert "verified" not in resultado
        assert "authorized" not in resultado
        assert "compacted" not in resultado  # no se re-ejecuta compact_block
    finally:
        _restore_compaction()
        _restore()


def test_F_maybe_run_eod_evaluation_completo_sigue_devolviendo_true_pese_a_fallo_de_compaction():
    """Integración contra la función PÚBLICA real (`maybe_run_eod_evaluation`):
    un fallo REAL dentro de la compaction (no un mock que reemplace toda la
    protección) debe quedar contenido por el try/except propio de
    `_maybe_run_observation_compaction()` -- la función completa sigue
    devolviendo True (el EOD en sí fue exitoso), exactamente la garantía de
    aislamiento pedida. Si esta prueba fallara devolviendo False, sería la
    señal de que la protección INTERNA del hook dejó de funcionar y el
    error se escapó hasta el try/except externo de
    `maybe_run_eod_evaluation` (que sí marca el EOD entero como fallido)."""
    _fresh()
    _fresh_compaction()
    saved = _install_fakes(session="afterhours", quotes={})
    try:
        market_date = market_hours.market_date()
        reg.set_meta(current_market_date=market_date)

        from atlas_live.radar import eod_report as eod_mod

        orig_eod = eod_mod.run_eod_evaluation
        eod_mod.run_eod_evaluation = lambda *a, **k: SimpleNamespace(
            market_date=market_date, n_estudiadas=0, n_candidatas=0, n_senales=0, n_evaluadas=0,
            n_aciertos=0, n_reached_20=0, n_reached_50=0, n_reached_100=0, n_falsas_senales=0,
            n_deteccion_tardia=0, n_direccion_correcta=0, n_direccion_incorrecta=0,
            mejores_oportunidades=[], posibles_no_detectadas=[],
        )

        orig_provisional = _coc.run_provisional_for_date
        llamado = {"n": 0}

        def _boom(*a, **k):
            llamado["n"] += 1
            raise RuntimeError("fallo real simulado dentro de la compaction")

        _coc.run_provisional_for_date = _boom
        try:
            resultado = w.maybe_run_eod_evaluation()
        finally:
            _coc.run_provisional_for_date = orig_provisional
            eod_mod.run_eod_evaluation = orig_eod

        assert llamado["n"] == 1  # la compaction SI se intento y SI fallo
        assert resultado is True  # pero el EOD completo se reporta exitoso igual
        meta = reg.get_meta()
        assert meta.get("state") == "EOD_COMPLETO"
        assert "RuntimeError" in (meta.get("observation_compaction_ultimo_error") or "")
    finally:
        _restore_compaction()
        _uninstall_fakes(saved)
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
