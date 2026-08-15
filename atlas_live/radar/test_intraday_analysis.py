"""Tests del análisis de 1 minuto (2026-08-14). DataFrames sintéticos, sin red."""

import pandas as pd

from atlas_live.radar import intraday_analysis as ia


def _df(closes, volumes=None, vwaps=None, start="2026-08-14T09:30:00Z"):
    idx = pd.date_range(start=start, periods=len(closes), freq="1min", tz="UTC")
    volumes = volumes or [1000] * len(closes)
    vwaps = vwaps if vwaps is not None else closes
    return pd.DataFrame({
        "Open": closes, "High": closes, "Low": closes, "Close": closes,
        "Volume": volumes, "VWAP": vwaps,
    }, index=idx)


def test_price_vs_vwap():
    df = _df([10, 10, 11], vwaps=[10, 10, 10])
    r = ia.price_vs_vwap(df)
    assert r == 10.0  # 11 vs vwap 10 -> +10%


def test_velocity_sube():
    df = _df([10, 10.5, 11, 11.5, 12, 12.5])
    v = ia.velocity_pct_per_min(df, lookback_minutes=5)
    assert v is not None and v > 0


def test_velocity_vacio():
    df = pd.DataFrame()
    assert ia.velocity_pct_per_min(df) is None


def test_acceleration_detecta_aceleracion_real():
    # primeros minutos planos, últimos minutos subiendo fuerte
    closes = [10, 10, 10, 10, 10, 10, 11, 12, 13, 14, 15]
    df = _df(closes)
    a = ia.acceleration(df, lookback_minutes=5)
    assert a is not None and a > 0


def test_lifecycle_impulso_inicial():
    closes = [10, 10.1, 10.3, 10.6, 11.0, 11.6, 12.4]
    df = _df(closes)
    fase = ia.lifecycle_phase(df)
    assert fase in ("impulso_inicial", "impulso_sostenido")


def test_lifecycle_recuperacion():
    closes = [10, 12, 14, 12, 10.5, 11, 12, 13]  # sube, cae, se recupera
    df = _df(closes)
    fase = ia.lifecycle_phase(df)
    assert fase == "recuperacion"


def test_lifecycle_indeterminado_con_pocos_datos():
    df = _df([10, 10.1])
    assert ia.lifecycle_phase(df) == "indeterminado"


def test_rvol_intradia_sin_historial_da_none():
    df = _df([10, 10.5, 11])
    assert ia.rvol_intraday(df, {}) is None


def test_rvol_intradia_con_historial():
    df = _df([10, 10.5, 11], volumes=[500, 600, 3000])
    hist = {df.index[-1].strftime("%H:%M"): [1000, 1000, 1000]}
    r = ia.rvol_intraday(df, hist)
    assert r == 3.0  # 3000 / promedio(1000) = 3.0


def test_analyze_completo_sin_datos():
    r = ia.analyze(pd.DataFrame())
    assert r.n_velas == 0
    assert r.lifecycle_phase == "indeterminado"


def test_analyze_completo_con_datos():
    closes = [10, 10.2, 10.5, 10.9, 11.4, 12.0]
    df = _df(closes)
    r = ia.analyze(df)
    assert r.n_velas == 6
    assert r.vwap_actual == 12.0
    assert r.velocity_pct_per_min is not None
    assert r.notes is not None  # sin historial multi-día -> nota explícita


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
