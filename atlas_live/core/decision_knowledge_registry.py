"""Snapshot inmutable de decisión+conocimiento (2026-09-03, Hito 3,
Fases 3.0/3.1, autorizado explícitamente).

CONOCIMIENTO (`learned_evidence`, Fase 4/5 del circuito de aprendizaje)
    → DECISIÓN (`atlas_decision_core.decide()`, sin modificarlo)
        → SNAPSHOT INMUTABLE (este módulo)
            → [TRIBUNAL -- solo lectura, `decision_outcome_tribunal.py`]

DB propia (`decision_knowledge_snapshot.db`, vía `db_path()`, mismo patrón
de aislamiento que `current_top_opportunity_registry.py`/
`raw_data_consolidation_registry.py`) -- NUNCA importa
`candidate_registry.py`/`atlas_decision_core.py`/`learned_evidence.py` como
lógica: este módulo SOLO persiste y lee lo que el llamador (`server.py`) ya
calculó con esos módulos, sin modificar ninguno de ellos.

INMUTABLE por diseño: en todo este archivo no existe ninguna sentencia
`UPDATE` ni `DELETE` contra `decision_knowledge_snapshot` -- una vez
insertada, una fila nunca cambia ni desaparece (ver
`test_decision_knowledge_registry.py::test_modulo_nunca_escribe_UPDATE_ni_DELETE`,
escaneo estático del código fuente).

CONOCIMIENTO INLINE, no un puntero: cada columna de conocimiento
(`validation_state`, `sample_size`, `baseline_pct_20`,
`wilson_lower/upper_bound_20_pct`, etc.) se copia TAL CUAL desde el dict
que ya devuelve `learned_evidence.get_learned_evidence()` -- nunca se
guarda solo una referencia a la fila de `live_experience_knowledge` que la
produjo. Esto es deliberado (pedido explícito): aunque
`live_experience_knowledge` ya es append-only por diseño propio, reconstruir
"qué sabía Atlas en ese instante" nunca debe depender de que esa tabla siga
existiendo o sin cambios -- defensa en profundidad.

TRANSITION-ONLY, con comparación de tupla completa (no solo `decision`):
`record_decision_knowledge_snapshot()` lee la ÚLTIMA fila para
`(ticker, market_date)` y compara `(decision, methodology_version,
computed_as_of, computed_at)` contra los valores nuevos -- inserta SOLO si
difiere o si no hay fila previa ese día. Esto es intencionalmente distinto
de un `UNIQUE(ticker, market_date, decision)`: una restricción así
perdería la transición real A->B->A (la tercera fila, "vuelta a A",
colisionaría con la primera). Comparar solo contra la ÚLTIMA fila (nunca
contra "cualquier fila anterior de ese valor") captura correctamente
CUALQUIER secuencia de transiciones, incluida A->B->A, sin perder ninguna
y sin duplicar por requests HTTP idénticos repetidos (pedido explícito del
usuario, corrección sobre el plan original).

Deliberadamente DISTINTO de `shadow_decision_log`
(`atlas_live/radar/candidate_registry.py`, protegido, sin tocar): esa tabla
es una alerta ligera que SOLO registra divergencias (`shadow_differs=True`).
Esta tabla registra el evento COMPLETO (con y sin divergencia, con y sin
conocimiento disponible) -- es la fuente que necesita el Tribunal
(Fase 3.2) para construir las poblaciones A/B, nunca se leen ni se
fusionan entre sí."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.config.config import db_path

DB_PATH = db_path("decision_knowledge_snapshot.db", default=Path(__file__).parent)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_knowledge_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    market_date TEXT NOT NULL,
    decision_timestamp TEXT NOT NULL,
    decision TEXT NOT NULL,
    decision_shadow TEXT,
    shadow_differs INTEGER NOT NULL,
    apply_recalibration_active INTEGER NOT NULL,
    knowledge_available INTEGER NOT NULL,
    knowledge_reason TEXT,
    methodology_version TEXT,
    computed_as_of TEXT,
    computed_at TEXT,
    validation_state TEXT,
    sample_size INTEGER,
    historical_success_pct_20 REAL,
    baseline_pct_20 REAL,
    lift_20 REAL,
    wilson_lower_bound_20_pct REAL,
    wilson_upper_bound_20_pct REAL,
    core_methodology_version TEXT NOT NULL,
    direction TEXT,
    timing_deteccion TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dks_ticker_date ON decision_knowledge_snapshot(ticker, market_date);
CREATE INDEX IF NOT EXISTS idx_dks_market_date ON decision_knowledge_snapshot(market_date);
CREATE INDEX IF NOT EXISTS idx_dks_condition ON decision_knowledge_snapshot(direction, timing_deteccion, methodology_version);
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


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    return dict(r)


def _last_snapshot(conn: sqlite3.Connection, ticker: str, market_date: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM decision_knowledge_snapshot
           WHERE ticker=? AND market_date=? ORDER BY id DESC LIMIT 1""",
        (ticker, market_date),
    ).fetchone()


