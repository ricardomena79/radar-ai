"""Registro de auditoría de elegibilidad de conocimiento (Hito 3, Fase 3.3,
2026-09-03, autorizado explícitamente en Plan Mode).

`knowledge_eligibility.classify_eligibility()` (puro, sin DB) -> este
módulo (persistencia append-only) -> `GET /api/admin/knowledge-eligibility-report`
(solo lectura).

Mismo patrón EXACTO que `decision_knowledge_registry.py` (Hito 3.0/3.1,
sin tocar): DB propia (`knowledge_eligibility.db`), split `_connect()`
(lectura-escritura, solo usado por `record_eligibility_snapshot`) /
`_ro_connect()` (lectura real, `mode=ro` + `PRAGMA query_only=ON`, nunca
crea el archivo), `_db_exists()` como guard antes de cualquier lectura,
INMUTABLE (ninguna sentencia `UPDATE`/`DELETE` en todo el archivo, ver
`test_knowledge_eligibility_registry.py`, escaneo estático).

TRANSITION-ONLY, clave de identidad `(direction, timing_deteccion,
methodology_version)` -- NO `(ticker, market_date)`: la elegibilidad es una
propiedad del CONOCIMIENTO (la condición agregada en
`live_experience_knowledge`), no de un ticker puntual. Esto es lo que
permite responder "¿cambió la elegibilidad de esta condición a través del
tiempo?" sin mezclar miles de tickers que comparten la misma evidencia
subyacente. Compara la tupla `(eligibility_state, computed_as_of,
computed_at)` contra la ÚLTIMA fila para esa condición -- inserta solo si
difiere, mismo mecanismo que `decision_knowledge_registry._identity_tuple()`.

`direction`/`timing_deteccion`/`methodology_version` pueden ser `None`
(conocimiento no disponible en absoluto) -- las comparaciones de identidad
usan `IS` en vez de `=` en SQL para tratar `NULL` de forma segura (`NULL =
NULL` no es verdadero en SQL estándar; `NULL IS NULL` sí)."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.config.config import db_path

DB_PATH = db_path("knowledge_eligibility.db", default=Path(__file__).parent)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_eligibility_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    direction TEXT,
    timing_deteccion TEXT,
    methodology_version TEXT,
    evaluated_as_of TEXT NOT NULL,
    evaluated_at TEXT NOT NULL,
    eligibility_state TEXT NOT NULL,
    reasons TEXT,
    validation_state TEXT,
    sample_size INTEGER,
    wilson_lower_bound_20_pct REAL,
    wilson_upper_bound_20_pct REAL,
    baseline_pct_20 REAL,
    lift_20 REAL,
    computed_as_of TEXT,
    computed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kel_condition ON knowledge_eligibility_log(direction, timing_deteccion, methodology_version);
CREATE INDEX IF NOT EXISTS idx_kel_evaluated_as_of ON knowledge_eligibility_log(evaluated_as_of);
CREATE INDEX IF NOT EXISTS idx_kel_state ON knowledge_eligibility_log(eligibility_state);
"""


def _connect() -> sqlite3.Connection:
    """Lectura-escritura -- USAR SOLO desde `record_eligibility_snapshot()`.
    Las funciones de lectura usan `_ro_connect()` -- mismo criterio que
    `decision_knowledge_registry.py` (corrección 2026-09-02 tras un `disk
    I/O error` real en producción: una conexión de lectura nunca debe
    intentar `PRAGMA journal_mode=WAL` + `CREATE TABLE/INDEX IF NOT
    EXISTS`)."""
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(_SCHEMA)  # CREATE TABLE/INDEX IF NOT EXISTS -- nunca DROP, nunca recrea
    return conn


def _db_exists() -> bool:
    return Path(DB_PATH).exists()


def _ro_connect() -> sqlite3.Connection:
    """Conexión read-only REAL de SQLite -- `mode=ro` + `PRAGMA
    query_only=ON`. NUNCA `PRAGMA journal_mode=WAL`, NUNCA
    `executescript(_SCHEMA)`, NUNCA crea el archivo si no existe -- por eso
    SIEMPRE se llama detrás de `_db_exists()`, nunca sola."""
    uri = Path(DB_PATH).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    return dict(r)


