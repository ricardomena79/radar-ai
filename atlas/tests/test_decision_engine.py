"""Prueba manual del Decision Engine usando AAPL, NVDA, PLTR y SOXL.

El Money Flow Score depende del sector del símbolo, así que primero se
escanea un subconjunto de acciones (estratificado, incluyendo AAPL/NVDA/PLTR)
con MoneyFlowEngine, y ese resultado ya calculado se inyecta al
DecisionEngine. SOXL es un ETF apalancado sin sector/industria en Yahoo
Finance, así que su Money Flow Score queda en None a propósito: el motor
debe manejarlo con gracia, no fallar.
"""

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.providers.yahoo_finance import YahooFinanceProvider
from atlas.data.universe import get_equities
from atlas.engine.decision_engine import DecisionEngine
from atlas.engine.money_flow_engine import MoneyFlowEngine

SYMBOLS = ["AAPL", "NVDA", "PLTR", "SOXL"]
SAMPLE_SIZE = 100


def _sample(assets, count: int):
    if len(assets) <= count:
        return list(assets)
    step = max(1, len(assets) // count)
    return assets[::step][:count]


def _build_sample_universe() -> dict:
    equities = _sample(get_equities(), SAMPLE_SIZE)
    universe = {asset.symbol: asset for asset in equities}
    for symbol in ["AAPL", "NVDA", "PLTR"]:
        if symbol not in universe:
            for asset in get_equities():
                if asset.symbol == symbol:
                    universe[symbol] = asset
                    break
    return universe


def _print_result(result) -> None:
    print(f"\n{result.symbol} [{result.mode}] -> {result.decision}   Confianza: {result.confidence:.0f}%")
    print("-" * 60)
    print(f"  Atlas Score       : {result.atlas_score:.2f}")
    print(f"  Momentum Score    : {result.momentum_score:.2f}")
    money_flow_display = f"{result.money_flow_score:.2f}" if result.money_flow_score is not None else "N/D"
    print(f"  Money Flow Score  : {money_flow_display}")

    print("  Cumple:")
    for condition in result.met_conditions:
        print(f"    - {condition}")

    print("  Falta:")
    for condition in result.missing_conditions:
        print(f"    - {condition}")

    if result.next_events:
        print("  Qué debe ocurrir para cambiar de estado:")
        for event in result.next_events:
            print(f"    - {event}")

    if result.unavailable_conditions:
        print("  Sin datos suficientes para evaluar:")
        for item in result.unavailable_conditions:
            print(f"    - {item}")


def test_decision_engine() -> None:
    collector = DataCollector(YahooFinanceProvider())

    sample_universe = _build_sample_universe()
    money_flow_engine = MoneyFlowEngine(collector=collector, universe_provider=lambda: sample_universe)
    money_flow_engine.scan()

    standard_engine = DecisionEngine(collector=collector, money_flow_engine=money_flow_engine, mode="standard")
    market_open_engine = DecisionEngine(collector=collector, money_flow_engine=money_flow_engine, mode="market_open")

    print("=" * 60)
    print("ATLAS - DECISION ENGINE (modo standard)")
    print("=" * 60)

    for symbol in SYMBOLS:
        result = standard_engine.decide(symbol)

        assert result.symbol == symbol
        assert result.mode == "standard"
        assert result.decision in ("COMPRAR", "VIGILAR", "DESCARTAR")
        assert 0.0 <= result.confidence <= 100.0
        assert len(result.met_conditions) + len(result.missing_conditions) > 0

        _print_result(result)

    print("\n" + "=" * 60)
    print("ATLAS - DECISION ENGINE (modo market_open, 9:30-10:30)")
    print("=" * 60)

    for symbol in SYMBOLS:
        result = market_open_engine.decide(symbol)

        assert result.mode == "market_open"
        assert result.decision in ("COMPRAR", "VIGILAR", "DESCARTAR")
        assert 0.0 <= result.confidence <= 100.0

        _print_result(result)

    print("\n" + "=" * 60)
    print("OK: Decision Engine funciona correctamente para los 4 símbolos, en ambos modos.")


if __name__ == "__main__":
    test_decision_engine()
