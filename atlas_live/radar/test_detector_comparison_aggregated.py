"""Tests de `detector_comparison.py::quality_report_aggregated()` (2026-09-02,
autorizado explícitamente) -- fixtures sintéticas reales insertadas vía las
funciones de escritura ya existentes (`record_detection`/`record_shadow_detection`/
`record_outcome`/`record_shadow_decision`), sobre DBs temporales aisladas.
Confirma: equivalencia con `quality_report()` para las métricas que ya
existían, semántica correcta de las métricas nuevas (+100%, tiempo al
objetivo, quién detectó primero agregado, PM-RVOL, conocimiento vs.
baseline), que nunca retiene detalle completo, y que un día con error no
tumba el resto."""

import json
import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.radar import candidate_registry as reg
from atlas_live.radar import detector_comparison as dc
from atlas_live.radar import shadow_detector_registry as sreg

_ORIG_REG_DB = reg.DB_PATH
_ORIG_SHADOW_DB = sreg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_dca_reg_{_uuid.uuid4().hex}.db"
    sreg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_dca_shadow_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None


def _restore():
    reg.DB_PATH = _ORIG_REG_DB
    sreg.DB_PATH = _ORIG_SHADOW_DB


def _detect(ticker, market_date, detected_at, session="regular", **pm_kwargs):
    reg.record_detection(
        ticker, market_date, session, detected_at, "sweep-1",
        price_at_detection=10.0, change_pct_at_detection=6.0,
        volume_at_detection=500_000, average_volume_at_detection=100_000,
        relative_volume_at_detection=5.0, dollar_volume_at_detection=5_000_000.0,
        gates_fired=[{"gate": "cambio_de_precio"}],
        **pm_kwargs,
    )


def _shadow(ticker, market_date, detected_at, session="regular"):
    # record_shadow_detection() no acepta `detected_at` -- usa siempre la
    # hora real (`_now()`). Para poder probar el matching temporal con
    # timestamps controlados, se corrige con un UPDATE directo después del
    # INSERT real -- único punto de este archivo que toca SQL crudo, y solo
    # sobre la DB temporal de test, nunca sobre datos reales.
    sreg.record_shadow_detection(
        ticker=ticker, market_date=market_date, session=session,
        price=10.05, change_pct=6.3, volume=505_000, average_volume=100_000,
        relative_volume=5.05, dollar_volume=5_075_250.0,
        price_source="tradier", price_basis="tradier_last", price_is_stale=False,
        universe_source="piggyback_radar", gates_fired=[{"gate": "cambio_de_precio"}],
        snapshot={"price": 10.05},
    )
    with sreg._connect() as conn:
        conn.execute(
            "UPDATE shadow_candidate_detection SET detected_at=? WHERE ticker=? AND market_date=? "
            "AND id = (SELECT MAX(id) FROM shadow_candidate_detection WHERE ticker=? AND market_date=?)",
            (detected_at, ticker, market_date, ticker, market_date),
        )
        conn.commit()


def _outcome(ticker, market_date, reached_20, reached_50, reached_100,
             max_return, minutes_to_max=None, category="buena_oportunidad"):
    reg.record_outcome(
        ticker=ticker, market_date=market_date, run_up_before_detection_pct=None,
        max_price_after_detection=10.0 * (1 + max_return / 100), max_return_after_detection_pct=max_return,
        minutes_to_max=minutes_to_max, reached_20=reached_20, reached_50=reached_50, reached_100=reached_100,
        category=category, confiable_para_aprendizaje=True, is_final=True,
    )


