"""Registro de Señales de Atlas (2026-08-09).

Materia prima de la VALIDACIÓN EN VIVO: registra las oportunidades reales que
el scanner existente detecta en premarket/apertura, y -- por separado -- el
resultado posterior de cada una. Es un REGISTRO, no un detector: no decide
"compra", solo guarda qué se vio y, después, qué pasó.

REGLA FUNDAMENTAL (anti-leakage), impuesta por el propio esquema:
  - `signals`      = información disponible EN EL MOMENTO DE DETECCIÓN.
                     Los campos de detección son WRITE-ONCE (nunca se
                     reescriben); solo `state` transiciona de forma
                     controlada. NO tiene ninguna columna de resultado.
  - `signal_observations` = la trayectoria de seguimiento (append-only), lo
                     que se fue viendo ciclo a ciclo DESPUÉS de detectar.
  - `signal_results` = el resultado calculado al cerrar (hitos, máximo, fin
                     de impulso...). Tabla FÍSICAMENTE separada: es imposible
                     que un valor futuro entre en la señal original.

Deduplicación: la identidad de una oportunidad es (ticker, market_date). El
polling puede AGREGAR observaciones, pero nunca crea una segunda señal ni
reescribe la detección (INSERT OR IGNORE sobre la clave única).

Persistencia: `signal_registry.db` vía `config.db_path` -> sobrevive a
reinicios/deploys en el Volume de Railway, igual que el resto de las bases.
Cero mock: si no hay señales, las consultas devuelven listas vacías.
"""

import json
import sqlite3
import uuid as _uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.config.config import db_path

DB_PATH = db_path("signal_registry.db", default=Path(__file__).parent)

# --- Estados de una señal (máquina de estados controlada) ---
DETECTADA = "DETECTADA"
OBSERVANDO = "OBSERVANDO"
RESUELTA_ACIERTO = "RESUELTA_ACIERTO"
RESUELTA_FALLO = "RESUELTA_FALLO"
RESUELTA_SIN_DATOS = "RESUELTA_SIN_DATOS"
DESCARTADA = "DESCARTADA"

_RESUELTA = {RESUELTA_ACIERTO, RESUELTA_FALLO, RESUELTA_SIN_DATOS}
STATES = {DETECTADA, OBSERVANDO} | _RESUELTA | {DESCARTADA}

# Transiciones permitidas. Los estados RESUELTA_* son terminales (una señal
# resuelta no vuelve a "nunca detectada"); DESCARTADA es terminal también.
_ALLOWED_TRANSITIONS = {
    DETECTADA: {OBSERVANDO, DESCARTADA} | _RESUELTA,
    OBSERVANDO: _RESUELTA | {DESCARTADA},
}


class InvalidTransitionError(Exception):
    """Se intentó una transición de estado no permitida."""


class AlreadyResolvedError(Exception):
    """Se intentó escribir un resultado para una señal que ya tiene uno."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_uuid TEXT NOT NULL UNIQUE,
    ticker TEXT NOT NULL,
    exchange TEXT,
    name TEXT,
    market_date TEXT NOT NULL,
    session TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    price_at_detection REAL,
    price_as_of TEXT,
    provider TEXT,
    features_json TEXT,
    score REAL,
    reasons_json TEXT,
    conditions_json TEXT,
    historical_group TEXT,
    similar_historical_cases INTEGER,
    detector_version TEXT,
    feature_version TEXT,
    data_version TEXT,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(ticker, market_date)
);
CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(market_date);
CREATE INDEX IF NOT EXISTS idx_signals_state ON signals(state);

-- Seguimiento (append-only): la trayectoria observada DESPUÉS de detectar.
CREATE TABLE IF NOT EXISTS signal_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_uuid TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    session TEXT,
    price REAL,
    return_pct REAL,
    recorded_at TEXT NOT NULL,
    UNIQUE(signal_uuid, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_obs_signal ON signal_observations(signal_uuid);

-- Resultado (separado): el desenlace calculado al cerrar la trayectoria.
CREATE TABLE IF NOT EXISTS signal_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_uuid TEXT NOT NULL UNIQUE,
    resolved_at TEXT NOT NULL,
    max_return_pct REAL,
    max_at TEXT,
    return_at_10min REAL,
    minutes_to_1pct REAL,
    minutes_to_3pct REAL,
    minutes_to_10pct REAL,
    minutes_to_30pct REAL,
    minutes_to_50pct REAL,
    minutes_to_100pct REAL,
    minutes_to_150pct REAL,
    minutes_to_200pct REAL,
    continued_after_open INTEGER,
    lost_momentum INTEGER,
    momentum_end_at TEXT,
    retracement_pct REAL,
    result TEXT,
    resolution_reason TEXT,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    _ensure_columns(conn)
    return conn


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Migración aditiva NO destructiva: agrega columnas nuevas a DBs ya
    pobladas, sin borrar ni recrear filas. Idempotente."""
    sig = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
    for col in ("exchange", "name"):
        if col not in sig:
            conn.execute(f"ALTER TABLE signals ADD COLUMN {col} TEXT")
    res = {r[1] for r in conn.execute("PRAGMA table_info(signal_results)")}
    for col in ("minutes_to_1pct", "minutes_to_3pct"):
        if col not in res:
            conn.execute(f"ALTER TABLE signal_results ADD COLUMN {col} REAL")
    conn.commit()


