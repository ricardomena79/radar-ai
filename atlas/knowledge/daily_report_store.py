"""Informe diario: el resumen de cierre de sesión de todas las
recomendaciones (eventos EXPLOSION) del día, más la autoevaluación de Atlas.

Un informe por fecha (UNIQUE). Lo genera atlas_live/evolution_worker.py una
sola vez por día de mercado, cuando ya se completó el checkpoint de cierre
de sesión de los eventos de ese día -- no antes, porque hasta ese momento
no hay resultado real que resumir.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.knowledge.event_store import connect


@dataclass(frozen=True)
class DailyReport:
    """El informe de cierre de sesión de una fecha."""

    date: str  # "YYYY-MM-DD"
    total_tickers_analyzed: int
    total_recommendations: int
    wins: int
    losses: int
    win_rate: Optional[float]
    avg_return_percent: Optional[float]
    best_ticker: Optional[str]
    best_return_percent: Optional[float]
    worst_ticker: Optional[str]
    worst_return_percent: Optional[float]
    top_patterns: List[Dict[str, Any]] = field(default_factory=list)
    bottom_patterns: List[Dict[str, Any]] = field(default_factory=list)
    self_assessment: Dict[str, str] = field(default_factory=dict)
    generated_at: Optional[str] = None
    id: Optional[int] = field(default=None, compare=False)


def _row_to_report(row: sqlite3.Row) -> DailyReport:
    return DailyReport(
        id=row["id"],
        date=row["date"],
        total_tickers_analyzed=row["total_tickers_analyzed"],
        total_recommendations=row["total_recommendations"],
        wins=row["wins"],
        losses=row["losses"],
        win_rate=row["win_rate"],
        avg_return_percent=row["avg_return_percent"],
        best_ticker=row["best_ticker"],
        best_return_percent=row["best_return_percent"],
        worst_ticker=row["worst_ticker"],
        worst_return_percent=row["worst_return_percent"],
        top_patterns=json.loads(row["top_patterns"]) if row["top_patterns"] else [],
        bottom_patterns=json.loads(row["bottom_patterns"]) if row["bottom_patterns"] else [],
        self_assessment=json.loads(row["self_assessment"]) if row["self_assessment"] else {},
        generated_at=row["generated_at"],
    )


class DailyReportStore:
    """CRUD de informes diarios, sobre la misma base SQLite de knowledge/."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._connection = connect(db_path)
        self._create_schema()

    def _create_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                total_tickers_analyzed INTEGER,
                total_recommendations INTEGER,
                wins INTEGER,
                losses INTEGER,
                win_rate REAL,
                avg_return_percent REAL,
                best_ticker TEXT,
                best_return_percent REAL,
                worst_ticker TEXT,
                worst_return_percent REAL,
                top_patterns TEXT,
                bottom_patterns TEXT,
                self_assessment TEXT,
                generated_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute("CREATE INDEX IF NOT EXISTS idx_daily_reports_date ON daily_reports(date)")
        self._connection.commit()

    def exists(self, date: str) -> bool:
        """Indica si ya se generó el informe de una fecha (para no regenerarlo)."""
        row = self._connection.execute("SELECT 1 FROM daily_reports WHERE date = ?", (date,)).fetchone()
        return row is not None

    def save(self, report: DailyReport) -> int:
        """Guarda el informe de una fecha. Si ya existía, lo reemplaza (una
        fecha solo tiene un informe vigente)."""
        now = datetime.now(timezone.utc).isoformat()
        self._connection.execute("DELETE FROM daily_reports WHERE date = ?", (report.date,))
        cursor = self._connection.execute(
            """
            INSERT INTO daily_reports (
                date, total_tickers_analyzed, total_recommendations, wins, losses,
                win_rate, avg_return_percent, best_ticker, best_return_percent,
                worst_ticker, worst_return_percent, top_patterns, bottom_patterns,
                self_assessment, generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report.date,
                report.total_tickers_analyzed,
                report.total_recommendations,
                report.wins,
                report.losses,
                report.win_rate,
                report.avg_return_percent,
                report.best_ticker,
                report.best_return_percent,
                report.worst_ticker,
                report.worst_return_percent,
                json.dumps(report.top_patterns),
                json.dumps(report.bottom_patterns),
                json.dumps(report.self_assessment),
                now,
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def get(self, date: str) -> Optional[DailyReport]:
        """Devuelve el informe de una fecha, o None si no se generó todavía."""
        row = self._connection.execute("SELECT * FROM daily_reports WHERE date = ?", (date,)).fetchone()
        return _row_to_report(row) if row else None

    def list_recent(self, limit: int = 30) -> List[DailyReport]:
        """Los informes más recientes, más nuevo primero."""
        rows = self._connection.execute(
            "SELECT * FROM daily_reports ORDER BY date DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_report(row) for row in rows]

    def close(self) -> None:
        """Cierra la conexión SQLite."""
        self._connection.close()
