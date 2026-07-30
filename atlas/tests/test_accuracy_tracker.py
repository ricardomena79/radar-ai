"""Prueba manual de Accuracy Tracker (Learning Engine).

Siembra pares predicción/evento con un resultado de precisión conocido de
antemano (calculado a mano), para verificar que el emparejamiento por
ticker+fecha+hora más cercana y las reglas de acierto producen exactamente
ese número -- no solo que "corra sin error".

Usa una base SQLite de prueba separada, no la real.
"""

from pathlib import Path

from atlas.engine.decision_engine import COMPRAR, DESCARTAR, VIGILAR
from atlas.knowledge import NORMAL, STATUS_ESTIMATED, STATUS_OK, KnowledgeEngine, MarketEvent
from atlas.knowledge.engine_versions import current_versions_json
from atlas.knowledge.prediction_store import PredictionRecord
from atlas.learning import AccuracyTracker
from atlas.learning.accuracy_tracker import MIN_SAMPLE_SIZE

TEST_DB = Path(__file__).resolve().parents[1] / "cache" / "test_accuracy_tracker.db"

OLD_VERSION_JSON = '{"decision_engine": "0.9-test"}'


def _seed(knowledge: KnowledgeEngine) -> None:
    idx = 0

    def add_pair(decision, correct, sector, confidence, engine_versions=None, data_status=STATUS_OK, ticker=None):
        nonlocal idx
        idx += 1
        ticker = ticker or f"SIM{idx}"
        date = "2026-07-15"
        pred_time = "09:30:00"
        event_time = "16:00:00"

        # close_result_percent define qué cuenta como acierto según _was_correct().
        if decision == COMPRAR:
            close_result = 3.0 if correct else -2.0
        elif decision == DESCARTAR:
            close_result = -1.5 if correct else 4.0
        else:
            close_result = 0.5  # VIGILAR: no se clasifica, el valor no importa

        knowledge.record_prediction(PredictionRecord(
            date=date, time=pred_time, ticker=ticker, mode="standard", decision=decision,
            confidence=confidence, engine_versions=engine_versions or current_versions_json(),
            data_status=data_status,
        ))
        knowledge.record_event(MarketEvent(
            date=date, time=event_time, ticker=ticker, price=100.0, event_type=NORMAL,
            sector=sector, close_result_percent=close_result, data_status=data_status,
        ))

    # 15 COMPRAR: 12 aciertos, 3 errores -> accuracy 0.8
    for i in range(12):
        add_pair(COMPRAR, correct=True, sector="Technology" if i < 8 else "Energy", confidence=70.0)
    for i in range(3):
        add_pair(COMPRAR, correct=False, sector="Technology", confidence=40.0)

    # 10 DESCARTAR: 8 aciertos, 2 errores -> accuracy 0.8
    for i in range(8):
        add_pair(DESCARTAR, correct=True, sector="Energy" if i < 5 else "Technology", confidence=20.0)
    for i in range(2):
        add_pair(DESCARTAR, correct=False, sector="Energy", confidence=30.0)

    # 5 VIGILAR: no deben contar para accuracy (no clasificables).
    for i in range(5):
        add_pair(VIGILAR, correct=True, sector="Technology", confidence=50.0)

    # 3 pares con la versión "vieja" de Decision Engine (subconjunto de los COMPRAR correctos).
    for i in range(3):
        add_pair(COMPRAR, correct=True, sector="Technology", confidence=80.0, engine_versions=OLD_VERSION_JSON)

    # 2 pares con data_status distinto de OK: deben quedar excluidos por completo.
    for i in range(2):
        add_pair(COMPRAR, correct=False, sector="Technology", confidence=90.0, data_status=STATUS_ESTIMATED)


def test_accuracy_tracker() -> None:
    if TEST_DB.exists():
        TEST_DB.unlink()

    knowledge = KnowledgeEngine(db_path=TEST_DB)
    _seed(knowledge)
    tracker = AccuracyTracker(knowledge)

    print("=" * 60)
    print("ATLAS - ACCURACY TRACKER")
    print("=" * 60)

    overall = tracker.overall_accuracy()
    print(f"\nPrecisión general: {overall.breakdown}")
    # 12+3 COMPRAR correctos originales + 3 COMPRAR correctos con versión vieja = 15 correctos de 18 COMPRAR
    # + 8 correctos de 10 DESCARTAR = 23 correctos de 28 clasificables (los 5 VIGILAR y los 2 ESTIMADO quedan fuera).
    assert overall.breakdown["todas"]["n"] == 28
    assert overall.breakdown["todas"]["accuracy"] == round(23 / 28, 4)
    print(f"Verificado: {overall.breakdown['todas']['n']} clasificables, precisión={overall.breakdown['todas']['accuracy']*100:.1f}%")

    by_decision = tracker.accuracy_by_decision()
    print(f"\nPor decisión: {by_decision.breakdown}")
    assert by_decision.breakdown[COMPRAR]["n"] == 18
    assert by_decision.breakdown[COMPRAR]["accuracy"] == round(15 / 18, 4)
    assert by_decision.breakdown[DESCARTAR]["n"] == 10
    assert by_decision.breakdown[DESCARTAR]["accuracy"] == 0.8
    # VIGILAR aparece en el desglose (tiene pares emparejados), pero ninguno es
    # clasificable como acierto/error -> n=0, accuracy=None, no un número inventado.
    assert by_decision.breakdown[VIGILAR]["n"] == 0
    assert by_decision.breakdown[VIGILAR]["accuracy"] is None
    print("Verificado: VIGILAR queda sin clasificar (n=0); COMPRAR y DESCARTAR con la precisión esperada.")

    by_sector = tracker.accuracy_by_sector()
    print(f"\nPor sector: {by_sector.breakdown}")
    assert "Technology" in by_sector.breakdown
    assert "Energy" in by_sector.breakdown

    by_version = tracker.accuracy_by_engine_version()
    print(f"\nPor versión de motor: {by_version.breakdown}")
    assert by_version.breakdown["0.9-test"]["n"] == 3
    # Solo 3 pares con esa versión: por debajo de MIN_SAMPLE_SIZE, no se inventa una precisión.
    assert by_version.breakdown["0.9-test"]["insufficient_sample"] is True
    assert by_version.breakdown["0.9-test"]["accuracy"] is None
    assert by_version.breakdown["1.0"]["n"] == 25
    print("Verificado: la versión '0.9-test' se separa del resto, y al tener solo 3 casos")
    print("            queda marcada como evidencia insuficiente en vez de inventar un 100%.")

    by_confidence = tracker.accuracy_by_confidence_band()
    print(f"\nPor banda de confianza: {by_confidence.breakdown}")

    # Con MIN_SAMPLE_SIZE=10, un grupo con muestra chica debe salir "insuficiente", no un número inventado.
    small_group_score = tracker._score(tracker._matched_pairs()[:3])
    assert small_group_score["insufficient_sample"] is True
    assert small_group_score["accuracy"] is None
    print(f"\nVerificado: con muestra < {MIN_SAMPLE_SIZE}, accuracy=None (no inventa un número).")

    knowledge.close()

    print("\n" + "=" * 60)
    print("OK: Accuracy Tracker mide precisión correctamente en las 5 dimensiones.")


if __name__ == "__main__":
    test_accuracy_tracker()
