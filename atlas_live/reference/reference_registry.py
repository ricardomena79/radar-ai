"""Registro de la base histórica de referencia (2026-08-15).

`historical_reference.db` -- mismo patrón que `study_registry.py`/
`candidate_registry.py` (`db_path()` -> Volume de Railway, `_connect()` con
`busy_timeout` y esquema creado una sola vez por proceso).

Separación anti-leakage: `daily_features` (lo que se sabía HASTA ese día) y
`daily_outcome` (lo que pasó DESPUÉS) son tablas físicamente distintas,
igual que `explosion_features`/`explosion_outcome` en `market_study` y
`candidate_detection`/`candidate_outcome` en `radar`. `reference_checkpoint`
permite resumir el batch de construcción si se corta a mitad de camino.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.config.config import db_path

DB_PATH = db_path("historical_reference.db", default=Path(__file__).parent)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reference_checkpoint (
    symbol TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    n_features INTEGER NOT NULL DEFAULT 0,
    n_outcomes INTEGER NOT NULL DEFAULT 0,
    note TEXT
);

CREATE TABLE IF NOT EXISTS daily_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    price REAL, change_pct REAL, gap_pct REAL, volume INTEGER,
    average_volume_20d REAL, relative_volume REAL, daily_range_pct REAL,
    volatility_14d_pct REAL, drop_from_peak_10d_pct REAL, rebound_from_trough_pct REAL,
    peak_gain_10d_pct REAL,
    direction TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_reffeat_date ON daily_features(date);

CREATE TABLE IF NOT EXISTS daily_outcome (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    max_advance_pct REAL, max_drawdown_pct REAL, days_to_max INTEGER,
    continuo INTEGER, n_dias_posteriores_usados INTEGER,
    outcome_direction TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_refout_date ON daily_outcome(date);

CREATE TABLE IF NOT EXISTS reference_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

_schema_ready_for: Optional[str] = None


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    global _schema_ready_for
    if _schema_ready_for != str(DB_PATH):
        conn.executescript(_SCHEMA)
        _ensure_column(conn, "daily_features", "direction", "TEXT")
        _ensure_column(conn, "daily_features", "timing_deteccion", "TEXT")
        _ensure_column(conn, "daily_features", "peak_gain_10d_pct", "REAL")
        _ensure_column(conn, "daily_outcome", "outcome_direction", "TEXT")
        _schema_ready_for = str(DB_PATH)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    return dict(r)


# --------------------------- checkpoint ---------------------------

def is_processed(symbol: str) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM reference_checkpoint WHERE symbol=?", (symbol,)).fetchone()
        return row is not None


def processed_symbols() -> set:
    with _connect() as conn:
        return {r["symbol"] for r in conn.execute("SELECT symbol FROM reference_checkpoint").fetchall()}


def processed_ok_symbols() -> List[str]:
    with _connect() as conn:
        rows = conn.execute("SELECT symbol FROM reference_checkpoint WHERE status='ok'").fetchall()
        return [r["symbol"] for r in rows]


def mark_processed(symbol: str, status: str, n_features: int, n_outcomes: int, note: Optional[str] = None) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO reference_checkpoint (symbol, status, processed_at, n_features, n_outcomes, note) "
            "VALUES (?,?,?,?,?,?)",
            (symbol, status, _now(), n_features, n_outcomes, note),
        )
        conn.commit()


# --------------------------- features / outcome ---------------------------

def record_features(symbol: str, feats, timing_deteccion: Optional[str] = None) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO daily_features
               (symbol, date, price, change_pct, gap_pct, volume, average_volume_20d, relative_volume,
                daily_range_pct, volatility_14d_pct, drop_from_peak_10d_pct, rebound_from_trough_pct,
                peak_gain_10d_pct, direction, timing_deteccion, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (symbol, feats.date, feats.price, feats.change_pct, feats.gap_pct, feats.volume,
             feats.average_volume_20d, feats.relative_volume, feats.daily_range_pct, feats.volatility_14d_pct,
             feats.drop_from_peak_10d_pct, feats.rebound_from_trough_pct, feats.peak_gain_10d_pct,
             feats.direction, timing_deteccion, _now()),
        )
        conn.commit()
        return cur.rowcount > 0


def update_timing(symbol: str, date: str, timing_deteccion: Optional[str], peak_gain_10d_pct: Optional[float]) -> bool:
    """Corrige `timing_deteccion`/`peak_gain_10d_pct` de una fila YA
    guardada (UPDATE, nunca INSERT ni DELETE) -- usado para aplicar una
    regla de clasificación revisada a filas ya procesadas sin reiniciar ni
    volver a descargar el resto de la fila (2026-08-15, revisión de
    'agotamiento')."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE daily_features SET timing_deteccion=?, peak_gain_10d_pct=? WHERE symbol=? AND date=?",
            (timing_deteccion, peak_gain_10d_pct, symbol, date),
        )
        conn.commit()
        return cur.rowcount > 0


