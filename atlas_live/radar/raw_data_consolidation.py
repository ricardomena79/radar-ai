"""Análisis por bloque `(ticker, market_date)` de datos crudos de alta
frecuencia (2026-09-02, Hito 2, autorizado explícitamente) -- SOLO
LECTURA sobre `candidate_registry.DB_PATH`/`shadow_detector_registry.DB_PATH`
(nunca los importa como módulos de lógica, solo lee su `DB_PATH` y su
schema conocido -- mismo patrón de aislamiento ya usado en
`u3c3_exclusive_diagnostics.py`; nunca modifica `candidate_gates.py`,
`candidate_tracker.py`, `candidate_registry.py` ni `shadow_detector_registry.py`).

UNA sola consulta agregada (`COUNT`/`MIN`/`MAX`/`SUM`) por bloque, usando
el índice `(ticker, market_date)` que YA existe en ambas tablas
(`idx_obs_ticker_date` / `idx_shadow_ticker_date`) -- NUNCA una agrupación
global sobre toda la tabla, NUNCA ordena, NUNCA carga una fila individual
a Python. Conexión
en modo read-only REAL de SQLite (`mode=ro` + `PRAGMA query_only=ON`,
mismo mecanismo ya verificado empíricamente en `u3c3_exclusive_diagnostics.py`
que bloquea escrituras a nivel del motor)."""

import hashlib
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from atlas_live.radar import candidate_registry as reg
from atlas_live.radar import shadow_detector_registry as sreg

METHODOLOGY_VERSION = "v1_count_sum_minmax"

# Config por tabla -- nombres de columna FIJOS, nunca construidos a partir
# de un valor externo (mismo criterio que `_RADAR_CANDIDATES_TABLE_NAMES`
# en `data_dir_diagnostics.py`).
_TABLE_CONFIG: Dict[str, Dict[str, Any]] = {
    "candidate_observation": {
        "db_path_getter": lambda: reg.DB_PATH,
        "timestamp_col": "observed_at",
        "price_col": "price",
        "volume_col": "volume",
    },
    "shadow_candidate_detection": {
        "db_path_getter": lambda: sreg.DB_PATH,
        "timestamp_col": "detected_at",
        "price_col": "price",
        "volume_col": "volume",
    },
}


def _ro_connect(path: Path) -> sqlite3.Connection:
    """Conexión read-only REAL de SQLite -- mismo mecanismo que
    `u3c3_exclusive_diagnostics.py::_ro_connect()`."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def analyze_block(source_table: str, ticker: str, market_date: str) -> Optional[Dict[str, Any]]:
    """UNA consulta agregada para `(ticker, market_date)` -- usa el índice
    ya existente, nunca ordena, nunca agrupa más de un bloque a la vez.
    `ticker`/`market_date` viajan SIEMPRE como parámetros ligados
    (`?`), nunca interpolados en el SQL -- sin riesgo de inyección sin
    importar qué valor se reciba. `source_table` se valida contra
    `_TABLE_CONFIG` (allowlist fija) antes de usarse en el nombre de la
    tabla/columnas del `SELECT`.

    `None` si el bloque no tiene ninguna fila -- nunca se inventa un
    resumen vacío."""
    if source_table not in _TABLE_CONFIG:
        raise ValueError(f"source_table inválida: {source_table!r}. Debe ser una de {tuple(_TABLE_CONFIG)}")
    cfg = _TABLE_CONFIG[source_table]
    db_path = cfg["db_path_getter"]()
    ts_col, price_col, vol_col = cfg["timestamp_col"], cfg["price_col"], cfg["volume_col"]

    with _ro_connect(db_path) as conn:
        row = conn.execute(
            f"""SELECT COUNT(*) AS n, MIN({ts_col}) AS min_ts, MAX({ts_col}) AS max_ts,
                       MAX({price_col}) AS max_price, SUM({vol_col}) AS sum_volume
                FROM {source_table} WHERE ticker=? AND market_date=?""",  # nosec: source_table/columnas de allowlist fija
            (ticker, market_date),
        ).fetchone()

    if row is None or row["n"] == 0:
        return None

    summary = {
        "n_observaciones": row["n"],
        "max_price_visto": row["max_price"],
        "sum_volume": row["sum_volume"],
        "primer_timestamp": row["min_ts"],
        "ultimo_timestamp": row["max_ts"],
    }
    checksum = _compute_checksum(source_table, ticker, market_date, row)

    return {
        "source_table": source_table,
        "block_key": f"{ticker}|{market_date}",
        "block_granularity": "ticker_market_date",
        "row_count_covered": row["n"],
        "min_timestamp_covered": row["min_ts"],
        "max_timestamp_covered": row["max_ts"],
        "summary": summary,
        "raw_data_checksum": checksum,
        "methodology_version": METHODOLOGY_VERSION,
    }


def _compute_checksum(source_table: str, ticker: str, market_date: str, row: sqlite3.Row) -> str:
    """Fingerprint determinista sobre el AGREGADO ya calculado (nunca
    sobre las filas completas -- inviable para 12M+ filas). Cambia si el
    bloque crudo cambia (más filas, distinto rango de timestamps, etc.);
    no requiere ninguna lectura adicional más allá de la misma fila
    agregada de `analyze_block()`."""
    payload = (
        f"{source_table}|{ticker}|{market_date}|{row['n']}|{row['min_ts']}|"
        f"{row['max_ts']}|{row['max_price']}|{row['sum_volume']}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
