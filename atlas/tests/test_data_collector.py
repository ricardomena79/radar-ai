"""Prueba manual del Data Collector usando AAPL, NVDA, PLTR y SOXL."""

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.providers.yahoo_finance import YahooFinanceProvider
from atlas.data.universe import is_available

SYMBOLS = ["AAPL", "NVDA", "PLTR", "SOXL"]


def _print_quote(quote) -> None:
    print(f"{quote.symbol:6} {quote.name}")
    print(f"  price            : {quote.last_price}")
    print(f"  change_percent   : {quote.change_percent:.2f}%")
    print(f"  volume           : {quote.volume}")
    print(f"  average_volume   : {quote.average_volume}")
    print(f"  relative_volume  : {quote.relative_volume:.2f}")
    print(f"  market_cap       : {quote.market_cap}")
    print(f"  float_shares     : {quote.float_shares}")
    print(f"  timestamp        : {quote.timestamp}")


def test_data_collector() -> None:
    collector = DataCollector(YahooFinanceProvider())

    print("=" * 40)
    print("ATLAS - PRUEBA DE DATA COLLECTOR")
    print("=" * 40)

    for symbol in SYMBOLS:
        assert is_available(symbol), f"{symbol} debería existir en el Universo Racional"

        quote = collector.get_quote(symbol)
        assert quote.symbol == symbol
        assert quote.last_price is not None and quote.last_price > 0
        _print_quote(quote)
        print("-" * 40)

    quotes = collector.get_quotes(SYMBOLS)
    assert len(quotes) == len(SYMBOLS)
    assert {q.symbol for q in quotes} == set(SYMBOLS)

    print(f"get_quotes({SYMBOLS}) devolvió {len(quotes)} cotizaciones.")
    print("=" * 40)
    print("OK: Data Collector funciona correctamente para get_quote y get_quotes.")


if __name__ == "__main__":
    test_data_collector()
