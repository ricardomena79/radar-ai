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

from atlas.config.config import data_dir

DEFAULT_DB_PATH = data_dir() / "atlas_knowledge.db"

EXPLOSION = "EXPLOSION"
COLLAPSE = "COLLAPSE"
FALSE_BREAKOUT = "FALSE_BREAKOUT"
NORMAL = "NORMAL"

EVENT_TYPES = {EXPLOSION, COLLAPSE, FALSE_BREAKOUT, NORMAL}

# Fuente de un dato: de dónde vino cada pieza de información.
SOURCE_YAHOO_FINANCE = "Yahoo Finance"
SOURCE_RACIONAL = "Racional"
SOURCE_CALCULATED = "Calculado por Atlas"
DATA_SOURCES = {SOURCE_YAHOO_FINANCE, SOURCE_RACIONAL, SOURCE_CALCULATED}

# Estado de un dato en el momento en que se capturó.
STATUS_OK = "OK"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_UNAVAILABLE = "NO DISPONIBLE"
STATUS_ESTIMATED = "ESTIMADO"
DATA_STATUSES = {STATUS_OK, STATUS_TIMEOUT, STATUS_UNAVAILABLE, STATUS_ESTIMATED}

# Columnas de contexto de mercado (Market Context Engine), compartidas por
# events y predictions. Centralizadas aquí para que ambos stores usen
# exactamente los mismos nombres/tipos y la misma migración.
CONTEXT_COLUMNS = [
    ("spy_price", "REAL"),
    ("spy_change_percent", "REAL"),
    ("qqq_price", "REAL"),
    ("qqq_change_percent", "REAL"),
    ("iwm_price", "REAL"),
    ("iwm_change_percent", "REAL"),
    ("vix_price", "REAL"),
    ("vix_change_percent", "REAL"),
    ("btc_price", "REAL"),
    ("btc_change_percent", "REAL"),
    ("sector_etf_symbol", "TEXT"),
    ("sector_etf_change_percent", "REAL"),
    ("leading_sector", "TEXT"),
    ("leading_industry", "TEXT"),
    ("sector_money_flow_score", "REAL"),
    ("day_of_week", "TEXT"),
    ("month", "TEXT"),
    ("earnings_season", "INTEGER"),  # 0/1/NULL: SQLite no tiene tipo booleano nativo
]

# Trazabilidad: de dónde vino el dato, cuándo se capturó exactamente, en qué
# estado, y qué versión de cada motor de Atlas participó.
PROVENANCE_COLUMNS = [
    ("data_source", "TEXT"),
    ("captured_at", "TEXT"),  # hora exacta de captura del dato (distinta de created_at, que es la inserción en la BD)
    ("data_status", "TEXT"),
    ("engine_versions", "TEXT"),  # JSON: {"atlas_core": "1.0", ...}
]

# Preparación para el futuro Market Replay Engine: dejar registrado dónde
# quedó un símbolo dentro del ranking del escaneo que lo produjo, sin
# necesidad de reconstruirlo después. No se usa todavía.
REPLAY_COLUMNS = [
    ("rank_in_scan", "INTEGER"),
    ("scan_size", "INTEGER"),
]


def ensure_columns(connection: sqlite3.Connection, table: str, columns) -> None:
    """Agrega a `table` las columnas de `columns` que todavía no existan (migración aditiva)."""
    existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, sql_type in columns:
        if name not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
    connection.commit()


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
    # Contexto de mercado (Market Context Engine), capturado junto al evento.
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


def _row_to_event(row: sqlite3.Row) -> MarketEvent:
    columns = row.keys()
    earnings_season = row["earnings_season"] if "earnings_season" in columns else None
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

        # Migración aditiva: agrega las columnas de contexto de mercado si la
        # tabla ya existía de una versión anterior sin ellas.
        ensure_columns(self._connection, "events", CONTEXT_COLUMNS)
        self._connection.execute("CREATE INDEX IF NOT EXISTS idx_events_day_of_week ON events(day_of_week)")
        self._connection.execute("CREATE INDEX IF NOT EXISTS idx_events_month ON events(month)")
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_leading_sector ON events(leading_sector)"
        )
        self._connection.commit()

        # Migración aditiva: trazabilidad del dato y preparación para el
        # futuro Market Replay Engine.
        ensure_columns(self._connection, "events", PROVENANCE_COLUMNS)
        ensure_columns(self._connection, "events", REPLAY_COLUMNS)
        self._connection.execute("CREATE INDEX IF NOT EXISTS idx_events_data_status ON events(data_status)")
        self._connection.execute("CREATE INDEX IF NOT EXISTS idx_events_rank_in_scan ON events(rank_in_scan)")
        self._connection.commit()

    def record_event(self, event: MarketEvent) -> int:
        """Guarda un evento y devuelve su id."""
        if event.event_type not in EVENT_TYPES:
            raise ValueError(f"event_type inválido: '{event.event_type}'. Válidos: {sorted(EVENT_TYPES)}")
        if event.data_source is not None and event.data_source not in DATA_SOURCES:
            raise ValueError(f"data_source inválido: '{event.data_source}'. Válidos: {sorted(DATA_SOURCES)}")
        if event.data_status is not None and event.data_status not in DATA_STATUSES:
            raise ValueError(f"data_status inválido: '{event.data_status}'. Válidos: {sorted(DATA_STATUSES)}")

        columns = [
            "date", "time", "ticker", "sector", "industry", "price", "gap_percent", "rvol",
            "volume", "float_shares", "market_cap", "atlas_score", "momentum_score",
            "money_flow_score", "decision", "max_result_percent", "close_result_percent",
            "event_type",
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
            event.spy_price,
            event.spy_change_percent,
            event.qqq_price,
            event.qqq_change_percent,
            event.iwm_price,
            event.iwm_change_percent,
            event.vix_price,
            event.vix_change_percent,
            event.btc_price,
            event.btc_change_percent,
            event.sector_etf_symbol,
            event.sector_etf_change_percent,
            event.leading_sector,
            event.leading_industry,
            event.sector_money_flow_score,
            event.day_of_week,
            event.month,
            (None if event.earnings_season is None else int(event.earnings_season)),
            event.data_source,
            event.captured_at,
            event.data_status,
            event.engine_versions,
            event.rank_in_scan,
            event.scan_size,
            datetime.now(timezone.utc).isoformat(),
        ]
        assert len(columns) == len(values)

        placeholders = ", ".join("?" for _ in columns)
        cursor = self._connection.execute(
            f"INSERT INTO events ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def get_event(self, event_id: int) -> Optional[MarketEvent]:
        """Devuelve el evento por id, o None si no existe."""
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
        """Total de eventos registrados."""
        row = self._connection.execute("SELECT COUNT(*) AS n FROM events").fetchone()
        return int(row["n"])

    def count_by_type(self) -> dict:
        """Conteo de eventos agrupado por event_type."""
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
        """Cierra la conexión SQLite."""
        self._connection.close()
