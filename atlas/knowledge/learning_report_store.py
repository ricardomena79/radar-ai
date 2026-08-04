"""Registro permanente de cada corrida del Learning Engine, en SQLite, 100% local.

Cada vez que Learning Engine ejecuta generate_learning_report() -- precisión
general, precisión por decisión, evolución de patrones y propuestas de
calibración -- este módulo guarda ese resultado como una fila nueva, sin
sobrescribir ni resumir lo anterior. Es historia, no una foto del último
estado: pensado para analizar tendencias y comparar períodos a lo largo de
años de funcionamiento.

Learning Engine sigue siendo quien analiza. Esta tabla solo conserva lo que
Learning Engine ya concluyó. Calibration Manager sigue siendo el único que
aprueba o aplica cambios -- este store no participa de esa decisión, solo
deja constancia del estado de cada propuesta en el momento en que se generó
(consultar Calibration Manager para el estado vigente/actual).
"""

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from atlas.knowledge.event_store import connect, ensure_columns

_TABLE = "learning_reports"


@dataclass(frozen=True)
class LearningReportRecord:
    """Un reporte de aprendizaje ya generado y persistido."""

    date: str  # "YYYY-MM-DD", para consultas por rango de fechas
    generated_at: str  # timestamp completo ISO 8601
    session_evaluated: Optional[str]
    overall_accuracy: Dict[str, Any]
    accuracy_by_decision: Dict[str, Any]
    pattern_reports: List[Dict[str, Any]]
    calibration_proposals: List[Dict[str, Any]]
    events_analyzed_count: int
    patterns_analyzed_count: int
    patterns_confirmed_count: int
    calibration_proposals_count: int
    executive_summary: str
    # True si hubo muestra suficiente para calcular precisión real (según el
    # propio umbral de Accuracy Tracker, MIN_SAMPLE_SIZE). False no es un
    # error del sistema: es un día con pocos datos, y debe quedar marcado
    # como tal explícitamente, no inferido del texto del resumen.
    data_sufficient: bool = True
    id: Optional[int] = field(default=None, compare=False)


def _row_to_report(row: sqlite3.Row) -> LearningReportRecord:
    return LearningReportRecord(
        id=row["id"],
        date=row["date"],
        generated_at=row["generated_at"],
        session_evaluated=row["session_evaluated"],
        overall_accuracy=json.loads(row["overall_accuracy_json"]) if row["overall_accuracy_json"] else {},
        accuracy_by_decision=json.loads(row["accuracy_by_decision_json"]) if row["accuracy_by_decision_json"] else {},
        pattern_reports=json.loads(row["pattern_reports_json"]) if row["pattern_reports_json"] else [],
        calibration_proposals=json.loads(row["calibration_proposals_json"]) if row["calibration_proposals_json"] else [],
        events_analyzed_count=row["events_analyzed_count"],
        patterns_analyzed_count=row["patterns_analyzed_count"],
        patterns_confirmed_count=row["patterns_confirmed_count"],
        calibration_proposals_count=row["calibration_proposals_count"],
        executive_summary=row["executive_summary"],
        data_sufficient=bool(row["data_sufficient"]),
    )


class LearningReportStore:
    """Historial completo de reportes de Learning Engine, sobre la misma base de la Knowledge Base."""

    def __init__(self, db_path=None) -> None:
        self._connection = connect(db_path)
        self._create_schema()

    def _create_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                session_evaluated TEXT,
                overall_accuracy_json TEXT,
                accuracy_by_decision_json TEXT,
                pattern_reports_json TEXT,
                calibration_proposals_json TEXT,
                events_analyzed_count INTEGER NOT NULL DEFAULT 0,
                patterns_analyzed_count INTEGER NOT NULL DEFAULT 0,
                patterns_confirmed_count INTEGER NOT NULL DEFAULT 0,
                calibration_proposals_count INTEGER NOT NULL DEFAULT 0,
                executive_summary TEXT
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_learning_reports_date ON learning_reports(date)"
        )
        self._connection.commit()
        # Migración aditiva: agregada después de la creación inicial de la tabla.
        ensure_columns(self._connection, _TABLE, [("data_sufficient", "INTEGER NOT NULL DEFAULT 1")])

    def record_report(self, report: LearningReportRecord) -> int:
        """Guarda un reporte nuevo. Nunca actualiza ni borra uno anterior."""
        cursor = self._connection.execute(
            """
            INSERT INTO learning_reports (
                date, generated_at, session_evaluated,
                overall_accuracy_json, accuracy_by_decision_json,
                pattern_reports_json, calibration_proposals_json,
                events_analyzed_count, patterns_analyzed_count,
                patterns_confirmed_count, calibration_proposals_count,
                executive_summary, data_sufficient
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report.date,
                report.generated_at,
                report.session_evaluated,
                json.dumps(report.overall_accuracy),
                json.dumps(report.accuracy_by_decision),
                json.dumps(report.pattern_reports),
                json.dumps(report.calibration_proposals),
                report.events_analyzed_count,
                report.patterns_analyzed_count,
                report.patterns_confirmed_count,
                report.calibration_proposals_count,
                report.executive_summary,
                int(report.data_sufficient),
            ),
        )
        self._connection.commit()
        return cursor.lastrowid

    def get_reports(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[LearningReportRecord]:
        """Historial de reportes, más reciente primero, opcionalmente filtrado por rango de fechas."""
        query = f"SELECT * FROM {_TABLE}"
        conditions = []
        params: List[Any] = []
        if start_date is not None:
            conditions.append("date >= ?")
            params.append(start_date)
        if end_date is not None:
            conditions.append("date <= ?")
            params.append(end_date)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        rows = self._connection.execute(query, params).fetchall()
        return [_row_to_report(row) for row in rows]

    def get_latest(self) -> Optional[LearningReportRecord]:
        """El reporte más reciente, o None si todavía no se generó ninguno."""
        rows = self.get_reports(limit=1)
        return rows[0] if rows else None

    def has_report_for_date(self, date: str) -> bool:
        """True si ya existe al menos un reporte para esa fecha (guardia de idempotencia)."""
        row = self._connection.execute(
            f"SELECT 1 FROM {_TABLE} WHERE date = ? LIMIT 1", (date,)
        ).fetchone()
        return row is not None

    def count(self) -> int:
        """Total de reportes históricos guardados."""
        row = self._connection.execute(f"SELECT COUNT(*) AS n FROM {_TABLE}").fetchone()
        return int(row["n"])

    def close(self) -> None:
        self._connection.close()
