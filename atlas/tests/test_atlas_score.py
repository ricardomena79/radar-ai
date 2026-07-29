"""Prueba manual del Atlas Score v1 usando AAPL, NVDA, PLTR y SOXL."""

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.providers.yahoo_finance import YahooFinanceProvider
from atlas.engine.atlas_score import WEIGHTS, calculate_atlas_score

SYMBOLS = ["AAPL", "NVDA", "PLTR", "SOXL"]


def test_atlas_score() -> None:
    collector = DataCollector(YahooFinanceProvider())

    print("=" * 60)
    print("ATLAS SCORE v1")
    print("=" * 60)

    for symbol in SYMBOLS:
        result = calculate_atlas_score(symbol, collector)

        assert result.symbol == symbol
        assert 0 <= result.total <= 100
        assert len(result.components) == len(WEIGHTS)

        print(f"\n{symbol} -> Atlas Score = {result.total:.2f} / 100")
        print("-" * 60)
        for component in result.components:
            print(
                f"  {component.name:16} score={component.score:6.2f}  "
                f"peso={component.weight:.2f}  aporte={component.weighted_score:6.2f}"
            )
            print(f"    {component.explanation}")

    print("\n" + "=" * 60)
    print("OK: Atlas Score calculado correctamente para los 4 símbolos.")


if __name__ == "__main__":
    test_atlas_score()
