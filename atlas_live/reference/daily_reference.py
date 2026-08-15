"""Cálculo de features/resultado diarios sobre un DataFrame de barras
diarias ya obtenido (`TradierProvider.get_history(period="3mo")`).

Funciones puras, sin red. Anti-leakage por construcción: `compute_features`
para el día en `idx` SOLO lee `df.iloc[:idx+1]` (nunca una fila posterior);
`compute_outcome` para el día en `idx` SOLO lee `df.iloc[idx+1:]` (nunca la
fila del día mismo ni anteriores).
"""

from dataclasses import dataclass
from typing import Optional

import pandas as pd

MIN_BASELINE_DAYS = 20   # días previos necesarios para relative_volume/volatilidad
MIN_FORWARD_DAYS = 10    # días posteriores reales necesarios para calcular el resultado
NEUTRAL_BAND_PCT = 1.0   # |change_pct| por debajo de esto se considera NEUTRAL, no una dirección real


def classify_direction(change_pct: Optional[float]) -> str:
    """Dirección como dimensión INDEPENDIENTE de la intensidad -- pedido
    explícito: un -40% es explosivo en magnitud pero NUNCA es una
    "oportunidad explosiva alcista". Nunca se mezcla con calidad/lifecycle."""
    if change_pct is None:
        return "INDEFINIDA"
    if change_pct > NEUTRAL_BAND_PCT:
        return "ALCISTA"
    if change_pct < -NEUTRAL_BAND_PCT:
        return "BAJISTA"
    return "NEUTRAL"


@dataclass
class DailyFeatures:
    date: str
    price: float
    change_pct: float
    gap_pct: float
    volume: int
    average_volume_20d: float
    relative_volume: Optional[float]
    daily_range_pct: float
    volatility_14d_pct: float
    drop_from_peak_10d_pct: Optional[float]   # cuánto bajó desde el máximo de los 10 días PREVIOS (incluye hoy)
    rebound_from_trough_pct: Optional[float]  # si venía de un mínimo reciente, cuánto rebotó hoy
    peak_gain_10d_pct: Optional[float]        # cuánto SUBIÓ hasta ese pico desde el inicio de la ventana de 10d
    direction: str = "INDEFINIDA"   # ALCISTA/BAJISTA/NEUTRAL -- dimensión independiente de la intensidad


@dataclass
class DailyOutcome:
    date: str
    max_advance_pct: float
    max_drawdown_pct: float
    days_to_max: int
    continuo: bool   # el cierre 10 días después sigue por encima del cierre del día D
    n_dias_posteriores_usados: int
    outcome_direction: str = "INDEFINIDA"  # dirección del movimiento POSTERIOR (no la del día de detección)


def compute_features(df: pd.DataFrame, idx: int) -> Optional[DailyFeatures]:
    """Features del día `df.index[idx]`, usando SOLO `df.iloc[:idx+1]`."""
    if idx < MIN_BASELINE_DAYS:
        return None
    row = df.iloc[idx]
    prev_close = df.iloc[idx - 1]["Close"]
    if not prev_close:
        return None

    change_pct = round(100 * (row["Close"] - prev_close) / prev_close, 3)
    gap_pct = round(100 * (row["Open"] - prev_close) / prev_close, 3)
    daily_range_pct = round(100 * (row["High"] - row["Low"]) / row["Close"], 3) if row["Close"] else 0.0

    baseline = df.iloc[idx - MIN_BASELINE_DAYS:idx]  # 20 días ANTES de hoy, hoy excluido
    avg_volume = baseline["Volume"].mean()
    relative_volume = round(row["Volume"] / avg_volume, 3) if avg_volume else None

    vol_window = df.iloc[max(0, idx - 14):idx]  # rango típico de los 14 días previos, hoy excluido
    if len(vol_window) > 0 and (vol_window["Close"] > 0).all():
        ranges = 100 * (vol_window["High"] - vol_window["Low"]) / vol_window["Close"]
        volatility_14d_pct = round(ranges.mean(), 3)
    else:
        volatility_14d_pct = 0.0

    lookback = df.iloc[max(0, idx - 9):idx + 1]  # 10 días incluyendo hoy, nunca después
    peak = lookback["Close"].max()
    drop_from_peak = round(100 * (row["Close"] - peak) / peak, 3) if peak else None
    trough = lookback["Close"].min()
    rebound = round(100 * (row["Close"] - trough) / trough, 3) if trough else None
    # Cuánto SUBIÓ hasta ese pico desde el primer cierre de la ventana --
    # distingue un pico que vino de una subida real (para poder hablar de
    # "agotamiento" de esa subida) de un simple drift lateral sin rally
    # previo (ver nota en phase_classifier.py sobre la revisión de esta regla).
    window_start_close = lookback["Close"].iloc[0]
    peak_gain = round(100 * (peak - window_start_close) / window_start_close, 3) if window_start_close else None

    return DailyFeatures(
        date=str(df.index[idx].date()), price=round(float(row["Close"]), 4), change_pct=change_pct,
        gap_pct=gap_pct, volume=int(row["Volume"]), average_volume_20d=round(float(avg_volume), 1) if avg_volume else 0.0,
        relative_volume=relative_volume, daily_range_pct=daily_range_pct, volatility_14d_pct=volatility_14d_pct,
        drop_from_peak_10d_pct=drop_from_peak, rebound_from_trough_pct=rebound, peak_gain_10d_pct=peak_gain,
        direction=classify_direction(change_pct),
    )


