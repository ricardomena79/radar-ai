"""Registro de catalizadores/noticias (2026-08-23, Motor de Catalizadores).

Base persistente `catalyst_events.db` (config.db_path -> Volume Railway),
MISMO patrón exacto que `atlas_live/radar/candidate_registry.py`:

  - catalyst_event: una fila por catalizador REAL distinto. Dedup por
    (ticker, source, source_id) cuando el proveedor trae un id (noticias
    de Finnhub) -- upsert (INSERT ... ON CONFLICT), nunca duplica.
  - catalyst_lifecycle_log: append-only, una fila por CAMBIO de
    lifecycle_state (mismo criterio que alert_stage_log -- nunca una fila
    por cada sondeo, solo por transición real).
  - catalyst_score_snapshot: write-once por (ticker, market_date) --
    congela CATALYST_SCORE/MRNA_SIMILARITY_SCORE la primera vez que un
    catalizador cruza el piso de relevancia, mismo criterio "congelar
    para calificar después" que magnitud_prediction.
  - catalyst_poll_state: 1 fila por ticker, salud del último sondeo --
    alimenta el banner "NEWS/CATALYST DATA OFFLINE" (Fase 10).

Nunca se lee desde candidate_gates.py, el score en vivo ni
decision_engine.py -- capa aparte, cruzada solo en el endpoint de
lectura (server.py)."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.config.config import db_path

DB_PATH = db_path("catalyst_events.db", default=Path(__file__).parent)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalyst_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    catalyst_type TEXT NOT NULL,
    headline TEXT NOT NULL,
    summary TEXT,
    source TEXT NOT NULL,
    source_id TEXT,
    url TEXT,
    published_at TEXT,
    event_date TEXT,
    event_time TEXT,
    importance TEXT NOT NULL,
    direction TEXT NOT NULL,
    confidence REAL NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(ticker, source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_catalyst_ticker ON catalyst_event(ticker);
CREATE INDEX IF NOT EXISTS idx_catalyst_event_date ON catalyst_event(event_date);
CREATE INDEX IF NOT EXISTS idx_catalyst_published ON catalyst_event(published_at);

CREATE TABLE IF NOT EXISTS catalyst_lifecycle_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalyst_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL,
    days_to_event REAL,
    price_change_since_published_pct REAL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lifecycle_catalyst ON catalyst_lifecycle_log(catalyst_id);

CREATE TABLE IF NOT EXISTS catalyst_score_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    market_date TEXT NOT NULL,
    catalyst_id INTEGER,
    frozen_at TEXT NOT NULL,
    catalyst_score REAL NOT NULL,
    mrna_similarity_score REAL NOT NULL,
    score_components TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(ticker, market_date)
);
CREATE INDEX IF NOT EXISTS idx_score_snapshot_date ON catalyst_score_snapshot(market_date);

CREATE TABLE IF NOT EXISTS catalyst_poll_state (
    ticker TEXT PRIMARY KEY,
    last_polled_at TEXT NOT NULL,
    last_poll_ok INTEGER NOT NULL,
    last_error TEXT,
    n_events_found INTEGER NOT NULL DEFAULT 0
);
"""

_schema_ready_for: Optional[str] = None


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    global _schema_ready_for
    if _schema_ready_for != str(DB_PATH):
        conn.executescript(_SCHEMA)
        _schema_ready_for = str(DB_PATH)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    return dict(r)


# --------------------------- catalyst_event ---------------------------