def _build_two_days_scenario():
    """DÍA 1 (2026-08-26): MATCH1 (matched, unified 60s DESPUÉS), SOLOLEG1
    (solo legacy, premarket con PM-RVOL VALID), SOLOUNI1 (solo unified).
    DÍA 2 (2026-08-27): MATCH2 (matched, unified 60s ANTES), SOLOLEG2 (solo
    legacy, premarket sin historial suficiente para PM-RVOL)."""
    _detect("MATCH1", "2026-08-26", "2026-08-26T14:30:00+00:00", session="regular")
    _shadow("MATCH1", "2026-08-26", "2026-08-26T14:31:00+00:00", session="regular")
    _outcome("MATCH1", "2026-08-26", reached_20=1, reached_50=0, reached_100=0,
              max_return=25.0, minutes_to_max=45, category="buena_oportunidad")

    _detect("SOLOLEG1", "2026-08-26", "2026-08-26T08:00:00+00:00", session="premarket",
             pm_percentile_at_detection=75.0, pm_percentile_state_at_detection="VALID",
             pm_acceleration_at_detection=2.5, pm_acceleration_state_at_detection="VALID")
    _outcome("SOLOLEG1", "2026-08-26", reached_20=0, reached_50=0, reached_100=0,
              max_return=3.0, minutes_to_max=None, category="falsa_senal")

    _shadow("SOLOUNI1", "2026-08-26", "2026-08-26T16:00:00+00:00", session="regular")

    _detect("MATCH2", "2026-08-27", "2026-08-27T10:00:00+00:00", session="regular")
    _shadow("MATCH2", "2026-08-27", "2026-08-27T09:59:00+00:00", session="regular")
    _outcome("MATCH2", "2026-08-27", reached_20=1, reached_50=1, reached_100=0,
              max_return=55.0, minutes_to_max=120, category="mejor_oportunidad")

    _detect("SOLOLEG2", "2026-08-27", "2026-08-27T08:05:00+00:00", session="premarket",
             pm_percentile_state_at_detection="INSUFFICIENT_UNIVERSE",
             pm_acceleration_state_at_detection="INSUFFICIENT_HISTORY")
    _outcome("SOLOLEG2", "2026-08-27", reached_20=0, reached_50=0, reached_100=0,
              max_return=1.0, minutes_to_max=None, category="oportunidad_moderada")


def test_equivalencia_con_quality_report_en_metricas_existentes():
    _fresh()
    try:
        _build_two_days_scenario()
        market_dates = ["2026-08-26", "2026-08-27"]

        original = dc.quality_report(market_dates)
        agregado = dc.quality_report_aggregated(market_dates)

        campos_compartidos = [
            "total_legacy", "total_unified", "detectadas_por_ambos", "solo_legacy", "solo_unified",
            "recall_relativo_unified", "tasa_deteccion_compartida", "muestra_total",
            "estado_validacion_muestra", "outcome_n_evaluable", "outcome_pct_reached_20",
            "outcome_pct_reached_50", "outcome_magnitud_maxima_promedio", "outcome_magnitud_maxima_mediana",
            "solo_unified_outcome_status",
        ]
        for campo in campos_compartidos:
            assert agregado[campo] == original[campo], f"{campo}: {agregado[campo]!r} != {original[campo]!r}"

        # "Quién detectó primero" agregado == suma manual de los 2 días de
        # quality_report()'s `por_dia` (que sí retiene el detalle por día).
        suma_unified_antes = sum(d["unified_antes_que_legacy"] for d in original["por_dia"])
        suma_legacy_antes = sum(d["legacy_antes_que_unified"] for d in original["por_dia"])
        suma_simultaneas = sum(d["detecciones_simultaneas"] for d in original["por_dia"])
        assert agregado["quien_detecto_primero"]["unified_antes_que_legacy"] == suma_unified_antes
        assert agregado["quien_detecto_primero"]["legacy_antes_que_unified"] == suma_legacy_antes
        assert agregado["quien_detecto_primero"]["simultaneas"] == suma_simultaneas
        # MATCH1: unified 60s DESPUÉS de legacy -> legacy_antes_que_unified.
        # MATCH2: unified 60s ANTES de legacy -> unified_antes_que_legacy.
        assert agregado["quien_detecto_primero"]["legacy_antes_que_unified"] == 1
        assert agregado["quien_detecto_primero"]["unified_antes_que_legacy"] == 1
    finally:
        _restore()


