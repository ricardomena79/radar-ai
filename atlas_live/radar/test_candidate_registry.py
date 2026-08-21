"""Tests del registro de candidatas del radar (2026-08-14). DB temporal, sin red."""

import tempfile
import uuid as _uuid
from pathlib import Path

import pytest

from atlas_live.radar import candidate_registry as reg

_ORIG = reg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_radar_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None


def _restore():
    reg.DB_PATH = _ORIG


def test_primera_deteccion_es_idempotente():
    _fresh()
    try:
        nueva1 = reg.record_detection(
            "AAPL", "2026-08-14", "regular", "2026-08-14T14:35:00Z", "sweep-1",
            305.0, 4.2, 1_000_000, 500_000, 2.0, 305_000_000,
            gates_fired=[{"name": "cambio_de_precio", "value": 4.2}],
        )
        nueva2 = reg.record_detection(
            "AAPL", "2026-08-14", "regular", "2026-08-14T14:40:00Z", "sweep-2",
            310.0, 5.0, 1_200_000, 500_000, 2.4, 372_000_000,
            gates_fired=[{"name": "aceleracion", "value": 1.0}],
        )
        assert nueva1 is True
        assert nueva2 is False  # ya existía -- no se pisa
        candidatas = reg.list_candidates_for_date("2026-08-14")
        assert len(candidatas) == 1
        assert candidatas[0]["price_at_detection"] == 305.0  # quedó el ORIGINAL, no el segundo intento
    finally:
        _restore()


def test_observaciones_se_acumulan_no_desaparece_la_candidata():
    _fresh()
    try:
        reg.record_detection("TSLA", "2026-08-14", "regular", "2026-08-14T14:00:00Z", "sweep-1",
                              340.0, 3.0, 500_000, 300_000, 1.6, 170_000_000, gates_fired=[])
        for i in range(5):
            reg.record_observation("TSLA", "2026-08-14", f"2026-08-14T14:0{i}:00Z", f"sweep-{i+2}",
                                    340.0 + i, 3.0 + i, 500_000, 1.6 + i * 0.1, gates_fired_now=[])
        obs = reg.get_observations("TSLA", "2026-08-14")
        assert len(obs) == 5
        assert reg.count_candidates_for_date("2026-08-14") == 1  # sigue siendo UNA candidata, con 5 observaciones
    finally:
        _restore()


def test_outcome_es_upsert_una_sola_fila_por_ticker_y_dia():
    """2026-08-18: `record_outcome` dejó de ser INSERT-once -- ahora es
    upsert, para poder pasar de "en curso" (is_final=False) a "final"
    (is_final=True) sin duplicar filas. Sigue habiendo UNA sola fila por
    (ticker, market_date), pero la segunda llamada SÍ actualiza los
    valores (a diferencia del comportamiento viejo)."""
    _fresh()
    try:
        reg.record_detection("NVDA", "2026-08-14", "premarket", "2026-08-14T08:00:00Z", "sweep-1",
                              100.0, 5.0, 200_000, 150_000, 1.3, 20_000_000, gates_fired=[])
        ok1 = reg.record_outcome("NVDA", "2026-08-14", run_up_before_detection_pct=5.0,
                                  max_price_after_detection=130.0, max_return_after_detection_pct=30.0,
                                  minutes_to_max=45.0, reached_20=True, reached_50=False, reached_100=False,
                                  category="mejor_oportunidad", is_final=False)
        ok2 = reg.record_outcome("NVDA", "2026-08-14", run_up_before_detection_pct=5.0,
                                  max_price_after_detection=140.0, max_return_after_detection_pct=40.0,
                                  minutes_to_max=90.0, reached_20=True, reached_50=False, reached_100=False,
                                  category="mejor_oportunidad", is_final=True)
        assert ok1 is True
        assert ok2 is True
        outcomes = reg.list_outcomes_for_date("2026-08-14")
        assert len(outcomes) == 1  # sigue siendo una sola fila, no duplicada
        assert outcomes[0]["max_return_after_detection_pct"] == 40.0  # se actualizó al valor final
        assert outcomes[0]["is_final"] == 1
        assert reg.has_outcome("NVDA", "2026-08-14")
        assert reg.has_final_outcome("NVDA", "2026-08-14")
    finally:
        _restore()


def test_has_final_outcome_distingue_en_curso_de_final():
    _fresh()
    try:
        reg.record_detection("TSLA", "2026-08-14", "regular", "2026-08-14T14:00:00Z", "s1",
                              300.0, 2.0, 100_000, 80_000, 1.2, 30_000_000, gates_fired=[])
        reg.record_outcome("TSLA", "2026-08-14", run_up_before_detection_pct=2.0,
                            max_price_after_detection=310.0, max_return_after_detection_pct=3.3,
                            minutes_to_max=20.0, reached_20=False, reached_50=False, reached_100=False,
                            category="oportunidad_moderada", is_final=False)
        assert reg.has_outcome("TSLA", "2026-08-14") is True
        assert reg.has_final_outcome("TSLA", "2026-08-14") is False  # todavía "en curso"

        reg.record_outcome("TSLA", "2026-08-14", run_up_before_detection_pct=2.0,
                            max_price_after_detection=315.0, max_return_after_detection_pct=5.0,
                            minutes_to_max=180.0, reached_20=False, reached_50=False, reached_100=False,
                            category="oportunidad_moderada", is_final=True)
        assert reg.has_final_outcome("TSLA", "2026-08-14") is True
    finally:
        _restore()


