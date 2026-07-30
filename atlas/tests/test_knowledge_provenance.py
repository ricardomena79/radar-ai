"""Prueba manual de la trazabilidad de datos (fuente, hora de captura, estado,
versión de motores) y de las columnas de preparación para el futuro Market
Replay Engine (rank_in_scan, scan_size).

Usa una base SQLite de prueba separada, no la real.
"""

from datetime import datetime, timezone
from pathlib import Path

from atlas.knowledge import (
    NORMAL,
    STATUS_ESTIMATED,
    STATUS_OK,
    SOURCE_CALCULATED,
    SOURCE_YAHOO_FINANCE,
    CURRENT_ENGINE_VERSIONS,
    KnowledgeEngine,
    MarketEvent,
    PredictionRecord,
    current_versions_json,
    parse_versions_json,
)

TEST_DB_PATH = Path(__file__).resolve().parents[1] / "cache" / "test_knowledge_provenance.db"


def test_knowledge_provenance() -> None:
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()

    knowledge = KnowledgeEngine(db_path=TEST_DB_PATH)

    print("=" * 60)
    print("ATLAS - TRAZABILIDAD Y VERSIONES DE MOTORES")
    print("=" * 60)

    captured_at = datetime.now(timezone.utc).isoformat()
    versions_json = current_versions_json()

    event = MarketEvent(
        date="2026-07-30", time="09:45:00", ticker="AAPL", sector="Technology",
        industry="Consumer Electronics", price=338.19, gap_percent=-0.10, rvol=0.87,
        volume=48_852_885, event_type=NORMAL, decision="VIGILAR",
        atlas_score=64.79, momentum_score=51.0, money_flow_score=42.5,
        data_source=SOURCE_YAHOO_FINANCE, captured_at=captured_at, data_status=STATUS_OK,
        engine_versions=versions_json, rank_in_scan=3, scan_size=104,
    )
    event_id = knowledge.record_event(event)

    prediction = PredictionRecord(
        date="2026-07-30", time="09:44:30", ticker="AAPL", mode="standard",
        decision="VIGILAR", confidence=49.0, atlas_score=64.79, momentum_score=51.0,
        money_flow_score=42.5, event_id=event_id,
        data_source=SOURCE_CALCULATED, captured_at=captured_at, data_status=STATUS_OK,
        engine_versions=versions_json, rank_in_scan=3, scan_size=104,
    )
    prediction_id = knowledge.record_prediction(prediction)

    stored_event = knowledge.events.get_event(event_id)
    stored_predictions = knowledge.predictions.get_predictions(ticker="AAPL", limit=1)
    stored_prediction = stored_predictions[0]

    assert stored_event.data_source == SOURCE_YAHOO_FINANCE
    assert stored_event.captured_at == captured_at
    assert stored_event.data_status == STATUS_OK
    assert parse_versions_json(stored_event.engine_versions) == CURRENT_ENGINE_VERSIONS
    assert stored_event.rank_in_scan == 3
    assert stored_event.scan_size == 104

    assert stored_prediction.id == prediction_id
    assert stored_prediction.data_source == SOURCE_CALCULATED
    assert parse_versions_json(stored_prediction.engine_versions) == CURRENT_ENGINE_VERSIONS

    print("\n--- EVENTO CON TRAZABILIDAD COMPLETA ---")
    print(f"  ticker           : {stored_event.ticker}")
    print(f"  data_source      : {stored_event.data_source}")
    print(f"  captured_at      : {stored_event.captured_at}")
    print(f"  data_status      : {stored_event.data_status}")
    print(f"  engine_versions  : {parse_versions_json(stored_event.engine_versions)}")
    print(f"  rank_in_scan     : {stored_event.rank_in_scan} / {stored_event.scan_size}")

    # Un dato ESTIMADO: por ejemplo, si Yahoo no respondió y se usó el
    # último precio conocido como estimación.
    estimated_event = MarketEvent(
        date="2026-07-30", time="09:46:00", ticker="XYZ", price=10.0,
        event_type=NORMAL, data_source=SOURCE_YAHOO_FINANCE, data_status=STATUS_ESTIMATED,
    )
    knowledge.record_event(estimated_event)

    # Validación: un data_status inválido debe rechazarse, no guardarse en silencio.
    try:
        knowledge.record_event(
            MarketEvent(date="2026-07-30", time="09:47:00", ticker="BAD", price=1.0,
                        event_type=NORMAL, data_status="INVENTADO")
        )
        raise AssertionError("Se esperaba ValueError por data_status inválido")
    except ValueError as exc:
        print(f"\nValidación OK: data_status inválido fue rechazado ({exc})")

    stats = knowledge.get_statistics()
    print(f"\nTotal de eventos: {stats.total_events}  (incluye 1 ESTIMADO)")

    knowledge.close()

    print("\n" + "=" * 60)
    print("OK: trazabilidad de datos y versiones de motores funcionan correctamente.")


if __name__ == "__main__":
    test_knowledge_provenance()
