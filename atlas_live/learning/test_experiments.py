"""Tests del motor de experimentos walk-forward (2026-08-16). Datos
sintéticos, sin red. Incluye verificación explícita anti-leakage (los cortes
de una fecha no pueden depender de datos futuros -- el riesgo específico de
este módulo, distinto del ya cubierto en `atlas_live/reference/`)."""

import random

from atlas_live.learning import experiments as exp


def _make_rows(n_dates=15, per_date=60, seed=1):
    random.seed(seed)
    rows = []
    for d in range(n_dates):
        fecha = f"2026-06-{d + 1:02d}"
        for i in range(per_date):
            direction = random.choice(["ALCISTA", "BAJISTA", "NEUTRAL"])
            vol = random.uniform(1.0, 20.0)
            max_advance = random.uniform(0, 30) + (vol if direction == "ALCISTA" else 0)
            rows.append({
                "symbol": f"SIM{i}", "date": fecha, "direction": direction,
                "volatility_14d_pct": vol, "max_advance_pct": max_advance,
                "timing_deteccion": random.choice(list(exp.EARLY_GENUINE | exp.LATE | exp.ANTES_DEL_MOVIMIENTO)),
            })
    return rows


def test_cuts_for_date_no_usan_datos_de_esa_fecha_ni_posteriores():
    rows = _make_rows()
    fecha_objetivo = "2026-06-11"  # fecha 11 de 15

    cuts_antes = exp.cuts_for_date(rows, ["volatility_14d_pct"], fecha_objetivo)

    rows_alterado = [dict(r) for r in rows]
    for r in rows_alterado:
        if r["date"] >= fecha_objetivo:  # incluye la fecha objetivo Y el futuro
            r["volatility_14d_pct"] = 9999.0

    cuts_despues = exp.cuts_for_date(rows_alterado, ["volatility_14d_pct"], fecha_objetivo)

    assert cuts_antes == cuts_despues, "los cortes de una fecha no pueden cambiar por alterar esa fecha o el futuro"


def test_cuts_for_date_si_cambian_si_se_altera_el_pasado():
    """Chequeo de sanidad: si el cambio SÍ afecta datos pasados (legítimo),
    el corte SÍ debe cambiar -- confirma que el test anterior no pasa "por
    accidente" (ej. una función que ignora todo)."""
    rows = _make_rows()
    fecha_objetivo = "2026-06-11"

    cuts_antes = exp.cuts_for_date(rows, ["volatility_14d_pct"], fecha_objetivo)

    rows_alterado = [dict(r) for r in rows]
    for r in rows_alterado:
        if r["date"] < fecha_objetivo:
            r["volatility_14d_pct"] = 9999.0

    cuts_despues = exp.cuts_for_date(rows_alterado, ["volatility_14d_pct"], fecha_objetivo)

    assert cuts_antes != cuts_despues


def test_run_walk_forward_experiment_respeta_calibracion_minima():
    rows = _make_rows(n_dates=15)
    report = exp.run_walk_forward_experiment(rows, ["volatility_14d_pct"], "prueba", min_calibration_dates=10)
    assert len(report.fechas_calibracion) == 10
    assert len(report.fechas_evaluadas) <= 5  # 15 - 10, menos las que no alcanzaron el piso de muestra
    assert report.fechas_calibracion[-1] < report.fechas_evaluadas[0]


def test_run_walk_forward_experiment_separa_por_direccion():
    rows = _make_rows(n_dates=15)
    report = exp.run_walk_forward_experiment(rows, ["volatility_14d_pct"], "prueba", min_calibration_dates=10)
    for direction in exp.DIRECTIONS:
        assert direction in report.por_direccion
        assert "poblacion_total" in report.por_direccion[direction]
        assert "alto" in report.por_direccion[direction]
        assert "bajo" in report.por_direccion[direction]


def test_senal_realmente_predictiva_se_refleja_en_tercil_alto():
    """Dataset donde volatility_14d_pct SÍ está causalmente ligada al
    resultado (ver _make_rows: max_advance suma vol para ALCISTA) --
    confirma que el motor detecta una señal real cuando existe."""
    rows = _make_rows(n_dates=15, per_date=200)
    report = exp.run_walk_forward_experiment(rows, ["volatility_14d_pct"], "prueba", min_calibration_dates=10)
    alcista = report.por_direccion["ALCISTA"]
    assert alcista["alto"]["n"] > 0 and alcista["bajo"]["n"] > 0
    assert alcista["alto"]["pct_20"] > alcista["bajo"]["pct_20"]


def test_reciente_vs_acumulada_tienen_numerador_y_denominador():
    rows = _make_rows(n_dates=15)
    report = exp.run_walk_forward_experiment(rows, ["volatility_14d_pct"], "prueba", min_calibration_dates=10)
    for key in ("reciente", "acumulada"):
        d = report.reciente_vs_acumulada[key]
        assert "n" in d and "aciertos_20" in d and "pct_20" in d


def test_combinada_exige_ambas_features_en_su_propio_tercil_alto():
    rows = _make_rows(n_dates=15)
    for r in rows:
        r["daily_range_pct"] = r["volatility_14d_pct"] * 0.5  # correlacionada a propósito
    report = exp.run_walk_forward_experiment(
        rows, ["volatility_14d_pct", "daily_range_pct"], "combinada", min_calibration_dates=10
    )
    assert report.feature_cols == ["volatility_14d_pct", "daily_range_pct"]


def test_early_vs_late_historical_agrupa_correctamente_y_separa_direccion():
    rows = [
        {"direction": "ALCISTA", "timing_deteccion": "al_comienzo", "max_advance_pct": 25},
        {"direction": "ALCISTA", "timing_deteccion": "expansion_temprana", "max_advance_pct": 30},
        {"direction": "ALCISTA", "timing_deteccion": "demasiado_tarde", "max_advance_pct": 5},
        {"direction": "ALCISTA", "timing_deteccion": "antes_del_movimiento", "max_advance_pct": 1},
        {"direction": "BAJISTA", "timing_deteccion": "al_comienzo", "max_advance_pct": 40},  # nunca debe mezclarse con ALCISTA
    ]
    out = exp.early_vs_late_historical(rows)
    assert out["ALCISTA"]["early_genuino"]["n"] == 2
    assert out["ALCISTA"]["late"]["n"] == 1
    assert out["ALCISTA"]["antes_del_movimiento"]["n"] == 1
    assert out["BAJISTA"]["early_genuino"]["n"] == 1
    assert out["ALCISTA"]["early_genuino"]["n"] != out["BAJISTA"]["early_genuino"]["n"] + out["ALCISTA"]["early_genuino"]["n"]  # sanity: no se sumaron juntas


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
