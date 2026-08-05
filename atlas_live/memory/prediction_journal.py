"""Prediction Journal -- primer componente de la integración en tiempo real
(propuesta aprobada el 2026-08-02, ver MEMORY_ENGINE.md).

Incorpora los 3 ajustes de arquitectura pedidos al aprobar la propuesta:

  1. **Dos flujos de ranking, separados**: `record_dynamic_snapshot()`
     guarda una foto del ranking en cualquier momento del premarket
     (append-only, se puede llamar tantas veces como se quiera, informativo,
     nunca se usa para calificar); `seal_ranking()` guarda EL ranking
     oficial del día, una única vez, de forma verificablemente inmutable
     (una segunda llamada para la misma fecha levanta `AlreadySealedError`
     -- no hay forma de sobrescribir un sellado ya hecho).
  2. **Journal, no solo Log**: cada predicción sellada guarda no solo el
     símbolo y su posición, sino la explicación completa que ya produce
     `demo_ranking.py` (condición matcheada, evidencia, los 4 niveles del
     Ranking Score) -- y, más adelante, el resultado real. Nada de esto se
     reescribe: la predicción es un campo, el resultado es otro campo que
     se completa una sola vez al cierre (`grade_sealed_prediction()`,
     protegido por su propio guardia de "ya calificado").
  3. **Tiempo de anticipación**: `grade_sealed_prediction()` calcula
     `anticipation_minutes` = tiempo entre la primera vez que el símbolo
     apareció en CUALQUIER snapshot dinámico de ese día (`first_detected_at`,
     el detalle más fino que hoy se puede medir) y el momento del
     movimiento confirmado que se le pase (hoy, en la práctica, el
     snapshot post-apertura de Radar Explosivo -- no hay granularidad
     intradía más fina disponible todavía, eso depende de los checkpoints
     intermedios del Entregable 8 del Memory Engine, no implementados).

100% de solo lectura respecto a `scan_worker.py`, Atlas Core y la
validación V2 -- este módulo no toca nada de eso, solo persiste. La
conexión real al escaneo en vivo de premarket es una etapa aparte, todavía
no implementada (ver MEMORY_ENGINE.md, riesgos de la propuesta).
"""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from atlas.config.config import data_dir

DB_PATH = data_dir() / "prediction_journal.db"


class AlreadySealedError(Exception):
    """Se intentó sellar el ranking de una fecha que ya tiene un sellado."""


class AlreadyGradedError(Exception):
    """Se intentó calificar una predicción sellada que ya fue calificada."""


@dataclass(frozen=True)
class JournaledCandidate:
    """Lo mínimo que necesita el Journal de cada candidato -- mismos campos
    que ya produce `demo_ranking.RankedCandidate` / `ranking_score.RankingScore`,
    sin importar esos módulos acá para no crear una dependencia circular
    (este módulo es más fundacional; `demo_ranking` lo va a llamar a él)."""

    symbol: str
    rank: int
    score: Optional[float]
    probability_pct: Optional[float]
    confidence: str
    semaforo: str
    ranking_score_nivel1: float
    ranking_score_nivel2: int
    ranking_score_nivel3: float
    ranking_score_nivel4: float
    evidence_condition: Optional[str]
    evidence_sample_size: int
    evidence_wilson_lower_bound_pct: Optional[float]
    explanation: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS dynamic_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    snapshot_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    rank INTEGER NOT NULL,
    score REAL,
    probability_pct REAL,
    confidence TEXT,
    semaforo TEXT,
    ranking_score_nivel1 REAL,
    ranking_score_nivel2 INTEGER,
    ranking_score_nivel3 REAL,
    ranking_score_nivel4 REAL,
    evidence_condition TEXT,
    evidence_sample_size INTEGER,
    evidence_wilson_lower_bound_pct REAL,
    explanation TEXT,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dynamic_date ON dynamic_snapshots(date);
