"""Registro de candidatas del radar de universo completo (2026-08-14).

Base persistente `radar_candidates.db` (config.db_path -> Volume Railway).
Separación anti-leakage (mismo criterio que `signal_registry`/
`study_registry`):

  - candidate_detection: condiciones EN el momento de la primera detección
    (precio, cambio %, volumen, RVOL, qué puertas dispararon). WRITE-ONCE
    por (ticker, market_date) -- una candidata se detecta una sola vez por
    día, nunca se re-detecta ni se pisa.
  - candidate_observation: seguimiento continuo (append-only) -- una
    candidata NO desaparece si deja de estar entre las primeras; cada
    barrido que la sigue viendo agrega una fila.
  - candidate_intraday_metrics: resultado del análisis de 1 minuto
    (velocidad, aceleración, VWAP, RVOL intradía, fase) -- tabla separada,
    se llena después y por separado de la detección.
  - candidate_outcome: RESULTADO real posterior (máximo alcanzado, bandas
    +20/+50/+100%, categoría) -- se calcula SOLO al cierre del mercado,
    nunca durante el día (evita fuga de información hacia la detección).

Idempotente: `record_detection`/`record_outcome` usan INSERT OR IGNORE por
(ticker, market_date). Nunca se pierde una candidata en silencio -- toda
fila de `candidate_detection` queda, se resuelva bien o mal.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.config.config import db_path

DB_PATH = db_path("radar_candidates.db", default=Path(__file__).parent)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidate_detection (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    market_date TEXT NOT NULL,
    session TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    sweep_id TEXT,
    price_at_detection REAL,
    change_pct_at_detection REAL,
    volume_at_detection INTEGER,
    average_volume_at_detection INTEGER,
    relative_volume_at_detection REAL,
    dollar_volume_at_detection REAL,
    gates_fired TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'tradier',
    created_at TEXT NOT NULL,
    UNIQUE(ticker, market_date)
);
CREATE INDEX IF NOT EXISTS idx_det_date ON candidate_detection(market_date);

CREATE TABLE IF NOT EXISTS candidate_observation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    market_date TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    sweep_id TEXT,
    price REAL,
    change_pct REAL,
    volume INTEGER,
    relative_volume REAL,
    gates_fired_now TEXT,
    vwap REAL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_ticker_date ON candidate_observation(ticker, market_date);

CREATE TABLE IF NOT EXISTS candidate_intraday_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    market_date TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    vwap REAL,
    price_vs_vwap_pct REAL,
    velocity_pct_per_min REAL,
    acceleration REAL,
    rvol_intradia REAL,
    lifecycle_phase TEXT,
    n_velas_analizadas INTEGER,
    notes TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intraday_ticker_date ON candidate_intraday_metrics(ticker, market_date);

CREATE TABLE IF NOT EXISTS candidate_outcome (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    market_date TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    run_up_before_detection_pct REAL,
    max_price_after_detection REAL,
    max_return_after_detection_pct REAL,
    minutes_to_max REAL,
    reached_20 INTEGER NOT NULL DEFAULT 0,
    reached_50 INTEGER NOT NULL DEFAULT 0,
    reached_100 INTEGER NOT NULL DEFAULT 0,
    category TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(ticker, market_date)
);
CREATE INDEX IF NOT EXISTS idx_outcome_date ON candidate_outcome(market_date);

CREATE TABLE IF NOT EXISTS radar_meta (
    key TEXT PRIMARY KEY,
    value TEXT
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


# --------------------------- detección ---------------------------

def is_detected(ticker: str, market_date: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM candidate_detection WHERE ticker=? AND market_date=?", (ticker, market_date)
        ).fetchone()
        return row is not None


def record_detection(
    ticker: str, market_date: str, session: str, detected_at: str, sweep_id: str,
    price_at_detection: Optional[float], change_pct_at_detection: Optional[float],
    volume_at_detection: Optional[int], average_volume_at_detection: Optional[int],
    relative_volume_at_detection: Optional[float], dollar_volume_at_detection: Optional[float],
    gates_fired: List[Dict[str, Any]], source: str = "tradier",
) -> bool:
    """Registra la primera detección. Devuelve True si fue nueva (INSERT
    real), False si ya existía (idempotente, nunca se pisa)."""
    with _connect() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO candidate_detection
               (ticker, market_date, session, detected_at, sweep_id, price_at_detection,
                change_pct_at_detection, volume_at_detection, average_volume_at_detection,
                relative_volume_at_detection, dollar_volume_at_detection, gates_fired, source, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ticker, market_date, session, detected_at, sweep_id, price_at_detection,
             change_pct_at_detection, volume_at_detection, average_volume_at_detection,
             relative_volume_at_detection, dollar_volume_at_detection,
             json.dumps(gates_fired, ensure_ascii=False), source, _now()),
        )
        conn.commit()
        return cur.rowcount > 0


def record_observation(
    ticker: str, market_date: str, observed_at: str, sweep_id: str,
    price: Optional[float], change_pct: Optional[float], volume: Optional[int],
    relative_volume: Optional[float], gates_fired_now: List[Dict[str, Any]],
    vwap: Optional[float] = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO candidate_observation
               (ticker, market_date, observed_at, sweep_id, price, change_pct, volume,
                relative_volume, gates_fired_now, vwap, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (ticker, market_date, observed_at, sweep_id, price, change_pct, volume,
             relative_volume, json.dumps(gates_fired_now, ensure_ascii=False), vwap, _now()),
        )
        conn.commit()


def get_observations(ticker: str, market_date: str) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM candidate_observation WHERE ticker=? AND market_date=? ORDER BY observed_at",
            (ticker, market_date),
        ).fetchall()
        return [_row(r) for r in rows]


def list_candidates_for_date(market_date: str) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM candidate_detection WHERE market_date=? ORDER BY detected_at", (market_date,)
        ).fetchall()
        out = []
        for r in rows:
            d = _row(r)
            d["gates_fired"] = json.loads(d["gates_fired"]) if d.get("gates_fired") else []
            out.append(d)
        return out


def count_candidates_for_date(market_date: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM candidate_detection WHERE market_date=?", (market_date,)
        ).fetchone()
        return row["n"] if row else 0


# --------------------------- análisis 1 minuto ---------------------------

def record_intraday_metrics(
    ticker: str, market_date: str, vwap: Optional[float], price_vs_vwap_pct: Optional[float],
    velocity_pct_per_min: Optional[float], acceleration: Optional[float],
    rvol_intradia: Optional[float], lifecycle_phase: Optional[str], n_velas_analizadas: int,
    notes: Optional[str] = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO candidate_intraday_metrics
               (ticker, market_date, computed_at, vwap, price_vs_vwap_pct, velocity_pct_per_min,
                acceleration, rvol_intradia, lifecycle_phase, n_velas_analizadas, notes, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ticker, market_date, _now(), vwap, price_vs_vwap_pct, velocity_pct_per_min,
             acceleration, rvol_intradia, lifecycle_phase, n_velas_analizadas, notes, _now()),
        )
        conn.commit()


def get_latest_intraday_metrics(ticker: str, market_date: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM candidate_intraday_metrics WHERE ticker=? AND market_date=?
               ORDER BY computed_at DESC LIMIT 1""",
            (ticker, market_date),
        ).fetchone()
        return _row(row) if row else None