def _tradingview_symbol(exchange: Optional[str], ticker: str) -> str:
    """Símbolo TradingView `PREFIJO:TICKER` sin depender del módulo de estudio."""
    from atlas_live.market_study.universe import tradingview_symbol
    return tradingview_symbol(exchange, ticker)


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    return dict(r)


def _signal_row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    d["features"] = json.loads(d["features_json"]) if d.get("features_json") else None
    d["reasons"] = json.loads(d["reasons_json"]) if d.get("reasons_json") else None
    d["conditions"] = json.loads(d["conditions_json"]) if d.get("conditions_json") else None
    d["tradingview_symbol"] = _tradingview_symbol(d.get("exchange"), d["ticker"])
    return d


# ---------------------------------------------------------------------------
# Detección (write-once) + deduplicación
# ---------------------------------------------------------------------------

def register_signal(
    ticker: str,
    market_date: str,
    session: str,
    detected_at: str,
    price_at_detection: Optional[float],
    price_as_of: Optional[str],
    provider: Optional[str],
    features: Optional[Dict[str, Any]],
    score: Optional[float],
    reasons: Optional[List[str]],
    conditions: Optional[List[str]],
    historical_group: Optional[str],
    similar_historical_cases: Optional[int],
    detector_version: str,
    feature_version: str,
    data_version: str,
    exchange: Optional[str] = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """Registra la DETECCIÓN de una oportunidad. Idempotente por
    (ticker, market_date): si ya existe una señal para esa oportunidad, NO se
    reescribe la detección -- se devuelve la existente con `created=False`.
    Solo campos de detección: esta función no acepta ningún dato de resultado.
    `exchange`/`name` son IDENTIDAD del instrumento (no resultado).
    """
    new_uuid = str(_uuid.uuid4())
    with closing(_connect()) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO signals ("
            "signal_uuid, ticker, exchange, name, market_date, session, detected_at, price_at_detection, price_as_of, provider, "
            "features_json, score, reasons_json, conditions_json, historical_group, similar_historical_cases, "
            "detector_version, feature_version, data_version, state, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_uuid, ticker, exchange, name, market_date, session, detected_at, price_at_detection, price_as_of, provider,
                json.dumps(features, ensure_ascii=False) if features is not None else None,
                score,
                json.dumps(reasons, ensure_ascii=False) if reasons is not None else None,
                json.dumps(conditions, ensure_ascii=False) if conditions is not None else None,
                historical_group, similar_historical_cases,
                detector_version, feature_version, data_version, DETECTADA, _now(),
            ),
        )
        conn.commit()
        created = cur.rowcount > 0
        row = conn.execute(
            "SELECT * FROM signals WHERE ticker = ? AND market_date = ?", (ticker, market_date)
        ).fetchone()
    result = _signal_row(row)
    result["created"] = created
    return result


def get_signal(signal_uuid: str) -> Optional[Dict[str, Any]]:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM signals WHERE signal_uuid = ?", (signal_uuid,)).fetchone()
    return _signal_row(row) if row else None


def get_signal_by_opportunity(ticker: str, market_date: str) -> Optional[Dict[str, Any]]:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT * FROM signals WHERE ticker = ? AND market_date = ?", (ticker, market_date)
        ).fetchone()
    return _signal_row(row) if row else None


# ---------------------------------------------------------------------------
# Estados (transiciones controladas)
# ---------------------------------------------------------------------------

def set_state(signal_uuid: str, new_state: str) -> None:
    if new_state not in STATES:
        raise ValueError(f"Estado inválido: {new_state!r}")
    with closing(_connect()) as conn:
        row = conn.execute("SELECT state FROM signals WHERE signal_uuid = ?", (signal_uuid,)).fetchone()
        if row is None:
            raise KeyError(f"No existe señal {signal_uuid!r}")
        current = row["state"]
        if current == new_state:
            return
        allowed = _ALLOWED_TRANSITIONS.get(current, set())
        if new_state not in allowed:
            raise InvalidTransitionError(f"Transición no permitida: {current} -> {new_state}")
        conn.execute("UPDATE signals SET state = ? WHERE signal_uuid = ?", (new_state, signal_uuid))
        conn.commit()


# ---------------------------------------------------------------------------
# Seguimiento (observaciones append-only)
# ---------------------------------------------------------------------------

