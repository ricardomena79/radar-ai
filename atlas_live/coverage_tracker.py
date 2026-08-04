"""Cobertura rotativa de Atlas Live: cuándo se analizó cada símbolo por última vez.

No es Knowledge Base (conocimiento del mercado) ni Decision Journal
(conocimiento del operador): es un registro puramente operativo, propio
de Atlas Live, para que ningún símbolo del universo quede permanentemente
afuera de la Etapa 2 solo por no haber tenido actividad "espectacular" ese
día en particular. Vive en su propio archivo SQLite, separado de
atlas_knowledge.db -- Decision Recorder sigue siendo el único escritor de
la Knowledge Base; esto no compite con ese rol, es un dato completamente
distinto.

Dos usos:
  1. REQUIRED_SYMBOLS como red de seguridad: un símbolo "requerido" solo
     se fuerza a entrar a la Etapa 2 si no fue seleccionado naturalmente
     Y hace tiempo que no se analiza -- deja de ser una ventaja permanente.
  2. Cobertura rotativa general: si sobra lugar en la Etapa 2 después de
     los candidatos de GlobalRadar, se completa con los símbolos del
     universo que hace más tiempo no se analizan.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "atlas" / "cache" / "symbol_coverage.db"


class CoverageTracker:
    """Registra la última vez que cada símbolo pasó por la Etapa 2 (Atlas Core)."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(path))
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def _create_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS symbol_coverage (
                symbol TEXT PRIMARY KEY,
                last_analyzed_at TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def mark_analyzed(self, symbols: Iterable[str], when: Optional[str] = None) -> None:
        """Registra que estos símbolos acaban de pasar por la Etapa 2."""
        timestamp = when or datetime.now(timezone.utc).isoformat()
        rows = [(symbol, timestamp) for symbol in symbols]
        if not rows:
            return
        self._connection.executemany(
            """
            INSERT INTO symbol_coverage (symbol, last_analyzed_at) VALUES (?, ?)
            ON CONFLICT(symbol) DO UPDATE SET last_analyzed_at = excluded.last_analyzed_at
            """,
            rows,
        )
        self._connection.commit()

    def last_analyzed_at(self, symbol: str) -> Optional[str]:
        """ISO timestamp de la última vez que se analizó, o None si nunca."""
        row = self._connection.execute(
            "SELECT last_analyzed_at FROM symbol_coverage WHERE symbol = ?", (symbol,)
        ).fetchone()
        return row[0] if row else None

    def rank_by_staleness(self, symbols: List[str]) -> List[str]:
        """`symbols` ordenados del que hace más tiempo no se analiza (o nunca) al más reciente."""
        if not symbols:
            return []
        placeholders = ",".join("?" * len(symbols))
        rows = self._connection.execute(
            f"SELECT symbol, last_analyzed_at FROM symbol_coverage WHERE symbol IN ({placeholders})",
            symbols,
        ).fetchall()
        known = {row[0]: row[1] for row in rows}
        # Un símbolo sin fila = nunca analizado; "" ordena antes que cualquier timestamp ISO real.
        return sorted(symbols, key=lambda s: known.get(s, ""))

    def close(self) -> None:
        self._connection.close()
