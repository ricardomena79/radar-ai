"""Memoria persistente del CONOCIMIENTO derivado de la experiencia propia de
Atlas (2026-08-25, Fase 2/5 del circuito de aprendizaje, autorizado
explícitamente).

Persiste EXACTAMENTE la salida de
`live_experience_scoring.compute_own_experience_table()` (Fase 1) -- cero
recálculo, cero transformación de la fórmula. Esta capa SOLO guarda y lee;
no decide nada, no se conecta a ningún gate/score/ranking/decisión.

Separación de capas (pedido explícito):
    EXPERIENCIA CRUDA (candidate_detection/candidate_outcome, otra DB)
        → CONOCIMIENTO CALCULADO (live_experience_scoring.py, en memoria)
            → MEMORIA PERSISTENTE DEL CONOCIMIENTO (este módulo)
                → [FUTURA DECISIÓN -- NO EN ESTA FASE, NO CONECTADO TODAVÍA]

DB propia (`live_experience_knowledge.db`, vía `db_path()`, mismo Volume
que los otros 10 archivos `.db` del proyecto) -- deliberadamente SIN
importar nada de `candidate_registry.py` (ni siquiera su `_ensure_column`,
que es idéntico acá pero implementado de forma local e independiente):
esta memoria de conocimiento no debe depender estructuralmente del
registro de candidatas.

APPEND-ONLY, sin excepción: sin UPDATE, sin DELETE, sin UPSERT, sin
`UNIQUE` sobre la identidad conceptual (direction, timing_deteccion,
bucket, computed_as_of, methodology_version) -- cada cálculo, incluidos
los recálculos del mismo día, queda conservado como una fila propia. La
resolución de "cuál es el más reciente" se hace en la LECTURA
(`latest_knowledge_as_of`, por `MAX(computed_at)`), nunca sobrescribiendo
en la escritura.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.config.config import db_path

DB_PATH = db_path("live_experience_knowledge.db", default=Path(__file__).parent)

# Identifica QUÉ fórmula/agrupación produjo una fila -- se sube a mano
# cuando la metodología cambie de verdad (ej. se agrega gates_fired como
# dimensión nueva de agrupación, Fase posterior, no en esta). Filas de
# versiones distintas nunca deben mezclarse al leer/comparar.
METHODOLOGY_VERSION = "v1_direction_timing_volatility_tercile"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_experience_knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    direction TEXT NOT NULL,
    timing_deteccion TEXT NOT NULL,
    bucket TEXT NOT NULL,
    n_evaluables INTEGER NOT NULL,
    n_aciertos_20 INTEGER NOT NULL,
    pct_20 REAL,
    wilson_lower_bound_20_pct REAL,
    wilson_upper_bound_20_pct REAL,
    baseline_pct_20 REAL,
    lift_20 REAL,
    mediana_max_advance_pct REAL,
    n_aciertos_50 INTEGER,
    pct_50 REAL,
    n_aciertos_100 INTEGER,
    pct_100 REAL,
    validation_state TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    computed_as_of TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lek_condicion ON live_experience_knowledge(direction, timing_deteccion, bucket);
CREATE INDEX IF NOT EXISTS idx_lek_as_of ON live_experience_knowledge(computed_as_of);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Migración aditiva local y mínima (deliberadamente NO importada de
    `candidate_registry.py` -- esta memoria de conocimiento no depende
    estructuralmente del registro de candidatas, pedido explícito).
    `ALTER TABLE ... ADD COLUMN` si la columna todavía no existe -- nunca
    toca una fila de datos."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(_SCHEMA)  # CREATE TABLE/INDEX IF NOT EXISTS -- nunca DROP, nunca recrea
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_experience_knowledge(
    rows: List[Dict[str, Any]], methodology_version: str = METHODOLOGY_VERSION,
) -> int:
    """Persiste `rows` (la salida TAL CUAL de
    `compute_own_experience_table()`) como filas nuevas -- INSERT puro,
    append-only. Nunca UPDATE, nunca UPSERT: dos llamadas con el mismo
    `computed_as_of` (recálculo del mismo día, o de un día distinto) NUNCA
    se pisan entre sí, cada una agrega sus propias filas. Devuelve cuántas
    filas se insertaron."""
    if not rows:
        return 0
    created_at = _now()
    with _connect() as conn:
        conn.executemany(
            """INSERT INTO live_experience_knowledge
               (direction, timing_deteccion, bucket, n_evaluables, n_aciertos_20, pct_20,
                wilson_lower_bound_20_pct, wilson_upper_bound_20_pct, baseline_pct_20, lift_20,
                mediana_max_advance_pct, n_aciertos_50, pct_50, n_aciertos_100, pct_100,
                validation_state, methodology_version, computed_as_of, computed_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    r["direction"], r["timing_deteccion"], r["bucket"], r["n_evaluables"], r["n_aciertos_20"],
                    r["pct_20"], r["wilson_lower_bound_20_pct"], r["wilson_upper_bound_20_pct"],
                    r["baseline_pct_20"], r["lift_20"], r["mediana_max_advance_pct"],
                    r["n_aciertos_50"], r["pct_50"], r["n_aciertos_100"], r["pct_100"],
                    r["validation_state"], methodology_version, r["computed_as_of"], r["computed_at"], created_at,
                )
                for r in rows
            ],
        )
        conn.commit()
    return len(rows)


def get_knowledge_for(
    as_of_date: str,
    direction: Optional[str] = None,
    timing_deteccion: Optional[str] = None,
    methodology_version: Optional[str] = METHODOLOGY_VERSION,
) -> List[Dict[str, Any]]:
    """Lectura interna de solo verificación (Fase 2 -- NO conectada a
    ningún gate/score/ranking/decisión; existe únicamente para poder
    testear que la persistencia funciona de punta a punta). Protección
    temporal OBLIGATORIA: nunca devuelve conocimiento con
    `computed_as_of > as_of_date` -- ese es exactamente el filtro que
    impide que conocimiento "del futuro" aparezca en una consulta
    histórica para una fecha anterior."""
    query = "SELECT * FROM live_experience_knowledge WHERE computed_as_of <= ?"
    params: List[Any] = [as_of_date]
    if direction is not None:
        query += " AND direction = ?"
        params.append(direction)
    if timing_deteccion is not None:
        query += " AND timing_deteccion = ?"
        params.append(timing_deteccion)
    if methodology_version is not None:
        query += " AND methodology_version = ?"
        params.append(methodology_version)
    query += " ORDER BY computed_as_of ASC, computed_at ASC"
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def latest_knowledge_as_of(
    as_of_date: str, direction: str, timing_deteccion: str, bucket: str,
    methodology_version: str = METHODOLOGY_VERSION,
) -> Optional[Dict[str, Any]]:
    """Resuelve "duplicados" (varios cálculos para la misma condición) EN
    LA LECTURA, nunca en la escritura -- devuelve la fila con
    `computed_at` más reciente entre las que cumplen
    `computed_as_of <= as_of_date`. `None` si no hay ninguna (nunca se
    inventa una)."""
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM live_experience_knowledge
               WHERE computed_as_of <= ? AND direction = ? AND timing_deteccion = ?
                     AND bucket = ? AND methodology_version = ?
               ORDER BY computed_at DESC LIMIT 1""",
            (as_of_date, direction, timing_deteccion, bucket, methodology_version),
        ).fetchone()
    return dict(row) if row is not None else None
