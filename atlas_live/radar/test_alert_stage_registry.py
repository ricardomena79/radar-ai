"""Tests de la capa de registro de alert_stage_log en candidate_registry.py
(2026-08-17, Fase 4). DB temporal, sin red."""

import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.radar import candidate_registry as reg

_ORIG = reg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_alertstage_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None


def _restore():
    reg.DB_PATH = _ORIG


def test_latest_alert_stage_none_si_nunca_se_registro():
    _fresh()
    try:
        assert reg.latest_alert_stage("AAPL", "2026-08-17") is None
    finally:
        _restore()


def test_record_alert_stage_primera_vez_inserta():
    _fresh()
    try:
        inserted = reg.record_alert_stage("AAPL", "2026-08-17", "10:00:00Z", "PREPARACION")
        assert inserted is True
        assert reg.latest_alert_stage("AAPL", "2026-08-17") == "PREPARACION"
    finally:
        _restore()


def test_record_alert_stage_mismo_stage_no_duplica():
    _fresh()
    try:
        reg.record_alert_stage("AAPL", "2026-08-17", "10:00:00Z", "PREPARACION")
        inserted = reg.record_alert_stage("AAPL", "2026-08-17", "10:01:00Z", "PREPARACION")
        assert inserted is False
        history = reg.alert_stage_history_for_date("2026-08-17")
        assert len(history) == 1  # nunca duplica, solo transiciones reales
    finally:
        _restore()


def test_record_alert_stage_cambio_de_ventana_si_inserta():
    _fresh()
    try:
        reg.record_alert_stage("AAPL", "2026-08-17", "10:00:00Z", "PREPARACION")
        inserted = reg.record_alert_stage("AAPL", "2026-08-17", "10:05:00Z", "ALERTA_TEMPRANA")
        assert inserted is True
        assert reg.latest_alert_stage("AAPL", "2026-08-17") == "ALERTA_TEMPRANA"
        history = reg.alert_stage_history_for_date("2026-08-17")
        assert [h["stage"] for h in history] == ["PREPARACION", "ALERTA_TEMPRANA"]
    finally:
        _restore()


def test_record_alert_stage_guarda_todos_los_campos_de_evidencia():
    _fresh()
    try:
        reg.record_alert_stage(
            "AAPL", "2026-08-17", "10:00:00Z", "ALERTA_FUERTE",
            relative_volume_hoy=5.2, volatility_14d_pct=12.5, dias_volumen_elevado=3,
            aceleracion_volumen=1.8, timing_deteccion_hoy="antes_del_movimiento", racional_available=True,
        )
        history = reg.alert_stage_history_for_date("2026-08-17")
        row = history[0]
        assert row["relative_volume_hoy"] == 5.2
        assert row["volatility_14d_pct"] == 12.5
        assert row["dias_volumen_elevado"] == 3
        assert row["aceleracion_volumen"] == 1.8
        assert row["timing_deteccion_hoy"] == "antes_del_movimiento"
        assert row["racional_available"] == 1
    finally:
        _restore()


def test_current_alert_stages_for_date_una_fila_por_ticker_la_mas_reciente():
    _fresh()
    try:
        reg.record_alert_stage("AAPL", "2026-08-17", "10:00:00Z", "PREPARACION")
        reg.record_alert_stage("AAPL", "2026-08-17", "10:05:00Z", "ALERTA_TEMPRANA")
        reg.record_alert_stage("TSLA", "2026-08-17", "10:02:00Z", "ALERTA_FUERTE")

        current = reg.current_alert_stages_for_date("2026-08-17")
        by_ticker = {c["ticker"]: c["stage"] for c in current}
        assert by_ticker == {"AAPL": "ALERTA_TEMPRANA", "TSLA": "ALERTA_FUERTE"}
    finally:
        _restore()


def test_alert_stage_history_scoped_a_la_fecha():
    _fresh()
    try:
        reg.record_alert_stage("AAPL", "2026-08-17", "10:00:00Z", "PREPARACION")
        reg.record_alert_stage("AAPL", "2026-08-18", "10:00:00Z", "PREPARACION")
        assert len(reg.alert_stage_history_for_date("2026-08-17")) == 1
        assert len(reg.alert_stage_history_for_date("2026-08-18")) == 1
    finally:
        _restore()


def test_effectiveness_report_avanza_a_inicio_y_tiempo_real():
    _fresh()
    try:
        reg.record_alert_stage("AAPL", "2026-08-17", "2026-08-17T10:00:00+00:00", "ALERTA_FUERTE",
                                racional_available=True)
        reg.record_alert_stage("AAPL", "2026-08-17", "2026-08-17T10:06:00+00:00", "INICIO",
                                racional_available=True)
        report = reg.alert_stage_effectiveness_report("2026-08-17")

        fuerte = report["general"]["ALERTA_FUERTE"]
        assert fuerte["n_candidatas"] == 1
        assert fuerte["n_avanza_a_inicio_o_confirmacion"] == 1
        assert fuerte["tiempo_promedio_hasta_inicio_min"] == 6.0
    finally:
        _restore()


def test_effectiveness_report_cuenta_outcome_y_falso_positivo():
    _fresh()
    try:
        reg.record_alert_stage("AAPL", "2026-08-17", "2026-08-17T10:00:00+00:00", "ALERTA_FUERTE",
                                racional_available=False)
        reg.record_outcome("AAPL", "2026-08-17", None, None, 5.0, None, False, False, False, "sin_movimiento")

        reg.record_alert_stage("TSLA", "2026-08-17", "2026-08-17T10:00:00+00:00", "ALERTA_FUERTE",
                                racional_available=True)
        reg.record_outcome("TSLA", "2026-08-17", None, None, 35.0, None, True, False, False, "moderado")

        report = reg.alert_stage_effectiveness_report("2026-08-17")
        fuerte = report["general"]["ALERTA_FUERTE"]
        assert fuerte["n_candidatas"] == 2
        assert fuerte["n_con_outcome_cerrado"] == 2
        assert fuerte["n_reached_20"] == 1       # TSLA
        assert fuerte["n_falso_positivo"] == 1   # AAPL nunca llego a +20%

        # split racional: AAPL(false) tuvo el falso positivo, TSLA(true) el acierto
        assert report["racional_available_false"]["ALERTA_FUERTE"]["n_falso_positivo"] == 1
        assert report["racional_available_true"]["ALERTA_FUERTE"]["n_reached_20"] == 1
    finally:
        _restore()


def test_effectiveness_report_cuenta_cada_candidata_una_sola_vez_por_ventana():
    _fresh()
    try:
        reg.record_alert_stage("AAPL", "2026-08-17", "2026-08-17T10:00:00+00:00", "PREPARACION")
        reg.record_alert_stage("AAPL", "2026-08-17", "2026-08-17T10:02:00+00:00", "ALERTA_TEMPRANA")
        reg.record_alert_stage("AAPL", "2026-08-17", "2026-08-17T10:04:00+00:00", "ALERTA_FUERTE")
        report = reg.alert_stage_effectiveness_report("2026-08-17")
        assert report["general"]["PREPARACION"]["n_candidatas"] == 1
        assert report["general"]["ALERTA_TEMPRANA"]["n_candidatas"] == 1
        assert report["general"]["ALERTA_FUERTE"]["n_candidatas"] == 1
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
