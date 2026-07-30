"""Prueba manual del Momentum Engine usando AAPL, NVDA, PLTR y SOXL."""

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.providers.yahoo_finance import YahooFinanceProvider
from atlas.engine.momentum_engine import WEIGHTS, calculate_momentum_score

SYMBOLS = ["AAPL", "NVDA", "PLTR", "SOXL"]


def test_momentum_engine() -> None:
    collector = DataCollector(YahooFinanceProvider())

    print("=" * 60)
    print("MOMENTUM ENGINE")
    print("=" * 60)

    for symbol in SYMBOLS:
        result = calculate_momentum_score(symbol, collector)

        assert result.symbol == symbol
        assert 0 <= result.momentum_score <= 100
        assert len(result.components) == len(WEIGHTS)

        print(f"\n{symbol} -> Momentum Score = {result.momentum_score:.2f} / 100")
        print("-" * 60)
        for component in result.components:
            print(
                f"  {component.name:16} score={component.score:6.2f}  "
                f"peso={component.weight:.2f}  aporte={component.weighted_score:6.2f}"
            )
            print(f"    {component.explanation}")

    print("\n" + "=" * 60)
    print("OK: Momentum Engine calculado correctamente para los 4 símbolos.")


if __name__ == "__main__":
    test_momentum_engine()
