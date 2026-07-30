"""Registro persistente de predicciones de Atlas, en la misma base SQLite local.

Guarda lo que el Decision Engine predijo para un símbolo en un momento dado
(decisión, confianza, scores) para poder compararlo más adelante con el
resultado real registrado en events.py -- esa comparación (acierto/error)
es trabajo de un futuro Learning Engine, no de este módulo: aquí solo se
registra y se consulta.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from atlas.knowledge.event_store import connect


@dataclass(frozen=True)
class PredictionRecord:
    """Una predicción del Decision Engine, registrada en el momento en que se hizo."""

    date: str
    time: str
    ticker: str
    mode: str
    decision: str
    confidence: float
    atlas_score: Optional[float] = None
    momentum_score: Optional[float] = None
    money_flow_score: Optional[float] = None
    event_id: Optional[int] = None  # vínculo opcional a un evento ya confirmado en events
    id: Optional[int] = field(default=None, compare=False)


def _row_to_prediction(row: sqlite3.Row) -> PredictionRecord:
    return PredictionRecord(
        id=row["id"],
        date=row["date"],
        time=row["time"],
        ticker=row["ticker"],
        mode=row["mode"],
        decision=row["decision"],
        confidence=row["confidence"],
        atlas_score=row["atlas_score"],
        momentum_score=row["momentum_score"],
        money_flow_score=row["money_flow_score"],
        event_id=row["event_id"],
    )


class PredictionStore:
    """CRUD de predicciones históricas del Decision Engine sobre una base SQLite local."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._connection = connect(db_path)
        self._create_schema()

    def _create_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                ticker TEXT NOT NULL,
                mode TEXT NOT NULL,
                decision TEXT NOT NULL,
                confidence REAL NOT NULL,
                atlas_score REAL,
                momentum_score REAL,
                money_flow_score REAL,
                event_id INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY (event_id) REFERENCES events(id)
            )
            """
        )
        self._connection.execute("CREATE INDEX IF NOT EXISTS idx_predictions_ticker ON predictions(ticker)")
        self._connection.execute("CREATE INDEX IF NOT EXISTS idx_predictions_date ON predictions(date)")
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_predictions_decision ON predictions(decision)"
        )
        self._connection.commit()

    def record_prediction(self, prediction: PredictionRecord) -> int:
        """Guarda una predicción y devuelve su id."""
        cursor = self._connection.execute(
            """
            INSERT INTO predictions (
                date, time, ticker, mode, decision, confidence,
                atlas_score, momentum_score, money_flow_score, event_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prediction.date,
                prediction.time,
                prediction.ticker,
                prediction.mode,
                prediction.decision,
                prediction.confidence,
                prediction.atlas_score,
                prediction.momentum_score,
                prediction.money_flow_score,
                prediction.event_id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def get_predictions(
        self,
        ticker: Optional[str] = None,
        decision: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 100,
    ) -> List[PredictionRecord]:
        """Consulta predicciones históricas por ticker, decisión y/o rango de fechas."""
        clauses = []
        params: list = []

        if ticker is not None:
            clauses.append("ticker = ?")
            params.append(ticker)
        if decision is not None:
            clauses.append("decision = ?")
            params.append(decision)
        if start_date is not None:
            clauses.append("date >= ?")
            params.append(start_date)
        if end_date is not None:
            clauses.append("date <= ?")
            params.append(end_date)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM predictions {where} ORDER BY date DESC, time DESC LIMIT ?"
        params.append(limit)

        rows = self._connection.execute(query, params).fetchall()
        return [_row_to_prediction(row) for row in rows]

    def count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()
        return int(row["n"])

    def count_by_decision(self) -> dict:
        rows = self._connection.execute(
            "SELECT decision, COUNT(*) AS n FROM predictions GROUP BY decision"
        ).fetchall()
        return {row["decision"]: row["n"] for row in rows}

    def close(self) -> None:
        self._connection.close()
