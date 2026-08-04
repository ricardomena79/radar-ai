"""Motor de cálculo de los componentes individuales del Atlas Score.

Cada función es independiente y reutilizable: recibe únicamente los datos
que necesita (precios, volumen, capitalización) y devuelve un ComponentScore
(0-100) con una explicación legible. Ningún componente decide nada ni
conoce a los demás; solo mide un aspecto objetivo del instrumento.
"""

import math
import os
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from atlas.indicators import atr as calc_atr
from atlas.indicators import dollar_volume as calc_dollar_volume
from atlas.indicators import ema
from atlas.indicators import relative_volume as calc_relative_volume
from atlas.indicators import rsi as calc_rsi
from atlas.indicators import vwap as calc_vwap


def env_float(name: str, default: float) -> float:
    """Lee un umbral configurable desde el entorno (Railway/`.env`), con default fijo si falta o es inválido."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


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


MARKET_CAP_SWEET_SPOT_MIN = env_float("ATLAS_MARKET_CAP_SWEET_SPOT_MIN", 200_000_000)
MARKET_CAP_SWEET_SPOT_MAX = env_float("ATLAS_MARKET_CAP_SWEET_SPOT_MAX", 5_000_000_000)
MARKET_CAP_BASE_SCORE = env_float("ATLAS_MARKET_CAP_BASE_SCORE", 60.0)
MARKET_CAP_SWEET_SPOT_BONUS = env_float("ATLAS_MARKET_CAP_SWEET_SPOT_BONUS", 15.0)


def score_market_cap(market_cap: Optional[float]) -> ComponentScore:
    """Capitalización de mercado como contexto, no como filtro.

    No premia el tamaño en sí (una mega-cap estable rara vez se mueve 5-20%
    en minutos) ni penaliza a las small/microcaps (que sí pueden hacerlo):
    todo instrumento parte de un score neutro-positivo (`MARKET_CAP_BASE_SCORE`)
    y solo recibe un bono moderado (`MARKET_CAP_SWEET_SPOT_BONUS`) si cae en el
    "punto dulce" de liquidez real donde los movimientos explosivos son más
    probables. Nada queda excluido ni fuertemente castigado por tamaño.
    Umbrales configurables vía variables de entorno (ATLAS_MARKET_CAP_*) para
    poder ajustarlos con evidencia real sin tocar código.
    """
    if not market_cap:
        return ComponentScore(
            name="market_cap",
            score=MARKET_CAP_BASE_SCORE,
            explanation="Sin datos de capitalización disponibles (por ejemplo, en ETFs)",
        )

    in_sweet_spot = MARKET_CAP_SWEET_SPOT_MIN <= market_cap <= MARKET_CAP_SWEET_SPOT_MAX
    bonus = MARKET_CAP_SWEET_SPOT_BONUS if in_sweet_spot else 0.0
    score = _clamp(MARKET_CAP_BASE_SCORE + bonus)
    zone = "dentro del punto dulce de liquidez" if in_sweet_spot else "fuera del punto dulce (sin penalización)"
    return ComponentScore(
        name="market_cap",
        score=score,
        explanation=f"Capitalización de mercado ~ ${market_cap:,.0f} ({zone})",
    )
