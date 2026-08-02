"""Timeline de Mission Control -- Entregable Nº2.

Registro cronológico PERMANENTE de eventos importantes (inicio, fin,
error, pausas, cancelaciones, hitos) de cualquier proceso instrumentado.
Se guarda en SQLite (`atlas_live/mission_control/timeline.db`),
reutilizando el mismo tipo de almacenamiento que ya usan Decision Journal
y la Knowledge Base de Atlas Core -- no introduce una herramienta nueva
al proyecto (ATLAS_MISSION_CONTROL.md, sección 4).

Alcance de este archivo: guardar y consultar eventos. NO implementa la
Supervisión Inteligente (Entregable 7, que también escribirá acá cuando
exista) ni la inferencia sobre procesos heredados (Entregable 3, en curso
aparte) -- este módulo solo sabe escribir y leer, no decide qué es una
anomalía.
"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path(__file__).parent / "timeline.db"

# Catálogo fijo (ATLAS_MISSION_CONTROL.md, sección 4).
EVENT_TYPES = {
    "process_started",
    "process_completed",
    "process_error",
    "process_stopped",
    "process_paused",
    "process_resumed",
    "state_changed",
    "alert_raised",
    "alert_resolved",
    "milestone",
}

# Orden de severidad, de menor a mayor -- lo usa get_recent_events() para
# filtrar "esto y todo lo más grave" sin tener que hardcodear comparaciones.
SEVERITY_ORDER = ["INFO", "WARNING", "ERROR", "CRITICAL"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    process_type TEXT NOT NULL,
    label TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    metadata TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_timeline_run_id ON timeline(run_id);
CREATE INDEX IF NOT EXISTS idx_timeline_timestamp ON timeline(timestamp);
CREATE INDEX IF NOT EXISTS idx_timeline_severity ON timeline(severity);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def record_event(
    run_id: str,
    process_type: str,
    label: str,
    event_type: str,
    severity: str,
    message: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Agrega un evento al Timeline. Nunca se edita un evento ya escrito --
    solo se agregan filas nuevas."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"event_type inválido: {event_type!r}. Debe ser uno de {sorted(EVENT_TYPES)}")
    if severity not in SEVERITY_ORDER:
        raise ValueError(f"severity inválida: {severity!r}. Debe ser una de {SEVERITY_ORDER}")

    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO timeline (run_id, process_type, label, timestamp, event_type, severity, message, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id, process_type, label,
                datetime.now(timezone.utc).isoformat(),
                event_type, severity, message,
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        conn.commit()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["metadata"] = json.loads(d["metadata"]) if d["metadata"] else {}
    return d


def get_events_for_run(run_id: str) -> List[Dict[str, Any]]:
    """Historial completo de una ejecución, en orden cronológico."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM timeline WHERE run_id = ? ORDER BY timestamp ASC, id ASC",
            (run_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_recent_events(limit: int = 100, min_severity: Optional[str] = None) -> List[Dict[str, Any]]:
    """Últimos eventos de cualquier proceso, más recientes primero.
    `min_severity` filtra "esto y todo lo más grave" (ej. min_severity="WARNING"
    devuelve WARNING, ERROR y CRITICAL, pero no INFO)."""
    query = "SELECT * FROM timeline"
    params: List[Any] = []
    if min_severity is not None:
        if min_severity not in SEVERITY_ORDER:
            raise ValueError(f"min_severity inválida: {min_severity!r}. Debe ser una de {SEVERITY_ORDER}")
        allowed = SEVERITY_ORDER[SEVERITY_ORDER.index(min_severity):]
        placeholders = ",".join("?" * len(allowed))
        query += f" WHERE severity IN ({placeholders})"
        params.extend(allowed)
    query += " ORDER BY timestamp DESC, id DESC LIMIT ?"
    params.append(limit)

    with closing(_connect()) as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]