def test_meta_y_status():
    _fresh()
    try:
        reg.set_meta(state="RUNNING", sweeps_total=10, sweeps_ok=9, sweeps_error=1,
                     current_market_date="2026-08-14", ultimo_sweep_at="2026-08-14T14:00:00Z",
                     ultimo_sweep_duracion_s=13.5)
        reg.record_detection("AMD", "2026-08-14", "regular", "2026-08-14T14:00:00Z", "s1",
                              150.0, 4.0, 1, 1, 1.5, 150, gates_fired=[])
        status = reg.radar_status()
        assert status["state"] == "RUNNING"
        assert status["sweeps_total"] == 10
        assert status["candidatas_hoy"] == 1
        assert status["market_date_actual"] == "2026-08-14"
    finally:
        _restore()


def test_gates_fired_se_serializan_y_deserializan():
    _fresh()
    try:
        gates = [{"name": "cambio_de_precio", "reason": "x", "value": 5.0}, {"name": "volumen_relativo", "reason": "y", "value": 2.1}]
        reg.record_detection("META", "2026-08-14", "regular", "2026-08-14T14:00:00Z", "s1",
                              500.0, 5.0, 1, 1, 1.5, 500, gates_fired=gates)
        candidatas = reg.list_candidates_for_date("2026-08-14")
        assert candidatas[0]["gates_fired"] == gates
    finally:
        _restore()


def test_list_all_evaluated_candidates_solo_incluye_con_outcome():
    _fresh()
    try:
        reg.record_detection("AAA", "2026-08-17", "regular", "2026-08-17T14:00:00Z", "s1",
                              10.0, 5.0, 1, 1, 1.5, 10, gates_fired=[])
        reg.record_detection("BBB", "2026-08-17", "regular", "2026-08-17T14:00:00Z", "s1",
                              20.0, 8.0, 1, 1, 1.5, 20, gates_fired=[])
        reg.set_phase_tag("AAA", "2026-08-17", "al_comienzo", direction_at_detection="ALCISTA")
        reg.record_outcome("AAA", "2026-08-17", run_up_before_detection_pct=5.0,
                            max_price_after_detection=12.0, max_return_after_detection_pct=25.0,
                            minutes_to_max=10.0, reached_20=True, reached_50=False, reached_100=False,
                            category="buena_oportunidad", direccion_correcta=True,
                            confiable_para_aprendizaje=True)
        # BBB nunca recibe outcome -- todavía abierta, no debe aparecer

        evaluados = reg.list_all_evaluated_candidates()
        assert len(evaluados) == 1
        assert evaluados[0]["ticker"] == "AAA"
        assert evaluados[0]["phase_tag"] == "al_comienzo"
        assert evaluados[0]["reached_20"] == 1
    finally:
        _restore()


def test_list_daily_summaries_orden_ascendente():
    _fresh()
    try:
        reg.record_daily_summary("2026-08-18", 100, 5, 3, 3, 1, 1, 0, 1, 0, 0)
        reg.record_daily_summary("2026-08-17", 100, 4, 2, 2, 1, 0, 0, 1, 0, 0)
        resumenes = reg.list_daily_summaries()
        assert [r["market_date"] for r in resumenes] == ["2026-08-17", "2026-08-18"]
    finally:
        _restore()


def test_recent_precision_numerador_y_denominador_explicitos():
    _fresh()
    try:
        reg.record_daily_summary("2026-08-17", 100, 4, 2, 2, 1, 0, 0, 1, 0, 0)
        reg.record_daily_summary("2026-08-18", 100, 4, 2, 3, 2, 0, 0, 1, 0, 0)
        r = reg.recent_precision(window_days=21)
        assert r["evaluables"] == 5
        assert r["aciertos"] == 3
        assert r["precision_pct"] == 60.0
        assert r["desde"] == "2026-08-17" and r["hasta"] == "2026-08-18"
    finally:
        _restore()


def test_marcador_racional_solo_cuenta_tickers_racional_disponibles(monkeypatch):
    """2026-08-18, pedido explícito del usuario: Atlas sigue aprendiendo del
    universo COMPLETO (el marcador Universal -- cumulative_precision/
    recent_precision/get_daily_summary -- no se toca, sigue contando TODO);
    el marcador Racional es un cálculo NUEVO y PARALELO que recalcula
    is_available() en cada llamada y solo cuenta esos tickers."""
    _fresh()
    try:
        racional_tickers = {"RAC"}
        monkeypatch.setattr("atlas.data.universe.is_available", lambda t: t in racional_tickers)

        for ticker in ("RAC", "NORAC"):
            reg.record_detection(ticker, "2026-08-18", "regular", "2026-08-18T14:00:00Z", "s1",
                                  10.0, 3.0, 1000, 500, 2.0, 1_000_000, gates_fired=[])
            reg.record_outcome(ticker, "2026-08-18", run_up_before_detection_pct=3.0,
                                max_price_after_detection=13.0, max_return_after_detection_pct=30.0,
                                minutes_to_max=20.0, reached_20=True, reached_50=True, reached_100=False,
                                category="buena_oportunidad")
        reg.record_daily_summary("2026-08-18", 100, 2, 2, 2, 2, 0, 0, 2, 2, 0)

        # Universal (sin tocar) -- sigue contando AMBOS tickers
        assert reg.cumulative_precision()["evaluables"] == 2
        assert reg.cumulative_precision()["aciertos"] == 2

        # Racional -- solo cuenta RAC
        dia_rac = reg.daily_precision_racional("2026-08-18")
        assert dia_rac["evaluables"] == 1 and dia_rac["aciertos"] == 1 and dia_rac["precision_pct"] == 100.0

        acum_rac = reg.cumulative_precision_racional()
        assert acum_rac["evaluables"] == 1 and acum_rac["aciertos"] == 1 and acum_rac["n_dias"] == 1

        reciente_rac = reg.recent_precision_racional(window_days=21)
        assert reciente_rac["evaluables"] == 1 and reciente_rac["aciertos"] == 1
        assert reciente_rac["dias_incluidos"] == 1
    finally:
        _restore()


