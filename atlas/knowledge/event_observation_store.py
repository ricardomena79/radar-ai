"""Registro de revisitas de eventos: el ciclo de vida completo de un movimiento.

event_store.py guarda el nacimiento de un evento (condiciones iniciales,
snapshot único). Este módulo guarda lo que pasó *después*: cada vez que
Atlas vuelve a mirar un evento ya registrado (+5m, +15m, +30m, +60m, cierre
de sesión, día siguiente), queda una fila acá con precio, máximo y mínimo
alcanzados desde el evento, retorno, volumen, momentum, money flow y el
estado del movimiento en ese momento.

Un evento puede tener hasta una fila por checkpoint (UNIQUE event_id+
checkpoint): revisitar un checkpoint ya registrado no lo duplica.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from atlas.knowledge.event_store import connect

# Orden cronológico del ciclo de revisitas de un evento. El propio nombre es
# el identificador (`checkpoint`) que se guarda en cada fila.
CHECKPOINT_5M = "+5m"
CHECKPOINT_15M = "+15m"
CHECKPOINT_30M = "+30m"
CHECKPOINT_60M = "+60m"
CHECKPOINT_EOD = "eod"
CHECKPOINT_NEXT_DAY = "next_day"
CHECKPOINTS_IN_ORDER = [
    CHECKPOINT_5M,
    CHECKPOINT_15M,
    CHECKPOINT_30M,
    CHECKPOINT_60M,
    CHECKPOINT_EOD,
    CHECKPOINT_NEXT_DAY,
]
# Los dos últimos checkpoints son los que cierran el ciclo del evento: a
# partir de ahí ya no hace falta seguir revisitándolo.
FINAL_CHECKPOINTS = {CHECKPOINT_EOD, CHECKPOINT_NEXT_DAY}


@dataclass(frozen=True)
class EventObservation:
    """Una revisita real de un evento ya registrado."""

    event_id: int
    ticker: str
    checkpoint: str
    observed_at: str  # ISO 8601
    price: Optional[float] = None
    max_price_since_event: Optional[float] = None
    min_price_since_event: Optional[float] = None
    max_return_percent: Optional[float] = None
    return_percent: Optional[float] = None
    # Minutos reales entre el evento y el instante (dentro de la historia
    # intradía ya consultada) en que se registró max_price_since_event -- no
    # una estimación, sale del índice de tiempo real de las barras de yfinance.
    minutes_to_max: Optional[int] = None
    volume: Optional[int] = None
    momentum_score: Optional[float] = None
    money_flow_score: Optional[float] = None
    movement_state: Optional[str] = None
    data_status: Optional[str] = None
    id: Optional[int] = field(default=None, compare=False)


def _row_to_observation(row: sqlite3.Row) -> EventObservation:
    return EventObservation(
        id=row["id"],
        event_id=row["event_id"],
        ticker=row["ticker"],
        checkpoint=row["checkpoint"],
        observed_at=row["observed_at"],
        price=row["price"],
        max_price_since_event=row["max_price_since_event"],
        min_price_since_event=row["min_price_since_event"],
        max_return_percent=row["max_return_percent"],
        return_percent=row["return_percent"],
        minutes_to_max=row["minutes_to_max"],
        volume=row["volume"],
        momentum_score=row["momentum_score"],
        money_flow_score=row["money_flow_score"],
        movement_state=row["movement_state"],
        data_status=row["data_status"],
    )


class EventObservationStore:
    """CRUD de revisitas de eventos, sobre la misma base SQLite de knowledge/."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._connection = connect(db_path)
        self._create_schema()

    def _create_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS event_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                checkpoint TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                price REAL,
                max_price_since_event REAL,
                min_price_since_event REAL,
                max_return_percent REAL,
                return_percent REAL,
                minutes_to_max INTEGER,
                volume INTEGER,
                momentum_score REAL,
                money_flow_score REAL,
                movement_state TEXT,
                data_status TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (event_id, checkpoint),
                FOREIGN KEY (event_id) REFERENCES events(id)
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_obs_event ON event_observations(event_id)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_obs_ticker ON event_observations(ticker)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_obs_checkpoint ON event_observations(checkpoint)"
        )
        self._connection.commit()

    def record_observation(self, observation: EventObservation) -> int:
        """Guarda una revisita. Si el checkpoint ya estaba registrado para ese
        evento, no hace nada (idempotente) y devuelve su id existente."""
        existing = self.get_observation(observation.event_id, observation.checkpoint)
        if existing is not None:
            return existing.id

        cursor = self._connection.execute(
            """
            INSERT INTO event_observations (
                event_id, ticker, checkpoint, observed_at, price,
                max_price_since_event, min_price_since_event, max_return_percent,
                return_percent, minutes_to_max, volume, momentum_score, money_flow_score,
                movement_state, data_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation.event_id,
                observation.ticker,
                observation.checkpoint,
                observation.observed_at,
                observation.price,
                observation.max_price_since_event,
                observation.min_price_since_event,
                observation.max_return_percent,
                observation.return_percent,
                observation.minutes_to_max,
                observation.volume,
                observation.momentum_score,
                observation.money_flow_score,
                observation.movement_state,
                observation.data_status,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def get_observation(self, event_id: int, checkpoint: str) -> Optional[EventObservation]:
        """Devuelve la revisita de un evento en un checkpoint dado, o None si no existe."""
        row = self._connection.execute(
            "SELECT * FROM event_observations WHERE event_id = ? AND checkpoint = ?",
            (event_id, checkpoint),
        ).fetchone()
        return _row_to_observation(row) if row else None

    def get_observations_for_event(self, event_id: int) -> List[EventObservation]:
        """Todas las revisitas de un evento, en el orden en que ocurrieron."""
        rows = self._connection.execute(
            "SELECT * FROM event_observations WHERE event_id = ? ORDER BY observed_at ASC",
            (event_id,),
        ).fetchall()
        return [_row_to_observation(row) for row in rows]

    def completed_checkpoints(self, event_id: int) -> set:
        """Checkpoints ya registrados para un evento (para saber cuáles faltan)."""
        rows = self._connection.execute(
            "SELECT checkpoint FROM event_observations WHERE event_id = ?", (event_id,)
        ).fetchall()
        return {row["checkpoint"] for row in rows}

    def count(self) -> int:
        """Total de revisitas registradas (observaciones completadas)."""
        row = self._connection.execute("SELECT COUNT(*) AS n FROM event_observations").fetchone()
        return int(row["n"])

    def count_by_state(self) -> dict:
        """Conteo de revisitas agrupado por movement_state."""
        rows = self._connection.execute(
            "SELECT movement_state, COUNT(*) AS n FROM event_observations GROUP BY movement_state"
        ).fetchall()
        return {(row["movement_state"] or "Sin clasificar"): row["n"] for row in rows}

    def close(self) -> None:
        """Cierra la conexión SQLite."""
        self._connection.close()
