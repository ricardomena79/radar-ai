"""Prueba manual de Decision Journal.

Decision Journal tiene su registro (record_trade/get_trades) implementado:
se verifica que funcione end-to-end, que sea un repositorio puro (sin
métodos de análisis ni estadísticas -- eso vive en Operator Learning
Engine), y que su base de datos quede completamente separada de la
Knowledge Base (archivos .db distintos).

Hito 6, Fase 6.1 (2026-09-04, autorizado explícitamente): este archivo
cubría también Research Lab y Strategy Lab, ambos eliminados por ser
interfaces 100% NotImplementedError sin ningún caller en producción --
ver `atlas/tests/test_strategic_modules.py` (git history) para los tests
retirados.
"""

from pathlib import Path

from atlas.decision_journal import DecisionJournal, Trade

# Solo se usa para el assert de aislamiento de bases de datos (línea 61) --
# ya no se instancia ningún KnowledgeEngine acá tras retirar Research/Strategy Lab.
KNOWLEDGE_TEST_DB = Path(__file__).resolve().parents[1] / "cache" / "test_strategic_knowledge.db"
JOURNAL_TEST_DB = Path(__file__).resolve().parents[1] / "cache" / "test_strategic_journal.db"


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
    test_decision_journal_records_and_stays_isolated()
    print("\nOK: Decision Journal quedó con su arquitectura lista.")