def test_marcador_racional_sin_universo_disponible_devuelve_cero_no_error(monkeypatch):
    """Si atlas.data.universe no está disponible (import falla) o ningún
    ticker es Racional, el marcador Racional debe devolver 0/None, nunca
    lanzar ni inventar un dato."""
    _fresh()
    try:
        monkeypatch.setattr("atlas.data.universe.is_available", lambda t: False)
        reg.record_detection("XYZ", "2026-08-18", "regular", "2026-08-18T14:00:00Z", "s1",
                              10.0, 3.0, 1000, 500, 2.0, 1_000_000, gates_fired=[])
        reg.record_outcome("XYZ", "2026-08-18", run_up_before_detection_pct=3.0,
                            max_price_after_detection=13.0, max_return_after_detection_pct=30.0,
                            minutes_to_max=20.0, reached_20=True, reached_50=True, reached_100=False,
                            category="buena_oportunidad")
        dia_rac = reg.daily_precision_racional("2026-08-18")
        assert dia_rac["evaluables"] == 0 and dia_rac["aciertos"] == 0 and dia_rac["precision_pct"] is None
    finally:
        _restore()


def test_set_experimental_signals_no_pisa_con_none():
    _fresh()
    try:
        reg.record_detection("XYZ", "2026-08-17", "regular", "2026-08-17T14:00:00Z", "s1",
                              10.0, 5.0, 1, 1, 1.5, 10, gates_fired=[])
        reg.set_experimental_signals("XYZ", "2026-08-17", volatility_14d_pct=6.5)
        reg.set_experimental_signals("XYZ", "2026-08-17", daily_range_pct=3.2)  # no debe borrar volatility_14d_pct
        candidatas = reg.list_candidates_for_date("2026-08-17")
        assert candidatas[0]["volatility_14d_pct_at_detection"] == 6.5
        assert candidatas[0]["daily_range_pct_at_detection"] == 3.2
    finally:
        _restore()


def test_early_vs_late_summary_agrupa_y_separa_direccion():
    _fresh()
    try:
        casos = [
            ("AAA", "al_comienzo", "ALCISTA", True, False, False),
            ("BBB", "expansion_temprana", "ALCISTA", True, True, False),
            ("CCC", "demasiado_tarde", "ALCISTA", False, False, False),
            ("DDD", "antes_del_movimiento", "ALCISTA", False, False, False),
            ("EEE", "al_comienzo", "BAJISTA", True, False, False),  # nunca debe sumarse con ALCISTA
        ]
        for ticker, phase_tag, direction, r20, r50, r100 in casos:
            reg.record_detection(ticker, "2026-08-17", "regular", "2026-08-17T14:00:00Z", "s1",
                                  10.0, 5.0, 1, 1, 1.5, 10, gates_fired=[])
            reg.set_phase_tag(ticker, "2026-08-17", phase_tag, direction_at_detection=direction)
            reg.record_outcome(ticker, "2026-08-17", run_up_before_detection_pct=5.0,
                                max_price_after_detection=12.0, max_return_after_detection_pct=25.0,
                                minutes_to_max=10.0, reached_20=r20, reached_50=r50, reached_100=r100,
                                category="x", confiable_para_aprendizaje=True)

        resumen = reg.early_vs_late_summary()
        assert resumen["ALCISTA"]["early_genuino"]["n"] == 2
        assert resumen["ALCISTA"]["early_genuino"]["aciertos_20"] == 2
        assert resumen["ALCISTA"]["late"]["n"] == 1
        assert resumen["ALCISTA"]["antes_del_movimiento"]["n"] == 1
        assert resumen["BAJISTA"]["early_genuino"]["n"] == 1
    finally:
        _restore()


def test_recent_precision_sin_datos_devuelve_no_disponible():
    _fresh()
    try:
        r = reg.recent_precision(window_days=21)
        assert r["evaluables"] == 0
        assert r["precision_pct"] is None
    finally:
        _restore()


def test_candidate_timeline_une_deteccion_observaciones_transiciones_y_outcome(monkeypatch):
    _fresh()
    try:
        monkeypatch.setattr("atlas.data.universe.is_available", lambda symbol: True)

        reg.record_detection("ZIM", "2026-08-17", "premarket", "2026-08-17T08:03:55Z", "sweep-1",
                              28.14, 0.0, 12264, 1249605, 0.0098, 345108.96,
                              gates_fired=[{"name": "cambio_de_comportamiento", "reason": "x", "value": 2.09}])
        reg.record_observation("ZIM", "2026-08-17", "2026-08-17T08:30:00Z", "sweep-2",
                                28.30, 1.5, 20000, 0.02, gates_fired_now=[{"name": "x", "reason": "y", "value": 1}])
        reg.record_observation("ZIM", "2026-08-17", "2026-08-17T09:00:00Z", "sweep-3",
                                28.69, 6.99, 400000, 0.32, gates_fired_now=[])
        reg.record_alert_stage("ZIM", "2026-08-17", "2026-08-17T08:03:55Z", "ALERTA_TEMPRANA",
                                relative_volume_hoy=0.0098, volatility_14d_pct=3.365,
                                dias_volumen_elevado=3, aceleracion_volumen=0.586,
                                timing_deteccion_hoy="antes_del_movimiento", racional_available=True)
        reg.record_alert_stage("ZIM", "2026-08-17", "2026-08-17T09:00:00Z", "INICIO",
                                relative_volume_hoy=0.32, volatility_14d_pct=3.365,
                                dias_volumen_elevado=3, aceleracion_volumen=0.586,
                                timing_deteccion_hoy="al_comienzo", racional_available=True)
        reg.record_outcome("ZIM", "2026-08-17", run_up_before_detection_pct=0.0,
                            max_price_after_detection=29.5, max_return_after_detection_pct=4.8,
                            minutes_to_max=120.0, reached_20=False, reached_50=False, reached_100=False,
                            category="en_curso")

        tl = reg.candidate_timeline("zim", "2026-08-17")  # minúsculas -- debe normalizar

        assert tl["ticker"] == "ZIM"
        assert tl["detection"]["price_at_detection"] == 28.14
        assert tl["detection"]["gates_fired"][0]["name"] == "cambio_de_comportamiento"

        assert len(tl["observaciones"]) == 2
        assert tl["observaciones"][0]["price"] == 28.30
        assert tl["observaciones"][1]["price"] == 28.69
        assert tl["observaciones"][1]["gates_fired_now"] == []  # decodificado, no string crudo

        assert [t["stage"] for t in tl["transiciones_alerta"]] == ["ALERTA_TEMPRANA", "INICIO"]
        assert tl["transiciones_alerta"][1]["observed_at"] == "2026-08-17T09:00:00Z"

        assert tl["outcome"]["max_return_after_detection_pct"] == 4.8
        assert tl["racional_available"] is True
    finally:
        _restore()