def record_outcome(symbol: str, outcome) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO daily_outcome
               (symbol, date, max_advance_pct, max_drawdown_pct, days_to_max, continuo, n_dias_posteriores_usados, outcome_direction, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (symbol, outcome.date, outcome.max_advance_pct, outcome.max_drawdown_pct, outcome.days_to_max,
             int(outcome.continuo), outcome.n_dias_posteriores_usados, outcome.outcome_direction, _now()),
        )
        conn.commit()
        return cur.rowcount > 0


def get_features_for_symbol(symbol: str) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM daily_features WHERE symbol=? ORDER BY date", (symbol,)).fetchall()
        return [_row(r) for r in rows]


def latest_volatility_14d_pct(symbol: str) -> Optional[float]:
    """Última `volatility_14d_pct` conocida para el símbolo (el día más
    reciente ya guardado en la Base Histórica) -- usada por CAPA 2 en vivo
    como aproximación de bajo costo (2026-08-16, Experimento A): evita pedir
    historial diario fresco por cada candidata detectada. Es un dato de
    ANTES de hoy por construcción (nunca del propio día en curso), así que
    no hay riesgo de fuga -- solo la limitación real de no estar
    actualizado al minuto, documentada en el llamador."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT volatility_14d_pct FROM daily_features WHERE symbol=? AND volatility_14d_pct IS NOT NULL "
            "ORDER BY date DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        return row["volatility_14d_pct"] if row else None


def percentile_change_pct(symbol: str, percentile: float) -> Optional[float]:
    """Percentil histórico de |change_pct| para el símbolo -- usado por el
    clasificador de fases para saber si un cambio actual es "grande para
    ESTE símbolo en particular" (no un piso genérico)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT ABS(change_pct) AS v FROM daily_features WHERE symbol=? AND change_pct IS NOT NULL ORDER BY v",
            (symbol,),
        ).fetchall()
        vals = [r["v"] for r in rows]
        if not vals:
            return None
        idx = min(len(vals) - 1, max(0, int(len(vals) * percentile)))
        return vals[idx]


def historical_phase_stats() -> List[Dict[str, Any]]:
    """"Precisión histórica" (backtest, NUNCA en vivo -- pedido explícito de
    no presentar esto como si Atlas ya estuviera entrenado): por
    `timing_deteccion`, cuántos días y qué fracción alcanzó +20/+50/+100%
    DESPUÉS de ese día, con `n` siempre explícito."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT f.timing_deteccion AS timing_deteccion, COUNT(*) AS n,
                      SUM(CASE WHEN o.max_advance_pct >= 20 THEN 1 ELSE 0 END) AS n_reached_20,
                      SUM(CASE WHEN o.max_advance_pct >= 50 THEN 1 ELSE 0 END) AS n_reached_50,
                      SUM(CASE WHEN o.max_advance_pct >= 100 THEN 1 ELSE 0 END) AS n_reached_100
               FROM daily_features f
               JOIN daily_outcome o ON o.symbol = f.symbol AND o.date = f.date
               WHERE f.timing_deteccion IS NOT NULL
               GROUP BY f.timing_deteccion"""
        ).fetchall()
        out = []
        for r in rows:
            d = _row(r)
            n = d["n"] or 0
            d["pct_reached_20"] = round(100 * (d["n_reached_20"] or 0) / n, 1) if n else None
            d["pct_reached_50"] = round(100 * (d["n_reached_50"] or 0) / n, 1) if n else None
            d["pct_reached_100"] = round(100 * (d["n_reached_100"] or 0) / n, 1) if n else None
            out.append(d)
        return out


def counts() -> Dict[str, int]:
    with _connect() as conn:
        return {
            "simbolos_procesados": conn.execute("SELECT COUNT(*) FROM reference_checkpoint").fetchone()[0],
            "daily_features": conn.execute("SELECT COUNT(*) FROM daily_features").fetchone()[0],
            "daily_outcome": conn.execute("SELECT COUNT(*) FROM daily_outcome").fetchone()[0],
            # "evaluables" reales = filas con features Y outcome para el mismo
            # symbol+date (2026-08-16, hallazgo real: `daily_outcome` solo
            # tiene 128.211 filas en TOTAL, pero varias son de días sin
            # `daily_features` -- MIN_BASELINE_DAYS y MIN_FORWARD_DAYS son
            # pisos distintos, así que los extremos del rango de cada símbolo
            # no calzan 1 a 1. Un caso "evaluable" de verdad necesita AMBOS.
            "evaluables_features_y_outcome": conn.execute(
                "SELECT COUNT(*) FROM daily_features f JOIN daily_outcome o "
                "ON o.symbol = f.symbol AND o.date = f.date"
            ).fetchone()[0],
        }


def set_meta(**kwargs) -> None:
    import json
    with _connect() as conn:
        for k, v in kwargs.items():
            conn.execute(
                "INSERT INTO reference_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v),
            )
        conn.commit()


def get_meta() -> Dict[str, Any]:
    import json
    with _connect() as conn:
        rows = conn.execute("SELECT key, value FROM reference_meta").fetchall()
        out = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value"])
            except (TypeError, ValueError):
                out[r["key"]] = r["value"]
        return out
