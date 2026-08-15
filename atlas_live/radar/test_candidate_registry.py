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
