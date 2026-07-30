"""Prueba manual del núcleo de conocimiento (atlas/knowledge/).

Usa una base SQLite de prueba separada (atlas/cache/test_atlas_knowledge.db,
ya ignorada por git) para no mezclar eventos sintéticos con la base real de
Atlas. Registra eventos de ejemplo de los 4 tipos, algunas predicciones, y
verifica: estadísticas, búsqueda de patrones similares y comparación de ADN
entre símbolos.
"""

from pathlib import Path

from atlas.knowledge import (
    COLLAPSE,
    EXPLOSION,
    FALSE_BREAKOUT,
    NORMAL,
    KnowledgeEngine,
    MarketEvent,
    PredictionRecord,
)

TEST_DB_PATH = Path(__file__).resolve().parents[1] / "cache" / "test_atlas_knowledge.db"


def _sample_events():
    return [
        MarketEvent(
            date="2026-07-28", time="09:35:00", ticker="SOXL", sector="Technology", industry="Semiconductors",
            price=110.32, gap_percent=16.7, rvol=2.3, volume=135_000_000, float_shares=None, market_cap=None,
            atlas_score=64.7, momentum_score=64.7, money_flow_score=None, decision="VIGILAR",
            max_result_percent=28.4, close_result_percent=19.9, event_type=EXPLOSION,
        ),
        MarketEvent(
            date="2026-07-25", time="10:05:00", ticker="MARA", sector="Financial Services", industry="Capital Markets",
            price=12.10, gap_percent=22.1, rvol=3.1, volume=80_000_000, float_shares=350_000_000, market_cap=4_200_000_000,
            atlas_score=71.2, momentum_score=78.5, money_flow_score=61.0, decision="COMPRAR",
            max_result_percent=34.0, close_result_percent=24.6, event_type=EXPLOSION,
        ),
        MarketEvent(
            date="2026-07-24", time="14:50:00", ticker="KGC", sector="Basic Materials", industry="Gold",
            price=23.17, gap_percent=-1.4, rvol=0.5, volume=6_000_000, float_shares=None, market_cap=None,
            atlas_score=38.0, momentum_score=30.5, money_flow_score=None, decision="DESCARTAR",
            max_result_percent=-2.1, close_result_percent=-9.8, event_type=COLLAPSE,
        ),
        MarketEvent(
            date="2026-07-23", time="09:40:00", ticker="CCJ", sector="Energy", industry="Uranium",
            price=84.71, gap_percent=-3.2, rvol=1.2, volume=4_500_000, float_shares=None, market_cap=None,
            atlas_score=41.5, momentum_score=33.2, money_flow_score=35.0, decision="DESCARTAR",
            max_result_percent=0.5, close_result_percent=-8.4, event_type=COLLAPSE,
        ),
        MarketEvent(
            date="2026-07-22", time="09:45:00", ticker="PLTR", sector="Technology", industry="Software - Infrastructure",
            price=124.80, gap_percent=4.5, rvol=2.0, volume=41_000_000, float_shares=2_100_000_000, market_cap=294_000_000_000,
            atlas_score=58.0, momentum_score=55.0, money_flow_score=48.0, decision="VIGILAR",
            max_result_percent=6.1, close_result_percent=0.4, event_type=FALSE_BREAKOUT,
        ),
        MarketEvent(
            date="2026-07-21", time="11:15:00", ticker="AAPL", sector="Technology", industry="Consumer Electronics",
            price=192.05, gap_percent=0.3, rvol=0.9, volume=48_000_000, float_shares=14_600_000_000, market_cap=4_900_000_000_000,
            atlas_score=52.0, momentum_score=48.0, money_flow_score=45.0, decision="VIGILAR",
            max_result_percent=1.2, close_result_percent=0.6, event_type=NORMAL,
        ),
        MarketEvent(
            date="2026-07-18", time="13:00:00", ticker="NVDA", sector="Technology", industry="Semiconductors",
            price=192.05, gap_percent=-0.5, rvol=1.0, volume=136_000_000, float_shares=23_200_000_000, market_cap=4_700_000_000_000,
            atlas_score=50.0, momentum_score=51.0, money_flow_score=42.0, decision="VIGILAR",
            max_result_percent=1.8, close_result_percent=-0.3, event_type=NORMAL,
        ),
    ]


def test_knowledge_engine() -> None:
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()

    engine = KnowledgeEngine(db_path=TEST_DB_PATH)

    print("=" * 60)
    print("ATLAS - KNOWLEDGE ENGINE")
    print("=" * 60)

    events = _sample_events()
    event_ids = [engine.record_event(event) for event in events]
    assert len(event_ids) == len(events)
    assert len(set(event_ids)) == len(events)
    print(f"Eventos registrados: {len(event_ids)}")

    predictions = [
        PredictionRecord(
            date="2026-07-28", time="09:34:00", ticker="SOXL", mode="market_open",
            decision="VIGILAR", confidence=58.0, atlas_score=54.7, momentum_score=64.8, money_flow_score=None,
        ),
        PredictionRecord(
            date="2026-07-25", time="10:04:00", ticker="MARA", mode="market_open",
            decision="COMPRAR", confidence=74.0, atlas_score=71.2, momentum_score=78.5, money_flow_score=61.0,
        ),
        PredictionRecord(
            date="2026-07-24", time="14:49:00", ticker="KGC", mode="standard",
            decision="DESCARTAR", confidence=22.0, atlas_score=38.0, momentum_score=30.5, money_flow_score=None,
        ),
    ]
    prediction_ids = [engine.record_prediction(p) for p in predictions]
    assert len(set(prediction_ids)) == len(predictions)
    print(f"Predicciones registradas: {len(prediction_ids)}")

    stats = engine.get_statistics()
    assert stats.total_events == len(events)
    assert stats.total_predictions == len(predictions)

    print("\n--- ESTADÍSTICAS ---")
    print(f"Total de eventos       : {stats.total_events}")
    print(f"Eventos por tipo        : {stats.events_by_type}")
    print(f"Eventos por sector      : {stats.events_by_sector}")
    print(f"Total de predicciones   : {stats.total_predictions}")
    print(f"Predicciones por decisión: {stats.predictions_by_decision}")

    reference = engine.events.get_event(event_ids[0])  # SOXL, EXPLOSION
    similar = engine.find_similar_events(reference, top_n=3)
    assert isinstance(similar, list)

    print(f"\n--- PATRONES SIMILARES A {reference.ticker} (EXPLOSION) ---")
    for candidate, similarity in similar:
        print(f"  {candidate.ticker:6} {candidate.event_type:14} similitud={similarity:5.1f}%  gap={candidate.gap_percent}  rvol={candidate.rvol}")

    dna_soxl = engine.get_symbol_dna("SOXL")
    assert dna_soxl is not None and dna_soxl.sample_size == 1
    print(f"\n--- ADN DE SOXL ---")
    print(f"  Muestras: {dna_soxl.sample_size}  Features: {dna_soxl.features}")

    dna_similarity = engine.compare_dna("AAPL", "NVDA")
    print(f"\n--- COMPARACIÓN DE ADN: AAPL vs NVDA ---")
    print(f"  Similitud: {dna_similarity}%")

    engine.close()

    print("\n" + "=" * 60)
    print("OK: Knowledge Engine funciona correctamente (eventos, predicciones, patrones, ADN, stats).")


if __name__ == "__main__":
    test_knowledge_engine()
