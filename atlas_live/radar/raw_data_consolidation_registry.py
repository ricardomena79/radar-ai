"""Manifiesto de consolidación de datos crudos (2026-09-02, Hito 2,
autorizado explícitamente). Registra, por bloque `(ticker, market_date)`,
que la experiencia cruda de alta frecuencia (`candidate_observation` /
`shadow_candidate_detection`) fue analizada y resumida -- este módulo
NUNCA borra ni modifica ninguna fila cruda, solo persiste el manifiesto.

`status` avanza EXCLUSIVAMENTE de forma manual, nunca automática:
`provisional -> verified -> compaction_authorized -> compacted`. Esta
fase (Hito 2, solo consolidación) implementa hasta `verified` --
`compaction_authorized`/`compacted` existen en el schema para que una
FASE FUTURA, con su propia autorización separada, pueda usarlos. Ningún
código de este módulo ni de `raw_data_consolidation_pipeline.py` escribe
esos 2 estados.

DB propia (`raw_data_consolidation.db`), deliberadamente desacoplada de
`candidate_registry.py`/`shadow_detector_registry.py` -- mismo principio
arquitectónico ya usado por `atlas_live/learning/live_experience_knowledge.py`
("esta memoria de conocimiento no debe depender estructuralmente del
registro de candidatas").

`UNIQUE(source_table, block_key, methodology_version)` + `INSERT OR
IGNORE`: a diferencia de `live_experience_knowledge` (append-only, sin
UNIQUE, porque cada fila es una ESTIMACIÓN evolutiva), un manifiesto de
consolidación necesita una única respuesta canónica por bloque -- una
segunda corrida del mismo bloque nunca lo cuenta de nuevo (write-once,
mismo patrón que `candidate_detection`/`magnitud_prediction`)."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.config.config import db_path

DB_PATH = db_path("raw_data_consolidation.db", default=Path(__file__).parent)

VALID_SOURCE_TABLES = ("candidate_observation", "shadow_candidate_detection")
VALID_STATUSES = ("provisional", "verified", "compaction_authorized", "compacted")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_data_consolidation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_table TEXT NOT NULL,
    block_key TEXT NOT NULL,
    block_granularity TEXT NOT NULL,
    row_count_covered INTEGER NOT NULL,
    min_timestamp_covered TEXT,
    max_timestamp_covered TEXT,
    summary_json TEXT NOT NULL,
    raw_data_checksum TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    verified_at TEXT,
    status TEXT NOT NULL DEFAULT 'provisional',
    created_at TEXT NOT NULL,
    UNIQUE(source_table, block_key, methodology_version)
);
CREATE INDEX IF NOT EXISTS idx_rdc_source_status ON raw_data_consolidation(source_table, status);
CREATE INDEX IF NOT EXISTS idx_rdc_block ON raw_data_consolidation(source_table, block_key);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(_SCHEMA)  # CREATE TABLE/INDEX IF NOT EXISTS -- nunca DROP, nunca recrea
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_source_table(source_table: str) -> None:
    if source_table not in VALID_SOURCE_TABLES:
        raise ValueError(f"source_table inválida: {source_table!r}. Debe ser una de {VALID_SOURCE_TABLES}")


def record_provisional(
    source_table: str,
    block_key: str,
    block_granularity: str,
    row_count_covered: int,
    min_timestamp_covered: Optional[str],
    max_timestamp_covered: Optional[str],
    summary: Dict[str, Any],
    raw_data_checksum: str,
    methodology_version: str,
) -> bool:
    """`INSERT OR IGNORE` -- write-once por `(source_table, block_key,
    methodology_version)`. Devuelve `True` si insertó una fila NUEVA,
    `False` si el bloque ya estaba consolidado bajo esa metodología
    (idempotente -- una segunda corrida del mismo bloque NUNCA lo cuenta
    como experiencia nueva, respondiendo directamente al pedido de evitar
    doble contabilización)."""
    _validate_source_table(source_table)
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO raw_data_consolidation
               (source_table, block_key, block_granularity, row_count_covered,
                min_timestamp_covered, max_timestamp_covered, summary_json,
                raw_data_checksum, methodology_version, computed_at, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,'provisional',?)""",
            (
                source_table, block_key, block_granularity, row_count_covered,
                min_timestamp_covered, max_timestamp_covered,
                json.dumps(summary, ensure_ascii=False, sort_keys=True),
                raw_data_checksum, methodology_version, now, now,
            ),
        )
        conn.commit()
        return cur.rowcount > 0


def get_block(source_table: str, block_key: str, methodology_version: str) -> Optional[Dict[str, Any]]:
    """Solo lectura. `None` si el bloque no tiene ningún manifiesto bajo
    esa metodología -- nunca se inventa uno."""
    _validate_source_table(source_table)
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM raw_data_consolidation
               WHERE source_table=? AND block_key=? AND methodology_version=?""",
            (source_table, block_key, methodology_version),
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["summary"] = json.loads(d["summary_json"])
    return d


def mark_verified(source_table: str, block_key: str, methodology_version: str) -> bool:
    """Avanza `provisional -> verified` -- SOLO si el estado actual es
    exactamente `'provisional'` (el `WHERE status='provisional'` hace que
    esto sea un no-op seguro si ya estaba verificado, y estructuralmente
    imposible que retroceda desde `compaction_authorized`/`compacted`,
    aunque esta fase nunca escriba esos 2 estados). `UPDATE` puntual por
    clave -- nunca un `DELETE`+`INSERT`, nunca toca otras filas."""
    _validate_source_table(source_table)
    with _connect() as conn:
        cur = conn.execute(
            """UPDATE raw_data_consolidation SET status='verified', verified_at=?
               WHERE source_table=? AND block_key=? AND methodology_version=? AND status='provisional'""",
            (_now(), source_table, block_key, methodology_version),
        )
        conn.commit()
        return cur.rowcount > 0


def list_blocks(source_table: Optional[str] = None) -> List[Dict[str, Any]]:
    """Solo lectura -- todos los manifiestos, opcionalmente filtrados por
    `source_table`. Usado por el endpoint de consulta de estado."""
    if source_table is not None:
        _validate_source_table(source_table)
    with _connect() as conn:
        if source_table is not None:
            rows = conn.execute(
                "SELECT * FROM raw_data_consolidation WHERE source_table=? ORDER BY block_key",
                (source_table,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM raw_data_consolidation ORDER BY source_table, block_key"
            ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["summary"] = json.loads(d["summary_json"])
        out.append(d)
    return out
