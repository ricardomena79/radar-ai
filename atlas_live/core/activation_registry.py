"""Registro de activación controlada (Hito 3, Fase 3.5, 2026-09-03,
autorizado explícitamente en Plan Mode).

Tres responsabilidades, una sola DB nueva (`activation.db`) -- mismo
patrón `_connect()`/`_ro_connect()`/`_db_exists()` de 3.0/3.3/3.4:

1. **Estado del mecanismo** (`activation_mechanism_state`): interruptor
   maestro `"OFF"`/`"ON_CONTROLADO"`. Append-only (cada cambio inserta una
   fila nueva, nunca `UPDATE` -- mismo criterio de auditoría inmutable que
   el resto del proyecto) -- `get_mechanism_state()` lee la ÚLTIMA fila.
   **FAIL-SAFE ABSOLUTO**: cualquier excepción, DB inexistente, fila
   corrupta o valor desconocido -> `"OFF"`, SIEMPRE -- nunca puede
   devolver algo distinto de exactamente `"OFF"`/`"ON_CONTROLADO"`.

2. **Revocación** (`activation_revocation_log`): append-only, SIN mecanismo
   de "des-revocar" (no pedido -- mantiene la garantía "la revocación gana
   siempre" sin el riesgo de una reactivación silenciosa). Scope `"GLOBAL"`
   (revoca cualquier condición) o `"CONDICION"` (una `(direction,
   timing_deteccion, methodology_version)` puntual).

3. **Auditoría por evento** (`activation_state_log`): append-only,
   TRANSITION-ONLY por `(ticker, market_date)` -- mismo mecanismo exacto
   que `decision_knowledge_registry.py`/`knowledge_eligibility_registry.py`/
   `shadow_observation_registry.py`. Solo se escribe cuando el mecanismo
   está `"ON_CONTROLADO"` -- mientras esté en `"OFF"` (el default,
   indefinidamente hasta que un humano lo cambie a mano), el llamador
   (`server.py`) ni siquiera llama a `record_activation_state()` -- cero
   filas, cero costo (ver `server.py`, bloque de Fase 3.5)."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.config.config import db_path

DB_PATH = db_path("activation.db", default=Path(__file__).parent)

_VALID_MECHANISM_STATES = ("OFF", "ON_CONTROLADO")
_VALID_SCOPES = ("GLOBAL", "CONDICION")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS activation_mechanism_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    reason TEXT NOT NULL,
    changed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activation_revocation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    direction TEXT,
    timing_deteccion TEXT,
    methodology_version TEXT,
    reason TEXT NOT NULL,
    revoked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activation_state_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    market_date TEXT NOT NULL,
    decision_timestamp TEXT NOT NULL,
    activation_state TEXT NOT NULL,
    reason TEXT NOT NULL,
    eligibility_state TEXT,
    mechanism_state TEXT NOT NULL,
    decision_controlada TEXT,
    direction TEXT,
    timing_deteccion TEXT,
    methodology_version TEXT,
    validation_state TEXT,
    sample_size INTEGER,
    computed_as_of TEXT,
    computed_at TEXT,
    core_methodology_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_asl_ticker_date ON activation_state_log(ticker, market_date);
CREATE INDEX IF NOT EXISTS idx_asl_market_date ON activation_state_log(market_date);
CREATE INDEX IF NOT EXISTS idx_asl_state ON activation_state_log(activation_state);
"""


def _connect() -> sqlite3.Connection:
    """Lectura-escritura -- USAR SOLO desde `set_mechanism_state()`,
    `revoke()` y `record_activation_state()`. Las funciones de lectura
    usan `_ro_connect()`, mismo criterio que el resto del Hito 3."""
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(_SCHEMA)
    return conn


def _db_exists() -> bool:
    return Path(DB_PATH).exists()


def _ro_connect() -> sqlite3.Connection:
    """Conexión read-only REAL -- `mode=ro` + `PRAGMA query_only=ON`.
    NUNCA crea el archivo -- SIEMPRE se llama detrás de `_db_exists()`."""
    uri = Path(DB_PATH).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    return dict(r)


# --- 1) estado del mecanismo -----------------------------------------------

