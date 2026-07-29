"""Prueba manual de la biblioteca atlas.indicators usando únicamente AAPL."""

import pandas as pd

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.providers.yahoo_finance import YahooFinanceProvider
from atlas.indicators import (
    atr,
    dollar_volume,
    ema,
    gap_percent,
    macd,
    relative_volume,
    rsi,
    sma,
    volatility,
    vwap,
)

SYMBOL = "AAPL"


def _last(series: pd.Series) -> float:
    return series.dropna().iloc[-1]


def test_indicators_on_aapl() -> None:
    collector = DataCollector(YahooFinanceProvider())

    daily = collector.get_history(SYMBOL, period="6mo", interval="1d")
    assert not daily.empty, "El historial diario de AAPL no debería estar vacío"

    intraday = collector.get_history(SYMBOL, period="1d", interval="5m")
    assert not intraday.empty, "El historial intradía de AAPL no debería estar vacío"

    quote = collector.get_quote(SYMBOL)

    close = daily["Close"]
    high = daily["High"]
    low = daily["Low"]

    sma_result = sma(close, period=20)
    ema_result = ema(close, period=20)
    rsi_result = rsi(close, period=14)
    macd_result = macd(close)
    atr_result = atr(high, low, close, period=14)
    volatility_result = volatility(close, period=20)
    vwap_result = vwap(intraday["High"], intraday["Low"], intraday["Close"], intraday["Volume"])

    rvol = relative_volume(quote.volume, quote.average_volume)
    dvol = dollar_volume(quote.last_price, quote.volume)
    gap = gap_percent(quote.open, quote.previous_close)

    assert 0 <= _last(rsi_result) <= 100
    assert _last(atr_result) > 0
    assert rvol > 0
    assert dvol > 0

    print("=" * 40)
    print(f"ATLAS - PRUEBA DE INDICADORES ({SYMBOL})")
    print("=" * 40)
    print(f"SMA(20)          : {_last(sma_result):.2f}")
    print(f"EMA(20)          : {_last(ema_result):.2f}")
    print(f"RSI(14)          : {_last(rsi_result):.2f}")
    print(f"MACD line        : {_last(macd_result.macd_line):.4f}")
    print(f"MACD signal      : {_last(macd_result.signal_line):.4f}")
    print(f"MACD histogram   : {_last(macd_result.histogram):.4f}")
    print(f"ATR(14)          : {_last(atr_result):.2f}")
    print(f"Volatility(20)   : {_last(volatility_result):.4f}")
    print(f"VWAP (hoy)       : {_last(vwap_result):.2f}")
    print(f"Relative Volume  : {rvol:.2f}")
    print(f"Dollar Volume    : {dvol:,.2f}")
    print(f"Gap %            : {gap:.2f}%")
    print("=" * 40)
    print("OK: todos los indicadores se calcularon correctamente.")


if __name__ == "__main__":
    test_indicators_on_aapl()