def _identity_tuple(row: sqlite3.Row) -> tuple:
    return (row["decision"], row["methodology_version"], row["computed_as_of"], row["computed_at"])


def record_decision_knowledge_snapshot(
    ticker: str,
    market_date: str,
    decision_timestamp: str,
    decision: str,
    decision_shadow: Optional[str],
    shadow_differs: bool,
    learned_evidence: Optional[Dict[str, Any]],
    direction: Optional[str],
    timing_deteccion: Optional[str],
    core_methodology_version: str,
    apply_recalibration_active: bool = False,
) -> bool:
    """Persiste UN evento de decisión+conocimiento -- transition-only
    (ver docstring del módulo): compara la tupla
    `(decision, methodology_version, computed_as_of, computed_at)` contra
    la ÚLTIMA fila ya registrada para `(ticker, market_date)`, e inserta
    SOLO si difiere o si no había ninguna fila previa ese día. Devuelve
    `True` si insertó una fila nueva, `False` si el evento ya estaba
    representado (idempotente ante requests HTTP repetidos).

    `learned_evidence=None` o `learned_evidence.get("available")` falso
    -> se persiste igual (`knowledge_available=0`, el resto de columnas de
    conocimiento en `NULL`, `decision_shadow` viaja tal cual lo haya
    calculado el llamador -- normalmente `None` en ese caso) -- nunca se
    omite la fila: la ausencia de conocimiento es en sí un dato de
    auditoría real ("qué sabía Atlas en ese instante": nada, y por qué)."""
    le = learned_evidence or {}
    available = bool(le.get("available"))

    nueva = {
        "decision": decision,
        "methodology_version": le.get("methodology_version") if available else None,
        "computed_as_of": le.get("computed_as_of") if available else None,
        "computed_at": le.get("computed_at") if available else None,
    }

    with _connect() as conn:
        anterior = _last_snapshot(conn, ticker, market_date)
        if anterior is not None:
            anterior_tupla = _identity_tuple(anterior)
            nueva_tupla = (nueva["decision"], nueva["methodology_version"], nueva["computed_as_of"], nueva["computed_at"])
            if anterior_tupla == nueva_tupla:
                return False  # mismo evento -- request repetido, no duplica

        now = _now()
        conn.execute(
            """INSERT INTO decision_knowledge_snapshot
               (ticker, market_date, decision_timestamp, decision, decision_shadow, shadow_differs,
                apply_recalibration_active, knowledge_available, knowledge_reason,
                methodology_version, computed_as_of, computed_at, validation_state, sample_size,
                historical_success_pct_20, baseline_pct_20, lift_20,
                wilson_lower_bound_20_pct, wilson_upper_bound_20_pct,
                core_methodology_version, direction, timing_deteccion, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ticker, market_date, decision_timestamp, decision, decision_shadow, int(shadow_differs),
                int(apply_recalibration_active), int(available), le.get("reason"),
                le.get("methodology_version") if available else None,
                le.get("computed_as_of") if available else None,
                le.get("computed_at") if available else None,
                le.get("validation_state") if available else None,
                le.get("sample_size") if available else None,
                le.get("historical_success_pct_20") if available else None,
                le.get("baseline_pct_20") if available else None,
                le.get("lift_20") if available else None,
                le.get("wilson_lower_bound_20_pct") if available else None,
                le.get("wilson_upper_bound_20_pct") if available else None,
                core_methodology_version, direction, timing_deteccion, now,
            ),
        )
        conn.commit()
        return True


def get_snapshots_for(ticker: str, market_date: str) -> List[Dict[str, Any]]:
    """Solo lectura -- todas las transiciones registradas ese día para esa
    candidata, en orden cronológico."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM decision_knowledge_snapshot
               WHERE ticker=? AND market_date=? ORDER BY id ASC""",
            (ticker, market_date),
        ).fetchall()
    return [_row(r) for r in rows]


def latest_snapshot_for(ticker: str, market_date: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = _last_snapshot(conn, ticker, market_date)
    return _row(row) if row is not None else None


def list_snapshots(
    market_date: Optional[str] = None,
    direction: Optional[str] = None,
    timing_deteccion: Optional[str] = None,
    limit: int = 5000,
) -> List[Dict[str, Any]]:
    """Solo lectura, paginado con un límite explícito -- nunca una carga
    sin acotar (mismo criterio ya usado en todo el proyecto para evitar
    cargas grandes en memoria)."""
    query = "SELECT * FROM decision_knowledge_snapshot WHERE 1=1"
    params: List[Any] = []
    if market_date is not None:
        query += " AND market_date=?"
        params.append(market_date)
    if direction is not None:
        query += " AND direction=?"
        params.append(direction)
    if timing_deteccion is not None:
        query += " AND timing_deteccion=?"
        params.append(timing_deteccion)
    query += " ORDER BY id ASC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row(r) for r in rows]