def test_candidate_timeline_sin_datos_no_inventa_nada(monkeypatch):
    _fresh()
    try:
        monkeypatch.setattr("atlas.data.universe.is_available", lambda symbol: False)
        tl = reg.candidate_timeline("ZZZZ", "2026-08-17")
        assert tl["detection"] is None
        assert tl["observaciones"] == []
        assert tl["transiciones_alerta"] == []
        assert tl["outcome"] is None
        assert tl["racional_available"] is False
    finally:
        _restore()


def test_live_opportunities_expone_deteccion_temprana_sin_alerta(monkeypatch):
    """Prioridad 1 (Fase 6, 2026-08-18): una candidata detectada por Tradier
    que TODAVIA no cruzó ningún umbral de alert_stage debe seguir apareciendo
    -- con stage="DETECCION_TEMPRANA", nunca desaparecer."""
    _fresh()
    try:
        monkeypatch.setattr("atlas.data.universe.is_available", lambda symbol: True)
        reg.record_detection("BRBS", "2026-08-17", "premarket", "2026-08-17T12:55:21Z", "s1",
                              3.69, 0.0, 1000, 500, 1.0, 3690,
                              gates_fired=[{"name": "cambio_de_comportamiento", "reason": "RVOL x2.1", "value": 2.1}])

        ops = reg.live_opportunities("2026-08-17")
        assert len(ops) == 1
        o = ops[0]
        assert o["ticker"] == "BRBS"
        assert o["stage"] == reg.DETECCION_TEMPRANA
        assert o["price_at_detection"] == 3.69
        assert o["gates_fired"][0]["reason"] == "RVOL x2.1"
        assert o["racional_available"] is True
    finally:
        _restore()


def test_live_opportunities_incluye_no_perseguir_nunca_filtra(monkeypatch):
    """Prioridad 1: una candidata en NO_PERSEGUIR sigue apareciendo -- la
    etapa es información, nunca un filtro de qué se muestra."""
    _fresh()
    try:
        monkeypatch.setattr("atlas.data.universe.is_available", lambda symbol: False)
        reg.record_detection("XYZ", "2026-08-17", "regular", "2026-08-17T14:00:00Z", "s1",
                              10.0, 5.0, 1000, 500, 2.0, 10000, gates_fired=[{"name": "cambio_de_precio", "reason": "x", "value": 5.0}])
        reg.record_alert_stage("XYZ", "2026-08-17", "2026-08-17T14:30:00Z", "NO_PERSEGUIR",
                                relative_volume_hoy=1.0, volatility_14d_pct=2.0, dias_volumen_elevado=0,
                                aceleracion_volumen=0.0, timing_deteccion_hoy="demasiado_tarde", racional_available=False)

        ops = reg.live_opportunities("2026-08-17")
        assert len(ops) == 1
        assert ops[0]["stage"] == "NO_PERSEGUIR"
        assert ops[0]["racional_available"] is False  # recalculado en vivo, no cacheado del alert_stage_log
    finally:
        _restore()


def test_live_opportunities_racional_available_se_recalcula_en_vivo(monkeypatch):
    """`racional_available` nunca se lee de la fila vieja de alert_stage_log
    -- se recalcula en cada llamada (mismo criterio que _tag_alert_stage)."""
    _fresh()
    try:
        reg.record_detection("XYZ", "2026-08-17", "regular", "2026-08-17T14:00:00Z", "s1",
                              10.0, 5.0, 1000, 500, 2.0, 10000, gates_fired=[])
        reg.record_alert_stage("XYZ", "2026-08-17", "2026-08-17T14:30:00Z", "ALERTA_TEMPRANA",
                                relative_volume_hoy=1.0, volatility_14d_pct=2.0, dias_volumen_elevado=1,
                                aceleracion_volumen=0.0, timing_deteccion_hoy="antes_del_movimiento",
                                racional_available=False)  # el valor guardado hace rato es False

        monkeypatch.setattr("atlas.data.universe.is_available", lambda symbol: True)  # ahora SÍ está disponible
        ops = reg.live_opportunities("2026-08-17")
        assert ops[0]["racional_available"] is True  # refleja el valor EN VIVO, no el guardado
    finally:
        _restore()


# --- Retroceso desde máximo intradía (2026-08-18, caso real YYAI) ---

def test_max_price_today_sin_observaciones_es_none():
    _fresh()
    try:
        assert reg.max_price_today("YYAI", "2026-08-18") is None
    finally:
        _restore()


