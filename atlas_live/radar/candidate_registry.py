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

-- Reinicio del aprendizaje (2026-08-15): resumen auditable de cada día de
-- mercado -- estudiadas/candidatas/señales/aciertos/falsos positivos/
-- tardías/bandas, para que el informe de cierre y la precisión acumulada
-- se calculen de una sola fuente, nunca recalculados "a ojo".
CREATE TABLE IF NOT EXISTS daily_summary (
    market_date TEXT PRIMARY KEY,
    n_estudiadas INTEGER,
    n_candidatas INTEGER,
    n_senales INTEGER,
    n_evaluables INTEGER,
    n_aciertos INTEGER,
    n_falsos_positivos INTEGER,
    n_tardias INTEGER,
    n_reached_20 INTEGER,
    n_reached_50 INTEGER,
    n_reached_100 INTEGER,
    computed_at TEXT NOT NULL
);
"""

_schema_ready_for: Optional[str] = None


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Agrega una columna si todavía no existe -- migración aditiva y segura
    para bases ya creadas (`CREATE TABLE IF NOT EXISTS` no agrega columnas a
    una tabla existente). No toca ni una fila de datos. Necesario porque
    `radar_candidates.db` ya está desplegada en producción con el esquema
    de CAPA 2 (sin `es_senal`/`phase_tag`)."""
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
        _ensure_column(conn, "candidate_detection", "es_senal", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "candidate_detection", "phase_tag", "TEXT")
        _ensure_column(conn, "candidate_detection", "direction_at_detection", "TEXT")
        _ensure_column(conn, "candidate_detection", "comportamiento_post_apertura", "TEXT")
        # Experimentos A/C (2026-08-16) -- SOLO diagnóstico: nunca se leen en
        # candidate_gates.py/phase_classifier.py, no afectan qué se detecta
        # ni el orden en que se muestra. Sirven para comparar, con datos en
        # vivo, si estas señales realmente mejoran algo frente al baseline.
        _ensure_column(conn, "candidate_detection", "volatility_14d_pct_at_detection", "REAL")
        _ensure_column(conn, "candidate_detection", "daily_range_pct_at_detection", "REAL")
        _ensure_column(conn, "candidate_outcome", "perdio_momentum_inmediato", "INTEGER")
        _ensure_column(conn, "candidate_outcome", "direccion_correcta", "INTEGER")
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


def mark_as_signal(ticker: str, market_date: str) -> None:
    """Reinicio del aprendizaje (2026-08-15): una candidata pasa a "señal"
    cuando sigue activa en un segundo barrido (no fue un parpadeo de un solo
    tick) -- llamado desde `candidate_tracker` al procesar la segunda
    observación de una candidata ya existente."""
    with _connect() as conn:
        conn.execute(
            "UPDATE candidate_detection SET es_senal=1 WHERE ticker=? AND market_date=?", (ticker, market_date)
        )
        conn.commit()


def set_phase_tag(
    ticker: str, market_date: str, phase_tag: str,
    direction_at_detection: Optional[str] = None, comportamiento_post_apertura: Optional[str] = None,
) -> None:
    """Guarda las 3 dimensiones independientes del clasificador de fase
    (2026-08-15) -- cada una se actualiza por separado, ninguna se pisa con
    None si no se pasó."""
    sets = ["phase_tag=?"]
    params: list = [phase_tag]
    if direction_at_detection is not None:
        sets.append("direction_at_detection=?")
        params.append(direction_at_detection)
    if comportamiento_post_apertura is not None:
        sets.append("comportamiento_post_apertura=?")
        params.append(comportamiento_post_apertura)
    params += [ticker, market_date]
    with _connect() as conn:
        conn.execute(
            f"UPDATE candidate_detection SET {', '.join(sets)} WHERE ticker=? AND market_date=?", params
        )
        conn.commit()


def set_experimental_signals(
    ticker: str, market_date: str,
    volatility_14d_pct: Optional[float] = None, daily_range_pct: Optional[float] = None,
) -> None:
    """Experimentos A/C (2026-08-16) -- guarda las señales de diagnóstico en
    `candidate_detection`. Igual que `set_phase_tag`: cada campo se
    actualiza solo si se pasó, nunca se pisa con None. Estas columnas no las
    lee ningún gate ni el ranking -- son exclusivamente para comparar,
    después, contra el baseline."""
    sets = []
    params: list = []
    if volatility_14d_pct is not None:
        sets.append("volatility_14d_pct_at_detection=?")
        params.append(volatility_14d_pct)
    if daily_range_pct is not None:
        sets.append("daily_range_pct_at_detection=?")
        params.append(daily_range_pct)
    if not sets:
        return
    params += [ticker, market_date]
    with _connect() as conn:
        conn.execute(f"UPDATE candidate_detection SET {', '.join(sets)} WHERE ticker=? AND market_date=?", params)
        conn.commit()


