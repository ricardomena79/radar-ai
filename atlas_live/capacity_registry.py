"""Registro de snapshots de capacidad de almacenamiento (2026-09-07,
autorizado explícitamente -- Capacity Monitor).

Mismo patrón EXACTO que `atlas_live/core/knowledge_eligibility_registry.py`
(Hito 3.3): DB propia (`capacity_monitor.db`), split `_connect()`
(lectura-escritura) / `_ro_connect()` (mode=ro real + PRAGMA query_only=ON,
nunca crea el archivo). Tabla append-only, NUNCA `UPDATE`/`DELETE` (ver
`test_capacity_registry.py`, escaneo estático).

Deliberadamente SEPARADO de `candidate_registry.py`/`radar_candidates.db`
-- este monitor audita el espacio que el radar consume, así que no debe
compartir archivo ni tabla con lo que está auditando (evita cualquier
ambigüedad de "¿esta escritura es del radar o del monitor?" al medir
crecimiento). Snapshot deliberadamente MÍNIMO: 4 campos reales
(timestamp/total/used/free) -- pedido explícito del usuario ("un snapshot
de capacidad debería ser muy pequeño")."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.config.config import db_path

DB_PATH = db_path("capacity_monitor.db", default=Path(__file__).parent)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS capacity_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    total_bytes INTEGER NOT NULL,
    used_bytes INTEGER NOT NULL,
    free_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_capacity_observed_at ON capacity_snapshot(observed_at);
"""


def _connect() -> sqlite3.Connection:
    """Lectura-escritura -- USAR SOLO desde `record_snapshot()`. Las
    funciones de lectura usan `_ro_connect()` (mismo criterio que
    `knowledge_eligibility_registry.py`: una conexión de solo lectura
    nunca debe intentar `PRAGMA journal_mode=WAL` ni crear el schema)."""
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(_SCHEMA)
    return conn


def _db_exists() -> bool:
    return Path(DB_PATH).exists()


def _ro_connect() -> sqlite3.Connection:
    """Read-only real -- `mode=ro` + `PRAGMA query_only=ON`. Nunca crea el
    archivo -- se llama SIEMPRE detrás de `_db_exists()`."""
    uri = Path(DB_PATH).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_snapshot(total_bytes: int, used_bytes: int, free_bytes: int, observed_at: Optional[str] = None) -> None:
    """Append-only -- nunca actualiza ni reemplaza una fila existente. La
    decisión de CUÁNDO llamar a esto (throttle por tiempo, no por sweep)
    vive en `capacity_monitor.py`, no acá -- este módulo solo persiste lo
    que se le pide, sin ninguna lógica de negocio."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO capacity_snapshot (observed_at, total_bytes, used_bytes, free_bytes, created_at) "
            "VALUES (?,?,?,?,?)",
            (observed_at or _now(), int(total_bytes), int(used_bytes), int(free_bytes), _now()),
        )
        conn.commit()


def latest_snapshot() -> Optional[Dict[str, Any]]:
    if not _db_exists():
        return None
    with _ro_connect() as conn:
        row = conn.execute(
            "SELECT * FROM capacity_snapshot ORDER BY observed_at DESC, id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def list_snapshots(since: Optional[str] = None, limit: int = 5000) -> List[Dict[str, Any]]:
    """Más viejo primero (para que un consumidor de crecimiento pueda
    recorrerlas en orden temporal sin tener que invertir la lista)."""
    if not _db_exists():
        return []
    with _ro_connect() as conn:
        if since is not None:
            rows = conn.execute(
                "SELECT * FROM capacity_snapshot WHERE observed_at >= ? ORDER BY observed_at ASC, id ASC LIMIT ?",
                (since, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM capacity_snapshot ORDER BY observed_at ASC, id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
