"""Conocimiento a partir de la EXPERIENCIA PROPIA de Atlas (2026-08-25,
Fase 1/5 del circuito de aprendizaje, autorizado explícitamente).

A diferencia de `historical_scoring.py` (que analiza `historical_reference.db`,
un dataset de mercado ~13.000 símbolos AJENO a lo que Atlas mismo detectó),
este módulo analiza las PROPIAS detecciones y resultados de Atlas:
`candidate_detection` JOIN `candidate_outcome`.

Reutiliza deliberadamente, sin duplicar:
  - `historical_scoring.compute_reference_table()` -- agrupación
    (direction, timing_deteccion) + terciles de features + `BucketStats`,
    genérica sobre cualquier lista de filas con esas claves.
  - `experiments.MIN_PRIOR_ROWS_FOR_CUTS` -- piso de muestra para un corte
    de tercil.
  - `candidate_registry.wilson_confidence_interval()`/`precision_validation_state()`
    -- mismo intervalo de Wilson y los mismos 3 estados (MUESTRA_INSUFICIENTE/
    EN_VALIDACION/VALIDACION_ROBUSTA) ya usados en Precisión de Magnitud.

SOLO análisis en memoria -- no crea tabla nueva, no persiste nada, no
modifica `candidate_gates.py`/`priority_classifier.py`/`candidate_tracker.py`/
`decision_engine.py`, no cambia ninguna decisión/score/ranking/semáforo en
vivo. Es exclusivamente diagnóstico, standalone -- misma regla ya aplicada a
`historical_scoring.py`.

Anti-leakage temporal (la pieza que `historical_scoring.py` explícitamente
NO tiene, declarado en su propio docstring): la tabla de conocimiento para
una fecha `as_of_date` se calcula EXCLUSIVAMENTE con detecciones de fecha
< `as_of_date` -- nunca de esa fecha ni de fechas posteriores. El filtro
vive en dos capas independientes -- el `WHERE` SQL de `_load_rows_from_db()`
Y un filtro Python redundante dentro de `compute_own_experience_table()`
que se aplica a CUALQUIER `rows` que reciba (de la DB o inyectada para
tests) -- para que ninguna fila "del futuro" pueda sobrevivir sin importar
su origen.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from atlas_live.learning import experiments
from atlas_live.learning.historical_scoring import compute_reference_table
from atlas_live.radar.candidate_registry import DB_PATH, precision_validation_state, wilson_confidence_interval

# Métrica principal de esta Fase 1 (pedido explícito): +20%. La salida ya
# incluye n_aciertos_50/pct_50/n_aciertos_100/pct_100 (gratis, `BucketStats`
# ya los calcula) para que Fase 2/3 pueda agregar wilson/lift/baseline de
# +50/+100 y tiempo-hasta-objetivo repitiendo el MISMO patrón de abajo, sin
# rehacer la carga de datos ni la agrupación.
UMBRAL_PRINCIPAL_PCT = 20


def _load_rows_from_db(as_of_date: str) -> List[Dict[str, Any]]:
    """Lee `candidate_detection` UNIDO con `candidate_outcome` -- mismo join
    `(ticker, market_date)` que ya usa `list_all_evaluated_candidates()` --
    filtrado a resultados FINALES (`is_final=1`), de calidad suficiente para
    aprender (`confiable_para_aprendizaje=1`, mismo piso de $50.000 de
    dollar-volume ya establecido en todo el proyecto), y estrictamente
    anteriores a `as_of_date` (anti-leakage). Import local de sqlite3, mismo
    criterio que `historical_scoring._load_rows_from_db()`: este módulo no
    depende de la DB cuando se usa con `rows` sintéticas (tests)."""
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT d.ticker AS ticker, d.market_date AS market_date,
                      d.direction_at_detection AS direction,
                      d.phase_tag AS timing_deteccion,
                      d.volatility_14d_pct_at_detection AS volatility_14d_pct,
                      d.daily_range_pct_at_detection AS daily_range_pct,
                      o.max_return_after_detection_pct AS max_advance_pct
               FROM candidate_detection d
               JOIN candidate_outcome o ON o.ticker = d.ticker AND o.market_date = d.market_date
               WHERE o.is_final = 1 AND o.confiable_para_aprendizaje = 1 AND d.market_date < ?""",
            (as_of_date,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def compute_own_experience_table(
    as_of_date: str,
    feature_cols: Sequence[str] = ("volatility_14d_pct",),
    min_rows: int = experiments.MIN_PRIOR_ROWS_FOR_CUTS,
    rows: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Tabla de conocimiento sobre la EXPERIENCIA PROPIA de Atlas, walk-forward
    segura para `as_of_date`. `rows=None` (uso real) carga desde la DB;
    pasar `rows` explícito permite testear la lógica estadística sin tocar
    ninguna base de datos.

    SOLO MIDE -- no decide nada, no se conecta a ningún gate/score/ranking.
    Devuelve una lista de dicts, una por combinación
    `(direction, timing_deteccion, bucket)` con evidencia REAL -- nunca
    inventa una fila para un grupo sin datos (mismo criterio que
    `historical_scoring.score_candidate()`: sin evidencia, no hay fila)."""
    if rows is None:
        rows = _load_rows_from_db(as_of_date)

    # Defensa en profundidad anti-leakage: se re-aplica el filtro temporal
    # acá, sobre CUALQUIER origen de `rows` (DB o sintéticas) -- ninguna
    # fila con `market_date >= as_of_date` (o sin `market_date`) puede
    # sobrevivir a esta función.
    rows = [r for r in rows if r.get("market_date") is not None and r["market_date"] < as_of_date]

    table = compute_reference_table(rows, feature_cols, min_rows=min_rows)

    # Baseline poblacional (2026-08-25): tasa de +20% sobre TODAS las filas
    # walk-forward-seguras cargadas para este `as_of_date`, sin segmentar --
    # referencia contra la que se compara cada grupo/bucket (lift).
    evaluables_con_dato = [r for r in rows if r.get("max_advance_pct") is not None]
    n_baseline = len(evaluables_con_dato)
    n_aciertos_baseline = sum(1 for r in evaluables_con_dato if r["max_advance_pct"] >= UMBRAL_PRINCIPAL_PCT)
    baseline_pct_20 = round(100 * n_aciertos_baseline / n_baseline, 2) if n_baseline else None

    computed_at = datetime.now(timezone.utc).isoformat()
    salida: List[Dict[str, Any]] = []
    for (direction, timing), ref in table.items():
        for bucket_label, stats in ref.buckets.items():
            if stats.n == 0:
                continue  # sin evidencia real para este bucket -- no se reporta una fila vacía
            d = stats.to_dict()
            n = d["n"]
            n_aciertos_20 = d["aciertos_20"]
            pct_20 = d["pct_20"]
            wilson = wilson_confidence_interval(n_aciertos_20, n)
            lift_20 = round(pct_20 / baseline_pct_20, 3) if (pct_20 is not None and baseline_pct_20) else None
            salida.append({
                "direction": direction, "timing_deteccion": timing, "bucket": bucket_label,
                "n_evaluables": n,
                "n_aciertos_20": n_aciertos_20, "pct_20": pct_20,
                "wilson_lower_bound_20_pct": wilson[0] if wilson else None,
                "wilson_upper_bound_20_pct": wilson[1] if wilson else None,
                "baseline_pct_20": baseline_pct_20,
                "lift_20": lift_20,
                "mediana_max_advance_pct": d["mediana_max_advance_pct"],
                "validation_state": precision_validation_state(n),
                "computed_as_of": as_of_date,
                "computed_at": computed_at,
                # Extensible (pedido explícito) -- +50/+100 ya calculados por
                # BucketStats, sin costo adicional; Fase 2/3 puede agregar
                # wilson/lift para estos mismos campos repitiendo el patrón
                # de arriba, sin tocar la carga de datos ni la agrupación.
                "n_aciertos_50": d["aciertos_50"], "pct_50": d["pct_50"],
                "n_aciertos_100": d["aciertos_100"], "pct_100": d["pct_100"],
            })
    return salida
