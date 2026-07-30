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

from atlas.knowledge.event_store import (
    CONTEXT_COLUMNS,
    DATA_SOURCES,
    DATA_STATUSES,
    PROVENANCE_COLUMNS,
    REPLAY_COLUMNS,
    connect,
    ensure_columns,
)


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
    # Contexto de mercado (Market Context Engine), capturado junto a la predicción.
    spy_price: Optional[float] = None
    spy_change_percent: Optional[float] = None
    qqq_price: Optional[float] = None
    qqq_change_percent: Optional[float] = None
    iwm_price: Optional[float] = None
    iwm_change_percent: Optional[float] = None
    vix_price: Optional[float] = None
    vix_change_percent: Optional[float] = None
    btc_price: Optional[float] = None
    btc_change_percent: Optional[float] = None
    sector_etf_symbol: Optional[str] = None
    sector_etf_change_percent: Optional[float] = None
    leading_sector: Optional[str] = None
    leading_industry: Optional[str] = None
    sector_money_flow_score: Optional[float] = None
    day_of_week: Optional[str] = None
    month: Optional[str] = None
    earnings_season: Optional[bool] = None
    # Trazabilidad del dato.
    data_source: Optional[str] = None
    captured_at: Optional[str] = None
    data_status: Optional[str] = None
    engine_versions: Optional[str] = None  # JSON, ver atlas.knowledge.engine_versions
    # Preparación para el futuro Market Replay Engine.
    rank_in_scan: Optional[int] = None
    scan_size: Optional[int] = None
    id: Optional[int] = field(default=None, compare=False)


def _row_to_prediction(row: sqlite3.Row) -> PredictionRecord:
    columns = row.keys()
    earnings_season = row["earnings_season"] if "earnings_season" in columns else None
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
        spy_price=row["spy_price"] if "spy_price" in columns else None,
        spy_change_percent=row["spy_change_percent"] if "spy_change_percent" in columns else None,
        qqq_price=row["qqq_price"] if "qqq_price" in columns else None,
        qqq_change_percent=row["qqq_change_percent"] if "qqq_change_percent" in columns else None,
        iwm_price=row["iwm_price"] if "iwm_price" in columns else None,
        iwm_change_percent=row["iwm_change_percent"] if "iwm_change_percent" in columns else None,
        vix_price=row["vix_price"] if "vix_price" in columns else None,
        vix_change_percent=row["vix_change_percent"] if "vix_change_percent" in columns else None,
        btc_price=row["btc_price"] if "btc_price" in columns else None,
        btc_change_percent=row["btc_change_percent"] if "btc_change_percent" in columns else None,
        sector_etf_symbol=row["sector_etf_symbol"] if "sector_etf_symbol" in columns else None,
        sector_etf_change_percent=(
            row["sector_etf_change_percent"] if "sector_etf_change_percent" in columns else None
        ),
        leading_sector=row["leading_sector"] if "leading_sector" in columns else None,
        leading_industry=row["leading_industry"] if "leading_industry" in columns else None,
        sector_money_flow_score=(
            row["sector_money_flow_score"] if "sector_money_flow_score" in columns else None
        ),
        day_of_week=row["day_of_week"] if "day_of_week" in columns else None,
        month=row["month"] if "month" in columns else None,
        earnings_season=(None if earnings_season is None else bool(earnings_season)),
        data_source=row["data_source"] if "data_source" in columns else None,
        captured_at=row["captured_at"] if "captured_at" in columns else None,
        data_status=row["data_status"] if "data_status" in columns else None,
        engine_versions=row["engine_versions"] if "engine_versions" in columns else None,
        rank_in_scan=row["rank_in_scan"] if "rank_in_scan" in columns else None,
        scan_size=row["scan_size"] if "scan_size" in columns else None,
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

        # Migración aditiva: agrega las columnas de contexto de mercado si la
        # tabla ya existía de una versión anterior sin ellas.
        ensure_columns(self._connection, "predictions", CONTEXT_COLUMNS)
        self._connection.commit()

        # Migración aditiva: trazabilidad del dato y preparación para el
        # futuro Market Replay Engine.
        ensure_columns(self._connection, "predictions", PROVENANCE_COLUMNS)
        ensure_columns(self._connection, "predictions", REPLAY_COLUMNS)
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_predictions_data_status ON predictions(data_status)"
        )
        self._connection.commit()

    def record_prediction(self, prediction: PredictionRecord) -> int:
        """Guarda una predicción y devuelve su id."""
        if prediction.data_source is not None and prediction.data_source not in DATA_SOURCES:
            raise ValueError(
                f"data_source inválido: '{prediction.data_source}'. Válidos: {sorted(DATA_SOURCES)}"
            )
        if prediction.data_status is not None and prediction.data_status not in DATA_STATUSES:
            raise ValueError(
                f"data_status inválido: '{prediction.data_status}'. Válidos: {sorted(DATA_STATUSES)}"
            )

        columns = [
            "date", "time", "ticker", "mode", "decision", "confidence",
            "atlas_score", "momentum_score", "money_flow_score", "event_id",
            "spy_price", "spy_change_percent", "qqq_price", "qqq_change_percent",
            "iwm_price", "iwm_change_percent", "vix_price", "vix_change_percent",
            "btc_price", "btc_change_percent", "sector_etf_symbol", "sector_etf_change_percent",
            "leading_sector", "leading_industry", "sector_money_flow_score",
            "day_of_week", "month", "earnings_season",
            "data_source", "captured_at", "data_status", "engine_versions",
            "rank_in_scan", "scan_size",
            "created_at",
        ]
        values = [
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
            prediction.spy_price,
            prediction.spy_change_percent,
            prediction.qqq_price,
            prediction.qqq_change_percent,
            prediction.iwm_price,
            prediction.iwm_change_percent,
            prediction.vix_price,
            prediction.vix_change_percent,
            prediction.btc_price,
            prediction.btc_change_percent,
            prediction.sector_etf_symbol,
            prediction.sector_etf_change_percent,
            prediction.leading_sector,
            prediction.leading_industry,
            prediction.sector_money_flow_score,
            prediction.day_of_week,
            prediction.month,
            (None if prediction.earnings_season is None else int(prediction.earnings_season)),
            prediction.data_source,
            prediction.captured_at,
            prediction.data_status,
            prediction.engine_versions,
            prediction.rank_in_scan,
            prediction.scan_size,
            datetime.now(timezone.utc).isoformat(),
        ]
        assert len(columns) == len(values)

        placeholders = ", ".join("?" for _ in columns)
        cursor = self._connection.execute(
            f"INSERT INTO predictions ({', '.join(columns)}) VALUES ({placeholders})",
            values,
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
