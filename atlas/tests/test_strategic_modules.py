"""Prueba manual de los tres módulos estratégicos: Research Lab, Strategy Lab y Decision Journal.

Research Lab y Strategy Lab son solo interfaz todavía: se verifica que se
puedan instanciar y que cada método declarado lance NotImplementedError (no
un error inesperado, no un resultado inventado).

Decision Journal sí tiene su registro (record_trade/get_trades) implementado:
se verifica que funcione end-to-end, que sea un repositorio puro (sin
métodos de análisis ni estadísticas -- eso vive en Operator Learning
Engine), y que su base de datos quede completamente separada de la
Knowledge Base (archivos .db distintos).
"""

from pathlib import Path

from atlas.decision_journal import DecisionJournal, Trade
from atlas.knowledge import KnowledgeEngine
from atlas.research_lab import ResearchLab
from atlas.strategy_lab import StrategyLab, StrategyRule

KNOWLEDGE_TEST_DB = Path(__file__).resolve().parents[1] / "cache" / "test_strategic_knowledge.db"
JOURNAL_TEST_DB = Path(__file__).resolve().parents[1] / "cache" / "test_strategic_journal.db"


def test_research_lab_is_interface_only() -> None:
    knowledge = KnowledgeEngine(db_path=KNOWLEDGE_TEST_DB)
    lab = ResearchLab(knowledge)

    calls = [
        lambda: lab.find_factor_combinations(["gap_percent", "rvol"]),
        lambda: lab.compare_thresholds("rvol", [1.5, 2.0, 3.0]),
        lambda: lab.discover_emerging_patterns(),
        lambda: lab.detect_decaying_patterns(),
        lambda: lab.discover_antipatterns(),
        lambda: lab.compare_sectors(),
        lambda: lab.analyze_market_context_influence(),
        lambda: lab.run_all(),
    ]
    for call in calls:
        try:
            call()
            raise AssertionError("Se esperaba NotImplementedError")
        except NotImplementedError:
            pass

    knowledge.close()
    print("Research Lab: todas las investigaciones declaradas responden NotImplementedError (esperado).")


def test_strategy_lab_is_interface_only() -> None:
    knowledge = KnowledgeEngine(db_path=KNOWLEDGE_TEST_DB)
    lab = StrategyLab(knowledge)

    top1 = StrategyRule(name="Top 1", entry_rule="Top 1 por Atlas Score", take_profit_percent=3.0, stop_loss_percent=2.0)
    top3 = StrategyRule(name="Top 3", entry_rule="Top 3 por Atlas Score", take_profit_percent=5.0, stop_loss_percent=3.0)

    for call in (lambda: lab.simulate(top1), lambda: lab.compare([top1, top3])):
        try:
            call()
            raise AssertionError("Se esperaba NotImplementedError")
        except NotImplementedError:
            pass

    knowledge.close()
    print("Strategy Lab: simulate() y compare() responden NotImplementedError (esperado).")


def test_decision_journal_records_and_stays_isolated() -> None:
    if JOURNAL_TEST_DB.exists():
        JOURNAL_TEST_DB.unlink()

    journal = DecisionJournal(db_path=JOURNAL_TEST_DB)

    trade = Trade(
        date="2026-07-30", time="09:45:00", ticker="AAPL",
        buy_price=331.00, sell_price=338.19, buy_reason="Ruptura de VWAP con RVOL alto",
        sell_reason="Objetivo de +2% alcanzado", atlas_rank_at_time=3,
        atlas_score=64.79, momentum_score=51.0, money_flow_score=42.5,
        evidence_level="B", final_result="GANANCIA", profit_loss_percent=2.17,
    )
    trade_id = journal.record_trade(trade)

    stored = journal.get_trades(ticker="AAPL", limit=1)[0]
    assert stored.id == trade_id
    assert stored.buy_reason == "Ruptura de VWAP con RVOL alto"
    assert stored.profit_loss_percent == 2.17

    # Decision Journal es un repositorio puro: no debe exponer ningún método
    # de análisis ni de estadísticas (eso vive en Operator Learning Engine).
    forbidden_methods = (
        "get_statistics",
        "find_best_time_windows",
        "find_best_asset_types",
        "detect_recurring_errors",
        "detect_early_exits",
        "find_ignored_recommendations",
    )
    for method_name in forbidden_methods:
        assert not hasattr(journal, method_name), f"Decision Journal no debería exponer '{method_name}'"

    journal.close()

    assert JOURNAL_TEST_DB.exists()
    assert JOURNAL_TEST_DB.resolve() != KNOWLEDGE_TEST_DB.resolve()
    assert JOURNAL_TEST_DB.name != "atlas_knowledge.db"

    print(f"Decision Journal: operación registrada y leída correctamente (id={trade_id}).")
    print(f"  Base de datos aislada: {JOURNAL_TEST_DB.name} (distinta de la Knowledge Base)")
    print("  Confirmado: no expone get_statistics() ni métodos de análisis (repositorio puro).")


if __name__ == "__main__":
    test_research_lab_is_interface_only()
    test_strategy_lab_is_interface_only()
    test_decision_journal_records_and_stays_isolated()
    print("\nOK: Research Lab, Strategy Lab y Decision Journal quedaron con su arquitectura lista.")