def rolling_percentile_abs_change(df: pd.DataFrame, idx: int, percentile: float = 0.9, window: int = 60) -> Optional[float]:
    """Percentil de |change_pct| usando SOLO `df.iloc[:idx]` (el día `idx`
    mismo queda afuera) -- para clasificar el timing de detección de un día
    histórico sin filtrar información de días futuros hacia atrás en el
    tiempo. Ventana de hasta 60 días previos (no todo el historial
    disponible del símbolo, para que el percentil represente comportamiento
    "reciente", no todo el pasado remoto)."""
    start = max(MIN_BASELINE_DAYS, idx - window)
    if idx <= start:
        return None
    changes = []
    for j in range(start, idx):
        prev_close = df.iloc[j - 1]["Close"]
        if prev_close:
            changes.append(abs(100 * (df.iloc[j]["Close"] - prev_close) / prev_close))
    if not changes:
        return None
    changes.sort()
    pos = min(len(changes) - 1, max(0, int(len(changes) * percentile)))
    return round(changes[pos], 3)


def compute_outcome(df: pd.DataFrame, idx: int, min_forward_days: int = MIN_FORWARD_DAYS) -> Optional[DailyOutcome]:
    """Resultado del día `df.index[idx]`, usando SOLO `df.iloc[idx+1:]`.
    Devuelve None si no hay al menos `min_forward_days` posteriores REALES
    -- nunca trunca ni rellena con menos días de los pedidos."""
    forward = df.iloc[idx + 1:]
    if len(forward) < min_forward_days:
        return None

    base_close = df.iloc[idx]["Close"]
    if not base_close:
        return None

    window = forward.iloc[:max(min_forward_days, len(forward))]
    max_close = window["High"].max()
    min_close = window["Low"].min()
    max_advance = round(100 * (max_close - base_close) / base_close, 3)
    max_drawdown = round(100 * (min_close - base_close) / base_close, 3)
    days_to_max = int(window["High"].values.argmax()) + 1  # +1 porque idx+1 es el primer día posterior

    tenth_day_close = window.iloc[min(min_forward_days, len(window)) - 1]["Close"]
    continuo = bool(tenth_day_close > base_close)

    # Dirección del movimiento POSTERIOR (no la del día de detección): cuál
    # de los dos extremos domina -- el avance máximo o el retroceso máximo.
    if max_advance >= NEUTRAL_BAND_PCT and max_advance >= abs(max_drawdown):
        outcome_direction = "ALCISTA"
    elif abs(max_drawdown) >= NEUTRAL_BAND_PCT and abs(max_drawdown) > max_advance:
        outcome_direction = "BAJISTA"
    else:
        outcome_direction = "NEUTRAL"

    return DailyOutcome(
        date=str(df.index[idx].date()), max_advance_pct=max_advance, max_drawdown_pct=max_drawdown,
        days_to_max=days_to_max, continuo=continuo, n_dias_posteriores_usados=len(window),
        outcome_direction=outcome_direction,
    )
