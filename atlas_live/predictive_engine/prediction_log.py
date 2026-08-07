"""Prediction Log del Motor Predictivo -- registro append-only de cada
predicción emitida por cualquier capacidad (arquitectura aprobada
2026-08-06, ver DECISIONES.md, Fase 1.1).

Principio fundamental explícito del usuario: el motor no solo debe
registrar si una predicción fue correcta o incorrecta -- debe poder
explicar por qué. Por eso cada fila guarda, además del resultado de la
capacidad, una fotografía (`feature_snapshot`, JSON) de las variables
disponibles en el momento de la predicción (Gap, RVOL, volumen, market
cap, hora -- sector/float/noticias reservados, ver
`build_feature_snapshot`). Ninguna lógica de este módulo interpreta ese
snapshot todavía: el análisis de qué variable pesó más (`attribution.py`)
es una fase futura, explícitamente no implementada -- "Solo deja la
arquitectura preparada para que pueda incorporarse sin rediseñar el motor
en el futuro."

Calificación (`graded_at`, `actual_outcome`, `correct`) queda NULL hasta
que `grading.py` (Sprint 5, no implementado todavía) la complete
comparando contra el resultado real del Exit Journal -- mismo patrón de
"campo reservado, llenado una sola vez" que ya usa
`prediction_journal.py` (`AlreadySealedError`/`grade_sealed_prediction`).
"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.config.config import db_path

from atlas_live.predictive_engine.engine import CapabilityResult

DB_PATH = db_path("predictive_engine.db", default=Path(__file__).parent)


class AlreadyGradedError(Exception):
    """Se intentó calificar una predicción que ya tiene `graded_at` --
    mismo guardia que `AlreadySealedError` de `prediction_journal.py`, la
    calificación se llena una sola vez."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    predicted_at TEXT NOT NULL,
    capability TEXT NOT NULL,
    recommendation TEXT,
    value REAL,
    unit TEXT,
    confidence TEXT NOT NULL,
    sample_size INTEGER NOT NULL,
    range_low REAL,
    range_high REAL,
    evidence_condition TEXT,
    explanation TEXT,
    feature_snapshot TEXT NOT NULL,
    graded_at TEXT,
    actual_outcome TEXT,
    correct INTEGER
);
CREATE INDEX IF NOT EXISTS idx_predictions_symbol_date ON predictions(symbol, date);
CREATE INDEX IF NOT EXISTS idx_predictions_capability ON predictions(capability);
CREATE INDEX IF NOT EXISTS idx_predictions_ungraded ON predictions(graded_at);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    if d.get("feature_snapshot"):
        d["feature_snapshot"] = json.loads(d["feature_snapshot"])
    return d


def build_feature_snapshot(metrics: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """Fotografía de las variables disponibles al momento de la predicción,
    reutilizando `row["explosive"]["metrics"]` que Radar Explosivo ya
    calculó ese ciclo (no repite ningún cálculo). `sector`/`float`/`news`
    quedan reservados en `None` -- Radar Explosivo no los captura todavía
    (ver explosive_engine.py) -- mismo criterio que `price_overnight` en
    `Quote`: nunca inventar un valor para un campo sin dato real."""
    return {
        "gap_pct": metrics.get("gap_pct"),
        "change_pct": metrics.get("change_pct"),
        "relative_volume": metrics.get("relative_volume"),
        "dollar_volume": metrics.get("dollar_volume"),
        "volatility_score": metrics.get("volatility_score"),
        "market_cap": metrics.get("market_cap"),
        "price": metrics.get("price"),
        "market_state": metrics.get("market_state"),
        "hour_utc": now.hour,
        "sector": None,
        "float": None,
        "news": None,
    }


def record_prediction(
    symbol: str,
    date: str,
    predicted_at: str,
    result: CapabilityResult,
    feature_snapshot: Dict[str, Any],
) -> int:
    with closing(_connect()) as conn:
        cursor = conn.execute(
            "INSERT INTO predictions (symbol, date, predicted_at, capability, recommendation, value, unit, "
            "confidence, sample_size, range_low, range_high, evidence_condition, explanation, feature_snapshot) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                symbol,
                date,
                predicted_at,
                result.capability,
                result.recommendation,
                result.value,
                result.unit,
                result.confidence,
                result.sample_size,
                result.range_low,
                result.range_high,
                result.evidence_condition,
                result.explanation,
                json.dumps(feature_snapshot),
            ),
        )
        conn.commit()
        return cursor.lastrowid


