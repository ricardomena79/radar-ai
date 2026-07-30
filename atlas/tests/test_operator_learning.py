"""Prueba manual de Operator Learning Engine.

Decision Journal todavía no tiene historial real acumulado (Decision
Recorder recién se construyó en la etapa anterior), así que esta prueba
siembra un conjunto de operaciones representativo -- variado en horario,
motivo, nivel de evidencia, ranking de Atlas y mes -- para poder ejercitar
cada análisis con datos reales de la base (reales en el sentido de que
viven en SQLite y se leen con la misma API que usaría producción, no
mockeados en memoria).

Usa una base SQLite de prueba separada, no la real.
"""

from pathlib import Path

from atlas.decision_journal import DecisionJournal, Trade
from atlas.operator_learning import MIN_SAMPLE_SIZE, OperatorLearningEngine

JOURNAL_TEST_DB = Path(__file__).resolve().parents[1] / "cache" / "test_operator_learning_journal.db"


def _seed_trades(journal: DecisionJournal) -> None:
    trades = [
        # Mayo: mañana (09h) rinde bien, tarde (14h) mal. Rank Top 3 = mejor resultado.
        Trade(date="2026-05-04", time="09:35:00", ticker="AAPL", buy_price=180, sell_price=185,
              sell_reason="Objetivo alcanzado", atlas_rank_at_time=1, evidence_level="A+",
              final_result="GANANCIA", profit_loss_percent=2.8),
        Trade(date="2026-05-06", time="09:40:00", ticker="NVDA", buy_price=120, sell_price=126,
              sell_reason="Objetivo alcanzado", atlas_rank_at_time=2, evidence_level="A+",
              final_result="GANANCIA", profit_loss_percent=5.0),
        Trade(date="2026-05-11", time="14:20:00", ticker="XYZ", buy_price=10, sell_price=9.4,
              sell_reason="Vendí por miedo", atlas_rank_at_time=8, evidence_level="C",
              final_result="PERDIDA", profit_loss_percent=-6.0),
        Trade(date="2026-05-13", time="14:10:00", ticker="ABC", buy_price=50, sell_price=47.5,
              sell_reason="Vendí por miedo", atlas_rank_at_time=None, evidence_level=None,
              final_result="PERDIDA", profit_loss_percent=-5.0),
        Trade(date="2026-05-20", time="09:45:00", ticker="PLTR", buy_price=120, sell_price=124,
              sell_reason="Objetivo alcanzado", atlas_rank_at_time=3, evidence_level="B",
              final_result="GANANCIA", profit_loss_percent=3.3),
        # Junio: sigue el patrón, empieza a mejorar levemente.
        Trade(date="2026-06-02", time="09:50:00", ticker="AAPL", buy_price=190, sell_price=196,
              sell_reason="Objetivo alcanzado", atlas_rank_at_time=1, evidence_level="A+",
              final_result="GANANCIA", profit_loss_percent=3.2),
        Trade(date="2026-06-08", time="14:05:00", ticker="MARA", buy_price=15, sell_price=14.2,
              sell_reason="Vendí por miedo", atlas_rank_at_time=12, evidence_level="C",
              final_result="PERDIDA", profit_loss_percent=-5.3),
        Trade(date="2026-06-15", time="09:38:00", ticker="SOXL", buy_price=100, sell_price=108,
              sell_reason="Objetivo alcanzado", atlas_rank_at_time=2, evidence_level="A+",
              final_result="GANANCIA", profit_loss_percent=8.0),
        Trade(date="2026-06-19", time="11:15:00", ticker="CCJ", buy_price=80, sell_price=81,
              sell_reason="Objetivo alcanzado", atlas_rank_at_time=4, evidence_level="B",
              final_result="GANANCIA", profit_loss_percent=1.3),
        Trade(date="2026-06-25", time="14:30:00", ticker="KGC", buy_price=22, sell_price=21,
              sell_reason="Stop loss", atlas_rank_at_time=None, evidence_level=None,
              final_result="PERDIDA", profit_loss_percent=-4.5),
        # Julio: mejora clara respecto a mayo.
        Trade(date="2026-07-03", time="09:36:00", ticker="AAPL", buy_price=200, sell_price=210,
              sell_reason="Objetivo alcanzado", atlas_rank_at_time=1, evidence_level="A+",
              final_result="GANANCIA", profit_loss_percent=5.0),
        Trade(date="2026-07-09", time="09:42:00", ticker="NVDA", buy_price=180, sell_price=189,
              sell_reason="Objetivo alcanzado", atlas_rank_at_time=1, evidence_level="A+",
              final_result="GANANCIA", profit_loss_percent=5.0),
        Trade(date="2026-07-14", time="10:05:00", ticker="PLTR", buy_price=115, sell_price=121,
              sell_reason="Objetivo alcanzado", atlas_rank_at_time=3, evidence_level="A+",
              final_result="GANANCIA", profit_loss_percent=5.2),
        Trade(date="2026-07-22", time="14:15:00", ticker="RIOT", buy_price=9, sell_price=8.6,
              sell_reason="Vendí por miedo", atlas_rank_at_time=15, evidence_level="C",
              final_result="PERDIDA", profit_loss_percent=-4.4),
        Trade(date="2026-07-28", time="09:33:00", ticker="SOXL", buy_price=105, sell_price=112,
              sell_reason="Objetivo alcanzado", atlas_rank_at_time=2, evidence_level="A+",
              final_result="GANANCIA", profit_loss_percent=6.7),
    ]
    for trade in trades:
        journal.record_trade(trade)


