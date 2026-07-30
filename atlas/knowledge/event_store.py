"""Registro persistente de eventos de mercado, en SQLite, 100% local.

Guarda cada evento relevante detectado por Atlas -- no solo explosiones,
también colapsos, falsas rupturas y mercado normal -- con el contexto
completo (precio, gap, RVOL, scores, decisión, resultado) para que
pattern_store.py pueda buscar patrones similares más adelante.

Pensado para escalar a millones de filas: SQLite en modo WAL, con índices
sobre ticker, tipo de evento, sector, industria y fecha (las combinaciones
que se van a consultar: "explosiones por sector", "colapsos por industria",
series históricas de un ticker, etc.).
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "cache" / "atlas_knowledge.db"

EXPLOSION = "EXPLOSION"
COLLAPSE = "COLLAPSE"
FALSE_BREAKOUT = "FALSE_BREAKOUT"
NORMAL = "NORMAL"

EVENT_TYPES = {EXPLOSION, COLLAPSE, FALSE_BREAKOUT, NORMAL}


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Abre (y prepara) una conexión SQLite compartida por todos los stores de knowledge/."""
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    # WAL + synchronous=NORMAL: buen equilibrio de durabilidad/velocidad para
    # una base local que debe soportar millones de filas y lecturas frecuentes.
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@dataclass(frozen=True)
class MarketEvent:
    """Un evento de mercado registrado por Atlas."""

    date: str  # "YYYY-MM-DD"
    time: str  # "HH:MM:SS"
    ticker: str
    price: float
    event_type: str
    sector: Optional[str] = None
    industry: Optional[str] = None
    gap_percent: Optional[float] = None
    rvol: Optional[float] = None
    volume: Optional[int] = None
    float_shares: Optional[int] = None
    market_cap: Optional[float] = None
    atlas_score: Optional[float] = None
    momentum_score: Optional[float] = None
    money_flow_score: Optional[float] = None
    decision: Optional[str] = None
    max_result_percent: Optional[float] = None
    close_result_percent: Optional[float] = None
    id: Optional[int] = field(default=None, compare=False)


def _row_to_event(row: sqlite3.Row) -> MarketEvent:
    return MarketEvent(
        id=row["id"],
        date=row["date"],
        time=row["time"],
        ticker=row["ticker"],
        sector=row["sector"],
        industry=row["industry"],
        price=row["price"],
        gap_percent=row["gap_percent"],
        rvol=row["rvol"],
        volume=row["volume"],
        float_shares=row["float_shares"],
        market_cap=row["market_cap"],
        atlas_score=row["atlas_score"],
        momentum_score=row["momentum_score"],
        money_flow_score=row["money_flow_score"],
        decision=row["decision"],
        max_result_percent=row["max_result_percent"],
        close_result_percent=row["close_result_percent"],
        event_type=row["event_type"],
    )


class EventStore:
    """CRUD de eventos de mercado sobre una base SQLite local."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._connection = connect(db_path)
        self._create_schema()

    def _create_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                ticker TEXT NOT NULL,
                sector TEXT,
                industry TEXT,
                price REAL NOT NULL,
                gap_percent REAL,
                rvol REAL,
                volume INTEGER,
                float_shares INTEGER,
                market_cap REAL,
                atlas_score REAL,
                momentum_score REAL,
                money_flow_score REAL,
                decision TEXT,
                max_result_percent REAL,
                close_result_percent REAL,
                event_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        # Índices para las consultas que va a necesitar el resto del sistema:
        # explosiones/colapsos por sector o industria, historial de un
        # ticker, y series por fecha.
        self._connection.execute("CREATE INDEX IF NOT EXISTS idx_events_ticker ON events(ticker)")
        self._connection.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)")
        self._connection.execute("CREATE INDEX IF NOT EXISTS idx_events_sector ON events(sector)")
        self._connection.execute("CREATE INDEX IF NOT EXISTS idx_events_industry ON events(industry)")
        self._connection.execute("CREATE INDEX IF NOT EXISTS idx_events_date ON events(date)")
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_sector_type ON events(sector, event_type)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_industry_type ON events(industry, event_type)"
        )
        self._connection.commit()

    def record_event(self, event: MarketEvent) -> int:
        """Guarda un evento y devuelve su id."""
        if event.event_type not in EVENT_TYPES:
            raise ValueError(f"event_type inválido: '{event.event_type}'. Válidos: {sorted(EVENT_TYPES)}")

        cursor = self._connection.execute(
            """
            INSERT INTO events (
                date, time, ticker, sector, industry, price, gap_percent, rvol,
                volume, float_shares, market_cap, atlas_score, momentum_score,
                money_flow_score, decision, max_result_percent, close_result_percent,
                event_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.date,
                event.time,
                event.ticker,
                event.sector,
                event.industry,
                event.price,
                event.gap_percent,
                event.rvol,
                event.volume,
                event.float_shares,
                event.market_cap,
                event.atlas_score,
                event.momentum_score,
                event.money_flow_score,
                event.decision,
                event.max_result_percent,
                event.close_result_percent,
                event.event_type,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def get_event(self, event_id: int) -> Optional[MarketEvent]:
        row = self._connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return _row_to_event(row) if row else None

    def get_events(
        self,
        ticker: Optional[str] = None,
        event_type: Optional[str] = None,
        sector: Optional[str] = None,
        industry: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 100,
    ) -> List[MarketEvent]:
        """Busca eventos por cualquier combinación de ticker/tipo/sector/industria/rango de fechas."""
        clauses = []
        params: list = []

        if ticker is not None:
            clauses.append("ticker = ?")
            params.append(ticker)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        if sector is not None:
            clauses.append("sector = ?")
            params.append(sector)
        if industry is not None:
            clauses.append("industry = ?")
            params.append(industry)
        if start_date is not None:
            clauses.append("date >= ?")
            params.append(start_date)
        if end_date is not None:
            clauses.append("date <= ?")
            params.append(end_date)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM events {where} ORDER BY date DESC, time DESC LIMIT ?"
        params.append(limit)

        rows = self._connection.execute(query, params).fetchall()
        return [_row_to_event(row) for row in rows]

    def count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) AS n FROM events").fetchone()
        return int(row["n"])

    def count_by_type(self) -> dict:
        rows = self._connection.execute(
            "SELECT event_type, COUNT(*) AS n FROM events GROUP BY event_type"
        ).fetchall()
        return {row["event_type"]: row["n"] for row in rows}

    def count_by_sector(self, event_type: Optional[str] = None) -> dict:
        """Conteo de eventos por sector, opcionalmente filtrado por tipo (ej. EXPLOSION)."""
        if event_type is not None:
            rows = self._connection.execute(
                "SELECT sector, COUNT(*) AS n FROM events WHERE event_type = ? GROUP BY sector",
                (event_type,),
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT sector, COUNT(*) AS n FROM events GROUP BY sector"
            ).fetchall()
        return {(row["sector"] or "Sin clasificar"): row["n"] for row in rows}

    @property
    def connection(self) -> sqlite3.Connection:
        """Conexión SQLite subyacente (usada por pattern_store.py para consultas de similitud)."""
        return self._connection

    def close(self) -> None:
        self._connection.close()
