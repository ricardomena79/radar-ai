"""Tests de anti-leakage y cálculo de features/resultado diarios (2026-08-15).
Series sintéticas, sin red."""

import pandas as pd

from atlas_live.reference import daily_reference as dr


def _synthetic_df(n=60, start="2026-05-01"):
    """Serie sintética: sube suavemente, con un pico y una caída a mitad de
    camino -- suficiente para ejercitar pico/valle/rebote y volumen."""
    idx = pd.date_range(start=start, periods=n, freq="B")
    closes, vols = [], []
    price = 10.0
    for i in range(n):
        if i == 30:
            price *= 1.5  # salto
        elif 30 < i <= 35:
            price *= 0.95  # retroceso
        else:
            price *= 1.002
        closes.append(price)
        vols.append(100_000 if i != 30 else 500_000)
    df = pd.DataFrame({
        "Open": closes, "High": [c * 1.02 for c in closes], "Low": [c * 0.98 for c in closes],
        "Close": closes, "Volume": vols,
    }, index=idx)
    return df


def test_features_none_antes_del_piso_de_baseline():
    df = _synthetic_df()
    assert dr.compute_features(df, 5) is None  # menos de MIN_BASELINE_DAYS=20 previos


def test_features_no_usan_datos_futuros():
    """Verificación explícita anti-leakage: cambiar una vela FUTURA (después
    de idx) no debe alterar el resultado de compute_features(idx)."""
    df = _synthetic_df()
    idx = 25
    feats_antes = dr.compute_features(df, idx)

    df_alterado = df.copy()
    df_alterado.iloc[idx + 1:, df_alterado.columns.get_loc("Close")] *= 100  # vuela el futuro
    feats_despues = dr.compute_features(df_alterado, idx)

    assert feats_antes.change_pct == feats_despues.change_pct
    assert feats_antes.relative_volume == feats_despues.relative_volume
    assert feats_antes.volatility_14d_pct == feats_despues.volatility_14d_pct


def test_relative_volume_excluye_el_dia_actual_del_propio_promedio():
    df = _synthetic_df()
    idx = 30  # día del salto de volumen (500k vs 100k típico)
    feats = dr.compute_features(df, idx)
    # el promedio de 20 días previos NO debe incluir el propio salto de hoy
    assert feats.average_volume_20d < 200_000
    assert feats.relative_volume > 2.0  # hoy es notoriamente más alto que su propio promedio previo


def test_outcome_none_sin_suficientes_dias_posteriores_reales():
    df = _synthetic_df(n=25)
    # día 20: solo quedan 4 días posteriores reales, se pide un mínimo de 10
    assert dr.compute_outcome(df, 20, min_forward_days=10) is None


def test_outcome_no_usa_datos_del_dia_o_anteriores():
    """Verificación explícita anti-leakage: cambiar una vela ESTRICTAMENTE
    ANTERIOR a idx (idx mismo es la referencia base del cálculo -- alterarlo
    cambiaría legítimamente el resultado, eso no es leakage) no debe alterar
    el resultado de compute_outcome(idx)."""
    df = _synthetic_df()
    idx = 15
    outcome_antes = dr.compute_outcome(df, idx)

    df_alterado = df.copy()
    df_alterado.iloc[:idx, df_alterado.columns.get_loc("Close")] *= 0.01  # aplasta el pasado ESTRICTO (no idx)
    outcome_despues = dr.compute_outcome(df_alterado, idx)

    assert outcome_antes.max_advance_pct == outcome_despues.max_advance_pct
    assert outcome_antes.max_drawdown_pct == outcome_despues.max_drawdown_pct


def test_direction_es_dimension_independiente_de_la_intensidad():
    """Un -40% es BAJISTA de intensidad alta, nunca "oportunidad explosiva
    alcista" -- dirección y magnitud no se mezclan."""
    assert dr.classify_direction(-40.0) == "BAJISTA"
    assert dr.classify_direction(40.0) == "ALCISTA"
    assert dr.classify_direction(0.1) == "NEUTRAL"
    assert dr.classify_direction(None) == "INDEFINIDA"


def test_features_incluyen_direccion():
    df = _synthetic_df()
    feats = dr.compute_features(df, 25)
    assert feats.direction in ("ALCISTA", "BAJISTA", "NEUTRAL")


def test_peak_gain_10d_refleja_una_subida_real_hacia_el_pico():
    df = _synthetic_df()
    feats = dr.compute_features(df, 30)  # día del salto -- el pico de la ventana es el propio día
    assert feats.peak_gain_10d_pct is not None
    assert feats.peak_gain_10d_pct > 30  # el salto de 1.5x debería reflejarse como subida real


def test_outcome_direction_bajista_cuando_domina_el_retroceso():
    # serie que cae de forma sostenida tras el día de referencia
    idx = pd.date_range(start="2026-05-01", periods=30, freq="B")
    price = 100.0
    closes = []
    for i in range(30):
        if i > 10:
            price *= 0.97
        closes.append(price)
    df = pd.DataFrame({"Open": closes, "High": [c * 1.005 for c in closes], "Low": [c * 0.995 for c in closes],
                        "Close": closes, "Volume": [10000] * 30}, index=idx)
    outcome = dr.compute_outcome(df, 10, min_forward_days=10)
    assert outcome is not None
    assert outcome.outcome_direction == "BAJISTA"


def test_rolling_percentile_no_usa_datos_futuros():
    """Anti-leakage: alterar velas en idx o después no debe cambiar el
    percentil calculado para ese idx (usa SOLO df.iloc[:idx])."""
    df = _synthetic_df()
    idx = 40
    p_antes = dr.rolling_percentile_abs_change(df, idx)

    df_alterado = df.copy()
    df_alterado.iloc[idx:, df_alterado.columns.get_loc("Close")] *= 50
    p_despues = dr.rolling_percentile_abs_change(df_alterado, idx)

    assert p_antes == p_despues


def test_outcome_calcula_avance_y_retroceso_reales():
    df = _synthetic_df()
    idx = 29  # justo antes del salto -- el avance posterior debe ser grande
    outcome = dr.compute_outcome(df, idx, min_forward_days=10)
    assert outcome is not None
    assert outcome.max_advance_pct > 20  # el salto del día 30 debería reflejarse


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