def test_max_price_today_lee_el_maximo_real_persistido():
    """Simula el patrón real YYAI: sube a un pico, después baja -- el
    máximo debe seguir siendo el pico, aunque las observaciones más
    recientes tengan un precio menor. Lee de `candidate_observation`, la
    misma tabla que ya se puebla en cada barrido -- nada nuevo que romper."""
    _fresh()
    try:
        reg.record_observation("YYAI", "2026-08-18", "2026-08-18T10:00:00Z", "s1", 1.22, 0.0, 1000, 1.0, [])
        reg.record_observation("YYAI", "2026-08-18", "2026-08-18T12:00:00Z", "s2", 1.57, 28.7, 5000, 10.0, [])
        reg.record_observation("YYAI", "2026-08-18", "2026-08-18T13:00:00Z", "s3", 1.36, 11.5, 4000, 8.0, [])
        assert reg.max_price_today("YYAI", "2026-08-18") == 1.57
    finally:
        _restore()


def test_max_price_today_no_mezcla_dias_ni_tickers_distintos():
    _fresh()
    try:
        reg.record_observation("YYAI", "2026-08-17", "2026-08-17T10:00:00Z", "s1", 5.0, 0.0, 1000, 1.0, [])
        reg.record_observation("YYAI", "2026-08-18", "2026-08-18T10:00:00Z", "s2", 1.22, 0.0, 1000, 1.0, [])
        reg.record_observation("OTRO", "2026-08-18", "2026-08-18T10:00:00Z", "s3", 99.0, 0.0, 1000, 1.0, [])
        assert reg.max_price_today("YYAI", "2026-08-18") == 1.22
    finally:
        _restore()


def test_movers_since_detection_caso_xos_real():
    """Reproduce el patrón real de XOS (2026-08-18): detectada a $2.09,
    observaciones posteriores reales hasta un máximo de $4.60 (+120.1%) --
    debe aparecer en movers_since_detection con el % correcto."""
    _fresh()
    try:
        reg.record_detection("XOS", "2026-08-18", "premarket", "2026-08-18T08:53:38Z", "s1",
                              2.09, 0.0, 1000, 500, 1.74, 2000, gates_fired=[])
        reg.record_observation("XOS", "2026-08-18", "2026-08-18T08:53:38Z", "s1", 2.09, 0.0, 1000, 1.74, [])
        reg.record_observation("XOS", "2026-08-18", "2026-08-18T13:16:52Z", "s2", 4.595, 119.9, 60000000, 20.0, [])
        reg.record_observation("XOS", "2026-08-18", "2026-08-18T17:00:00Z", "s3", 4.60, 120.1, 65000000, 15.0, [])

        movers = reg.movers_since_detection("2026-08-18", min_pct=10.0)
        assert len(movers) == 1
        assert movers[0]["ticker"] == "XOS"
        assert movers[0]["max_price"] == 4.60
        assert movers[0]["max_pct_gain"] == pytest.approx(120.1, abs=0.1)
    finally:
        _restore()


def test_movers_since_detection_filtra_por_piso_minimo():
    _fresh()
    try:
        reg.record_detection("AAPL", "2026-08-18", "regular", "t1", "s1",
                              100.0, 0.0, 1000, 500, 1.0, 1000, gates_fired=[])
        reg.record_observation("AAPL", "2026-08-18", "t1", "s1", 100.0, 0.0, 1000, 1.0, [])
        reg.record_observation("AAPL", "2026-08-18", "t2", "s2", 105.0, 5.0, 1000, 1.0, [])  # +5%, bajo el piso

        assert reg.movers_since_detection("2026-08-18", min_pct=10.0) == []
        assert len(reg.movers_since_detection("2026-08-18", min_pct=3.0)) == 1
    finally:
        _restore()


def test_movers_since_detection_orden_de_mayor_a_menor():
    _fresh()
    try:
        for ticker, det_price, max_price in [("A", 10.0, 15.0), ("B", 10.0, 30.0), ("C", 10.0, 12.0)]:
            reg.record_detection(ticker, "2026-08-18", "regular", "t1", "s1",
                                  det_price, 0.0, 1000, 500, 1.0, 1000, gates_fired=[])
            reg.record_observation(ticker, "2026-08-18", "t1", "s1", det_price, 0.0, 1000, 1.0, [])
            reg.record_observation(ticker, "2026-08-18", "t2", "s2", max_price, 0.0, 1000, 1.0, [])

        movers = reg.movers_since_detection("2026-08-18", min_pct=10.0)
        assert [m["ticker"] for m in movers] == ["B", "A", "C"]  # +200%, +50%, +20%
    finally:
        _restore()


def test_record_alert_stage_persiste_retroceso_desde_maximo():
    _fresh()
    try:
        reg.record_detection("YYAI", "2026-08-18", "regular", "2026-08-18T10:20:00Z", "s1",
                              1.22, 0.0, 1000, 500, 1.0, 1000, gates_fired=[])
        reg.record_alert_stage("YYAI", "2026-08-18", "2026-08-18T14:00:00Z", "NO_PERSEGUIR",
                                relative_volume_hoy=11.7, direction="ALCISTA",
                                retroceso_desde_maximo_pct=12.739)
        ops = reg.live_opportunities("2026-08-18")
        assert ops[0]["retroceso_desde_maximo_pct"] == 12.739
    finally:
        _restore()


# --- Aprendizaje unificado (2026-08-18, pedido explícito del usuario) ---

def test_classify_learning_quality_confiable_por_dollar_volume():
    confiable, motivos = reg.classify_learning_quality({"dollar_volume_at_detection": 10_040_749,
                                                          "relative_volume_at_detection": 1.74})
    assert confiable is True
    assert motivos == []


def test_classify_learning_quality_no_confiable_dinero_insuficiente():
    # caso real de producción (2026-08-17): RCON, avg_vol=15.2M pero
    # volumen real operado en el momento de detección casi nulo.
    confiable, motivos = reg.classify_learning_quality({"dollar_volume_at_detection": 125,
                                                          "relative_volume_at_detection": None})
    assert confiable is False
    assert "dinero_insuficiente" in motivos