# --------------------------- resultado (EOD) ---------------------------

def has_outcome(ticker: str, market_date: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM candidate_outcome WHERE ticker=? AND market_date=?", (ticker, market_date)
        ).fetchone()
        return row is not None


def record_outcome(
    ticker: str, market_date: str, run_up_before_detection_pct: Optional[float],
    max_price_after_detection: Optional[float], max_return_after_detection_pct: Optional[float],
    minutes_to_max: Optional[float], reached_20: bool, reached_50: bool, reached_100: bool,
    category: str, notes: Optional[str] = None,
) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO candidate_outcome
               (ticker, market_date, computed_at, run_up_before_detection_pct, max_price_after_detection,
                max_return_after_detection_pct, minutes_to_max, reached_20, reached_50, reached_100,
                category, notes, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ticker, market_date, _now(), run_up_before_detection_pct, max_price_after_detection,
             max_return_after_detection_pct, minutes_to_max, int(reached_20), int(reached_50),
             int(reached_100), category, notes, _now()),
        )
        conn.commit()
        return cur.rowcount > 0


def list_outcomes_for_date(market_date: str) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM candidate_outcome WHERE market_date=? ORDER BY max_return_after_detection_pct DESC",
            (market_date,),
        ).fetchall()
        return [_row(r) for r in rows]


# --------------------------- meta / diagnóstico ---------------------------

def set_meta(**kwargs) -> None:
    with _connect() as conn:
        for k, v in kwargs.items():
            conn.execute(
                "INSERT INTO radar_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v),
            )
        conn.commit()


def get_meta() -> Dict[str, Any]:
    with _connect() as conn:
        rows = conn.execute("SELECT key, value FROM radar_meta").fetchall()
        out = {}
        for r in rows:
            v = r["value"]
            try:
                out[r["key"]] = json.loads(v)
            except (TypeError, ValueError):
                out[r["key"]] = v
        return out


def radar_status() -> Dict[str, Any]:
    meta = get_meta()
    today = meta.get("current_market_date")
    n_hoy = count_candidates_for_date(today) if today else 0
    return {
        "state": meta.get("state", "IDLE"),
        "session_actual": meta.get("session_actual"),
        "sweeps_total": meta.get("sweeps_total", 0),
        "sweeps_ok": meta.get("sweeps_ok", 0),
        "sweeps_error": meta.get("sweeps_error", 0),
        "ultimo_sweep_at": meta.get("ultimo_sweep_at"),
        "ultimo_sweep_duracion_s": meta.get("ultimo_sweep_duracion_s"),
        "ultimo_error": meta.get("ultimo_error"),
        "candidatas_hoy": n_hoy,
        "market_date_actual": today,
        "eod_ejecutado_para": meta.get("eod_ejecutado_para"),
    }