CREATE INDEX IF NOT EXISTS idx_dynamic_symbol ON dynamic_snapshots(date, symbol);

CREATE TABLE IF NOT EXISTS sealed_ranking_meta (
    date TEXT PRIMARY KEY,
    sealed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sealed_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    rank INTEGER NOT NULL,
    score REAL,
    probability_pct REAL,
    confidence TEXT,
    semaforo TEXT,
    ranking_score_nivel1 REAL,
    ranking_score_nivel2 INTEGER,
    ranking_score_nivel3 REAL,
    ranking_score_nivel4 REAL,
    evidence_condition TEXT,
    evidence_sample_size INTEGER,
    evidence_wilson_lower_bound_pct REAL,
    explanation TEXT,
    sealed_at TEXT NOT NULL,
    result_change_pct REAL,
    result_category TEXT,
    movement_confirmed_at TEXT,
    first_detected_at TEXT,
    anticipation_minutes REAL,
    graded_at TEXT,
    UNIQUE(date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_sealed_date ON sealed_predictions(date);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


# ---------------------------------------------------------------------------
# 1. Ranking dinámico -- append-only, tantas veces como se quiera
# ---------------------------------------------------------------------------

def record_dynamic_snapshot(date: str, snapshot_at: str, candidates: List[JournaledCandidate]) -> None:
    """Guarda una foto del ranking en un momento del premarket. No tiene
    ninguna restricción de unicidad -- se puede llamar repetidamente a
    medida que avanza el premarket, cada llamada agrega filas nuevas."""
    now = datetime.now(timezone.utc).isoformat()
    with closing(_connect()) as conn:
        for c in candidates:
            conn.execute(
                "INSERT INTO dynamic_snapshots ("
                "date, snapshot_at, symbol, rank, score, probability_pct, confidence, semaforo, "
                "ranking_score_nivel1, ranking_score_nivel2, ranking_score_nivel3, ranking_score_nivel4, "
                "evidence_condition, evidence_sample_size, evidence_wilson_lower_bound_pct, explanation, recorded_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    date, snapshot_at, c.symbol, c.rank, c.score, c.probability_pct, c.confidence, c.semaforo,
                    c.ranking_score_nivel1, c.ranking_score_nivel2, c.ranking_score_nivel3, c.ranking_score_nivel4,
                    c.evidence_condition, c.evidence_sample_size, c.evidence_wilson_lower_bound_pct,
                    c.explanation, now,
                ),
            )
        conn.commit()


def get_dynamic_snapshots(date: str, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    query = "SELECT * FROM dynamic_snapshots WHERE date = ?"
    params: List[Any] = [date]
    if symbol is not None:
        query += " AND symbol = ?"
        params.append(symbol)
    query += " ORDER BY snapshot_at ASC, id ASC"
    with closing(_connect()) as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 2. Ranking sellado -- una única vez por fecha, verificablemente inmutable
# ---------------------------------------------------------------------------

def is_sealed(date: str) -> bool:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT 1 FROM sealed_ranking_meta WHERE date = ?", (date,)).fetchone()
    return row is not None


def seal_ranking(date: str, sealed_at: str, candidates: List[JournaledCandidate]) -> None:
    """Registra el ranking oficial del día -- una sola vez. Si ya existe un
    sellado para esta fecha, no lo toca y levanta `AlreadySealedError`."""
    with closing(_connect()) as conn:
        existing = conn.execute("SELECT 1 FROM sealed_ranking_meta WHERE date = ?", (date,)).fetchone()
        if existing is not None:
            raise AlreadySealedError(f"La fecha {date!r} ya tiene un ranking sellado -- no se puede reemplazar.")

        conn.execute("INSERT INTO sealed_ranking_meta (date, sealed_at) VALUES (?, ?)", (date, sealed_at))
        for c in candidates:
            conn.execute(
                "INSERT INTO sealed_predictions ("
                "date, symbol, rank, score, probability_pct, confidence, semaforo, "
                "ranking_score_nivel1, ranking_score_nivel2, ranking_score_nivel3, ranking_score_nivel4, "
                "evidence_condition, evidence_sample_size, evidence_wilson_lower_bound_pct, explanation, sealed_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    date, c.symbol, c.rank, c.score, c.probability_pct, c.confidence, c.semaforo,
                    c.ranking_score_nivel1, c.ranking_score_nivel2, c.ranking_score_nivel3, c.ranking_score_nivel4,
                    c.evidence_condition, c.evidence_sample_size, c.evidence_wilson_lower_bound_pct,
                    c.explanation, sealed_at,
                ),
            )
        conn.commit()


def get_sealed_predictions(date: str) -> List[Dict[str, Any]]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM sealed_predictions WHERE date = ? ORDER BY rank ASC", (date,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_sealed_meta(date: str) -> Optional[Dict[str, Any]]:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM sealed_ranking_meta WHERE date = ?", (date,)).fetchone()
    return _row_to_dict(row) if row else None


