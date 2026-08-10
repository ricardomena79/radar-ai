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

-- Estado del job de estudio (clave-valor). Persistente -> sobrevive a
-- reinicios de Railway; el worker lo lee al arrancar para reanudar.
CREATE TABLE IF NOT EXISTS study_meta (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_schema_ready_for: Optional[str] = None


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    # El esquema (DDL) se crea una sola vez por ruta de DB, no en cada
    # conexión: correr CREATE en cada _connect toma un lock de escritura y,
    # con el worker de fondo escribiendo seguido, generaba "database is
    # locked". Idempotente: al cambiar DB_PATH (tests) se vuelve a crear.
    global _schema_ready_for
    if _schema_ready_for != str(DB_PATH):
        conn.executescript(_SCHEMA)
        _schema_ready_for = str(DB_PATH)
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


def set_meta(**kwargs: Any) -> None:
    """Guarda pares clave-valor del estado del job (idempotente por clave)."""
    with closing(_connect()) as conn:
        for k, v in kwargs.items():
            conn.execute(
                "INSERT INTO study_meta (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (k, str(v) if v is not None else None, _now()),
            )
        conn.commit()


def get_meta() -> Dict[str, Any]:
    with closing(_connect()) as conn:
        return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM study_meta")}


def study_status() -> Dict[str, Any]:
    """Estado completo del estudio para la Cabina (Fase 8): combina el
    checkpoint (procesados, explosiones por banda) con el meta del worker
    (estado, último símbolo, último avance, universo total, errores). Todo
    real; nunca 'completo' si no terminó. Vacío hasta que arranque el job."""
    meta = get_meta()
    s = summary()
    universo_total = int(meta.get("universe_total") or 0)
    procesados = s["simbolos_procesados"]
    pendientes = max(0, universo_total - procesados) if universo_total else None
    progreso = round(procesados / universo_total * 100, 1) if universo_total else None
    band = s["por_banda"]

    def _ge(threshold):
        # explosiones que alcanzaron AL MENOS `threshold` (bandas acumulativas)
        total = 0
        for b, n in band.items():
            try:
                val = 201 if b == ">200" else int(b)
            except ValueError:
                continue
            if val >= threshold:
                total += n
        return total

    return {
        "state": meta.get("state", "IDLE"),
        "provider": meta.get("provider", "yahoo_finance"),
        "universe_total": universo_total or None,
        "procesados": procesados,
        "pendientes": pendientes,
        "progreso_pct": progreso,
        "explosiones": {
            "+30": _ge(30), "+50": _ge(50), "+100": _ge(100),
            "+150": _ge(150), "+200": _ge(200),
        },
        "explosiones_totales": s["explosiones_totales"],
        "en_racional": s["explosiones_en_racional"],
        "fuera_de_racional": s["explosiones_fuera_de_racional"],
        "ultimo_simbolo": meta.get("last_symbol"),
        "ultimo_avance_at": meta.get("last_advance_at"),
        "ultimo_checkpoint_at": meta.get("last_advance_at"),
        "errores": int(meta.get("errors") or 0),
        "retries": int(meta.get("retries") or 0),
        "velocidad_symbols_min": meta.get("speed_symbols_min"),
    }


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