def get_mechanism_state() -> str:
    """FAIL-SAFE ABSOLUTO: cualquier problema -> `"OFF"`. Nunca lanza."""
    try:
        if not _db_exists():
            return "OFF"
        with _ro_connect() as conn:
            row = conn.execute(
                "SELECT state FROM activation_mechanism_state ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return "OFF"
        state = row["state"]
        return state if state in _VALID_MECHANISM_STATES else "OFF"
    except Exception:
        return "OFF"


def set_mechanism_state(state: str, reason: str) -> bool:
    """Único punto de escritura del interruptor maestro. Rechaza (lanza
    `ValueError`, no aplica ningún cambio) cualquier `state` que no sea
    EXACTAMENTE `"OFF"`/`"ON_CONTROLADO"`, o `reason` vacío/ausente --
    nunca un cambio ambiguo a medias. Llamado solo desde el endpoint
    admin correspondiente."""
    if state not in _VALID_MECHANISM_STATES:
        raise ValueError(f"state debe ser uno de {_VALID_MECHANISM_STATES}, recibido: {state!r}")
    if not reason or not reason.strip():
        raise ValueError("reason no puede estar vacío")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO activation_mechanism_state (state, reason, changed_at) VALUES (?,?,?)",
            (state, reason, _now()),
        )
        conn.commit()
    return True


def get_mechanism_history(limit: int = 100) -> List[Dict[str, Any]]:
    """Solo lectura -- historial completo de cambios de estado, más
    reciente primero. `[]` si la DB no existe."""
    if not _db_exists():
        return []
    with _ro_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM activation_mechanism_state ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row(r) for r in rows]


# --- 2) revocación -----------------------------------------------------------

def revoke(
    scope: str,
    reason: str,
    direction: Optional[str] = None,
    timing_deteccion: Optional[str] = None,
    methodology_version: Optional[str] = None,
) -> bool:
    """Revocación inmediata y permanente (sin mecanismo de "des-revocar").
    `scope="GLOBAL"` bloquea CUALQUIER activación futura, sin importar la
    condición. `scope="CONDICION"` requiere los 3 campos de condición."""
    if scope not in _VALID_SCOPES:
        raise ValueError(f"scope debe ser uno de {_VALID_SCOPES}, recibido: {scope!r}")
    if not reason or not reason.strip():
        raise ValueError("reason no puede estar vacío")
    if scope == "CONDICION" and not all([direction, timing_deteccion, methodology_version]):
        raise ValueError("scope=CONDICION requiere direction, timing_deteccion y methodology_version")
    with _connect() as conn:
        conn.execute(
            """INSERT INTO activation_revocation_log
               (scope, direction, timing_deteccion, methodology_version, reason, revoked_at)
               VALUES (?,?,?,?,?,?)""",
            (scope, direction, timing_deteccion, methodology_version, reason, _now()),
        )
        conn.commit()
    return True


def is_revoked(direction: Optional[str], timing_deteccion: Optional[str], methodology_version: Optional[str]) -> bool:
    """Solo lectura REAL. `False` si la DB no existe (nada revocado
    todavía). `True` si existe una revocación GLOBAL, o una revocación de
    CONDICION que matchea exactamente estos 3 valores."""
    if not _db_exists():
        return False
    with _ro_connect() as conn:
        global_row = conn.execute(
            "SELECT 1 FROM activation_revocation_log WHERE scope='GLOBAL' LIMIT 1"
        ).fetchone()
        if global_row is not None:
            return True
        condicion_row = conn.execute(
            """SELECT 1 FROM activation_revocation_log
               WHERE scope='CONDICION' AND direction=? AND timing_deteccion=? AND methodology_version=?
               LIMIT 1""",
            (direction, timing_deteccion, methodology_version),
        ).fetchone()
    return condicion_row is not None


def list_revocations(limit: int = 100) -> List[Dict[str, Any]]:
    """Solo lectura -- todas las revocaciones registradas. `[]` si la DB
    no existe."""
    if not _db_exists():
        return []
    with _ro_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM activation_revocation_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row(r) for r in rows]


# --- 3) auditoría por evento -------------------------------------------------

