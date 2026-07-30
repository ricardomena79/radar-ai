"""Prueba manual del Money Flow Engine sobre un subconjunto del Universo Racional.

Un escaneo del universo completo de acciones (~1646 símbolos) puede tardar
horas, ya que cada acción requiere calcular su Momentum Score completo
(3 llamadas a Yahoo Finance). Para verificar el pipeline en minutos, esta
prueba inyecta un subconjunto estratificado de acciones. Por defecto (sin
argumentos) el motor escanea atlas.data.universe.get_equities() completo.
"""

from atlas.data.universe import get_equities
from atlas.engine.money_flow_engine import MoneyFlowEngine

SAMPLE_SIZE = 120


def _sample(assets, count: int):
    if len(assets) <= count:
        return list(assets)
    step = max(1, len(assets) // count)
    return assets[::step][:count]


def _build_sample_universe() -> dict:
    equities = _sample(get_equities(), SAMPLE_SIZE)
    return {asset.symbol: asset for asset in equities}


def test_money_flow_engine() -> None:
    sample_universe = _build_sample_universe()
    engine = MoneyFlowEngine(universe_provider=lambda: sample_universe)

    sectors = engine.scan()

    assert engine.last_scan_duration_seconds is not None
    assert len(sectors) > 0
    scores = [g.money_flow_score for g in sectors]
    assert scores == sorted(scores, reverse=True)

    df = engine.to_dataframe()
    assert len(df) == len(sectors)

    total_stocks = sum(g.stock_count for g in sectors)

    print("=" * 70)
    print("ATLAS - MONEY FLOW ENGINE")
    print("=" * 70)
    print(f"Acciones en el universo de prueba : {len(sample_universe)}")
    print(f"Acciones analizadas con éxito      : {total_stocks}")
    print(f"Símbolos sin datos suficientes     : {engine.last_scan_errors}")
    print(f"Sectores detectados                : {len(sectors)}")
    print(f"Tiempo total del escaneo            : {engine.last_scan_duration_seconds:.1f}s")

    print("\n--- TOP 10 SECTORES POR MONEY FLOW SCORE ---")
    for rank, sector in enumerate(engine.top(10), start=1):
        print(
            f"\n{rank:2}. {sector.name:28} money_flow_score={sector.money_flow_score:6.2f}  "
            f"acciones={sector.stock_count:3}  positivas={sector.positive_count:3}  "
            f"momentum>70={sector.high_momentum_count:3}  "
            f"chg_prom={sector.avg_change_percent:+6.2f}%  rvol_prom={sector.avg_relative_volume:.2f}"
        )
        for stock in sector.top_stocks(3):
            print(
                f"      - {stock.symbol:6} chg={stock.change_percent:+6.2f}%  "
                f"rvol={stock.relative_volume or 0:.2f}  momentum={stock.momentum_score:6.2f}  {stock.name}"
            )

    print("\n" + "=" * 70)
    print("OK: Money Flow Engine funciona correctamente sobre el subconjunto de prueba.")


if __name__ == "__main__":
    test_money_flow_engine()
