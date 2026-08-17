"""Algoritmo estadístico de evaluación de Atlas (2026-08-17, Fase 3).

Convierte la Base Histórica (construida sobre el universo de mercado
completo -- ver `atlas_live/reference/`) en una tabla de referencia
consultable: dado un candidato con `(direction, timing_deteccion,
features)`, responde con evidencia real (`n`, aciertos, %) qué tan seguido,
históricamente, condiciones así llegaron a +20/+50/+100%, y dónde esas
mismas condiciones fallaron (falso positivo) y qué tan mal salió.

Reutiliza los primitivos de `experiments.py` (`_tercile_cuts`,
`_bucket_of_row`, `BucketStats`) pero SIN la restricción walk-forward: acá
no se predice fuera de muestra, es una tabla de referencia estática sobre
TODO el histórico disponible -- la separación anti-leakage temporal ya la
resuelve `daily_reference.py` al construir features/outcome (cada fila usa
solo datos hasta su propia fecha).

Standalone: no se importa desde `candidate_gates.py`, `candidate_tracker.py`
ni `decision_engine.py`. Conectar esto al radar en vivo es una decisión
aparte, pendiente de aprobación explícita.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from atlas_live.learning.experiments import (
    DIRECTIONS,
    MIN_PRIOR_ROWS_FOR_CUTS,
    BucketStats,
    _bucket_of_row,
    _tercile_cuts,
)

GroupKey = Tuple[str, str]  # (direction, timing_deteccion)


@dataclass
class GroupReference:
    group: GroupKey
    cuts: Dict[str, Optional[Sequence[float]]]
    buckets: Dict[str, BucketStats]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "direction": self.group[0],
            "timing_deteccion": self.group[1],
            "cuts": {k: (list(v) if v is not None else None) for k, v in self.cuts.items()},
            "buckets": {k: b.to_dict() for k, b in self.buckets.items()},
        }


def compute_reference_table(rows: List[Dict[str, Any]], feature_cols: Sequence[str],
                             min_rows: int = MIN_PRIOR_ROWS_FOR_CUTS) -> Dict[GroupKey, GroupReference]:
    """Tabla de referencia estática: agrupa TODO el histórico por
    `(direction, timing_deteccion)`, calcula cortes de tercil de
    `feature_cols` DENTRO de cada grupo, y acumula `BucketStats`
    (alto/medio/bajo/población_total) de `max_advance_pct`. `min_rows` es
    el piso de filas para que un corte tenga sentido (mismo criterio que
    `experiments.py`) -- grupos más chicos quedan con `cuts=None`, nunca se
    inventa un corte con poca muestra."""
    grupos: Dict[GroupKey, List[Dict[str, Any]]] = {}
    for r in rows:
        direction = r.get("direction")
        timing = r.get("timing_deteccion")
        if direction not in DIRECTIONS or not timing:
            continue
        grupos.setdefault((direction, timing), []).append(r)

    table: Dict[GroupKey, GroupReference] = {}
    for key, group_rows in grupos.items():
        cuts: Dict[str, Optional[Sequence[float]]] = {}
        for col in feature_cols:
            valores = [r[col] for r in group_rows if r.get(col) is not None]
            c = _tercile_cuts(valores)
            cuts[col] = c if (c is not None and len(group_rows) >= min_rows) else None

        buckets = {
            "poblacion_total": BucketStats("poblacion_total"), "alto": BucketStats("alto"),
            "medio": BucketStats("medio"), "bajo": BucketStats("bajo"),
        }
        for r in group_rows:
            buckets["poblacion_total"].add(r.get("max_advance_pct"))
            bucket = _bucket_of_row(r, feature_cols, cuts)
            if bucket is not None:
                buckets[bucket].add(r.get("max_advance_pct"))

        table[key] = GroupReference(group=key, cuts=cuts, buckets=buckets)

    return table


def score_candidate(table: Dict[GroupKey, GroupReference], direction: str, timing_deteccion: str,
                     feature_values: Dict[str, float]) -> Dict[str, Any]:
    """Evidencia histórica real para un candidato en vivo -- nunca decide
    nada, solo reporta lo que YA pasó históricamente en condiciones
    similares. `n=0`/`bucket=None` si el grupo no existe -- nunca se
    inventa una probabilidad sin evidencia."""
    ref = table.get((direction, timing_deteccion))
    if ref is None:
        return {"direction": direction, "timing_deteccion": timing_deteccion, "grupo_existe": False,
                "bucket": None, "n": 0, "aciertos_20": 0, "aciertos_50": 0, "aciertos_100": 0,
                "pct_20": None, "pct_50": None, "pct_100": None}

    feature_cols = list(ref.cuts.keys())
    bucket = _bucket_of_row(feature_values, feature_cols, ref.cuts)
    if bucket is None:
        # sin cortes suficientes en este grupo (o falta algún feature) --
        # se reporta la población total del grupo, nunca un bucket inventado.
        bucket_label, stats = "poblacion_total", ref.buckets["poblacion_total"]
    else:
        bucket_label, stats = bucket, ref.buckets[bucket]

    d = stats.to_dict()
    d.pop("label", None)
    return {"direction": direction, "timing_deteccion": timing_deteccion, "grupo_existe": True,
            "bucket": bucket_label, **d}


def false_positive_report(table: Dict[GroupKey, GroupReference], rows: List[Dict[str, Any]],
                           min_rows: int = MIN_PRIOR_ROWS_FOR_CUTS) -> List[Dict[str, Any]]:
    """Por cada grupo con bucket 'alto' (la condición que se marcaría como
    favorable) y piso de muestra suficiente, reporta cuántas veces esa
    condición NO llegó a +20% -- el falso positivo concreto, con `n`
    explícito -- y qué tan mal salió en promedio (`max_drawdown_pct` real
    de los fallos, nunca solo un porcentaje de acierto aislado)."""
    grupos: Dict[GroupKey, List[Dict[str, Any]]] = {}
    for r in rows:
        d, t = r.get("direction"), r.get("timing_deteccion")
        if d not in DIRECTIONS or not t:
            continue
        grupos.setdefault((d, t), []).append(r)

    out: List[Dict[str, Any]] = []
    for key, ref in sorted(table.items()):
        alto = ref.buckets.get("alto")
        if alto is None or alto.n < min_rows:
            continue
        fallos_20 = alto.n - alto.aciertos_20
        feature_cols = list(ref.cuts.keys())
        drawdowns = []
        for r in grupos.get(key, []):
            if _bucket_of_row(r, feature_cols, ref.cuts) != "alto":
                continue
            if (r.get("max_advance_pct") or 0) >= 20:
                continue  # fue acierto, no un fallo
            dd = r.get("max_drawdown_pct")
            if dd is not None:
                drawdowns.append(dd)
        out.append({
            "direction": key[0], "timing_deteccion": key[1],
            "n": alto.n, "aciertos_20": alto.aciertos_20, "fallos_20": fallos_20,
            "pct_fallo_20": round(100 * fallos_20 / alto.n, 1) if alto.n else None,
            "n_con_drawdown_dato": len(drawdowns),
            "max_drawdown_pct_promedio_en_fallos": round(sum(drawdowns) / len(drawdowns), 1) if drawdowns else None,
        })
    return out


REPORT_FEATURE_COLS = ("volatility_14d_pct", "daily_range_pct")


def _load_rows_from_db() -> List[Dict[str, Any]]:
    """Lee `daily_features` UNIDO con `daily_outcome` (mismo symbol+date) --
    mismo join que ya usa `scripts/run_experiments_abc.py` -- y con
    `reference_checkpoint` (por símbolo, LEFT JOIN) para traer
    `racional_available` a cada fila (2026-08-17, comparación
    Racional-disponible vs no-disponible). Import local: evita que este
    módulo dependa de sqlite/DB_PATH cuando se usa solo con filas
    sintéticas (como en los tests)."""
    import sqlite3

    from atlas_live.reference.reference_registry import DB_PATH

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT f.*, o.max_advance_pct, o.max_drawdown_pct, o.days_to_max, "
            "o.continuo, o.outcome_direction, c.racional_available "
            "FROM daily_features f JOIN daily_outcome o "
            "ON o.symbol = f.symbol AND o.date = f.date "
            "LEFT JOIN reference_checkpoint c ON c.symbol = f.symbol"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def generate_report(feature_cols: Sequence[str] = REPORT_FEATURE_COLS,
                     min_rows: int = MIN_PRIOR_ROWS_FOR_CUTS) -> Dict[str, Any]:
    """Reporte completo sobre la Base Histórica real ya construida (todo el
    universo de mercado procesado hasta el momento) -- evidencia real,
    nunca inventada: si la base todavía no tiene datos, `n_filas=0` y las
    listas quedan vacías, no se fabrica ningún hallazgo."""
    rows = _load_rows_from_db()
    table = compute_reference_table(rows, feature_cols, min_rows=min_rows)
    return {
        "n_filas": len(rows),
        "n_simbolos": len({r["symbol"] for r in rows}),
        "feature_cols": list(feature_cols),
        "min_rows_piso_de_muestra": min_rows,
        "tabla_por_grupo": [ref.to_dict() for _, ref in sorted(table.items())],
        "falsos_positivos": false_positive_report(table, rows, min_rows=min_rows),
    }
