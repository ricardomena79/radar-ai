"""Estadísticas del panel "Atlas Learning".

Solo lee lo que ya está guardado en la Knowledge Base (EventStore,
PatternRegistry) -- no calcula nada nuevo, no fabrica ningún número. La
única excepción es la base histórica: un hecho declarado (Atlas arrancó con
~30 días de historia), no algo que se pueda medir desde el código, así que
se expone como constante configurable, claramente marcada como tal.
"""

from datetime import datetime, timezone
from typing import Any, Dict

from atlas.engine.score_engine import env_float
from atlas.knowledge import EXPLOSION, EventStore, PatternRegistry

# Días de historia con los que Atlas ya contaba antes de este sistema de
# aprendizaje en vivo (dato declarado, no medido: ver docstring del módulo).
BASELINE_DAYS = env_float("ATLAS_LEARNING_BASELINE_DAYS", 30)

# Meta de días totales (base histórica + días en vivo) para considerar el
# aprendizaje "maduro" (100%). No hay todavía evidencia real para fijar este
# número con precisión -- es un punto de partida configurable, pensado para
# ajustarse con la experiencia real de operación.
TARGET_TOTAL_DAYS = env_float("ATLAS_LEARNING_TARGET_TOTAL_DAYS", 180)

# Mínimo de resultados reales conocidos para que un patrón cuente como
# "confirmado" -- mismo criterio de tamaño de muestra mínimo que ya usa
# atlas/learning/accuracy_tracker.py (MIN_SAMPLE_SIZE = 10), reutilizado acá
# en vez de inventar un umbral nuevo.
PATTERN_CONFIRMATION_MIN_OUTCOMES = env_float("ATLAS_PATTERN_CONFIRMATION_MIN_OUTCOMES", 10)

# Mínimo de recomendaciones evaluadas para salir del estado "Entrenando".
MIN_EVALUATED_FOR_LEARNING = env_float("ATLAS_LEARNING_MIN_EVALUATED", 10)

ESTADO_ENTRENANDO = "Entrenando"
ESTADO_APRENDIENDO = "Aprendiendo"
ESTADO_OPERATIVO = "Operativo"


def _learning_status(evaluated: int, learning_pct: float) -> str:
    if evaluated < MIN_EVALUATED_FOR_LEARNING:
        return ESTADO_ENTRENANDO
    if learning_pct >= 100:
        return ESTADO_OPERATIVO
    return ESTADO_APRENDIENDO


def get_learning_stats(last_scan_generated_at: str = None) -> Dict[str, Any]:
    """Arma el snapshot completo del panel Atlas Learning a partir de datos
    ya persistidos -- ningún valor de este dict se inventa."""
    event_store = EventStore()
    pattern_registry = PatternRegistry()
    try:
        events_registered = event_store.count()
        live_days = event_store.distinct_dates_count()
        recommendations_evaluated = event_store.count_evaluated(event_type=EXPLOSION)
        wins = event_store.count_wins(event_type=EXPLOSION)
        historical_accuracy = (
            round(wins / recommendations_evaluated * 100, 1) if recommendations_evaluated > 0 else None
        )

        patterns = pattern_registry.list_patterns()
        patterns_detected = len(patterns)
        patterns_confirmed = sum(
            1 for p in patterns if p.evidence.get("outcomes", 0) >= PATTERN_CONFIRMATION_MIN_OUTCOMES
        )

        learning_pct = round(min(100.0, (BASELINE_DAYS + live_days) / TARGET_TOTAL_DAYS * 100), 1)
        status = _learning_status(recommendations_evaluated, learning_pct)

        return {
            "learning_percent": learning_pct,
            "baseline_days": int(BASELINE_DAYS),
            "baseline_loaded": True,  # la base histórica es un hecho declarado, siempre completa
            "live_learning_days": live_days,
            "target_total_days": int(TARGET_TOTAL_DAYS),
            "events_registered": events_registered,
            "patterns_detected": patterns_detected,
            "patterns_confirmed": patterns_confirmed,
            "recommendations_evaluated": recommendations_evaluated,
            "historical_accuracy": historical_accuracy,
            "last_updated": last_scan_generated_at or datetime.now(timezone.utc).isoformat(),
            "status": status,
        }
    finally:
        event_store.close()
        pattern_registry.close()
