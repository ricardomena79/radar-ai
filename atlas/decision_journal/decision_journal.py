"""Decision Journal: registro de las operaciones del propio operador.

Representa el "conocimiento del operador" -- completamente independiente
del "conocimiento del mercado" que vive en atlas/knowledge/. Por eso usa su
propia base SQLite (`decision_journal.db`, distinta de `atlas_knowledge.db`)
y no importa nada de atlas.knowledge: no hay forma de que estos dos
conocimientos se mezclen por accidente, ni siquiera compartiendo una
conexión o una tabla.

`registrar una operación` (record_trade) es la función central y ya está
implementada: no requiere ningún algoritmo, es la estructura de datos que
pediste. Lo que todavía NO está implementado son los análisis futuros sobre
el propio operador (horarios, tipos de acciones, errores repetitivos,
ventas tempranas, recomendaciones ignoradas): esos métodos existen como
interfaz (firma + docstring) y lanzan NotImplementedError.
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


@dataclass(frozen=True)
class JournalStatistics:
    """Estadísticas agregadas simples del diario (conteos y promedios, no patrones)."""

    total_trades: int
    trades_with_result: int
    win_rate: Optional[float]
    avg_profit_loss_percent: Optional[float]


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

    def get_statistics(self) -> JournalStatistics:
        """Conteos y promedios simples. No es análisis de patrones (ver DecisionJournal)."""
        total = self._connection.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]
        row = self._connection.execute(
            """
            SELECT
                COUNT(*) AS with_result,
                AVG(CASE WHEN profit_loss_percent > 0 THEN 1.0 ELSE 0.0 END) AS win_rate,
                AVG(profit_loss_percent) AS avg_pl
            FROM trades WHERE profit_loss_percent IS NOT NULL
            """
        ).fetchone()

        return JournalStatistics(
            total_trades=int(total),
            trades_with_result=int(row["with_result"] or 0),
            win_rate=row["win_rate"],
            avg_profit_loss_percent=row["avg_pl"],
        )

    def close(self) -> None:
        self._connection.close()


class DecisionJournal:
    """Fachada del Decision Journal. Registra operaciones ya; analiza patrones del
    operador todavía no (ver los métodos que lanzan NotImplementedError abajo)."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.trades = DecisionJournalStore(db_path)

    def record_trade(self, trade: Trade) -> int:
        """Registra una operación del operador."""
        return self.trades.record_trade(trade)

    def get_trades(self, **kwargs) -> List[Trade]:
        """Consulta operaciones registradas (ver DecisionJournalStore.get_trades)."""
        return self.trades.get_trades(**kwargs)

    def get_statistics(self) -> JournalStatistics:
        """Conteos y promedios simples del diario."""
        return self.trades.get_statistics()

    # --- Análisis futuro del comportamiento del operador (todavía no implementado) ---

    def find_best_time_windows(self) -> list:
        """Horarios donde el operador obtiene mejores resultados."""
        raise NotImplementedError("Decision Journal: análisis de horarios todavía no implementado.")

    def find_best_asset_types(self) -> list:
        """Tipos de acciones/sectores donde el operador tiene mayor porcentaje de éxito."""
        raise NotImplementedError("Decision Journal: análisis de tipos de activo todavía no implementado.")

    def detect_recurring_errors(self) -> list:
        """Errores repetitivos del operador (patrones asociados a pérdidas)."""
        raise NotImplementedError("Decision Journal: detección de errores repetitivos todavía no implementada.")

    def detect_early_exits(self) -> list:
        """Ventas demasiado tempranas: casos donde el precio siguió subiendo después de vender."""
        raise NotImplementedError("Decision Journal: detección de ventas tempranas todavía no implementada.")

    def find_ignored_recommendations(self) -> list:
        """Operaciones donde el operador ignoró la recomendación de Atlas (Decision Engine)."""
        raise NotImplementedError(
            "Decision Journal: detección de recomendaciones ignoradas todavía no implementada."
        )

    def close(self) -> None:
        self.trades.close()
