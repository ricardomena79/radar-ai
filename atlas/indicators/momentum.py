"""Indicadores de momentum: RSI y MACD."""

from dataclasses import dataclass

import pandas as pd

from atlas.indicators.ema import ema


def _validate(series: pd.Series, period: int) -> None:
    if not isinstance(series, pd.Series):
        raise TypeError("series debe ser un pandas.Series")
    if period <= 0:
        raise ValueError("period debe ser mayor que 0")
    if len(series) < period + 1:
        raise ValueError(f"Se requieren al menos {period + 1} datos, se recibieron {len(series)}")


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Índice de fuerza relativa (RSI), suavizado a la Wilder."""
    _validate(series, period)

    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

    rs = avg_gain / avg_loss
    result = 100 - (100 / (1 + rs))

    return result.where(avg_loss != 0, 100.0)


@dataclass(frozen=True)
class MACDResult:
    """Resultado del MACD: línea MACD, línea de señal e histograma."""

    macd_line: pd.Series
    signal_line: pd.Series
    histogram: pd.Series


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> MACDResult:
    """MACD (Moving Average Convergence Divergence)."""
    if fast >= slow:
        raise ValueError("fast debe ser menor que slow")
    _validate(series, slow)

    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line

    return MACDResult(macd_line=macd_line, signal_line=signal_line, histogram=histogram)