def test_classify_learning_quality_dollar_volume_desconocido():
    confiable, motivos = reg.classify_learning_quality({"dollar_volume_at_detection": None,
                                                          "relative_volume_at_detection": 1.0})
    assert confiable is False
    assert "dinero_operado_desconocido" in motivos


def test_classify_learning_quality_umbral_configurable_por_env(monkeypatch):
    monkeypatch.setattr(reg, "LEARNING_MIN_DOLLAR_VOLUME", 1_000_000.0)
    confiable, motivos = reg.classify_learning_quality({"dollar_volume_at_detection": 500_000,
                                                          "relative_volume_at_detection": 1.0})
    assert confiable is False
    assert "dinero_insuficiente" in motivos


def test_compute_interim_outcome_calcula_desde_observaciones():
    _fresh()
    try:
        reg.record_detection("XOS", "2026-08-18", "premarket", "2026-08-18T04:53:38Z", "s1",
                              2.09, 0.0, 1000, 500, 1.74, 10_040_749, gates_fired=[])
        reg.record_observation("XOS", "2026-08-18", "2026-08-18T05:00:00Z", "s2", 2.5, 19.6, 1000, 1.74, [])
        reg.record_observation("XOS", "2026-08-18", "2026-08-18T06:00:00Z", "s3", 3.0, 43.5, 1000, 1.74, [])

        outcome = reg.compute_interim_outcome("XOS", "2026-08-18")
        assert outcome is not None
        assert outcome["is_final"] == 0
        assert outcome["max_return_after_detection_pct"] == round(100 * (3.0 - 2.09) / 2.09, 2)
        assert outcome["reached_20"] == 1
        assert outcome["reached_50"] == 0
        assert outcome["confiable_para_aprendizaje"] == 1  # dollar_volume real, sobre el piso
        assert outcome["run_up_before_detection_pct"] is None  # no se calcula en curso, exclusivo del EOD
    finally:
        _restore()


def test_compute_interim_outcome_nunca_pisa_resultado_final():
    _fresh()
    try:
        reg.record_detection("XOS", "2026-08-18", "premarket", "2026-08-18T04:53:38Z", "s1",
                              2.09, 0.0, 1000, 500, 1.74, 10_040_749, gates_fired=[])
        reg.record_outcome("XOS", "2026-08-18", run_up_before_detection_pct=0.0,
                            max_price_after_detection=4.95, max_return_after_detection_pct=136.8,
                            minutes_to_max=364.0, reached_20=True, reached_50=True, reached_100=True,
                            category="mejor_oportunidad", confiable_para_aprendizaje=True, is_final=True)
        reg.record_observation("XOS", "2026-08-18", "2026-08-18T06:00:00Z", "s2", 3.0, 43.5, 1000, 1.74, [])

        outcome = reg.compute_interim_outcome("XOS", "2026-08-18")
        assert outcome["is_final"] == 1
        assert outcome["max_return_after_detection_pct"] == 136.8  # el final, nunca recalculado desde 3.0
        assert len(reg.list_outcomes_for_date("2026-08-18")) == 1  # nunca duplicó la fila
    finally:
        _restore()


def test_compute_interim_outcome_sin_datos_devuelve_none():
    _fresh()
    try:
        assert reg.compute_interim_outcome("NOPE", "2026-08-18") is None  # nunca se detectó
        reg.record_detection("SINOBS", "2026-08-18", "regular", "t1", "s1",
                              10.0, 0.0, 1000, 500, 1.0, 1000, gates_fired=[])
        assert reg.compute_interim_outcome("SINOBS", "2026-08-18") is None  # sin observaciones aún
    finally:
        _restore()


def test_explosion_bands_tradier_solo_confiables_y_finales():
    _fresh()
    try:
        casos = [
            ("A", 50.0, True, True),    # confiable, final -- cuenta
            ("B", 150.0, True, True),   # confiable, final -- cuenta en más bandas
            ("C", 200.0, False, True),  # no confiable -- excluida por defecto
            ("D", 300.0, True, False),  # en curso, nunca final -- excluida
        ]
        for ticker, max_pct, confiable, is_final in casos:
            reg.record_detection(ticker, "2026-08-18", "regular", "t1", "s1",
                                  10.0, 0.0, 1000, 500, 1.0, 1_000_000, gates_fired=[])
            reg.record_outcome(ticker, "2026-08-18", run_up_before_detection_pct=0.0,
                                max_price_after_detection=10.0 * (1 + max_pct / 100),
                                max_return_after_detection_pct=max_pct, minutes_to_max=60.0,
                                reached_20=max_pct >= 20, reached_50=max_pct >= 50, reached_100=max_pct >= 100,
                                category="x", confiable_para_aprendizaje=confiable, is_final=is_final)

        bandas = reg.explosion_bands_tradier()
        assert bandas["n_total_evaluado"] == 2  # solo A y B
        assert bandas["por_banda_acumulativa"]["10"]["n"] == 2
        assert bandas["por_banda_acumulativa"]["50"]["n"] == 2
        assert bandas["por_banda_acumulativa"]["100"]["n"] == 1  # solo B (150%)
        assert "B" in bandas["por_banda_acumulativa"]["100"]["tickers"]
        assert bandas["por_banda_acumulativa"]["200"] == {"n": 0, "estado": "No disponible"}
    finally:
        _restore()


