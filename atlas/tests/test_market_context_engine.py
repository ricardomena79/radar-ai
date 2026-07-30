"""Prueba manual del Market Context Engine y su registro en la Knowledge Base.

Usa una base SQLite de prueba separada (no la real) para no mezclar datos
sintéticos con la base de conocimiento real de Atlas.
"""

import dataclasses
from pathlib import Path

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.providers.yahoo_finance import YahooFinanceProvider
from atlas.data.universe import get_equities
from atlas.engine.market_context_engine import MarketContextEngine
from atlas.engine.money_flow_engine import MoneyFlowEngine
from atlas.knowledge import NORMAL, KnowledgeEngine, MarketEvent, PredictionRecord

TEST_DB_PATH = Path(__file__).resolve().parents[1] / "cache" / "test_market_context.db"
SAMPLE_SIZE = 80


def _sample(assets, count: int):
    if len(assets) <= count:
        return list(assets)
    step = max(1, len(assets) // count)
    return assets[::step][:count]


def _build_sample_universe() -> dict:
    equities = _sample(get_equities(), SAMPLE_SIZE)
    return {asset.symbol: asset for asset in equities}


def test_market_context_engine() -> None:
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()

    collector = DataCollector(YahooFinanceProvider())

    sample_universe = _build_sample_universe()
    money_flow_engine = MoneyFlowEngine(collector=collector, universe_provider=lambda: sample_universe)
    money_flow_engine.scan()

    context_engine = MarketContextEngine(collector=collector)
    context = context_engine.get_context(sector="Technology", money_flow_engine=money_flow_engine)

    print("=" * 60)
    print("ATLAS - MARKET CONTEXT ENGINE")
    print("=" * 60)
    for key, value in dataclasses.asdict(context).items():
        print(f"  {key:28}: {value}")

    assert context.spy_price is not None
    assert context.qqq_price is not None
    assert context.day_of_week is not None
    assert context.month is not None
    assert context.earnings_season in (True, False)

    context_fields = dataclasses.asdict(context)

    knowledge = KnowledgeEngine(db_path=TEST_DB_PATH)

    event = MarketEvent(
        date="2026-07-30", time="09:45:00", ticker="AAPL", sector="Technology",
        industry="Consumer Electronics", price=338.19, gap_percent=-0.10, rvol=0.87,
        volume=48_852_885, float_shares=14_662_387_495, market_cap=4_967_117_094_912,
        atlas_score=64.79, momentum_score=51.0, money_flow_score=context.sector_money_flow_score,
        decision="VIGILAR", max_result_percent=None, close_result_percent=None,
        event_type=NORMAL, **context_fields,
    )
    event_id = knowledge.record_event(event)

    prediction = PredictionRecord(
        date="2026-07-30", time="09:44:30", ticker="AAPL", mode="standard",
        decision="VIGILAR", confidence=49.0, atlas_score=64.79, momentum_score=51.0,
        money_flow_score=context.sector_money_flow_score, event_id=event_id, **context_fields,
    )
    prediction_id = knowledge.record_prediction(prediction)

    stored_event = knowledge.events.get_event(event_id)
    stored_predictions = knowledge.predictions.get_predictions(ticker="AAPL", limit=1)

    assert stored_event is not None
    assert stored_event.spy_price == context.spy_price
    assert stored_event.day_of_week == context.day_of_week
    assert stored_event.earnings_season == context.earnings_season

    assert stored_predictions and stored_predictions[0].id == prediction_id
    assert stored_predictions[0].vix_price == context.vix_price

    stats = knowledge.get_statistics()
    assert stats.total_events == 1
    assert stats.total_predictions == 1

    print("\n--- EVENTO GUARDADO (con contexto) ---")
    print(f"  id={stored_event.id}  {stored_event.ticker}  spy={stored_event.spy_price}  "
          f"vix={stored_event.vix_price}  btc={stored_event.btc_price}  "
          f"sector_etf={stored_event.sector_etf_symbol}  leading_sector={stored_event.leading_sector}  "
          f"leading_industry={stored_event.leading_industry}  dia={stored_event.day_of_week}  "
          f"mes={stored_event.month}  earnings_season={stored_event.earnings_season}")

    print("\n--- ESTADÍSTICAS ---")
    print(f"  Total de eventos     : {stats.total_events}")
    print(f"  Total de predicciones: {stats.total_predictions}")

    knowledge.close()

    print("\n" + "=" * 60)
    print("OK: Market Context Engine calculado y registrado correctamente en la Knowledge Base.")


if __name__ == "__main__":
    test_market_context_engine()
