"""Motor de cálculo de los componentes individuales del Atlas Score.

Cada función es independiente y reutilizable: recibe únicamente los datos
que necesita (precios, volumen, capitalización) y devuelve un ComponentScore
(0-100) con una explicación legible. Ningún componente decide nada ni
conoce a los demás; solo mide un aspecto objetivo del instrumento.
"""

import math
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from atlas.indicators import atr as calc_atr
from atlas.indicators import dollar_volume as calc_dollar_volume
from atlas.indicators import ema
from atlas.indicators import relative_volume as calc_relative_volume
from atlas.indicators import rsi as calc_rsi
from atlas.indicators import vwap as calc_vwap


@dataclass(frozen=True)
class ComponentScore:
    """Resultado de un componente individual del Atlas Score. score está en [0, 100]."""

    name: str
    score: float
    explanation: str


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _last(series: pd.Series) -> float:
    return float(series.dropna().iloc[-1])


def score_momentum(close: pd.Series, period: int = 14) -> ComponentScore:
    """Momentum vía RSI: el RSI ya vive en [0, 100], se usa directamente como score."""
    rsi_value = _last(calc_rsi(close, period=period))
    score = _clamp(rsi_value)
    return ComponentScore(
        name="momentum",
        score=score,
        explanation=f"RSI({period}) = {rsi_value:.1f}",
    )


def score_relative_volume(volume: Optional[float], average_volume: Optional[float]) -> ComponentScore:
    """Volumen relativo: 1x el promedio = score 50; 2x o más = score 100."""
    if not volume or not average_volume:
        return ComponentScore(
            name="relative_volume",
            score=0.0,
            explanation="Sin datos de volumen para calcular el volumen relativo",
        )

    rvol = calc_relative_volume(volume, average_volume)
    score = _clamp(rvol * 50)
    return ComponentScore(
        name="relative_volume",
        score=score,
        explanation=f"Volumen relativo = {rvol:.2f}x el promedio",
    )


def score_ema_trend(close: pd.Series, fast: int = 9, slow: int = 21) -> ComponentScore:
    """Tendencia EMA: separación porcentual entre EMA rápida y EMA lenta. 0% = score 50."""
    ema_fast = _last(ema(close, period=fast))
    ema_slow = _last(ema(close, period=slow))
    spread_pct = ((ema_fast - ema_slow) / ema_slow) * 100 if ema_slow else 0.0

    score = _clamp(50 + spread_pct * 10)
    return ComponentScore(
        name="ema_trend",
        score=score,
        explanation=f"EMA({fast})={ema_fast:.2f} vs EMA({slow})={ema_slow:.2f}, separación={spread_pct:+.2f}%",
    )


def score_vwap_distance(
    last_price: float,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
) -> ComponentScore:
    """Distancia al VWAP intradía. Precio = VWAP → score 50."""
    vwap_value = _last(calc_vwap(high, low, close, volume))
    distance_pct = ((last_price - vwap_value) / vwap_value) * 100 if vwap_value else 0.0

    score = _clamp(50 + distance_pct * 10)
    return ComponentScore(
        name="vwap_distance",
        score=score,
        explanation=f"Precio {distance_pct:+.2f}% respecto al VWAP intradía ({vwap_value:.2f})",
    )


def score_atr(high: pd.Series, low: pd.Series, close: pd.Series, last_price: float, period: int = 14) -> ComponentScore:
    """Volatilidad vía ATR relativo al precio. 5% del precio o más = score 100."""
    atr_value = _last(calc_atr(high, low, close, period=period))
    atr_pct = (atr_value / last_price) * 100 if last_price else 0.0

    score = _clamp(atr_pct * 20)
    return ComponentScore(
        name="atr",
        score=score,
        explanation=f"ATR({period}) = {atr_value:.2f} ({atr_pct:.2f}% del precio)",
    )


def score_liquidity(price: float, volume: Optional[float]) -> ComponentScore:
    """Liquidez vía volumen en dólares, en escala logarítmica: $100K/día=0, $1000M/día=100."""
    if not volume:
        return ComponentScore(
            name="liquidity",
            score=0.0,
            explanation="Sin datos de volumen para calcular liquidez",
        )

    dollar_vol = calc_dollar_volume(price, volume)
    log_volume = math.log10(dollar_vol) if dollar_vol > 0 else 0.0
    score = _clamp((log_volume - 5) / (9 - 5) * 100)
    return ComponentScore(
        name="liquidity",
        score=score,
        explanation=f"Volumen en dólares ~ ${dollar_vol:,.0f}",
    )


def score_market_cap(market_cap: Optional[float]) -> ComponentScore:
    """Capitalización de mercado, en escala logarítmica: $50M=0, $500,000M=100."""
    if not market_cap:
        return ComponentScore(
            name="market_cap",
            score=50.0,
            explanation="Sin datos de capitalización disponibles (por ejemplo, en ETFs)",
        )

    log_cap = math.log10(market_cap)
    score = _clamp((log_cap - 7.7) / (11.7 - 7.7) * 100)
    return ComponentScore(
        name="market_cap",
        score=score,
        explanation=f"Capitalización de mercado ~ ${market_cap:,.0f}",
    )
