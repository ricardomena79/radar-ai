"""Prueba manual de Decision Recorder con datos reales de AAPL.

Verifica los tres caminos de escritura, y que cada uno va a su destino
correcto sin mezclarse: record_decision()/record_market_event() -> Knowledge
Base (mercado); record_trade() -> Decision Journal (operador).
"""

from pathlib import Path

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.providers.yahoo_finance import YahooFinanceProvider
from atlas.decision_journal import DecisionJournal, Trade
from atlas.decision_recorder import DecisionRecorder
from atlas.engine.atlas_score import calculate_atlas_score
from atlas.engine.decision_engine import DecisionEngine
from atlas.engine.market_context_engine import MarketContextEngine
from atlas.engine.momentum_engine import calculate_momentum_score
from atlas.knowledge import NORMAL, KnowledgeEngine
from atlas.knowledge.engine_versions import parse_versions_json

KNOWLEDGE_TEST_DB = Path(__file__).resolve().parents[1] / "cache" / "test_recorder_knowledge.db"
JOURNAL_TEST_DB = Path(__file__).resolve().parents[1] / "cache" / "test_recorder_journal.db"

SYMBOL = "AAPL"


def test_decision_recorder() -> None:
    for path in (KNOWLEDGE_TEST_DB, JOURNAL_TEST_DB):
        if path.exists():
            path.unlink()

    collector = DataCollector(YahooFinanceProvider())
    quote = collector.get_quote(SYMBOL)
    atlas_score = calculate_atlas_score(SYMBOL, collector)
    momentum_result = calculate_momentum_score(SYMBOL, collector)
    context = MarketContextEngine(collector=collector).get_context(sector=quote.sector)

    decision_engine = DecisionEngine(collector=collector)
    decision_result = decision_engine.decide(SYMBOL)

    knowledge = KnowledgeEngine(db_path=KNOWLEDGE_TEST_DB)
    journal = DecisionJournal(db_path=JOURNAL_TEST_DB)
    recorder = DecisionRecorder(knowledge_engine=knowledge, decision_journal=journal)

    print("=" * 60)
    print(f"ATLAS - DECISION RECORDER ({SYMBOL}, datos reales)")
    print("=" * 60)

    # 1. record_decision() -> Knowledge Base, tabla predictions.
    prediction_id = recorder.record_decision(quote=quote, decision_result=decision_result, context=context)
    stored_prediction = knowledge.predictions.get_predictions(ticker=SYMBOL, limit=1)[0]

    assert stored_prediction.id == prediction_id
    assert stored_prediction.decision == decision_result.decision
    assert stored_prediction.mode == decision_result.mode
    assert stored_prediction.confidence == decision_result.confidence
    assert stored_prediction.spy_price == context.spy_price
    assert stored_prediction.data_source == "Calculado por Atlas"
    versions = parse_versions_json(stored_prediction.engine_versions)
    assert versions["decision_engine"] == "1.0"

    print(f"1. record_decision(): predicción #{prediction_id} guardada")
    print(f"   decision={stored_prediction.decision}  confidence={stored_prediction.confidence:.0f}%  "
          f"mode={stored_prediction.mode}  spy_price={stored_prediction.spy_price}")
    print(f"   engine_versions={versions}")

    # 2. record_market_event() -> Knowledge Base, tabla events (con gap_percent calculado).
    event_id = recorder.record_market_event(
        quote=quote, event_type=NORMAL, atlas_score=atlas_score,
        momentum_result=momentum_result, decision_result=decision_result, context=context,
    )
    stored_event = knowledge.events.get_event(event_id)

    assert stored_event.ticker == SYMBOL
    assert stored_event.event_type == NORMAL
    assert stored_event.atlas_score == atlas_score.total
    assert stored_event.momentum_score == momentum_result.momentum_score
    assert stored_event.vix_price == context.vix_price
    expected_gap = None
    if quote.open is not None and quote.previous_close:
        expected_gap = round(((quote.open - quote.previous_close) / quote.previous_close) * 100, 6)
    if expected_gap is not None:
        assert stored_event.gap_percent is not None and round(stored_event.gap_percent, 6) == expected_gap

    print(f"\n2. record_market_event(): evento #{event_id} guardado")
    print(f"   price={stored_event.price}  gap_percent={stored_event.gap_percent}  "
          f"atlas_score={stored_event.atlas_score}  vix_price={stored_event.vix_price}")

    # 3. record_trade() -> Decision Journal, nunca la Knowledge Base.
    trade = Trade(
        date="2026-07-31", time="09:45:00", ticker=SYMBOL,
        buy_price=quote.last_price, buy_reason="Registrado vía Decision Recorder (prueba)",
        atlas_score=atlas_score.total, momentum_score=momentum_result.momentum_score,
    )
    trade_id = recorder.record_trade(trade)
    stored_trade = journal.get_trades(ticker=SYMBOL, limit=1)[0]

    assert stored_trade.id == trade_id
    assert stored_trade.buy_reason == "Registrado vía Decision Recorder (prueba)"

    print(f"\n3. record_trade(): operación #{trade_id} guardada en Decision Journal")

    # Separación de conocimientos: la base de eventos/predicciones y la del
    # operador siguen siendo archivos completamente distintos.
    assert knowledge.events.count() == 1
    assert knowledge.predictions.count() == 1
    assert journal.trades.get_trades(limit=10)
    assert KNOWLEDGE_TEST_DB.resolve() != JOURNAL_TEST_DB.resolve()

    recorder.close()

    print("\n" + "=" * 60)
    print("OK: Decision Recorder registra decisiones, eventos y operaciones correctamente,")
    print("    cada uno en su destino, sin mezclar mercado y operador.")


if __name__ == "__main__":
    test_decision_recorder()