def _last_activation(conn: sqlite3.Connection, ticker: str, market_date: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM activation_state_log
           WHERE ticker=? AND market_date=? ORDER BY id DESC LIMIT 1""",
        (ticker, market_date),
    ).fetchone()


def _identity_tuple(row: sqlite3.Row) -> tuple:
    return (row["activation_state"], row["reason"], row["decision_controlada"], row["computed_as_of"], row["computed_at"])


def record_activation_state(
    ticker: str,
    market_date: str,
    decision_timestamp: str,
    direction: Optional[str],
    timing_deteccion: Optional[str],
    core_methodology_version: str,
    mechanism_state: str,
    eligibility_state: Optional[str],
    gate: Dict[str, Any],
    decision_controlada: Optional[str],
    learned_evidence: Optional[Dict[str, Any]] = None,
) -> bool:
    """Persiste UN veredicto de `activation_gate.classify_activation()` --
    transition-only, compara `(activation_state, reason,
    decision_controlada, computed_as_of, computed_at)` contra la ÚLTIMA
    fila para `(ticker, market_date)`, inserta SOLO si difiere.
    `mechanism_state`/`eligibility_state` viajan tal cual los usó el
    llamador para construir `gate` (auditoría, no se recalculan acá). El
    llamador (`server.py`) es responsable de no invocar esta función
    cuando el mecanismo está `"OFF"` (ver docstring del módulo)."""
    le = learned_evidence or {}
    computed_at = le.get("computed_at")
    nueva_tupla = (
        gate.get("activation_state"), gate.get("reason"), decision_controlada,
        le.get("computed_as_of"), computed_at,
    )

    with _connect() as conn:
        anterior = _last_activation(conn, ticker, market_date)
        if anterior is not None and _identity_tuple(anterior) == nueva_tupla:
            return False

        now = _now()
        conn.execute(
            """INSERT INTO activation_state_log
               (ticker, market_date, decision_timestamp, activation_state, reason,
                eligibility_state, mechanism_state, decision_controlada, direction, timing_deteccion,
                methodology_version, validation_state, sample_size, computed_as_of, computed_at,
                core_methodology_version, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ticker, market_date, decision_timestamp,
                gate.get("activation_state"), gate.get("reason"),
                eligibility_state, mechanism_state, decision_controlada, direction, timing_deteccion,
                le.get("methodology_version"), le.get("validation_state"), le.get("sample_size"),
                le.get("computed_as_of"), computed_at,
                core_methodology_version, now,
            ),
        )
        conn.commit()
        return True


def get_activation_states_for(ticker: str, market_date: str) -> List[Dict[str, Any]]:
    if not _db_exists():
        return []
    with _ro_connect() as conn:
        rows = conn.execute(
            """SELECT * FROM activation_state_log
               WHERE ticker=? AND market_date=? ORDER BY id ASC""",
            (ticker, market_date),
        ).fetchall()
    return [_row(r) for r in rows]


def list_activation_states(
    market_date: Optional[str] = None,
    activation_state: Optional[str] = None,
    limit: int = 5000,
) -> List[Dict[str, Any]]:
    if not _db_exists():
        return []
    query = "SELECT * FROM activation_state_log WHERE 1=1"
    params: List[Any] = []
    if market_date is not None:
        query += " AND market_date=?"
        params.append(market_date)
    if activation_state is not None:
        query += " AND activation_state=?"
        params.append(activation_state)
    query += " ORDER BY id ASC LIMIT ?"
    params.append(limit)
    with _ro_connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row(r) for r in rows]


NOTA_ALCANCE = (
    "Reporte offline de solo lectura (Hito 3, Fase 3.5). apply_recalibration=True "
    "solo se ejecuta de forma aislada cuando este mecanismo esta ON_CONTROLADO "
    "y el gate determino ACTIVADO -- nunca modifica una decision real, nunca "
    "genera ordenes, nunca se conecta a un broker (no existe ninguno en este "
    "repo). decision_controlada es exclusivamente de auditoria."
)


def full_activation_report(
    market_date: Optional[str] = None,
    activation_state: Optional[str] = None,
    limit: int = 5000,
) -> Dict[str, Any]:
    """Reporte de solo lectura -- mismo estilo que
    `full_eligibility_report()`/`full_tribunal_report()`/
    `full_shadow_observation_report()`. Nunca lanza."""
    try:
        eventos = list_activation_states(market_date=market_date, activation_state=activation_state, limit=limit)
        conteos: Dict[str, int] = {estado: 0 for estado in ("NO_ACTIVO", "ACTIVADO", "BLOQUEADO", "REVOCADO")}
        for evento in eventos:
            conteos[evento["activation_state"]] = conteos.get(evento["activation_state"], 0) + 1
        return {
            "ok": True,
            "nota": NOTA_ALCANCE,
            "mechanism_state_actual": get_mechanism_state(),
            "n_eventos": len(eventos),
            "conteos_por_estado": conteos,
            "eventos": eventos,
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False, "nota": NOTA_ALCANCE, "mechanism_state_actual": "OFF",
            "n_eventos": 0, "conteos_por_estado": {}, "eventos": [], "error": str(exc),
        }