def _last_snapshot(
    conn: sqlite3.Connection, direction: Optional[str], timing_deteccion: Optional[str], methodology_version: Optional[str]
) -> Optional[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM knowledge_eligibility_log
           WHERE direction IS ? AND timing_deteccion IS ? AND methodology_version IS ?
           ORDER BY id DESC LIMIT 1""",
        (direction, timing_deteccion, methodology_version),
    ).fetchone()


def _identity_tuple(row: sqlite3.Row) -> tuple:
    return (row["eligibility_state"], row["computed_as_of"], row["computed_at"])


def record_eligibility_snapshot(
    direction: Optional[str],
    timing_deteccion: Optional[str],
    evaluated_as_of: str,
    eligibility_result: Dict[str, Any],
) -> bool:
    """Persiste UN resultado de `knowledge_eligibility.classify_eligibility()`
    -- transition-only: compara `(eligibility_state, computed_as_of,
    computed_at)` contra la ÚLTIMA fila ya registrada para
    `(direction, timing_deteccion, methodology_version)`, e inserta SOLO si
    difiere o si no había ninguna fila previa. Devuelve `True` si insertó
    una fila nueva, `False` si el evento ya estaba representado
    (idempotente ante requests repetidos)."""
    methodology_version = eligibility_result.get("methodology_version")
    nueva_tupla = (
        eligibility_result.get("eligibility_state"),
        eligibility_result.get("computed_as_of"),
        eligibility_result.get("computed_at"),
    )

    with _connect() as conn:
        anterior = _last_snapshot(conn, direction, timing_deteccion, methodology_version)
        if anterior is not None and _identity_tuple(anterior) == nueva_tupla:
            return False  # mismo evento -- consulta repetida, no duplica

        now = _now()
        conn.execute(
            """INSERT INTO knowledge_eligibility_log
               (direction, timing_deteccion, methodology_version, evaluated_as_of, evaluated_at,
                eligibility_state, reasons, validation_state, sample_size,
                wilson_lower_bound_20_pct, wilson_upper_bound_20_pct, baseline_pct_20, lift_20,
                computed_as_of, computed_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                direction, timing_deteccion, methodology_version, evaluated_as_of, now,
                eligibility_result.get("eligibility_state"),
                "; ".join(eligibility_result.get("reasons") or []),
                eligibility_result.get("validation_state"),
                eligibility_result.get("sample_size"),
                eligibility_result.get("wilson_lower_bound_20_pct"),
                eligibility_result.get("wilson_upper_bound_20_pct"),
                eligibility_result.get("baseline_pct_20"),
                eligibility_result.get("lift_20"),
                eligibility_result.get("computed_as_of"),
                eligibility_result.get("computed_at"),
                now,
            ),
        )
        conn.commit()
        return True


def latest_eligibility_for(
    direction: Optional[str], timing_deteccion: Optional[str], methodology_version: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Solo lectura REAL -- `None` tanto si no hay ninguna fila para esa
    condición como si la DB todavía no existe."""
    if not _db_exists():
        return None
    with _ro_connect() as conn:
        row = _last_snapshot(conn, direction, timing_deteccion, methodology_version)
    return _row(row) if row is not None else None


def list_eligibility_log(
    evaluated_as_of: Optional[str] = None,
    eligibility_state: Optional[str] = None,
    limit: int = 5000,
) -> List[Dict[str, Any]]:
    """Solo lectura REAL, paginado con un límite explícito. Si la DB
    todavía no existe, `[]` sin abrir ni crear nada -- es la función que
    usaría un futuro reporte admin, tiene que poder responder "sin
    evaluaciones todavía" sin ningún efecto colateral."""
    if not _db_exists():
        return []
    query = "SELECT * FROM knowledge_eligibility_log WHERE 1=1"
    params: List[Any] = []
    if evaluated_as_of is not None:
        query += " AND evaluated_as_of=?"
        params.append(evaluated_as_of)
    if eligibility_state is not None:
        query += " AND eligibility_state=?"
        params.append(eligibility_state)
    query += " ORDER BY id ASC LIMIT ?"
    params.append(limit)
    with _ro_connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row(r) for r in rows]


def full_eligibility_report(
    evaluated_as_of: Optional[str] = None,
    eligibility_state: Optional[str] = None,
    limit: int = 5000,
) -> Dict[str, Any]:
    """Reporte de solo lectura para `GET /api/admin/knowledge-eligibility-report`
    -- mismo estilo que `decision_outcome_tribunal.full_tribunal_report()`
    (Hito 3.2, sin tocar): nunca lanza, siempre devuelve un dict con `ok`.
    Agrega conteos por `eligibility_state` sobre las filas que ya trae
    `list_eligibility_log()` -- no vuelve a tocar la DB."""
    try:
        eventos = list_eligibility_log(evaluated_as_of=evaluated_as_of, eligibility_state=eligibility_state, limit=limit)
        conteos: Dict[str, int] = {estado: 0 for estado in ("NO_ELEGIBLE", "INSUFICIENTE", "ELEGIBLE")}
        for evento in eventos:
            conteos[evento["eligibility_state"]] = conteos.get(evento["eligibility_state"], 0) + 1
        return {
            "ok": True,
            "n_eventos": len(eventos),
            "conteos_por_estado": conteos,
            "eventos": eventos,
            "error": None,
        }
    except Exception as exc:  # nunca romper el endpoint por un fallo de lectura
        return {"ok": False, "n_eventos": 0, "conteos_por_estado": {}, "eventos": [], "error": str(exc)}
