"""Tests de `u3c3_exclusive_diagnostics.py` (2026-09-02, autorizado
explícitamente) -- DBs temporales aisladas, fixtures reales insertadas vía
las funciones de escritura ya existentes. Confirma: la conexión `mode=ro`
bloquea escrituras a nivel de SQLite, cada consulta agregada da el
resultado esperado sobre datos sintéticos controlados, el agrupamiento de
episodios es matemáticamente correcto para gaps conocidos, y ninguna
función modifica ninguna fila."""

import sqlite3
import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.radar import candidate_registry as reg
from atlas_live.radar import shadow_detector_registry as sreg
from atlas_live.radar import u3c3_exclusive_diagnostics as u3d

_ORIG_REG_DB = reg.DB_PATH
_ORIG_SHADOW_DB = sreg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_u3d_reg_{_uuid.uuid4().hex}.db"
    sreg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_u3d_shadow_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None


def _restore():
    reg.DB_PATH = _ORIG_REG_DB
    sreg.DB_PATH = _ORIG_SHADOW_DB


def _shadow(ticker, market_date, detected_at, session="regular", gates=None):
    sreg.record_shadow_detection(
        ticker=ticker, market_date=market_date, session=session,
        price=10.0, change_pct=6.0, volume=500_000, average_volume=100_000,
        relative_volume=5.0, dollar_volume=5_000_000.0,
        price_source="tradier", price_basis="tradier_last", price_is_stale=False,
        universe_source="piggyback_radar",
        gates_fired=gates if gates is not None else [{"gate": "cambio_de_precio"}],
        snapshot={"price": 10.0},
    )
    with sreg._connect() as conn:
        conn.execute(
            "UPDATE shadow_candidate_detection SET detected_at=? WHERE ticker=? AND market_date=? "
            "AND id = (SELECT MAX(id) FROM shadow_candidate_detection WHERE ticker=? AND market_date=?)",
            (detected_at, ticker, market_date, ticker, market_date),
        )
        conn.commit()


def _detect(ticker, market_date, detected_at, session="regular", gates=None):
    reg.record_detection(
        ticker, market_date, session, detected_at, "sweep-1",
        price_at_detection=10.0, change_pct_at_detection=6.0,
        volume_at_detection=500_000, average_volume_at_detection=100_000,
        relative_volume_at_detection=5.0, dollar_volume_at_detection=5_000_000.0,
        gates_fired=gates if gates is not None else [{"gate": "cambio_de_precio"}],
    )


def _outcome(ticker, market_date, reached_20=0, max_return=1.0, category="oportunidad_moderada"):
    reg.record_outcome(
        ticker=ticker, market_date=market_date, run_up_before_detection_pct=None,
        max_price_after_detection=10.0 * (1 + max_return / 100), max_return_after_detection_pct=max_return,
        minutes_to_max=None, reached_20=reached_20, reached_50=0, reached_100=0,
        category=category, confiable_para_aprendizaje=True, is_final=True,
    )


# --- Garantía read-only ------------------------------------------------

def test_ro_connect_bloquea_escritura_a_nivel_de_sqlite():
    _fresh()
    try:
        _shadow("AAA", "2026-08-26", "2026-08-26T14:00:00+00:00")
        conn = u3d._ro_connect(sreg.DB_PATH)
        try:
            import pytest
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("INSERT INTO shadow_candidate_detection (ticker, market_date, detected_at, session, "
                              "universe_source, gates_fired, snapshot_json, created_at) "
                              "VALUES ('X','2026-08-26','t','regular','x','[]','{}','t')")
        finally:
            conn.close()
    finally:
        _restore()


# --- B.1: volumen y distribución ----------------------------------------

