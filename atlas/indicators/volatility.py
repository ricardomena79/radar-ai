"""Indicadores de volatilidad: ATR y volatilidad histórica."""

import numpy as np
import pandas as pd


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range, suavizado a la Wilder."""
    if not (isinstance(high, pd.Series) and isinstance(low, pd.Series) and isinstance(close, pd.Series)):
        raise TypeError("high, low y close deben ser pandas.Series")
    if period <= 0:
        raise ValueError("period debe ser mayor que 0")
    if len(close) < period + 1:
        raise ValueError(f"Se requieren al menos {period + 1} datos, se recibieron {len(close)}")

    previous_close = close.shift(1)
    ranges = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    )
    true_range = ranges.max(axis=1)

    return true_range.ewm(alpha=1 / period, adjust=False).mean()


def volatility(series: pd.Series, period: int = 20, annualize: bool = True, trading_periods: int = 252) -> pd.Series:
    """Volatilidad histórica: desviación estándar móvil de los retornos logarítmicos."""
    if not isinstance(series, pd.Series):
        raise TypeError("series debe ser un pandas.Series")
    if period <= 0:
        raise ValueError("period debe ser mayor que 0")
    if len(series) < period + 1:
        raise ValueError(f"Se requieren al menos {period + 1} datos, se recibieron {len(series)}")

    log_returns = np.log(series / series.shift(1))
    rolling_std = log_returns.rolling(window=period).std()

    if annualize:
        rolling_std = rolling_std * np.sqrt(trading_periods)

    return rolling_std