def get_recent_sealed_days(limit: int = 10) -> List[Dict[str, Any]]:
    """El candidato #1 de cada uno de los últimos días sellados (Cabina del
    Piloto, Panel 10) -- calificado o no, según haya avanzado el ciclo de
    vida de ese día. Solo lectura."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM sealed_predictions WHERE rank = 1 ORDER BY date DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 3. Calificación al cierre -- resultado real + tiempo de anticipación
# ---------------------------------------------------------------------------

def grade_sealed_prediction(
    date: str,
    symbol: str,
    result_change_pct: float,
    result_category: str,
    movement_confirmed_at: str,
) -> None:
    """Completa el resultado real de una predicción ya sellada. Se puede
    llamar una sola vez por (date, symbol) -- si ya fue calificada, levanta
    `AlreadyGradedError` en vez de sobrescribir el resultado.

    `anticipation_minutes` se calcula automáticamente como la diferencia
    entre `movement_confirmed_at` y el `snapshot_at` más temprano en que
    ese símbolo apareció en CUALQUIER ranking dinámico de ese día
    (`first_detected_at`) -- si el símbolo nunca apareció en un snapshot
    dinámico (por ejemplo, si el sellado se hizo sin pasar por el flujo
    dinámico todavía), `anticipation_minutes` queda en `None`, nunca se
    inventa un valor."""
    with closing(_connect()) as conn:
        existing = conn.execute(
            "SELECT graded_at FROM sealed_predictions WHERE date = ? AND symbol = ?", (date, symbol)
        ).fetchone()
        if existing is None:
            raise KeyError(f"No hay una predicción sellada para symbol={symbol!r} en date={date!r}")
        if existing["graded_at"] is not None:
            raise AlreadyGradedError(f"{symbol!r} en {date!r} ya fue calificado -- no se sobrescribe.")

        first_row = conn.execute(
            "SELECT MIN(snapshot_at) AS first_detected_at FROM dynamic_snapshots WHERE date = ? AND symbol = ?",
            (date, symbol),
        ).fetchone()
        first_detected_at = first_row["first_detected_at"] if first_row else None

        anticipation_minutes: Optional[float] = None
        if first_detected_at is not None:
            t0 = datetime.fromisoformat(first_detected_at)
            t1 = datetime.fromisoformat(movement_confirmed_at)
            anticipation_minutes = (t1 - t0).total_seconds() / 60.0

        conn.execute(
            "UPDATE sealed_predictions SET result_change_pct=?, result_category=?, movement_confirmed_at=?, "
            "first_detected_at=?, anticipation_minutes=?, graded_at=? WHERE date=? AND symbol=?",
            (
                result_change_pct, result_category, movement_confirmed_at,
                first_detected_at, anticipation_minutes, datetime.now(timezone.utc).isoformat(),
                date, symbol,
            ),
        )
        conn.commit()