def count_signals_for_date(market_date: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM candidate_detection WHERE market_date=? AND es_senal=1", (market_date,)
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
    perdio_momentum_inmediato: Optional[bool] = None, direccion_correcta: Optional[bool] = None,
) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO candidate_outcome
               (ticker, market_date, computed_at, run_up_before_detection_pct, max_price_after_detection,
                max_return_after_detection_pct, minutes_to_max, reached_20, reached_50, reached_100,
                category, notes, perdio_momentum_inmediato, direccion_correcta, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ticker, market_date, _now(), run_up_before_detection_pct, max_price_after_detection,
             max_return_after_detection_pct, minutes_to_max, int(reached_20), int(reached_50),
             int(reached_100), category, notes,
             None if perdio_momentum_inmediato is None else int(perdio_momentum_inmediato),
             None if direccion_correcta is None else int(direccion_correcta), _now()),
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


# --------------------------- resumen diario / precisión (Reinicio 2026-08-15) ---------------------------

def record_daily_summary(
    market_date: str, n_estudiadas: int, n_candidatas: int, n_senales: int, n_evaluables: int,
    n_aciertos: int, n_falsos_positivos: int, n_tardias: int,
    n_reached_20: int, n_reached_50: int, n_reached_100: int,
) -> None:
    """Idempotente por fecha (INSERT OR REPLACE) -- si el informe de cierre
    se recalcula el mismo día (ej. tras corregir un dato), el resumen se
    actualiza, nunca se duplica una fila por día."""
    with _connect() as conn:
        conn.execute(
            """INSERT INTO daily_summary
               (market_date, n_estudiadas, n_candidatas, n_senales, n_evaluables, n_aciertos,
                n_falsos_positivos, n_tardias, n_reached_20, n_reached_50, n_reached_100, computed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(market_date) DO UPDATE SET
                 n_estudiadas=excluded.n_estudiadas, n_candidatas=excluded.n_candidatas,
                 n_senales=excluded.n_senales, n_evaluables=excluded.n_evaluables,
                 n_aciertos=excluded.n_aciertos, n_falsos_positivos=excluded.n_falsos_positivos,
                 n_tardias=excluded.n_tardias, n_reached_20=excluded.n_reached_20,
                 n_reached_50=excluded.n_reached_50, n_reached_100=excluded.n_reached_100,
                 computed_at=excluded.computed_at""",
            (market_date, n_estudiadas, n_candidatas, n_senales, n_evaluables, n_aciertos,
             n_falsos_positivos, n_tardias, n_reached_20, n_reached_50, n_reached_100, _now()),
        )
        conn.commit()