def test_explosion_bands_tradier_filtra_por_fecha():
    _fresh()
    try:
        reg.record_detection("A", "2026-08-17", "regular", "t1", "s1",
                              10.0, 0.0, 1000, 500, 1.0, 1_000_000, gates_fired=[])
        reg.record_outcome("A", "2026-08-17", run_up_before_detection_pct=0.0,
                            max_price_after_detection=15.0, max_return_after_detection_pct=50.0,
                            minutes_to_max=60.0, reached_20=True, reached_50=True, reached_100=False,
                            category="x", confiable_para_aprendizaje=True, is_final=True)
        reg.record_detection("B", "2026-08-18", "regular", "t1", "s1",
                              10.0, 0.0, 1000, 500, 1.0, 1_000_000, gates_fired=[])
        reg.record_outcome("B", "2026-08-18", run_up_before_detection_pct=0.0,
                            max_price_after_detection=13.0, max_return_after_detection_pct=30.0,
                            minutes_to_max=60.0, reached_20=True, reached_50=False, reached_100=False,
                            category="x", confiable_para_aprendizaje=True, is_final=True)

        bandas_17 = reg.explosion_bands_tradier("2026-08-17")
        assert bandas_17["n_total_evaluado"] == 1
        assert bandas_17["por_banda_acumulativa"]["50"]["n"] == 1

        bandas_todo = reg.explosion_bands_tradier()
        assert bandas_todo["n_total_evaluado"] == 2
    finally:
        _restore()


def test_candidate_full_history_ticker_sin_deteccion_es_none():
    _fresh()
    try:
        assert reg.candidate_full_history("NOPE", "2026-08-18") is None
    finally:
        _restore()


def test_candidate_full_history_separa_estado_inicial_evolucion_resultado(monkeypatch):
    _fresh()
    try:
        monkeypatch.setattr("atlas.data.universe.is_available", lambda symbol: True)
        reg.record_detection(
            "XOS", "2026-08-18", "premarket", "2026-08-18T04:53:38Z", "s1",
            2.09, 0.0, 1000, 500, 1.74, 10_040_749, gates_fired=[{"name": "volumen_relativo"}],
            price_basis_at_detection="tradier_last", bid_at_detection=2.08, ask_at_detection=2.10,
            spread_pct_at_detection=0.96,
        )
        reg.record_alert_stage("XOS", "2026-08-18", "2026-08-18T10:00:00Z", "NO_PERSEGUIR",
                                relative_volume_hoy=1.74, direction="ALCISTA")
        reg.record_observation("XOS", "2026-08-18", "2026-08-18T05:00:00Z", "s2", 4.95, 136.8, 1000, 1.74, [])
        reg.record_outcome("XOS", "2026-08-18", run_up_before_detection_pct=0.0,
                            max_price_after_detection=4.95, max_return_after_detection_pct=136.8,
                            minutes_to_max=364.0, reached_20=True, reached_50=True, reached_100=True,
                            category="mejor_oportunidad", confiable_para_aprendizaje=True, is_final=True)

        historia = reg.candidate_full_history("XOS", "2026-08-18")
        assert historia["ticker"] == "XOS"
        assert historia["racional_available"] is True

        ei = historia["estado_inicial"]
        assert ei["price_at_detection"] == 2.09
        assert ei["bid_at_detection"] == 2.08
        assert ei["ask_at_detection"] == 2.10
        assert ei["price_basis_at_detection"] == "tradier_last"
        assert ei["relative_volume_at_detection"] == 1.74
        assert ei["dollar_volume_at_detection"] == 10_040_749

        # el estado inicial nunca se pisa por la evolución posterior NO_PERSEGUIR
        assert ei["price_at_detection"] == 2.09
        assert historia["evolucion"]["etapas"][0]["stage"] == "NO_PERSEGUIR"
        assert historia["evolucion"]["max_price_visto_en_vivo"] == 4.95

        rf = historia["resultado_final"]
        assert rf["reached_100"] == 1
        assert rf["max_return_after_detection_pct"] == 136.8
    finally:
        _restore()


def test_caso_xos_obligatorio(monkeypatch):
    """Prueba obligatoria del caso real XOS (2026-08-18, pedido explícito
    del usuario): reconstruye exactamente los datos reales de producción y
    confirma que aparecen correctamente en candidate_full_history() y en
    la banda >=100% del Marcador Histórico Tradier."""
    _fresh()
    try:
        monkeypatch.setattr("atlas.data.universe.is_available", lambda symbol: True)
        reg.record_detection(
            "XOS", "2026-08-18", "premarket", "2026-08-18T04:53:38Z", "s1",
            2.09, 0.0, 1000, 500, 1.74, 10_040_749, gates_fired=[{"name": "volumen_relativo"}],
        )
        reg.record_alert_stage("XOS", "2026-08-18", "2026-08-18T09:00:00Z", "NO_PERSEGUIR",
                                relative_volume_hoy=1.74, direction="ALCISTA")
        reg.record_outcome(
            "XOS", "2026-08-18", run_up_before_detection_pct=0.0, max_price_after_detection=4.95,
            max_return_after_detection_pct=136.8, minutes_to_max=364.0,
            reached_20=True, reached_50=True, reached_100=True, category="mejor_oportunidad",
            confiable_para_aprendizaje=True, is_final=True,
        )

        historia = reg.candidate_full_history("XOS", "2026-08-18")
        assert historia["racional_available"] is True
        assert historia["estado_inicial"]["price_at_detection"] == 2.09
        assert historia["estado_inicial"]["relative_volume_at_detection"] == 1.74
        assert historia["estado_inicial"]["dollar_volume_at_detection"] == 10_040_749
        assert historia["evolucion"]["etapas"][-1]["stage"] == "NO_PERSEGUIR"
        assert historia["resultado_final"]["max_price_after_detection"] == 4.95
        assert historia["resultado_final"]["max_return_after_detection_pct"] == 136.8
        assert historia["resultado_final"]["minutes_to_max"] == 364.0
        assert historia["resultado_final"]["reached_100"] == 1
        assert historia["resultado_final"]["confiable_para_aprendizaje"] == 1

        bandas = reg.explosion_bands_tradier("2026-08-18")
        assert "XOS" in bandas["por_banda_acumulativa"]["100"]["tickers"]
        assert bandas["por_banda_acumulativa"]["100"]["max_absoluto_pct"] == 136.8
    finally:
        _restore()


