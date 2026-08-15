"""Análisis de 1 minuto para candidatas -- Etapa B (2026-08-14).

Funciones puras sobre el DataFrame que ya devuelve
`TradierProvider.get_intraday_timesales()` (columnas Open/High/Low/Close/
Volume/VWAP, índice = timestamp UTC). Sin red acá adentro -- el llamador
(`radar_worker`/quien dispare el análisis de candidatas) ya obtuvo el
DataFrame antes de llamar a estas funciones.

Complementa el pipeline existente (AtlasScore/MomentumScore/DecisionEngine/
explosive_engine) -- no lo reemplaza. `lifecycle_phase` es deliberadamente
DESCRIPTIVO ("qué está pasando ahora mismo"), no una predicción ni un score
inventado -- exactamente lo pedido: primero construir el historial real
(`candidate_outcome`), no inventar un algoritmo que prometa un % de subida.
"""

import statistics
from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd


@dataclass
class IntradayMetrics:
    n_velas: int
    vwap_actual: Optional[float]
    price_vs_vwap_pct: Optional[float]
    velocity_pct_per_min: Optional[float]
    acceleration: Optional[float]
    rvol_intradia: Optional[float]
    lifecycle_phase: str
    notes: Optional[str] = None


def price_vs_vwap(df: pd.DataFrame) -> Optional[float]:
    """% de distancia del último precio (Close) respecto al VWAP más reciente."""
    if df.empty or "VWAP" not in df.columns:
        return None
    last = df.iloc[-1]
    if last["VWAP"] in (None,) or pd.isna(last["VWAP"]) or not last["VWAP"]:
        return None
    return round(100 * (last["Close"] - last["VWAP"]) / last["VWAP"], 3)


def velocity_pct_per_min(df: pd.DataFrame, lookback_minutes: int = 5) -> Optional[float]:
    """Velocidad: cambio % de precio en los últimos `lookback_minutes`,
    normalizado por minuto. Requiere al menos 2 velas en la ventana."""
    if df.empty or len(df) < 2:
        return None
    window = df.tail(lookback_minutes + 1)
    if len(window) < 2:
        return None
    start_price = window.iloc[0]["Close"]
    end_price = window.iloc[-1]["Close"]
    minutes = len(window) - 1
    if not start_price or minutes <= 0:
        return None
    pct_change = 100 * (end_price - start_price) / start_price
    return round(pct_change / minutes, 4)


def acceleration(df: pd.DataFrame, lookback_minutes: int = 5) -> Optional[float]:
    """Aceleración: diferencia entre la velocidad de la ventana reciente y
    la ventana anterior de igual tamaño -- segunda derivada simple."""
    if df.empty or len(df) < (2 * lookback_minutes + 1):
        return None
    recent = df.tail(lookback_minutes + 1)
    prior = df.iloc[-(2 * lookback_minutes + 1):-lookback_minutes]
    if len(recent) < 2 or len(prior) < 2:
        return None
    v_recent = 100 * (recent.iloc[-1]["Close"] - recent.iloc[0]["Close"]) / recent.iloc[0]["Close"] / lookback_minutes
    v_prior = 100 * (prior.iloc[-1]["Close"] - prior.iloc[0]["Close"]) / prior.iloc[0]["Close"] / lookback_minutes
    return round(v_recent - v_prior, 4)


def rvol_intraday(df_today: pd.DataFrame, historical_minute_volumes: Dict[str, List[int]]) -> Optional[float]:
    """RVOL intradía: volumen del minuto MÁS RECIENTE de hoy contra el
    promedio histórico de ESE mismo minuto en días previos.

    `historical_minute_volumes`: {"HH:MM": [volumen_dia1, volumen_dia2, ...]}
    -- lo arma el llamador a partir de `TradierProvider.get_intraday_timesales`
    con start/end de varios días (mismo patrón validado en la sesión de
    investigación de Tradier). Si no hay suficiente historial (pedido
    explícito: "cuando los datos históricos lo permitan"), devuelve None en
    vez de inventar un número."""
    if df_today.empty:
        return None
    last_ts = df_today.index[-1]
    minute_key = last_ts.strftime("%H:%M")
    hist = historical_minute_volumes.get(minute_key)
    if not hist:
        return None
    avg_hist = statistics.mean(hist)
    if not avg_hist:
        return None
    vol_now = df_today.iloc[-1]["Volume"]
    return round(vol_now / avg_hist, 3)


def lifecycle_phase(df: pd.DataFrame, lookback_minutes: int = 10) -> str:
    """Descripción de la fase actual del movimiento -- heurística simple y
    transparente (no un score, no una predicción):

      - "impulso_inicial": subiendo y acelerando
      - "impulso_sostenido": subiendo, velocidad estable
      - "desaceleracion": subiendo pero perdiendo velocidad
      - "retroceso": bajando desde un máximo reciente
      - "recuperacion": bajó y ahora vuelve a subir
      - "lateral": sin movimiento direccional claro
      - "indeterminado": datos insuficientes
    """
    if df.empty or len(df) < 3:
        return "indeterminado"

    v = velocity_pct_per_min(df, lookback_minutes=min(lookback_minutes, len(df) - 1))
    a = acceleration(df, lookback_minutes=max(1, min(lookback_minutes, (len(df) - 1) // 2)))

    window = df.tail(lookback_minutes + 1)
    closes = window["Close"].tolist()
    if len(closes) < 3:
        return "indeterminado"

    peak = max(closes)
    peak_idx = closes.index(peak)
    trough_after_peak = min(closes[peak_idx:]) if peak_idx < len(closes) - 1 else None
    current = closes[-1]

    if trough_after_peak is not None and current > trough_after_peak and trough_after_peak < peak:
        drop_pct = 100 * (peak - trough_after_peak) / peak if peak else 0
        rebound_pct = 100 * (current - trough_after_peak) / trough_after_peak if trough_after_peak else 0
        if drop_pct > 1 and rebound_pct > 1 and current < peak:
            return "recuperacion"

    if v is None:
        return "indeterminado"
    if v > 0.05:
        if a is not None and a > 0:
            return "impulso_inicial"
        return "impulso_sostenido"
    if v < -0.05:
        return "retroceso"
    if a is not None and a < -0.05:
        return "desaceleracion"
    return "lateral"


def analyze(
    df: pd.DataFrame,
    historical_minute_volumes: Optional[Dict[str, List[int]]] = None,
) -> IntradayMetrics:
    """Corre todo el análisis de Etapa B sobre un DataFrame ya obtenido de
    Tradier timesales."""
    if df is None or df.empty:
        return IntradayMetrics(0, None, None, None, None, None, "indeterminado", notes="sin velas disponibles")

    rvol = rvol_intraday(df, historical_minute_volumes) if historical_minute_volumes else None
    return IntradayMetrics(
        n_velas=len(df),
        vwap_actual=round(float(df.iloc[-1]["VWAP"]), 4) if "VWAP" in df.columns and pd.notna(df.iloc[-1]["VWAP"]) else None,
        price_vs_vwap_pct=price_vs_vwap(df),
        velocity_pct_per_min=velocity_pct_per_min(df),
        acceleration=acceleration(df),
        rvol_intradia=rvol,
        lifecycle_phase=lifecycle_phase(df),
        notes=None if historical_minute_volumes else "sin historial multi-día -- RVOL intradía no calculado",
    )