def test_volume_and_distribution_conteos_reales():
    _fresh()
    try:
        _shadow("AAA", "2026-08-26", "2026-08-26T14:00:00+00:00")
        _shadow("AAA", "2026-08-26", "2026-08-26T14:01:00+00:00")
        _shadow("AAA", "2026-08-26", "2026-08-26T14:02:00+00:00")
        _shadow("BBB", "2026-08-26", "2026-08-26T14:00:00+00:00")
        _shadow("CCC", "2026-08-27", "2026-08-27T09:00:00+00:00")

        r = u3d.volume_and_distribution(market_dates=("2026-08-26", "2026-08-27"))
        assert r["total_filas_unified"] == 5
        assert r["n_tickers_distintos"] == 3
        assert {d["market_date"]: d["n"] for d in r["distribucion_por_market_date"]} == {
            "2026-08-26": 4, "2026-08-27": 1,
        }
        top = {t["ticker"]: t["n"] for t in r["top_50_tickers"]}
        assert top == {"AAA": 3, "BBB": 1, "CCC": 1}
        assert r["filas_por_ticker_stats"]["n"] == 3
        assert r["concentracion"]["pct_del_total_top50_tickers"] == 100.0
    finally:
        _restore()


# --- B.3: distribución por gate ------------------------------------------

def test_gates_distribution_json1():
    _fresh()
    try:
        _shadow("AAA", "2026-08-26", "2026-08-26T14:00:00+00:00",
                gates=[{"gate": "cambio_de_precio"}, {"gate": "volumen_relativo"}])
        _shadow("BBB", "2026-08-26", "2026-08-26T14:01:00+00:00", gates=[{"gate": "cambio_de_precio"}])

        r = u3d.gates_distribution(market_dates=("2026-08-26",))
        assert r["json1_disponible"] is True
        por_gate = {g["gate"]: g["n"] for g in r["distribucion_por_gate"]}
        assert por_gate == {"cambio_de_precio": 2, "volumen_relativo": 1}
    finally:
        _restore()


# --- B.7: episodios -- gaps conocidos, resultado calculado a mano --------

def test_episode_grouping_gaps_conocidos_da_resultado_esperado():
    _fresh()
    try:
        # Gaps: 25s, 45s, 120s, 220s, 350s -- diseñados para dar un
        # resultado DISTINTO en cada una de las 4 ventanas pedidas.
        base = [
            "2026-08-26T14:00:00+00:00",  # t=0
            "2026-08-26T14:00:25+00:00",  # +25s
            "2026-08-26T14:01:10+00:00",  # +45s (70s acum)
            "2026-08-26T14:03:10+00:00",  # +120s (190s acum)
            "2026-08-26T14:06:50+00:00",  # +220s (410s acum)
            "2026-08-26T14:12:40+00:00",  # +350s (760s acum)
        ]
        for ts in base:
            _shadow("EPI", "2026-08-26", ts)

        r = u3d.episode_grouping(market_dates=("2026-08-26",), windows_seconds=(30, 60, 180, 300))
        assert r["filas_totales_en_la_ventana"] == 6
        ep = r["episodios_shadow_unified_aproximado_por_ventana"]
        assert ep["ventana_30s"] == 5
        assert ep["ventana_60s"] == 4
        assert ep["ventana_180s"] == 3
        assert ep["ventana_300s"] == 2
        assert "mezcladas" in r["nota_metodologica"]
        assert "0.57%" in r["nota_metodologica"]
    finally:
        _restore()


def test_episode_grouping_particiona_por_ticker_y_por_dia():
    _fresh()
    try:
        # 2 tickers distintos, cada uno con 1 sola fila -- cada fila es su
        # propio episodio (prev IS NULL), sin importar la ventana.
        _shadow("AAA", "2026-08-26", "2026-08-26T14:00:00+00:00")
        _shadow("BBB", "2026-08-26", "2026-08-26T14:00:05+00:00")
        r = u3d.episode_grouping(market_dates=("2026-08-26",), windows_seconds=(30,))
        assert r["episodios_shadow_unified_aproximado_por_ventana"]["ventana_30s"] == 2
    finally:
        _restore()


# --- B.4: solo_legacy characteristics -------------------------------------