def test_operator_learning_engine() -> None:
    if JOURNAL_TEST_DB.exists():
        JOURNAL_TEST_DB.unlink()

    journal = DecisionJournal(db_path=JOURNAL_TEST_DB)
    _seed_trades(journal)

    engine = OperatorLearningEngine(journal)

    print("=" * 60)
    print("ATLAS - OPERATOR LEARNING ENGINE")
    print("=" * 60)

    report = engine.generate_report()
    assert len(report) > 0

    for insight in report:
        print(f"\n[{insight.category}] {insight.title}")
        print(f"  {insight.description}")

    # Verificaciones puntuales sobre el contenido, no solo que "no explote".
    time_insights = engine.analyze_time_windows()
    best = next(i for i in time_insights if "Mejor" in i.title)
    worst = next(i for i in time_insights if "Peor" in i.title)
    assert best.evidence["hour"] == 9
    assert worst.evidence["hour"] == 14
    print(f"\nVerificado: mejor horario=09h, peor horario=14h (coincide con los datos sembrados).")

    errors = engine.detect_recurring_errors()
    top_error = errors[0]
    assert "Vendí por miedo" in top_error.title
    assert top_error.evidence["count"] == 4
    print(f"Verificado: motivo repetido más frecuente en pérdidas = 'Vendí por miedo' (4 veces).")

    compliance = engine.analyze_atlas_compliance()[0]
    assert compliance.evidence["within_top"]["n"] == 9  # rank 1,2,3 trades sembrados
    print(f"Verificado: {compliance.evidence['within_top']['n']} operaciones dentro del Top 3 de Atlas.")

    evolution = engine.analyze_performance_evolution()[0]
    assert evolution.evidence["trend"] == "mejorando"
    print(f"Verificado: tendencia de desempeño = 'mejorando' (julio mejor que mayo).")

    discipline = engine.analyze_discipline()[0]
    assert discipline.evidence["Sin registrar"]["n"] == 2
    print(f"Verificado: 2 operaciones sin nivel de evidencia registrado.")

    # Las dos categorías sin datos suficientes deben declarar la interfaz, no inventar.
    for method in (engine.detect_early_exits, engine.detect_late_exits):
        try:
            method()
            raise AssertionError("Se esperaba NotImplementedError")
        except NotImplementedError as exc:
            print(f"\n{method.__name__}(): NotImplementedError (esperado) -> {exc}")

    total_trades = len(journal.get_trades(limit=1000))
    journal.close()

    print("\n" + "=" * 60)
    print(f"OK: Operator Learning Engine analizó {total_trades} operaciones correctamente.")


if __name__ == "__main__":
    test_operator_learning_engine()
