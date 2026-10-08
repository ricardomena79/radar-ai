"""Probabilidad medida de alcanzar umbrales fijos (+2% / +5% / +10%) por
condición `(direction, alert_stage)`.

En vez de un único "% proyectado" (una mediana, que por definición acierta
cerca de la mitad de las veces), cada condición muestra qué fracción de sus
casos pasados REALES llegó a cada umbral en algún momento del día después de
la detección (`candidate_outcome.max_return_after_detection_pct`).

Solo lectura y puramente informativo: no participa en gates, `estado_final`
ni en la predicción congelada que audita Precisión de Magnitud. Mismas
fuentes y filtros que la experiencia propia v2 (`alert_stage_log` +
`candidate_outcome` con `is_final=1` y `confiable_para_aprendizaje=1`),
con corte estricto `market_date < as_of_date` (sin usar el día en curso).
"""

import time
from typing import Any, Dict, List, Optional, Tuple

from atlas_live.learning import live_experience_scoring as les
from atlas_live.radar import candidate_registry as reg

THRESHOLDS_PCT: Tuple[float, ...] = (2.0, 5.0, 10.0)
CACHE_TTL_SECONDS = 1800.0

GroupKey = Tuple[str, str]

_cached: Optional[Tuple[str, float, Dict[GroupKey, Dict[str, Any]]]] = None


def compute_threshold_table(
    rows: List[Dict[str, Any]], thresholds: Tuple[float, ...] = THRESHOLDS_PCT,
) -> Dict[GroupKey, Dict[str, Any]]:
    """Agrupa `rows` (cada una con `direction`, `timing_deteccion` = el
    `alert_stage`, y `max_advance_pct`) y mide, por grupo, cuántos casos
    alcanzaron cada umbral. Las filas sin `max_advance_pct`, dirección o
    etapa se descartan -- nunca se imputan."""
    groups: Dict[GroupKey, List[float]] = {}
    for r in rows:
        direction, stage, mx = r.get("direction"), r.get("timing_deteccion"), r.get("max_advance_pct")
        if not direction or not stage or mx is None:
            continue
        groups.setdefault((direction, stage), []).append(float(mx))

    table: Dict[GroupKey, Dict[str, Any]] = {}
    for key, values in groups.items():
        n = len(values)
        por_umbral: Dict[str, Dict[str, Any]] = {}
        for t in thresholds:
            hits = sum(1 for v in values if v >= t)
            ci = reg.wilson_confidence_interval(hits, n)
            por_umbral[f"{t:g}"] = {
                "umbral_pct": t,
                "aciertos": hits,
                "pct": round(100.0 * hits / n, 1),
                "ci_inferior": ci[0] if ci else None,
                "ci_superior": ci[1] if ci else None,
            }
        table[key] = {
            "direction": key[0],
            "stage": key[1],
            "n": n,
            "validation_state": reg.precision_validation_state(n),
            "umbrales": por_umbral,
        }
    return table


def get_cached_threshold_table(
    as_of_date: str, ttl_seconds: float = CACHE_TTL_SECONDS,
) -> Dict[GroupKey, Dict[str, Any]]:
    """Tabla recalculada como mucho cada `ttl_seconds`, y siempre que cambie
    `as_of_date`. Si todavía no hay experiencia, devuelve `{}`."""
    global _cached
    now = time.monotonic()
    if _cached is not None:
        cached_date, cached_at, table = _cached
        if cached_date == as_of_date and now - cached_at < ttl_seconds:
            return table
    table = compute_threshold_table(les._load_rows_from_db_by_stage(as_of_date))
    _cached = (as_of_date, now, table)
    return table


def lookup(table: Dict[GroupKey, Dict[str, Any]], direction: Optional[str], stage: Optional[str]) -> Optional[Dict[str, Any]]:
    if not direction or not stage:
        return None
    return table.get((direction, stage))


def _reset_cache_for_tests() -> None:
    global _cached
    _cached = None
