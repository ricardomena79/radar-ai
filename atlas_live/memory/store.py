"""Memory Store -- Entregable Nº1 del Memory Engine.

Almacenamiento persistente, append-only, de UNA observación por símbolo/día
(o símbolo/día/checkpoint, cuando existan checkpoints intermedios -- ver
Entregable 8 de MEMORY_ENGINE.md). No clasifica nada por sí mismo (eso es
el Clasificador de Resultado, Entregable 2) -- este módulo solo sabe
guardar y consultar, igual que `timeline.py` de Mission Control frente a
`heartbeat.py`.

SQLite en modo WAL, mismo patrón que `atlas/knowledge/event_store.py` y
`atlas_live/mission_control/timeline.py` -- reutiliza un patrón ya probado
en el proyecto en vez de introducir una herramienta nueva.

Nunca se sobrescribe ni se borra una observación ya guardada -- mismo
principio de Atlas Core, Capa 3 ("nunca se borra conocimiento").

Columna `market_context`: reservada por decisión de diseño explícita
(MEMORY_ENGINE.md, "CONSIDERACIÓN DE DISEÑO AGREGADA", 2026-08-02) para
información de contexto general del mercado a futuro (ej. condición de
SPY/QQQ/VIX, sector líder del día). Hoy no se usa -- se acepta y se
persiste si se provee, pero ningún entregable actual la llena.
"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from atlas.config.config import data_dir

DB_PATH = data_dir() / "memory_store.db"

# Las 5 categorías de resultado del Clasificador (Entregable 2). Se validan
# acá también porque el Memory Store es la última línea de defensa contra
# una categoría mal escrita -- no debería confiar ciegamente en el llamador.
CATEGORIES = {"EXPLOSION", "NORMAL", "WEAK", "LOSER", "FALSE_BREAKOUT"}

# Métricas crudas que Radar Explosivo ya calcula -- mismas claves que
# `explosive.metrics` en los archivos de `atlas_live/backtest/results_*/`.
METRIC_FIELDS = (
    "price",
    "gap_pct",
    "change_pct",
    "relative_volume",
    "dollar_volume",
    "volatility_score",
    "market_cap",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    checkpoint_minutes INTEGER NOT NULL,
    category TEXT NOT NULL,
    price REAL,
    gap_pct REAL,
    change_pct REAL,
    relative_volume REAL,
    dollar_volume REAL,
    volatility_score REAL,
    market_cap REAL,
    sector TEXT,
    industry TEXT,
    market_cap_bucket TEXT,
    session TEXT,
    source_version TEXT,
    market_context TEXT,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observations_symbol ON observations(symbol);
CREATE INDEX IF NOT EXISTS idx_observations_date ON observations(date);
CREATE INDEX IF NOT EXISTS idx_observations_category ON observations(category);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def record_observation(
    symbol: str,
    date: str,
    checkpoint_minutes: int,
    category: str,
    metrics: Dict[str, Any],
    sector: Optional[str] = None,
    industry: Optional[str] = None,
    market_cap_bucket: Optional[str] = None,
    session: Optional[str] = None,
    source_version: Optional[str] = None,
    market_context: Optional[Dict[str, Any]] = None,
) -> None:
    """Agrega una observación al Memory Store. Nunca se edita una fila ya
    escrita -- solo se agregan filas nuevas (append-only)."""
    if category not in CATEGORIES:
        raise ValueError(f"category inválida: {category!r}. Debe ser una de {sorted(CATEGORIES)}")

    values = {field: metrics.get(field) for field in METRIC_FIELDS}

    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO observations ("
            "symbol, date, checkpoint_minutes, category, "
            "price, gap_pct, change_pct, relative_volume, dollar_volume, volatility_score, market_cap, "
            "sector, industry, market_cap_bucket, session, source_version, market_context, recorded_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                symbol, date, checkpoint_minutes, category,
                values["price"], values["gap_pct"], values["change_pct"],
                values["relative_volume"], values["dollar_volume"],
                values["volatility_score"], values["market_cap"],
                sector, industry, market_cap_bucket, session, source_version,
                json.dumps(market_context, ensure_ascii=False) if market_context is not None else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()


def bulk_insert_observations(rows: Iterable[Dict[str, Any]]) -> int:
    """Inserta muchas observaciones en una sola conexión/transacción --
    usado exclusivamente por el bootstrap del seed (ver seed.py) para
    importar decenas de miles de filas sin que cada una pague el costo de
    abrir y cerrar SQLite por separado, como sí hace `record_observation()`
    (pensada para un símbolo a la vez, en vivo).

    Cada `row` debe traer las mismas claves que `METRIC_FIELDS` más
    `symbol`, `date`, `checkpoint_minutes`, `category`, `sector`,
    `industry`, `market_cap_bucket`, `session`, `source_version` --
    exactamente lo que produce `seed._read_seed_rows()`. `market_context`
    no es un parámetro acá: el seed nunca lo trae (ver seed.py), así que
    todas las filas importadas quedan con `market_context = NULL`.

    No es responsabilidad de esta función decidir SI debe importar --
    eso es de `seed.ensure_seeded()` (el único lugar que garantiza no
    duplicar). Esta función simplemente inserta lo que se le pase."""
    now = datetime.now(timezone.utc).isoformat()
    values = []
    for row in rows:
        category = row["category"]
        if category not in CATEGORIES:
            raise ValueError(f"category inválida: {category!r}. Debe ser una de {sorted(CATEGORIES)}")
        values.append((
            row["symbol"], row["date"], row["checkpoint_minutes"], category,
            row.get("price"), row.get("gap_pct"), row.get("change_pct"),
            row.get("relative_volume"), row.get("dollar_volume"),
            row.get("volatility_score"), row.get("market_cap"),
            row.get("sector"), row.get("industry"), row.get("market_cap_bucket"),
            row.get("session"), row.get("source_version"),
            None,  # market_context: el seed nunca lo trae
            now,
        ))

    if not values:
        return 0

    with closing(_connect()) as conn:
        conn.executemany(
            "INSERT INTO observations ("
            "symbol, date, checkpoint_minutes, category, "
            "price, gap_pct, change_pct, relative_volume, dollar_volume, volatility_score, market_cap, "
            "sector, industry, market_cap_bucket, session, source_version, market_context, recorded_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        conn.commit()
    return len(values)


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["market_context"] = json.loads(d["market_context"]) if d["market_context"] else None
    return d


def get_observations(
    symbol: Optional[str] = None,
    date: Optional[str] = None,
    category: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Consulta observaciones, con filtros opcionales combinables. Orden
    cronológico por fecha de mercado, luego por orden de inserción."""
    query = "SELECT * FROM observations WHERE 1=1"
    params: List[Any] = []
    if symbol is not None:
        query += " AND symbol = ?"
        params.append(symbol)
    if date is not None:
        query += " AND date = ?"
        params.append(date)
    if category is not None:
        if category not in CATEGORIES:
            raise ValueError(f"category inválida: {category!r}. Debe ser una de {sorted(CATEGORIES)}")
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY date ASC, id ASC"

    with closing(_connect()) as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_observations() -> int:
    with closing(_connect()) as conn:
        return conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