def test_nunca_retiene_detalle_completo():
    _fresh()
    try:
        _build_two_days_scenario()
        resultado = dc.quality_report_aggregated(["2026-08-26", "2026-08-27"])

        for clave_prohibida in ("matched", "solo_legacy_detalle", "solo_unified_detalle", "por_dia"):
            assert clave_prohibida not in resultado

        como_json = json.dumps(resultado)
        assert "snapshot" not in como_json
        assert "gates_fired" not in como_json
    finally:
        _restore()


def test_reached_100_y_tiempo_a_objetivo_metricas_nuevas():
    _fresh()
    try:
        _build_two_days_scenario()
        resultado = dc.quality_report_aggregated(["2026-08-26", "2026-08-27"])

        # 4 outcomes evaluables (MATCH1/SOLOLEG1/MATCH2/SOLOLEG2), ninguno con reached_100.
        assert resultado["outcome_pct_reached_100"] == 0.0
        # minutes_to_max real solo en MATCH1 (45) y MATCH2 (120) -- los otros 2 son None.
        assert resultado["outcome_tiempo_a_objetivo_n"] == 2
        assert resultado["outcome_tiempo_a_objetivo_promedio_minutos"] == (45 + 120) / 2
        assert resultado["outcome_tiempo_a_objetivo_mediano_minutos"] == (45 + 120) / 2
    finally:
        _restore()


def test_pm_rvol_solo_poblacion_legacy_con_estados_reales():
    _fresh()
    try:
        _build_two_days_scenario()
        resultado = dc.quality_report_aggregated(["2026-08-26", "2026-08-27"])
        pm = resultado["pm_rvol"]

        assert pm["unavailable"] is False
        assert pm["unified_coverage"].startswith("no disponible")
        # SOLOLEG1 es el único con estado VALID -- percentil 75.0, acceleration 2.5.
        assert pm["percentile_conteo_por_estado"]["VALID"] == 1
        assert pm["percentile_conteo_por_estado"]["INSUFFICIENT_UNIVERSE"] == 1
        assert pm["percentile_promedio"] == 75.0
        assert pm["percentile_mediana"] == 75.0
        assert pm["acceleration_promedio"] == 2.5
        # MATCH1/MATCH2 son sesión "regular" -- record_detection() nunca les
        # pasó ningún estado PM (quedan None, no cuentan en el conteo).
        assert sum(pm["percentile_conteo_por_estado"].values()) == 2
    finally:
        _restore()


def test_pm_rvol_unavailable_cuando_nadie_tiene_estado_valid():
    _fresh()
    try:
        _detect("SOLO_NOT_PREMARKET", "2026-08-26", "2026-08-26T14:30:00+00:00", session="regular")
        _outcome("SOLO_NOT_PREMARKET", "2026-08-26", reached_20=0, reached_50=0, reached_100=0, max_return=1.0)

        resultado = dc.quality_report_aggregated(["2026-08-26"])
        pm = resultado["pm_rvol"]
        assert pm["unavailable"] is True
        assert "reason" in pm and pm["reason"]
        assert pm["percentile_promedio"] is None
    finally:
        _restore()


