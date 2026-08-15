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
