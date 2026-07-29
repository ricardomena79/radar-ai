"""Indicadores de volumen: Relative Volume, Dollar Volume y VWAP."""

import pandas as pd


def relative_volume(current_volume: float, average_volume: float) -> float:
    """Volumen relativo = volumen actual / volumen promedio."""
    if current_volume is None or average_volume is None:
        raise ValueError("current_volume y average_volume son obligatorios")
    if average_volume <= 0:
        raise ValueError("average_volume debe ser mayor que 0")
    return current_volume / average_volume


def dollar_volume(price: float, volume: float) -> float:
    """Volumen en dólares = precio * volumen."""
    if price is None or volume is None:
        raise ValueError("price y volume son obligatorios")
    if price < 0 or volume < 0:
        raise ValueError("price y volume no pueden ser negativos")
    return price * volume


def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    """Volume Weighted Average Price, acumulado sobre la serie recibida.

    La serie debe cubrir una sola sesión; el llamador es responsable de
    recortar los datos a la ventana intradía deseada.
    """
    series = (high, low, close, volume)
    if not all(isinstance(s, pd.Series) for s in series):
        raise TypeError("high, low, close y volume deben ser pandas.Series")
    if len({len(s) for s in series}) != 1:
        raise ValueError("high, low, close y volume deben tener la misma longitud")
    if len(close) == 0:
        raise ValueError("Se requiere al menos un dato")

    typical_price = (high + low + close) / 3
    cumulative_price_volume = (typical_price * volume).cumsum()
    cumulative_volume = volume.cumsum()

    return cumulative_price_volume / cumulative_volume
