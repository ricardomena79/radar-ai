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
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.config.config import db_path

DB_PATH = db_path("memory_store.db", default=Path(__file__).parent)

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
-- Idempotencia (2026-08-09): una única observación por símbolo/día/checkpoint.
-- Es la clave natural del Memory Store (una observación por símbolo/día, o
-- símbolo/día/checkpoint cuando hay checkpoints intermedios). El seed
-- histórico ya cumple esta unicidad (verificado: 73.123 filas = 73.123
-- tripletas distintas), así que el índice se crea sin conflicto sobre la
-- base existente. Con esto, un retry del servidor o un reinicio de Railway
-- que reintente el mismo cierre NO puede duplicar una observación
-- (`record_observation` usa INSERT OR IGNORE).
CREATE UNIQUE INDEX IF NOT EXISTS idx_observations_unique
    ON observations(symbol, date, checkpoint_minutes);
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
) -> bool:
    """Agrega una observación al Memory Store. Nunca se edita una fila ya
    escrita -- solo se agregan filas nuevas (append-only).

    Idempotente (2026-08-09): usa `INSERT OR IGNORE` sobre la clave única
    (symbol, date, checkpoint_minutes). Si esa tripleta ya existe, NO
    inserta nada y devuelve `False`; si insertó una fila nueva devuelve
    `True`. Así, un reintento o un reinicio que reprocese el mismo cierre
    de trayectoria no duplica la observación. Los llamadores antiguos
    (`backfill.py`) ignoran el valor de retorno -- su comportamiento no
    cambia porque el seed ya es único en esa tripleta."""
    if category not in CATEGORIES:
        raise ValueError(f"category inválida: {category!r}. Debe ser una de {sorted(CATEGORIES)}")

    values = {field: metrics.get(field) for field in METRIC_FIELDS}

    with closing(_connect()) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO observations ("
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
        return cur.rowcount > 0


def import_observation_row(row: Dict[str, Any]) -> bool:
    """Reinserta una observación COMPLETA (tal como la devuelve
    `get_observations`) preservando su `recorded_at` original -- para
    recuperación desde JSONL tras una pérdida del Volume (F5). Idempotente:
    INSERT OR IGNORE sobre la clave única (symbol, date, checkpoint_minutes),
    así reimportar dos veces no duplica ni pisa una fila en vivo existente.
    Devuelve True solo si insertó una fila nueva. No inventa `recorded_at`:
    usa el del archivo (si falta, cae al momento actual)."""
    if row.get("category") not in CATEGORIES:
        raise ValueError(f"category inválida en fila a importar: {row.get('category')!r}")
    mc = row.get("market_context")
    with closing(_connect()) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO observations ("
            "symbol, date, checkpoint_minutes, category, "
            "price, gap_pct, change_pct, relative_volume, dollar_volume, volatility_score, market_cap, "
            "sector, industry, market_cap_bucket, session, source_version, market_context, recorded_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["symbol"], row["date"], row["checkpoint_minutes"], row["category"],
                row.get("price"), row.get("gap_pct"), row.get("change_pct"),
                row.get("relative_volume"), row.get("dollar_volume"),
                row.get("volatility_score"), row.get("market_cap"),
                row.get("sector"), row.get("industry"), row.get("market_cap_bucket"),
                row.get("session"), row.get("source_version"),
                json.dumps(mc, ensure_ascii=False) if isinstance(mc, (dict, list)) else mc,
                row.get("recorded_at") or datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return cur.rowcount > 0


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["market_context"] = json.loads(d["market_context"]) if d["market_context"] else None
    return d


def get_observations(
    symbol: Optional[str] = None,
    date: Optional[str] = None,
    category: Optional[str] = None,
    source_version: Optional[str] = None,
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
    if source_version is not None:
        query += " AND source_version = ?"
        params.append(source_version)
    query += " ORDER BY date ASC, id ASC"

    with closing(_connect()) as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_observations(source_version: Optional[str] = None, date: Optional[str] = None) -> int:
    """Cuenta observaciones, con filtros opcionales combinables. Sin filtros
    devuelve el total (comportamiento anterior, no rompe llamadores). Con
    `source_version="live"` cuenta solo las incorporadas en vivo; con
    `date` las de un día de mercado (para "observaciones nuevas hoy")."""
    query = "SELECT COUNT(*) FROM observations WHERE 1=1"
    params: List[Any] = []
    if source_version is not None:
        query += " AND source_version = ?"
        params.append(source_version)
    if date is not None:
        query += " AND date = ?"
        params.append(date)
    with closing(_connect()) as conn:
        return conn.execute(query, params).fetchone()[0]


def observation_exists(symbol: str, date: str, checkpoint_minutes: int) -> bool:
    """True si ya existe una observación para esa tripleta única -- permite
    al write-back saltear un cierre ya procesado sin depender de la
    excepción del INSERT."""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT 1 FROM observations WHERE symbol = ? AND date = ? AND checkpoint_minutes = ? LIMIT 1",
            (symbol, date, checkpoint_minutes),
        ).fetchone()
    return row is not None


def last_recorded_at(source_version: Optional[str] = None) -> Optional[str]:
    """`recorded_at` (ISO, hora real de incorporación) de la observación más
    reciente, o `None` si no hay ninguna. Con `source_version="live"` da la
    hora de la última observación NUEVA -- lo que la Cabina muestra como
    "última observación". No inventa un timestamp: si no hay filas, es None."""
    query = "SELECT MAX(recorded_at) FROM observations WHERE 1=1"
    params: List[Any] = []
    if source_version is not None:
        query += " AND source_version = ?"
        params.append(source_version)
    with closing(_connect()) as conn:
        return conn.execute(query, params).fetchone()[0]