def get_daily_summary(market_date: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM daily_summary WHERE market_date=?", (market_date,)).fetchone()
        return _row(row) if row else None


def cumulative_precision() -> Dict[str, Any]:
    """Precisión acumulada desde el inicio de esta etapa (reinicio
    2026-08-15) -- suma real de todos los días con resumen registrado,
    nunca un promedio de porcentajes diarios (eso distorsiona con muestras
    chicas). Siempre devuelve numerador y denominador explícitos."""
    with _connect() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS n_dias, SUM(n_estudiadas) AS estudiadas, SUM(n_candidatas) AS candidatas,
                      SUM(n_senales) AS senales, SUM(n_evaluables) AS evaluables, SUM(n_aciertos) AS aciertos,
                      SUM(n_falsos_positivos) AS falsos_positivos, SUM(n_tardias) AS tardias,
                      SUM(n_reached_20) AS reached_20, SUM(n_reached_50) AS reached_50,
                      SUM(n_reached_100) AS reached_100
               FROM daily_summary"""
        ).fetchone()
    d = _row(row)
    evaluables = d.get("evaluables") or 0
    aciertos = d.get("aciertos") or 0
    d["precision_pct"] = round(100 * aciertos / evaluables, 1) if evaluables else None
    return d


def list_all_evaluated_candidates() -> List[Dict[str, Any]]:
    """Todas las candidatas con resultado ya evaluado (detección + outcome),
    de TODA la historia en vivo -- fuente única para
    `atlas_live.learning.maturity` (2026-08-15). Nunca incluye candidatas
    sin `candidate_outcome` (no cerradas todavía) ni datos de
    `atlas_live/reference/` (Base Histórica, siempre separada)."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT d.ticker AS ticker, d.market_date AS market_date, d.session AS session,
                      d.change_pct_at_detection AS change_pct_at_detection,
                      d.direction_at_detection AS direction_at_detection,
                      d.phase_tag AS phase_tag, d.comportamiento_post_apertura AS comportamiento_post_apertura,
                      d.volatility_14d_pct_at_detection AS volatility_14d_pct_at_detection,
                      d.daily_range_pct_at_detection AS daily_range_pct_at_detection,
                      o.reached_20 AS reached_20, o.reached_50 AS reached_50, o.reached_100 AS reached_100,
                      o.category AS category, o.direccion_correcta AS direccion_correcta
               FROM candidate_detection d
               JOIN candidate_outcome o ON o.ticker = d.ticker AND o.market_date = d.market_date
               ORDER BY d.market_date"""
        ).fetchall()
        return [_row(r) for r in rows]


def list_daily_summaries() -> List[Dict[str, Any]]:
    """Todos los resúmenes diarios, ordenados por fecha ascendente -- para
    ventanas de tiempo (consistencia/recencia/validación fuera de muestra
    en `atlas_live.learning.maturity`)."""
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM daily_summary ORDER BY market_date").fetchall()
        return [_row(r) for r in rows]


def recent_precision(window_days: int = 21) -> Dict[str, Any]:
    """Precisión de los últimos `window_days` días de mercado CON resumen
    (no días calendario) -- para mostrar siempre junto a la acumulada
    completa, nunca a solas (pedido explícito: la precisión puede bajar al
    crecer la muestra, y eso debe verse, no ocultarse)."""
    dias = list_daily_summaries()[-window_days:]
    evaluables = sum(d.get("n_evaluables") or 0 for d in dias)
    aciertos = sum(d.get("n_aciertos") or 0 for d in dias)
    return {
        "dias_incluidos": len(dias),
        "desde": dias[0]["market_date"] if dias else None,
        "hasta": dias[-1]["market_date"] if dias else None,
        "evaluables": evaluables,
        "aciertos": aciertos,
        "precision_pct": round(100 * aciertos / evaluables, 1) if evaluables else None,
    }


def phase_stats(phase_tag: Optional[str] = None) -> List[Dict[str, Any]]:
    """Estadística real por fase (Fase 4, clasificador A-G): conteo y % que
    alcanzó cada banda, con `n` SIEMPRE explícito -- una fase con n=2 se
    reporta con n=2, nunca se disfraza de porcentaje solo."""
    with _connect() as conn:
        query = """
            SELECT d.phase_tag AS phase_tag, COUNT(*) AS n,
                   SUM(o.reached_20) AS n_reached_20, SUM(o.reached_50) AS n_reached_50,
                   SUM(o.reached_100) AS n_reached_100
            FROM candidate_detection d
            JOIN candidate_outcome o ON o.ticker = d.ticker AND o.market_date = d.market_date
            WHERE d.phase_tag IS NOT NULL
        """
        params: tuple = ()
        if phase_tag:
            query += " AND d.phase_tag = ?"
            params = (phase_tag,)
        query += " GROUP BY d.phase_tag"
        rows = conn.execute(query, params).fetchall()
        out = []
        for r in rows:
            d = _row(r)
            n = d["n"] or 0
            d["pct_reached_20"] = round(100 * (d["n_reached_20"] or 0) / n, 1) if n else None
            d["pct_reached_50"] = round(100 * (d["n_reached_50"] or 0) / n, 1) if n else None
            d["pct_reached_100"] = round(100 * (d["n_reached_100"] or 0) / n, 1) if n else None
            out.append(d)
        return out


def early_vs_late_summary() -> Dict[str, Any]:
    """Hipótesis B del experimento (2026-08-16): agrupa TODAS las candidatas
    ya evaluadas en vivo en los 3 grupos de timing (early_genuino/late/
    antes_del_movimiento -- ver `atlas_live.learning.experiments`), separado
    por dirección. Nunca mezcla histórico (`atlas_live/reference/`) con esto
    -- exclusivamente `candidate_registry`, CAPA 2 en vivo. Con la base
    recién reiniciada, esto arranca vacío -- correctamente, no se fabrica
    nada mientras no haya evidencia real en vivo."""
    from atlas_live.learning import experiments as exp

    evaluados = list_all_evaluated_candidates()
    # `list_all_evaluated_candidates` trae reached_20/50/100 (bandas ya
    # calculadas por eod_report.py), no el % exacto -- alcanza para este
    # resumen, que reporta bandas, igual que el resto del proyecto.
    grupos: Dict[str, Dict[str, Dict[str, Any]]] = {
        d: {g: {"n": 0, "aciertos_20": 0, "aciertos_50": 0, "aciertos_100": 0}
            for g in ("early_genuino", "late", "antes_del_movimiento")}
        for d in exp.DIRECTIONS
    }
    for e in evaluados:
        direction = e.get("direction_at_detection")
        if direction not in exp.DIRECTIONS:
            continue
        grupo = exp.timing_group(e.get("phase_tag"))
        if grupo is None:
            continue
        bucket = grupos[direction][grupo]
        bucket["n"] += 1
        if e.get("reached_20"):
            bucket["aciertos_20"] += 1
        if e.get("reached_50"):
            bucket["aciertos_50"] += 1
        if e.get("reached_100"):
            bucket["aciertos_100"] += 1

    for d in grupos:
        for g, b in grupos[d].items():
            n = b["n"]
            b["pct_20"] = round(100 * b["aciertos_20"] / n, 1) if n else None
            b["pct_50"] = round(100 * b["aciertos_50"] / n, 1) if n else None
            b["pct_100"] = round(100 * b["aciertos_100"] / n, 1) if n else None
    return grupos


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