def upsert_catalyst_event(
    ticker: str, catalyst_type: str, headline: str, source: str,
    importance: str, direction: str, confidence: float,
    summary: Optional[str] = None, source_id: Optional[str] = None,
    url: Optional[str] = None, published_at: Optional[str] = None,
    event_date: Optional[str] = None, event_time: Optional[str] = None,
) -> int:
    """Upsert por (ticker, source, source_id) -- re-sondear la misma
    noticia solo actualiza `last_seen_at`/importancia/dirección, nunca
    duplica la fila. `source_id=None` (filas de calendario sin id de
    noticia) cae en el mismo UNIQUE con NULL -- SQLite trata cada NULL
    como distinto, así que una fila de calendario para el mismo
    (ticker, source) puede repetirse; se acepta a propósito (el
    dedup real de calendario lo hace el collector por (ticker, event_date)
    antes de llamar acá, ver catalyst_collector.py)."""
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO catalyst_event
               (ticker, catalyst_type, headline, summary, source, source_id, url,
                published_at, event_date, event_time, importance, direction,
                confidence, first_seen_at, last_seen_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(ticker, source, source_id) DO UPDATE SET
                 headline=excluded.headline, summary=excluded.summary, url=excluded.url,
                 published_at=excluded.published_at, event_date=excluded.event_date,
                 event_time=excluded.event_time, importance=excluded.importance,
                 direction=excluded.direction, confidence=excluded.confidence,
                 last_seen_at=excluded.last_seen_at""",
            (ticker, catalyst_type, headline, summary, source, source_id, url,
             published_at, event_date, event_time, importance, direction,
             confidence, now, now, now),
        )
        conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        row = conn.execute(
            "SELECT id FROM catalyst_event WHERE ticker=? AND source=? AND source_id IS ?",
            (ticker, source, source_id),
        ).fetchone()
        return row["id"] if row else -1


def get_events_for_ticker(ticker: str, market_date: Optional[str] = None) -> List[Dict[str, Any]]:
    query = "SELECT * FROM catalyst_event WHERE ticker=?"
    params: tuple = (ticker,)
    if market_date:
        query += " AND (event_date=? OR substr(published_at,1,10)=?)"
        params = (ticker, market_date, market_date)
    query += " ORDER BY last_seen_at DESC"
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
        return [_row(r) for r in rows]


def list_recent_events(limit: int = 100) -> List[Dict[str, Any]]:
    """Feed de noticias (Fase 8) -- más reciente primero, por
    `last_seen_at` (siempre poblado, a diferencia de `published_at` que
    puede faltar en filas de calendario)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM catalyst_event ORDER BY last_seen_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row(r) for r in rows]


def list_upcoming_events(days_ahead: int = 7, reference_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """Panel de catalizadores (Fase 7) -- eventos con `event_date` entre
    `reference_date - days_ahead` y `reference_date + days_ahead` (para
    que OCURRIDO/EXTENDIDA recientes también puedan mostrarse -- el
    filtro real de qué se destaca vive en el endpoint, esto es lectura
    amplia por ventana de fecha, no un recorte de "solo futuro")."""
    ref = reference_date or datetime.now(timezone.utc).date().isoformat()
    ref_dt = datetime.fromisoformat(ref)
    desde = (ref_dt - timedelta(days=days_ahead)).date().isoformat()
    hasta = (ref_dt + timedelta(days=days_ahead)).date().isoformat()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM catalyst_event WHERE event_date IS NOT NULL "
            "AND event_date >= ? AND event_date <= ? ORDER BY event_date ASC LIMIT 500",
            (desde, hasta),
        ).fetchall()
        return [_row(r) for r in rows]


# --------------------------- catalyst_lifecycle_log ---------------------------

def latest_lifecycle_state(catalyst_id: int) -> Optional[str]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT lifecycle_state FROM catalyst_lifecycle_log WHERE catalyst_id=? "
            "ORDER BY observed_at DESC LIMIT 1",
            (catalyst_id,),
        ).fetchone()
        return row["lifecycle_state"] if row else None


