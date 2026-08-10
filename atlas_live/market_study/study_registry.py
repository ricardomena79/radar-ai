"""Registro del estudio amplio de mercado (2026-08-10).

Base persistente `market_study.db` (config.db_path -> Volume Railway). Tres
tablas, con la MISMA separación anti-leakage que signal_registry:

  - study_checkpoint: qué símbolos ya se procesaron (para reanudar sin repetir
    ni empezar de cero). Persistencia incremental.
  - explosion_features: información conocida ANTES/EN la detección de cada
    explosión (gap de apertura, volumen previo, market cap, disponibilidad en
    Racional, versiones). WRITE-ONCE por (ticker, date).
  - explosion_outcome: el RESULTADO (máximo intradía, bandas +30/+50/+100/
    +150/+200 alcanzadas). Tabla FÍSICAMENTE separada.

Idempotente (INSERT OR IGNORE por (ticker, date)): reprocesar un símbolo no
duplica ni pisa una explosión ya registrada. Nunca inventa un valor.
"""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.config.config import db_path

DB_PATH = db_path("market_study.db", default=Path(__file__).parent)

DATA_VERSION = "0.1.0"
BANDS = [30, 50, 100, 150, 200]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS study_checkpoint (
    symbol TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    explosions_found INTEGER NOT NULL DEFAULT 0,
    note TEXT
);

CREATE TABLE IF NOT EXISTS explosion_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    prev_close REAL,
    open_price REAL,
    gap_open_pct REAL,
    prior_avg_volume REAL,
    market_cap REAL,
    available_in_racional INTEGER,
    source TEXT,
    data_version TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_feat_date ON explosion_features(date);
CREATE INDEX IF NOT EXISTS idx_feat_racional ON explosion_features(available_in_racional);

CREATE TABLE IF NOT EXISTS explosion_outcome (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    max_intraday_pct REAL,
    close_change_pct REAL,
    day_volume REAL,
    reached_30 INTEGER, reached_50 INTEGER, reached_100 INTEGER,
    reached_150 INTEGER, reached_200 INTEGER,
    band TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_out_date ON explosion_outcome(date);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    return dict(r)


# --------------------------- checkpoint ---------------------------

def is_processed(symbol: str) -> bool:
    with closing(_connect()) as conn:
        r = conn.execute("SELECT 1 FROM study_checkpoint WHERE symbol = ?", (symbol,)).fetchone()
    return r is not None


def mark_processed(symbol: str, status: str, explosions_found: int = 0, note: Optional[str] = None) -> None:
    """Marca un símbolo como procesado (idempotente: reemplaza su checkpoint).
    `status` in {ok, sin_datos, error}."""
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO study_checkpoint (symbol, status, processed_at, explosions_found, note) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET "
            "status=excluded.status, processed_at=excluded.processed_at, "
            "explosions_found=excluded.explosions_found, note=excluded.note",
            (symbol, status, _now(), explosions_found, note),
        )
        conn.commit()


def processed_symbols() -> set:
    with closing(_connect()) as conn:
        return {r[0] for r in conn.execute("SELECT symbol FROM study_checkpoint")}


# --------------------------- explosión (features + outcome) ---------------------------

def record_explosion(
    ticker: str, date: str,
    prev_close: Optional[float], open_price: Optional[float], gap_open_pct: Optional[float],
    prior_avg_volume: Optional[float], market_cap: Optional[float], available_in_racional: bool,
    max_intraday_pct: float, close_change_pct: Optional[float], day_volume: Optional[float],
    source: str = "yahoo_finance",
) -> bool:
    """Persiste UNA explosión: features (leakage-safe) y outcome en tablas
    separadas. Idempotente por (ticker, date). Devuelve True si insertó nueva.
    Las bandas alcanzadas y el máximo son RESULTADO -- van solo en outcome."""
    reached = {b: 1 if max_intraday_pct >= b else 0 for b in BANDS}
    band = "0"
    for b in BANDS:
        if max_intraday_pct >= b:
            band = str(b)
    if max_intraday_pct > 200:
        band = ">200"

    with closing(_connect()) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO explosion_features "
            "(ticker, date, prev_close, open_price, gap_open_pct, prior_avg_volume, market_cap, "
            "available_in_racional, source, data_version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ticker, date, prev_close, open_price, gap_open_pct, prior_avg_volume, market_cap,
             1 if available_in_racional else 0, source, DATA_VERSION, _now()),
        )
        inserted = cur.rowcount > 0
        conn.execute(
            "INSERT OR IGNORE INTO explosion_outcome "
            "(ticker, date, max_intraday_pct, close_change_pct, day_volume, "
            "reached_30, reached_50, reached_100, reached_150, reached_200, band, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ticker, date, max_intraday_pct, close_change_pct, day_volume,
             reached[30], reached[50], reached[100], reached[150], reached[200], band, _now()),
        )
        conn.commit()
    return inserted


def count_explosions(available_in_racional: Optional[bool] = None) -> int:
    query = "SELECT COUNT(*) FROM explosion_features WHERE 1=1"
    params: List[Any] = []
    if available_in_racional is not None:
        query += " AND available_in_racional = ?"
        params.append(1 if available_in_racional else 0)
    with closing(_connect()) as conn:
        return conn.execute(query, params).fetchone()[0]


def list_explosions(limit: int = 500, band: Optional[str] = None,
                    only_racional: bool = False) -> List[Dict[str, Any]]:
    """Explosiones con features + outcome (join). No mezcla: outcome viene de
    su tabla separada, se une solo para consulta."""
    query = (
        "SELECT f.ticker, f.date, f.gap_open_pct, f.prior_avg_volume, f.market_cap, "
        "f.available_in_racional, o.max_intraday_pct, o.band, o.day_volume, o.close_change_pct "
        "FROM explosion_features f JOIN explosion_outcome o "
        "ON f.ticker = o.ticker AND f.date = o.date WHERE 1=1"
    )
    params: List[Any] = []
    if band is not None:
        query += " AND o.band = ?"; params.append(band)
    if only_racional:
        query += " AND f.available_in_racional = 1"
    query += " ORDER BY o.max_intraday_pct DESC LIMIT ?"; params.append(limit)
    with closing(_connect()) as conn:
        return [_row(r) for r in conn.execute(query, params).fetchall()]


def summary() -> Dict[str, Any]:
    """Resumen del estudio: cobertura del checkpoint + explosiones por banda,
    separando disponibilidad en Racional. Todo con n explícito."""
    with closing(_connect()) as conn:
        procesados = conn.execute("SELECT COUNT(*) FROM study_checkpoint").fetchone()[0]
        por_estado = {r[0]: r[1] for r in conn.execute(
            "SELECT status, COUNT(*) FROM study_checkpoint GROUP BY status")}
        total_expl = conn.execute("SELECT COUNT(*) FROM explosion_features").fetchone()[0]
        en_racional = conn.execute(
            "SELECT COUNT(*) FROM explosion_features WHERE available_in_racional = 1").fetchone()[0]
        por_banda = {r[0]: r[1] for r in conn.execute(
            "SELECT band, COUNT(*) FROM explosion_outcome GROUP BY band")}
    return {
        "simbolos_procesados": procesados,
        "checkpoint_por_estado": por_estado,
        "explosiones_totales": total_expl,
        "explosiones_en_racional": en_racional,
        "explosiones_fuera_de_racional": total_expl - en_racional,
        "por_banda": por_banda,
    }