# --- "Que Atlas aprenda" de lo no detectado (2026-08-19, caso real ETHU/MSTU/BNTX) ---

def test_record_missed_mover_es_write_once_por_ticker_y_dia():
    _fresh()
    try:
        primero = reg.record_missed_mover("BNTX", "2026-08-19", 21.97)
        segundo = reg.record_missed_mover("BNTX", "2026-08-19", 99.0)  # no debe pisar el original
        assert primero is True
        assert segundo is False
        movidas = reg.list_missed_movers("2026-08-19")
        assert len(movidas) == 1
        assert movidas[0]["change_pct_final"] == 21.97
    finally:
        _restore()


def test_list_missed_movers_ordena_por_magnitud_y_filtra_por_fecha():
    _fresh()
    try:
        reg.record_missed_mover("ETHU", "2026-08-19", 38.04)
        reg.record_missed_mover("MSTU", "2026-08-19", -31.86)  # baja fuerte tambien cuenta
        reg.record_missed_mover("BNTX", "2026-08-19", 21.97)
        reg.record_missed_mover("OLD", "2026-08-18", 99.0)

        hoy = reg.list_missed_movers("2026-08-19")
        assert [m["ticker"] for m in hoy] == ["ETHU", "MSTU", "BNTX"]  # 38.04, |-31.86|, 21.97

        todo = reg.list_missed_movers()
        assert len(todo) == 4
    finally:
        _restore()


# --- Predicción de magnitud (2026-08-20, aprobado por el usuario) ---

def test_record_magnitud_prediction_es_write_once_por_ticker_y_dia():
    _fresh()
    try:
        primero = reg.record_magnitud_prediction(
            "MRNA", "2026-08-19", "2026-08-19T10:47:00Z", 28.0,
            estado_final_al_congelar="OPORTUNIDAD_PRIORITARIA", direction="ALCISTA",
            timing_deteccion="al_comienzo", bucket="alto", muestra_n=187,
        )
        segundo = reg.record_magnitud_prediction(
            "MRNA", "2026-08-19", "2026-08-19T14:00:00Z", 99.0,  # no debe pisar la original
        )
        assert primero is True
        assert segundo is False
        pred = reg.get_magnitud_prediction("MRNA", "2026-08-19")
        assert pred["predicted_pct"] == 28.0
        assert pred["muestra_n"] == 187
    finally:
        _restore()


def test_magnitud_predictions_for_date_lista_por_fecha():
    _fresh()
    try:
        reg.record_magnitud_prediction("MRNA", "2026-08-19", "2026-08-19T10:47:00Z", 28.0)
        reg.record_magnitud_prediction("CONL", "2026-08-19", "2026-08-19T09:38:00Z", 19.0)
        reg.record_magnitud_prediction("OLD", "2026-08-18", "2026-08-18T09:00:00Z", 10.0)

        hoy = reg.magnitud_predictions_for_date("2026-08-19")
        assert {p["ticker"] for p in hoy} == {"MRNA", "CONL"}
    finally:
        _restore()


def _record_outcome_simple(ticker, market_date, max_return_after_detection_pct, is_final=True):
    reg.record_outcome(
        ticker, market_date, run_up_before_detection_pct=None,
        max_price_after_detection=None, max_return_after_detection_pct=max_return_after_detection_pct,
        minutes_to_max=None, reached_20=False, reached_50=False, reached_100=False,
        category="mejor_oportunidad", is_final=is_final,
    )


def test_magnitud_precision_report_acierto_y_fallo_reales():
    _fresh()
    try:
        # MRNA: predijo >=28%, llegó a 170.6% -> acierto.
        reg.record_magnitud_prediction("MRNA", "2026-08-19", "2026-08-19T10:47:00Z", 28.0)
        _record_outcome_simple("MRNA", "2026-08-19", 170.6)
        # CONL: predijo >=19%, llegó a 15.1% -> falló.
        reg.record_magnitud_prediction("CONL", "2026-08-19", "2026-08-19T09:38:00Z", 19.0)
        _record_outcome_simple("CONL", "2026-08-19", 15.1)
        # PEND: predicción congelada pero el outcome todavía no cerró (is_final=False) -- no se evalúa.
        reg.record_magnitud_prediction("PEND", "2026-08-19", "2026-08-19T11:00:00Z", 10.0)
        _record_outcome_simple("PEND", "2026-08-19", 50.0, is_final=False)

        reporte = reg.magnitud_precision_report("2026-08-19")
        assert reporte["n_predicciones"] == 3
        assert reporte["n_evaluables"] == 2  # PEND queda afuera -- no cerró
        assert reporte["n_aciertos"] == 1
        assert reporte["precision_pct"] == 50.0

        por_ticker = {c["ticker"]: c for c in reporte["candidatas"]}
        assert por_ticker["MRNA"]["acierto"] is True
        assert por_ticker["CONL"]["acierto"] is False
        assert "PEND" not in por_ticker
    finally:
        _restore()


def test_magnitud_precision_report_sin_predicciones_no_rompe():
    _fresh()
    try:
        reporte = reg.magnitud_precision_report("2026-08-19")
        assert reporte == {
            "market_date": "2026-08-19", "n_predicciones": 0, "n_evaluables": 0,
            "n_aciertos": 0, "precision_pct": None, "candidatas": [],
        }
    finally:
        _restore()
