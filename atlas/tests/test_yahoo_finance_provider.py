"""Prueba manual de YahooFinanceProvider usando el ticker AAPL."""

from atlas.data.providers.yahoo_finance import YahooFinanceProvider


def test_aapl_quote() -> None:
    provider = YahooFinanceProvider()
    quote = provider.get_quote("AAPL")

    assert quote.symbol == "AAPL"
    assert quote.last_price is not None and quote.last_price > 0
    assert quote.previous_close is not None and quote.previous_close > 0
    assert quote.volume is not None and quote.volume > 0
    assert quote.open is not None
    assert quote.high is not None
    assert quote.low is not None
    assert quote.market_cap is not None and quote.market_cap > 0
    assert quote.average_volume is not None and quote.average_volume > 0
    assert quote.relative_volume is not None and quote.relative_volume > 0
    assert quote.timestamp is not None

    print("=" * 40)
    print("ATLAS - PRUEBA YahooFinanceProvider (AAPL)")
    print("=" * 40)
    print(f"Symbol           : {quote.symbol}")
    print(f"Name             : {quote.name}")
    print(f"Last price       : {quote.last_price}")
    print(f"Change %         : {quote.change_percent:.2f}%")
    print(f"Volume           : {quote.volume}")
    print(f"Open             : {quote.open}")
    print(f"High             : {quote.high}")
    print(f"Low              : {quote.low}")
    print(f"Previous close   : {quote.previous_close}")
    print(f"Market cap       : {quote.market_cap}")
    print(f"Float shares     : {quote.float_shares}")
    print(f"Average volume   : {quote.average_volume}")
    print(f"Relative volume  : {quote.relative_volume:.2f}")
    print(f"Timestamp        : {quote.timestamp}")
    print("=" * 40)
    print("OK: todos los campos se obtuvieron correctamente.")


if __name__ == "__main__":
    test_aapl_quote()
