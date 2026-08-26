"""Persistencia AISLADA del Detector Unificado en modo Shadow (2026-08-26,
U3-C2, autorizado explícitamente).

DB propia (`shadow_unified_detector.db`, vía `db_path()`, mismo patrón que
`live_experience_knowledge.py`) -- deliberadamente SIN importar nada de
`candidate_registry.py`: este registro shadow no debe depender
estructuralmente del registro de candidatas real, y sobre todo, el registro
real NO debe enterarse de que esto existe. Append-only, sin excepción: cada
barrido que dispara al menos una puerta agrega una fila nueva, nunca se
actualiza ni se borra una fila existente -- necesario para poder reconstruir
la secuencia exacta de disparos (punto 10/11 de la autorización: "permitir
comparar exactamente LEGACY vs UNIFIED, no solamente si apareció").

NUNCA escribe en `candidate_detection` ni en ninguna tabla de
`atlas_live/radar/candidate_registry.py` -- confirmado por test estructural
(`test_unified_detector.py::test_..._nunca_escribe_candidate_detection`)."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.config.config import db_path

DB_PATH = db_path("shadow_unified_detector.db", default=Path(__file__).parent)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_candidate_detection (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    market_date TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    session TEXT NOT NULL,
    price REAL,
    change_pct REAL,
    volume INTEGER,
    average_volume INTEGER,
    relative_volume REAL,
    dollar_volume REAL,
    price_source TEXT,
    price_basis TEXT,
    price_is_stale INTEGER,
    universe_source TEXT NOT NULL,
    gates_fired TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shadow_ticker_date ON shadow_candidate_detection(ticker, market_date);
CREATE INDEX IF NOT EXISTS idx_shadow_market_date ON shadow_candidate_detection(market_date);
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


def record_shadow_detection(
    ticker: str,
    market_date: str,
    session: str,
    price: Optional[float],
    change_pct: Optional[float],
    volume: Optional[int],
    average_volume: Optional[int],
    relative_volume: Optional[float],
    dollar_volume: Optional[float],
    price_source: Optional[str],
    price_basis: Optional[str],
    price_is_stale: Optional[bool],
    universe_source: str,
    gates_fired: List[Dict[str, Any]],
    snapshot: Dict[str, Any],
) -> None:
    """INSERT puro, append-only -- una fila por barrido en que al menos una
    puerta disparó (mismo criterio que `candidate_tracker.py` real, pero
    totalmente aislado). `gates_fired`/`snapshot` se guardan como JSON --
    reproducibilidad exacta de qué vio `evaluate_all_gates()` en ese
    instante, sin tener que reconstruir nada después."""
    with _connect() as conn:
        conn.execute(
            """INSERT INTO shadow_candidate_detection
               (ticker, market_date, detected_at, session, price, change_pct, volume,
                average_volume, relative_volume, dollar_volume, price_source, price_basis,
                price_is_stale, universe_source, gates_fired, snapshot_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ticker, market_date, _now(), session, price, change_pct, volume,
                average_volume, relative_volume, dollar_volume, price_source, price_basis,
                int(bool(price_is_stale)) if price_is_stale is not None else None,
                universe_source, json.dumps(gates_fired), json.dumps(snapshot), _now(),
            ),
        )


def list_shadow_detections(market_date: str) -> List[Dict[str, Any]]:
    """Lectura de solo diagnóstico -- todas las detecciones shadow de un
    día, más recientes primero. `gates_fired`/`snapshot_json` se devuelven
    ya deserializados."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM shadow_candidate_detection WHERE market_date = ? ORDER BY detected_at DESC",
            (market_date,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["gates_fired"] = json.loads(d["gates_fired"])
        d["snapshot"] = json.loads(d.pop("snapshot_json"))
        out.append(d)
    return out


def count_shadow_detections(market_date: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM shadow_candidate_detection WHERE market_date = ?", (market_date,)
        ).fetchone()
    return row["n"] if row else 0