def test_conocimiento_vs_baseline_downgrade_correcto_e_incorrecto():
    _fresh()
    try:
        # Evento 1 (día 1): LEK habría bajado a NO_TOCAR una candidata que
        # terminó siendo "falsa_senal" real -> downgrade CORRECTO.
        reg.record_shadow_decision(
            ticker="DIV1", market_date="2026-08-26", decision="VIGILAR", decision_shadow="NO_TOCAR",
            shadow_differs=True, validation_state="VALIDACION_ROBUSTA", sample_size=600,
            wilson_upper_bound_20_pct=30.0, baseline_pct_20=45.0,
        )
        _outcome("DIV1", "2026-08-26", reached_20=0, reached_50=0, reached_100=0,
                  max_return=2.0, category="falsa_senal")

        # Evento 2 (día 2): LEK habría bajado a NO_TOCAR una candidata que
        # SÍ era buena -> downgrade INCORRECTO.
        reg.record_shadow_decision(
            ticker="DIV2", market_date="2026-08-27", decision="OPORTUNIDAD_PRIORITARIA",
            decision_shadow="VIGILAR", shadow_differs=True, validation_state="EN_VALIDACION",
            sample_size=150, wilson_upper_bound_20_pct=20.0, baseline_pct_20=50.0,
        )
        _outcome("DIV2", "2026-08-27", reached_20=1, reached_50=0, reached_100=0,
                  max_return=30.0, category="buena_oportunidad")

        resultado = dc.quality_report_aggregated(["2026-08-26", "2026-08-27"])
        cvb = resultado["conocimiento_vs_baseline"]

        assert cvb["unavailable"] is False
        assert cvb["total_eventos_shadow_differs"] == 2
        assert cvb["downgrade_correcto"] == 1
        assert cvb["downgrade_incorrecto"] == 1
        assert cvb["n_evaluables_tasa"] == 2
        assert cvb["tasa_acierto_pct"] == 50.0
        assert cvb["wilson_ci"] is not None
    finally:
        _restore()


def test_conocimiento_vs_baseline_unavailable_sin_eventos():
    _fresh()
    try:
        _detect("SINDIV", "2026-08-26", "2026-08-26T14:30:00+00:00")
        resultado = dc.quality_report_aggregated(["2026-08-26"])
        cvb = resultado["conocimiento_vs_baseline"]
        assert cvb["unavailable"] is True
        assert "reason" in cvb and cvb["reason"]
    finally:
        _restore()


def test_dia_con_error_no_tumba_el_resto(monkeypatch):
    _fresh()
    try:
        _build_two_days_scenario()
        orig = dc.compare_legacy_vs_unified

        def _fake(market_date):
            if market_date == "2026-08-27":
                raise RuntimeError("fallo sintético de prueba")
            return orig(market_date)

        monkeypatch.setattr(dc, "compare_legacy_vs_unified", _fake)
        resultado = dc.quality_report_aggregated(["2026-08-26", "2026-08-27"])

        assert resultado["dias_procesados"] == 1
        assert resultado["dias_con_error"] == [{"market_date": "2026-08-27", "error": "RuntimeError: fallo sintético de prueba"}]
        # El día 1 se siguió procesando y agregando con normalidad.
        assert resultado["total_legacy"] == 2  # MATCH1 + SOLOLEG1, solo día 1
    finally:
        _restore()


def test_no_escribe_nada_en_ninguna_db():
    """Verifica integridad de DATOS (conteos por fecha + un valor
    puntual), no tamaño de archivo en bytes -- SQLite en modo WAL puede
    hacer un checkpoint automático (housekeeping interno, transparente,
    disparado por cualquier conexión nueva una vez que el WAL supera su
    umbral) que cambia el layout de bytes en disco SIN que ninguna fila
    haya cambiado -- confirmado empíricamente en esta sesión. Lo que
    importa -- y lo que se verifica acá -- es que ninguna fila se
    insertó/actualizó/borró."""
    _fresh()
    try:
        _build_two_days_scenario()
        conteo_legacy_antes = {d: reg.count_candidates_for_date(d) for d in ("2026-08-26", "2026-08-27")}
        conteo_shadow_antes = {d: sreg.count_shadow_detections(d) for d in ("2026-08-26", "2026-08-27")}
        match1_antes = reg.get_detection("MATCH1", "2026-08-26")

        dc.quality_report_aggregated(["2026-08-26", "2026-08-27"])

        for d in ("2026-08-26", "2026-08-27"):
            assert reg.count_candidates_for_date(d) == conteo_legacy_antes[d]
            assert sreg.count_shadow_detections(d) == conteo_shadow_antes[d]
        assert reg.get_detection("MATCH1", "2026-08-26") == match1_antes
    finally:
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
