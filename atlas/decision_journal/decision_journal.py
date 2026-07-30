"""Decision Journal: registro de las operaciones del propio operador.

Representa el "conocimiento del operador" -- completamente independiente
del "conocimiento del mercado" que vive en atlas/knowledge/. Por eso usa su
propia base SQLite (`decision_journal.db`, distinta de `atlas_knowledge.db`)
y no importa nada de atlas.knowledge: no hay forma de que estos dos
conocimientos se mezclen por accidente, ni siquiera compartiendo una
conexión o una tabla.

Responsabilidad única: registrar operaciones y exponer una API de lectura.
No analiza comportamiento, no genera estadísticas, no saca conclusiones --
eso es responsabilidad exclusiva de Operator Learning Engine, que consume
estos datos a través de `get_trades()`.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "cache" / "decision_journal.db"


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


@dataclass(frozen=True)
class Trade:
    """Una operación registrada por el operador."""

    date: str  # "YYYY-MM-DD"
    time: str  # "HH:MM:SS"
    ticker: str
    buy_price: Optional[float] = None
    sell_price: Optional[float] = None
    buy_reason: Optional[str] = None
    sell_reason: Optional[str] = None
    atlas_rank_at_time: Optional[int] = None
    atlas_score: Optional[float] = None
    momentum_score: Optional[float] = None
    money_flow_score: Optional[float] = None
    evidence_level: Optional[str] = None  # texto libre, ej. "A+", "B" -- sin escala fija todavía
    final_result: Optional[str] = None  # texto libre, ej. "GANANCIA", "PERDIDA"
    profit_loss_percent: Optional[float] = None
    id: Optional[int] = field(default=None, compare=False)


def _row_to_trade(row: sqlite3.Row) -> Trade:
    return Trade(
        id=row["id"],
        date=row["date"],
        time=row["time"],
        ticker=row["ticker"],
        buy_price=row["buy_price"],
        sell_price=row["sell_price"],
        buy_reason=row["buy_reason"],
        sell_reason=row["sell_reason"],
        atlas_rank_at_time=row["atlas_rank_at_time"],
        atlas_score=row["atlas_score"],
        momentum_score=row["momentum_score"],
        money_flow_score=row["money_flow_score"],
        evidence_level=row["evidence_level"],
        final_result=row["final_result"],
        profit_loss_percent=row["profit_loss_percent"],
    )


class DecisionJournalStore:
    """CRUD de operaciones del operador, sobre su propia base SQLite local."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._connection = _connect(db_path)
        self._create_schema()

    def _create_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                ticker TEXT NOT NULL,
                buy_price REAL,
                sell_price REAL,
                buy_reason TEXT,
                sell_reason TEXT,
                atlas_rank_at_time INTEGER,
                atlas_score REAL,
                momentum_score REAL,
                money_flow_score REAL,
                evidence_level TEXT,
                final_result TEXT,
                profit_loss_percent REAL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute("CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker)")
        self._connection.execute("CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(date)")
        self._connection.execute("CREATE INDEX IF NOT EXISTS idx_trades_result ON trades(final_result)")
        self._connection.commit()

    def record_trade(self, trade: Trade) -> int:
        """Guarda una operación y devuelve su id."""
        cursor = self._connection.execute(
            """
            INSERT INTO trades (
                date, time, ticker, buy_price, sell_price, buy_reason, sell_reason,
                atlas_rank_at_time, atlas_score, momentum_score, money_flow_score,
                evidence_level, final_result, profit_loss_percent, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade.date,
                trade.time,
                trade.ticker,
                trade.buy_price,
                trade.sell_price,
                trade.buy_reason,
                trade.sell_reason,
                trade.atlas_rank_at_time,
                trade.atlas_score,
                trade.momentum_score,
                trade.money_flow_score,
                trade.evidence_level,
                trade.final_result,
                trade.profit_loss_percent,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def get_trades(
        self,
        ticker: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 100,
    ) -> List[Trade]:
        """Consulta operaciones por ticker y/o rango de fechas."""
        clauses = []
        params: list = []

        if ticker is not None:
            clauses.append("ticker = ?")
            params.append(ticker)
        if start_date is not None:
            clauses.append("date >= ?")
            params.append(start_date)
        if end_date is not None:
            clauses.append("date <= ?")
            params.append(end_date)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM trades {where} ORDER BY date DESC, time DESC LIMIT ?"
        params.append(limit)

        rows = self._connection.execute(query, params).fetchall()
        return [_row_to_trade(row) for row in rows]

    def close(self) -> None:
        """Cierra la conexión SQLite."""
        self._connection.close()


class DecisionJournal:
    """Fachada del Decision Journal: registrar y leer, nada más.

    Cualquier análisis del comportamiento del operador (horarios, errores
    repetitivos, ventas tempranas/tardías, disciplina, cumplimiento de las
    recomendaciones de Atlas, evolución del desempeño) vive en
    Operator Learning Engine, que consume estos datos vía `get_trades()`.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.trades = DecisionJournalStore(db_path)

    def record_trade(self, trade: Trade) -> int:
        """Registra una operación del operador."""
        return self.trades.record_trade(trade)

    def get_trades(self, **kwargs) -> List[Trade]:
        """Consulta operaciones registradas (ver DecisionJournalStore.get_trades)."""
        return self.trades.get_trades(**kwargs)

    def close(self) -> None:
        """Cierra la conexión SQLite."""
        self.trades.close()