def record_observation(
    signal_uuid: str, observed_at: str, return_pct: Optional[float],
    price: Optional[float] = None, session: Optional[str] = None,
) -> bool:
    """Agrega un punto de seguimiento. Idempotente por (signal_uuid,
    observed_at). Al primer seguimiento, si la señal estaba DETECTADA pasa a
    OBSERVANDO. NO toca la detección original. Devuelve True si insertó."""
    with closing(_connect()) as conn:
        sig = conn.execute("SELECT state FROM signals WHERE signal_uuid = ?", (signal_uuid,)).fetchone()
        if sig is None:
            raise KeyError(f"No existe señal {signal_uuid!r}")
        cur = conn.execute(
            "INSERT OR IGNORE INTO signal_observations (signal_uuid, observed_at, session, price, return_pct, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (signal_uuid, observed_at, session, price, return_pct, _now()),
        )
        inserted = cur.rowcount > 0
        if inserted and sig["state"] == DETECTADA:
            conn.execute("UPDATE signals SET state = ? WHERE signal_uuid = ?", (OBSERVANDO, signal_uuid))
        conn.commit()
    return inserted


def get_observations(signal_uuid: str) -> List[Dict[str, Any]]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM signal_observations WHERE signal_uuid = ? ORDER BY observed_at ASC", (signal_uuid,)
        ).fetchall()
    return [_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Resultado (tabla separada, write-once por señal)
# ---------------------------------------------------------------------------

_RESULT_FIELDS = (
    "max_return_pct", "max_at", "return_at_10min",
    "minutes_to_1pct", "minutes_to_3pct",
    "minutes_to_10pct", "minutes_to_30pct", "minutes_to_50pct",
    "minutes_to_100pct", "minutes_to_150pct", "minutes_to_200pct",
    "continued_after_open", "lost_momentum", "momentum_end_at",
    "retracement_pct", "result", "resolution_reason",
)


def record_result(signal_uuid: str, resolved_at: str, result_state: str, **fields: Any) -> None:
    """Escribe el resultado de una señal en la tabla SEPARADA `signal_results`
    y marca la señal con su estado RESUELTA_*. Write-once: si ya tiene
    resultado, levanta `AlreadyResolvedError` (no se sobrescribe un
    desenlace). `result_state` debe ser uno de los RESUELTA_*."""
    if result_state not in _RESUELTA:
        raise ValueError(f"result_state debe ser RESUELTA_*: {result_state!r}")
    with closing(_connect()) as conn:
        sig = conn.execute("SELECT state FROM signals WHERE signal_uuid = ?", (signal_uuid,)).fetchone()
        if sig is None:
            raise KeyError(f"No existe señal {signal_uuid!r}")
        existing = conn.execute("SELECT 1 FROM signal_results WHERE signal_uuid = ?", (signal_uuid,)).fetchone()
        if existing is not None:
            raise AlreadyResolvedError(f"La señal {signal_uuid!r} ya tiene resultado -- no se sobrescribe.")
        cols = ["signal_uuid", "resolved_at"] + list(_RESULT_FIELDS) + ["created_at"]
        vals = [signal_uuid, resolved_at] + [fields.get(f) for f in _RESULT_FIELDS] + [_now()]
        placeholders = ", ".join("?" for _ in cols)
        conn.execute(f"INSERT INTO signal_results ({', '.join(cols)}) VALUES ({placeholders})", vals)
        # Estado: RESUELTA_* (terminal). Se permite desde DETECTADA/OBSERVANDO.
        if sig["state"] not in _RESUELTA:
            conn.execute("UPDATE signals SET state = ? WHERE signal_uuid = ?", (result_state, signal_uuid))
        conn.commit()


def get_result(signal_uuid: str) -> Optional[Dict[str, Any]]:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM signal_results WHERE signal_uuid = ?", (signal_uuid,)).fetchone()
    return _row(row) if row else None


# ---------------------------------------------------------------------------
# Consultas / listados
# ---------------------------------------------------------------------------

def list_signals(market_date: Optional[str] = None, state: Optional[str] = None,
                 limit: int = 500) -> List[Dict[str, Any]]:
    query = "SELECT * FROM signals WHERE 1=1"
    params: List[Any] = []
    if market_date is not None:
        query += " AND market_date = ?"; params.append(market_date)
    if state is not None:
        query += " AND state = ?"; params.append(state)
    query += " ORDER BY detected_at DESC LIMIT ?"; params.append(limit)
    with closing(_connect()) as conn:
        rows = conn.execute(query, params).fetchall()
    return [_signal_row(r) for r in rows]


def list_active(limit: int = 500) -> List[Dict[str, Any]]:
    """Señales todavía sin resolver (DETECTADA u OBSERVANDO)."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM signals WHERE state IN (?, ?) ORDER BY detected_at DESC LIMIT ?",
            (DETECTADA, OBSERVANDO, limit),
        ).fetchall()
    return [_signal_row(r) for r in rows]


def list_results(limit: int = 500) -> List[Dict[str, Any]]:
    """Señales resueltas con su resultado (join señal + resultado)."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT s.ticker, s.market_date, s.session, s.detected_at, s.score, s.historical_group, "
            "r.* FROM signal_results r JOIN signals s ON s.signal_uuid = r.signal_uuid "
            "ORDER BY r.resolved_at DESC LIMIT ?", (limit,),
        ).fetchall()
    return [_row(r) for r in rows]


def count_signals() -> int:
    with closing(_connect()) as conn:
        return conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