def test_solo_legacy_characteristics_agrega_correctamente():
    _fresh()
    try:
        # SOLOLEG1: legacy solo, sesión premarket, puerta comparativa.
        _detect("SOLOLEG1", "2026-08-26", "2026-08-26T08:00:00+00:00", session="premarket",
                gates=[{"gate": "aceleracion"}])
        # SOLOLEG2: legacy solo, sesión regular, puerta simple.
        _detect("SOLOLEG2", "2026-08-26", "2026-08-26T14:00:00+00:00", session="regular",
                gates=[{"gate": "cambio_de_precio"}])
        # MATCH: no debe contar como solo_legacy.
        _detect("MATCH", "2026-08-26", "2026-08-26T15:00:00+00:00", session="regular")
        _shadow("MATCH", "2026-08-26", "2026-08-26T15:00:30+00:00")

        r = u3d.solo_legacy_characteristics(market_dates=("2026-08-26",))
        assert r["total_solo_legacy"] == 2
        assert r["por_sesion"] == {"premarket": 1, "regular": 1}
        assert r["por_hora_utc"] == {8: 1, 14: 1}
        assert r["por_gate_disparada"] == {"aceleracion": 1, "cambio_de_precio": 1}
        assert r["evidencia_circunstancial_mecanismos"]["con_al_menos_una_puerta_comparativa"] == 1
        assert r["evidencia_circunstancial_mecanismos"]["solo_puertas_simples"] == 1
    finally:
        _restore()


# --- B.5: timing de matched -------------------------------------------------

def test_matched_timing_percentiles():
    _fresh()
    try:
        # MATCH1: legacy antes (diff=+60s). MATCH2: unified antes (diff=-30s).
        _detect("MATCH1", "2026-08-26", "2026-08-26T14:00:00+00:00")
        _shadow("MATCH1", "2026-08-26", "2026-08-26T14:01:00+00:00")
        _detect("MATCH2", "2026-08-26", "2026-08-26T15:00:00+00:00")
        _shadow("MATCH2", "2026-08-26", "2026-08-26T14:59:30+00:00")

        r = u3d.matched_timing_percentiles(market_dates=("2026-08-26",))
        assert r["n_matched_total"] == 2
        assert r["legacy_antes_que_unified"]["n"] == 1
        assert r["legacy_antes_que_unified"]["media"] == 60.0
        assert r["unified_antes_que_legacy"]["n"] == 1
        assert r["unified_antes_que_legacy"]["media"] == 30.0
        assert r["simultaneas"] == 0
    finally:
        _restore()


# --- B.6: cobertura estructural ---------------------------------------------

def test_structural_outcome_coverage_nunca_evalua_resultado():
    _fresh()
    try:
        _shadow("CONOUTCOME", "2026-08-26", "2026-08-26T14:00:00+00:00")
        _detect("CONOUTCOME", "2026-08-26", "2026-08-26T10:00:00+00:00")  # otro evento, mismo dia
        _outcome("CONOUTCOME", "2026-08-26")

        _shadow("SINOUTCOME", "2026-08-26", "2026-08-26T14:00:00+00:00")

        r = u3d.structural_outcome_coverage(market_dates=("2026-08-26",))
        assert r["total_pares_ticker_dia_con_deteccion_shadow"] == 2
        assert r["con_candidate_outcome_disponible"] == 1
        assert r["sin_candidate_outcome_disponible"] == 1
        assert r["pct_con_outcome_estructural"] == 50.0
        assert "NUNCA es una evaluacion de resultado" in r["nota"]
    finally:
        _restore()


# --- No escribe nada ---------------------------------------------------------

def test_full_report_no_modifica_ningun_dato():
    _fresh()
    try:
        _shadow("AAA", "2026-08-26", "2026-08-26T14:00:00+00:00")
        _detect("BBB", "2026-08-26", "2026-08-26T14:00:00+00:00")
        _outcome("BBB", "2026-08-26")

        conteo_shadow_antes = sreg.count_shadow_detections("2026-08-26")
        conteo_legacy_antes = reg.count_candidates_for_date("2026-08-26")

        u3d.full_report(market_dates=("2026-08-26",))

        assert sreg.count_shadow_detections("2026-08-26") == conteo_shadow_antes
        assert reg.count_candidates_for_date("2026-08-26") == conteo_legacy_antes
    finally:
        _restore()


# --- Endpoint no acepta parámetros externos (hardcoded dates) --------------

def test_diagnostic_market_dates_son_las_4_fechas_reales_de_u3c3():
    assert u3d.DIAGNOSTIC_MARKET_DATES == ("2026-08-26", "2026-08-27", "2026-08-28", "2026-08-31")


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
