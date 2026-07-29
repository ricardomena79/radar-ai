"""Medias móviles: EMA (exponencial) y SMA (simple)."""

import pandas as pd


def _validate(series: pd.Series, period: int) -> None:
    if not isinstance(series, pd.Series):
        raise TypeError("series debe ser un pandas.Series")
    if period <= 0:
        raise ValueError("period debe ser mayor que 0")
    if len(series) < period:
        raise ValueError(f"Se requieren al menos {period} datos, se recibieron {len(series)}")


def sma(series: pd.Series, period: int = 20) -> pd.Series:
    """Media móvil simple sobre `period` observaciones."""
    _validate(series, period)
    return series.rolling(window=period).mean()


def ema(series: pd.Series, period: int = 20) -> pd.Series:
    """Media móvil exponencial sobre `period` observaciones."""
    _validate(series, period)
    return series.ewm(span=period, adjust=False).mean()