def record_lifecycle_transition(
    catalyst_id: int, ticker: str, observed_at: str, lifecycle_state: str,
    days_to_event: Optional[float] = None,
    price_change_since_published_pct: Optional[float] = None,
) -> bool:
    """Registra una fila SOLO si `lifecycle_state` cambió desde la última
    registrada para este catalizador (mismo guard que
    `record_alert_stage`). Devuelve True si insertó."""
    if latest_lifecycle_state(catalyst_id) == lifecycle_state:
        return False
    with _connect() as conn:
        conn.execute(
            """INSERT INTO catalyst_lifecycle_log
               (catalyst_id, ticker, observed_at, lifecycle_state, days_to_event,
                price_change_since_published_pct, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (catalyst_id, ticker, observed_at, lifecycle_state, days_to_event,
             price_change_since_published_pct, _now()),
        )
        conn.commit()
        return True


# --------------------------- catalyst_score_snapshot ---------------------------

def get_score_snapshot(ticker: str, market_date: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM catalyst_score_snapshot WHERE ticker=? AND market_date=?",
            (ticker, market_date),
        ).fetchone()
        return _row(row) if row else None


def record_score_snapshot(
    ticker: str, market_date: str, frozen_at: str, catalyst_score: float,
    mrna_similarity_score: float, score_components: Dict[str, Any],
    catalyst_id: Optional[int] = None,
) -> bool:
    """Write-once por (ticker, market_date) -- INSERT OR IGNORE, mismo
    criterio que magnitud_prediction: la predicción/score congelado nunca
    se pisa, para poder calificarlo después contra el resultado real."""
    with _connect() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO catalyst_score_snapshot
               (ticker, market_date, catalyst_id, frozen_at, catalyst_score,
                mrna_similarity_score, score_components, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (ticker, market_date, catalyst_id, frozen_at, catalyst_score,
             mrna_similarity_score, json.dumps(score_components, ensure_ascii=False), _now()),
        )
        conn.commit()
        return cur.rowcount > 0


# --------------------------- catalyst_poll_state ---------------------------

def set_poll_state(ticker: str, ok: bool, error: Optional[str] = None, n_events: int = 0) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO catalyst_poll_state (ticker, last_polled_at, last_poll_ok, last_error, n_events_found)
               VALUES (?,?,?,?,?)
               ON CONFLICT(ticker) DO UPDATE SET
                 last_polled_at=excluded.last_polled_at, last_poll_ok=excluded.last_poll_ok,
                 last_error=excluded.last_error, n_events_found=excluded.n_events_found""",
            (ticker, _now(), int(ok), error, n_events),
        )
        conn.commit()


def get_poll_state(ticker: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM catalyst_poll_state WHERE ticker=?", (ticker,)).fetchone()
        return _row(row) if row else None


def provider_health_summary(stale_after_minutes: int = 30) -> Dict[str, Any]:
    """Fase 10 -- resume la salud real del proveedor de catalizadores
    para el banner de la Cabina. `status`:
      - "SIN_CONFIGURAR" -- catalyst_poll_state está vacía (el worker
        nunca corrió, típicamente sin FINNHUB_API_KEY).
      - "OFFLINE" -- hay historial, pero NINGÚN sondeo reciente
        (últimos `stale_after_minutes`) salió OK.
      - "OK" -- al menos un sondeo reciente salió bien."""
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM catalyst_poll_state").fetchone()["n"]
        if not total:
            return {"status": "SIN_CONFIGURAR", "last_successful_poll_at": None, "reason": "El motor de catalizadores todavía no corrió ningún sondeo."}

        cutoff = datetime.now(timezone.utc).timestamp() - stale_after_minutes * 60
        rows = conn.execute(
            "SELECT last_polled_at, last_poll_ok FROM catalyst_poll_state"
        ).fetchall()
        recientes_ok = [
            r for r in rows
            if r["last_poll_ok"] and datetime.fromisoformat(r["last_polled_at"]).timestamp() >= cutoff
        ]
        ultimo_ok = conn.execute(
            "SELECT MAX(last_polled_at) AS t FROM catalyst_poll_state WHERE last_poll_ok=1"
        ).fetchone()["t"]

        if recientes_ok:
            return {"status": "OK", "last_successful_poll_at": ultimo_ok, "reason": None}
        return {
            "status": "OFFLINE", "last_successful_poll_at": ultimo_ok,
            "reason": f"Sin sondeos exitosos en los últimos {stale_after_minutes} minutos.",
        }