def get_predictions(
    symbol: Optional[str] = None,
    date: Optional[str] = None,
    capability: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if symbol is not None:
        clauses.append("symbol = ?")
        params.append(symbol)
    if date is not None:
        clauses.append("date = ?")
        params.append(date)
    if capability is not None:
        clauses.append("capability = ?")
        params.append(capability)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with closing(_connect()) as conn:
        rows = conn.execute(
            f"SELECT * FROM predictions {where} ORDER BY predicted_at DESC, id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_ungraded(limit: int = 200) -> List[Dict[str, Any]]:
    """Predicciones todavía sin calificar -- consumido por `grading.py`
    (Sprint 5, no implementado todavía)."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM predictions WHERE graded_at IS NULL ORDER BY predicted_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_predictions() -> int:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()
    return row["n"]


def grade_prediction(prediction_id: int, actual_outcome: str, graded_at: str) -> None:
    """Llena las tres columnas reservadas desde el Sprint 1
    (`graded_at`, `actual_outcome`, `correct`) de una predicción ya
    registrada -- append-only: ningún otro campo de la fila se toca.
    `actual_outcome` es uno de "correcta" / "incorrecta" /
    "sin_evidencia_suficiente" (ver `grading.py`); `correct` se deriva
    para poder agregar con SQL simple (1/0/NULL)."""
    correct = {"correcta": 1, "incorrecta": 0}.get(actual_outcome)
    with closing(_connect()) as conn:
        existente = conn.execute(
            "SELECT graded_at FROM predictions WHERE id = ?", (prediction_id,)
        ).fetchone()
        if existente is None:
            raise ValueError(f"No existe ninguna predicción con id={prediction_id!r}")
        if existente["graded_at"] is not None:
            raise AlreadyGradedError(f"La predicción id={prediction_id!r} ya fue calificada -- no se recalifica.")
        conn.execute(
            "UPDATE predictions SET graded_at = ?, actual_outcome = ?, correct = ? WHERE id = ?",
            (graded_at, actual_outcome, correct, prediction_id),
        )
        conn.commit()


def get_accuracy_summary(capability: str = "entry_window") -> Dict[str, Any]:
    """Evidencia visible del propio desempeño de una capacidad --
    "aprender tanto de los aciertos como de los errores" (principio
    aprobado 2026-08-06). Cuenta simple sobre `actual_outcome`, sin ningún
    modelo ni ponderación nueva -- la atribución de qué variable explica
    cada acierto/error (`attribution.py`) sigue sin implementarse."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT actual_outcome, COUNT(*) AS n FROM predictions "
            "WHERE capability = ? AND actual_outcome IS NOT NULL GROUP BY actual_outcome",
            (capability,),
        ).fetchall()
    counts = {r["actual_outcome"]: r["n"] for r in rows}
    correctas = counts.get("correcta", 0)
    incorrectas = counts.get("incorrecta", 0)
    sin_evidencia = counts.get("sin_evidencia_suficiente", 0)
    evaluables = correctas + incorrectas
    return {
        "correctas": correctas,
        "incorrectas": incorrectas,
        "sin_evidencia_suficiente": sin_evidencia,
        "total_calificadas": correctas + incorrectas + sin_evidencia,
        "accuracy_pct": (correctas / evaluables * 100) if evaluables > 0 else None,
    }
