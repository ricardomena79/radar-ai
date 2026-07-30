"""Prueba manual de Calibration Advisor y la fachada LearningEngine.

Siembra evidencia de precisión (para Accuracy Tracker) y patrones (para
Pattern Evolution) con resultados esperados conocidos, y confirma que
Calibration Advisor genera exactamente las propuestas que corresponden --
ni de más ni de menos -- y que LearningEngine, como fachada, expone lo
mismo que sus submódulos sin que la fachada misma escriba nada.

Usa bases SQLite de prueba separadas, no las reales.
"""

from pathlib import Path

from atlas.engine.decision_engine import COMPRAR, DESCARTAR
from atlas.knowledge import NORMAL, KnowledgeEngine, MarketEvent, PatternRegistry
from atlas.knowledge.prediction_store import PredictionRecord
from atlas.learning import ENGINE_CALIBRATION, PATTERN_STATE_CHANGE, LearningEngine

KNOWLEDGE_TEST_DB = Path(__file__).resolve().parents[1] / "cache" / "test_learning_engine_knowledge.db"
PATTERN_TEST_DB = Path(__file__).resolve().parents[1] / "cache" / "test_learning_engine_patterns.db"


def _seed_accuracy_evidence(knowledge: KnowledgeEngine) -> None:
    """COMPRAR con buena precisión (no debería generar propuesta); DESCARTAR con
    precisión baja y muestra suficiente (sí debería generar una propuesta)."""
    idx = 0

    def add_pair(decision, correct):
        nonlocal idx
        idx += 1
        ticker = f"SIM{idx}"
        close_result = (3.0 if correct else -2.0) if decision == COMPRAR else (-1.5 if correct else 4.0)
        knowledge.record_prediction(PredictionRecord(
            date="2026-07-31", time="09:30:00", ticker=ticker, mode="standard",
            decision=decision, confidence=60.0,
        ))
        knowledge.record_event(MarketEvent(
            date="2026-07-31", time="16:00:00", ticker=ticker, price=100.0,
            event_type=NORMAL, close_result_percent=close_result,
        ))

    for _ in range(18):
        add_pair(COMPRAR, correct=True)
    for _ in range(2):
        add_pair(COMPRAR, correct=False)  # COMPRAR: 18/20 = 90% -> no amerita revisión

    for _ in range(4):
        add_pair(DESCARTAR, correct=True)
    for _ in range(8):
        add_pair(DESCARTAR, correct=False)  # DESCARTAR: 4/12 = 33% -> sí amerita revisión


def _seed_pattern_evidence(registry: PatternRegistry) -> None:
    """Un patrón con evidencia sólida en observación (debería proponerse ACTIVO)."""
    registry.register_pattern(
        "gap_alto_technology", "Gap alto en Technology", "combinacion_factores",
        evidence={"sample_size": 40, "win_rate": 0.72, "recent_sample_size": 15,
                  "recent_win_rate": 0.70, "baseline_win_rate": 0.50},
    )
    # Un segundo patrón, activo y sano: no debería generar propuesta.
    registry.register_pattern("rvol_extremo_energy", "RVOL extremo en Energy", "combinacion_factores",
                               evidence={"win_rate": 0.60})
    from atlas.knowledge import PATTERN_ACTIVE
    registry.transition_state("rvol_extremo_energy", PATTERN_ACTIVE, reason="setup de prueba",
                               evidence={"recent_win_rate": 0.70, "recent_sample_size": 50, "baseline_win_rate": 0.50})


def test_learning_engine_facade() -> None:
    for path in (KNOWLEDGE_TEST_DB, PATTERN_TEST_DB):
        if path.exists():
            path.unlink()

    knowledge = KnowledgeEngine(db_path=KNOWLEDGE_TEST_DB)
    registry = PatternRegistry(db_path=PATTERN_TEST_DB)
    _seed_accuracy_evidence(knowledge)
    _seed_pattern_evidence(registry)

    engine = LearningEngine(knowledge_engine=knowledge, pattern_registry=registry)

    print("=" * 60)
    print("ATLAS - LEARNING ENGINE (fachada) + CALIBRATION ADVISOR")
    print("=" * 60)

    report = engine.generate_learning_report()

    print(f"\nPrecisión general: {report.overall_accuracy.breakdown}")
    print(f"Precisión por decisión: {report.accuracy_by_decision.breakdown}")
    assert report.accuracy_by_decision.breakdown[COMPRAR]["accuracy"] == 0.9
    assert report.accuracy_by_decision.breakdown[DESCARTAR]["accuracy"] == round(4 / 12, 4)

    print(f"\nPatrones evaluados: {len(report.pattern_reports)}")
    for pr in report.pattern_reports:
        print(f"  {pr.pattern_key}: {pr.current_state} -> {pr.proposed_state}  ({pr.reason})")

    print(f"\nPropuestas de calibración generadas: {len(report.calibration_proposals)}")
    for proposal in report.calibration_proposals:
        print(f"  [{proposal.category}] {proposal.title}")

    # Debe haber exactamente 2 propuestas: 1 transición de patrón + 1 revisión de motor.
    assert len(report.calibration_proposals) == 2
    categories = {p.category for p in report.calibration_proposals}
    assert categories == {PATTERN_STATE_CHANGE, ENGINE_CALIBRATION}

    pattern_proposal = next(p for p in report.calibration_proposals if p.category == PATTERN_STATE_CHANGE)
    assert pattern_proposal.target == "gap_alto_technology"
    assert pattern_proposal.proposed_new_state == "Activo"

    engine_proposal = next(p for p in report.calibration_proposals if p.category == ENGINE_CALIBRATION)
    assert DESCARTAR in engine_proposal.title
    assert engine_proposal.sample_size == 12

    print("\nVerificado: exactamente 2 propuestas -- la transición del patrón con evidencia")
    print("            sólida, y la revisión de DESCARTAR por su precisión baja (33%).")
    print("            'rvol_extremo_energy' (sano) y COMPRAR (90%) correctamente NO generaron propuesta.")

    # La fachada es de solo lectura: correrla no debe haber cambiado nada en las bases.
    assert registry.get_pattern("gap_alto_technology").state == "En observación"
    assert knowledge.events.count() == 32
    assert knowledge.predictions.count() == 32

    knowledge.close()
    registry.close()

    print("\n" + "=" * 60)
    print("OK: LearningEngine orquesta Accuracy Tracker, Pattern Evolution y")
    print("    Calibration Advisor correctamente, sin escribir nada en ningún lado.")


if __name__ == "__main__":
    test_learning_engine_facade()
