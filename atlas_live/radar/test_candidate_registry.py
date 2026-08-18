"""Tests del registro de candidatas del radar (2026-08-14). DB temporal, sin red."""

import tempfile
import uuid as _uuid
from pathlib import Path

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


def test_outcome_idempotente_y_separado_de_deteccion():
    _fresh()
    try:
        reg.record_detection("NVDA", "2026-08-14", "premarket", "2026-08-14T08:00:00Z", "sweep-1",
                              100.0, 5.0, 200_000, 150_000, 1.3, 20_000_000, gates_fired=[])
        ok1 = reg.record_outcome("NVDA", "2026-08-14", run_up_before_detection_pct=5.0,
                                  max_price_after_detection=130.0, max_return_after_detection_pct=30.0,
                                  minutes_to_max=45.0, reached_20=True, reached_50=False, reached_100=False,
                                  category="mejor_oportunidad")
        ok2 = reg.record_outcome("NVDA", "2026-08-14", run_up_before_detection_pct=999,
                                  max_price_after_detection=1.0, max_return_after_detection_pct=1.0,
                                  minutes_to_max=1.0, reached_20=False, reached_50=False, reached_100=False,
                                  category="otra_cosa")
        assert ok1 is True
        assert ok2 is False  # idempotente, no se pisa
        outcomes = reg.list_outcomes_for_date("2026-08-14")
        assert len(outcomes) == 1
        assert outcomes[0]["max_return_after_detection_pct"] == 30.0
        assert reg.has_outcome("NVDA", "2026-08-14")
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
                            category="buena_oportunidad", direccion_correcta=True)
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
                                category="x")

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
