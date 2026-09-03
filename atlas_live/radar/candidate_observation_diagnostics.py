"""Diagnóstico read-only de `candidate_observation`/`radar_candidates.db`
(2026-09-03, auditoría de espacio previa a una eventual compactación,
autorizado explícitamente). PURAMENTE DE LECTURA -- conexión `mode=ro` +
`PRAGMA query_only=ON` (mismo mecanismo ya verificado empíricamente en
`raw_data_consolidation.py`/`u3c3_exclusive_diagnostics.py` que bloquea
escrituras a nivel del motor SQLite). NUNCA `CREATE`/`INSERT`/`UPDATE`/
`DELETE`/`VACUUM`/checkpoint -- ni una sola sentencia de escritura en todo
este módulo. No modifica `candidate_registry.py`, no le importa nada de
lógica, solo lee su `DB_PATH` (un atributo, nunca una función).

Objetivo único: reunir la evidencia que ningún endpoint existente expone
(`page_size`/`page_count`/`freelist_count`/`auto_vacuum`/`journal_mode`,
tamaño físico exacto, conteo de `candidate_observation`, distribución de
filas por bloque `(ticker, market_date)`) para poder calcular, con datos
reales y no supuestos, cuánto Volume hace falta antes de compactar nada.
Este módulo NUNCA borra, compacta ni autoriza compactación -- eso sigue
sin implementarse, a la espera de una autorización futura y separada.

Seguridad frente a picos de memoria/espacio temporal (pedido explícito):
la distribución por bloque se calcula sobre el AGREGADO por bloque
(`GROUP BY ticker, market_date`, unos pocos miles de filas esperadas, no
las 12M crudas) -- el `EXPLAIN QUERY PLAN` de esa consulta se incluye TAL
CUAL en el resultado como evidencia real de si SQLite usa el índice
existente `idx_obs_ticker_date(ticker, market_date)` (que coincide
exactamente con el orden del GROUP BY) en vez de asumirlo. Un tope
defensivo (`MAX_BLOCK_DISTRIBUTION_ROWS`) corta el cálculo de percentiles
si el número real de bloques resultara muchísimo mayor al esperado, en
vez de arriesgarse a cargar una cantidad sin límite en memoria de Python."""

import sqlite3
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas_live.radar import candidate_registry as reg

# Tope defensivo -- con los volúmenes actuales (miles de bloques, no
# millones) nunca debería alcanzarse; existe para no cargar una cantidad
# sin límite de agregados a memoria de Python si el patrón de datos
# cambiara radicalmente en el futuro.
MAX_BLOCK_DISTRIBUTION_ROWS = 200_000


def _resolve_path(path: Optional[Path]) -> Path:
    """`reg.DB_PATH` se lee AQUÍ, en cada llamada -- nunca como valor por
    defecto de un parámetro (eso lo capturaría una sola vez, al definir la
    función, e ignoraría cualquier reasignación posterior de
    `candidate_registry.DB_PATH` -- el mismo patrón de aislamiento que
    usan los tests de todo este proyecto)."""
    return Path(path) if path is not None else reg.DB_PATH


def _ro_connect(path: Path) -> sqlite3.Connection:
    """Conexión read-only REAL de SQLite -- mismo mecanismo ya verificado
    empíricamente en `raw_data_consolidation.py::_ro_connect()`. Lanza
    `OperationalError: unable to open database file` si el archivo no
    existe -- por eso cada función pública de este módulo comprueba
    `Path(path).exists()` antes de llamar a esta."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def pragma_diagnostics(path: Optional[Path] = None) -> Dict[str, Any]:
    """`page_size`/`page_count`/`freelist_count`/`auto_vacuum`/
    `journal_mode` -- las 5 consultas pedidas explícitamente, todas
    lecturas puras de PRAGMA, ninguna modifica nada."""
    path = _resolve_path(path)
    if not path.exists():
        return {"error": "archivo no existe", "path": str(path)}
    with _ro_connect(path) as conn:
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        freelist_count = conn.execute("PRAGMA freelist_count").fetchone()[0]
        auto_vacuum = conn.execute("PRAGMA auto_vacuum").fetchone()[0]
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    return {
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "auto_vacuum": auto_vacuum,
        "journal_mode": journal_mode,
        # derivados, puro cálculo aritmético sobre lo ya leído -- nunca una consulta nueva.
        "estimated_used_bytes": page_size * (page_count - freelist_count) if page_size and page_count is not None else None,
        "estimated_free_within_file_bytes": page_size * freelist_count if page_size and freelist_count is not None else None,
    }


def file_sizes(path: Optional[Path] = None) -> Dict[str, Any]:
    """Tamaño físico exacto del `.db` principal + sus compañeros
    `-wal`/`-shm` si existen -- `Path.stat()`, ni una sola consulta SQL,
    no requiere ni siquiera abrir SQLite."""
    p = _resolve_path(path)
    wal = Path(str(p) + "-wal")
    shm = Path(str(p) + "-shm")
    return {
        "db_bytes": p.stat().st_size if p.exists() else None,
        "wal_bytes": wal.stat().st_size if wal.exists() else None,
        "shm_bytes": shm.stat().st_size if shm.exists() else None,
    }


def candidate_observation_total_rows(path: Optional[Path] = None) -> Optional[int]:
    """`COUNT(*)` puro -- SQLite lo resuelve en streaming (nunca carga
    filas a Python), seguro incluso sobre 12M+ filas."""
    path = _resolve_path(path)
    if not path.exists():
        return None
    with _ro_connect(path) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM candidate_observation").fetchone()
    return row["n"] if row else None


def _percentile(sorted_values: List[int], p: float) -> Optional[float]:
    if not sorted_values:
        return None
    k = (len(sorted_values) - 1) * p
    f, c = int(k), min(int(k) + 1, len(sorted_values) - 1)
    if f == c:
        return float(sorted_values[f])
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def block_distribution(path: Optional[Path] = None) -> Dict[str, Any]:
    """Distribución de filas por bloque `(ticker, market_date)`. La
    consulta agregada (`GROUP BY ticker, market_date`) coincide EXACTAMENTE
    con el orden del índice ya existente `idx_obs_ticker_date` -- se
    incluye el `EXPLAIN QUERY PLAN` real en el resultado como evidencia de
    si SQLite lo está usando, nunca se asume. Solo se traen a Python los
    AGREGADOS por bloque (miles esperados), nunca las filas crudas."""
    path = _resolve_path(path)
    if not path.exists():
        return {"error": "archivo no existe", "path": str(path)}
    with _ro_connect(path) as conn:
        plan = [dict(r) for r in conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT ticker, market_date, COUNT(*) FROM candidate_observation GROUP BY ticker, market_date"
        ).fetchall()]
        rows = conn.execute(
            "SELECT ticker, market_date, COUNT(*) AS n FROM candidate_observation GROUP BY ticker, market_date"
        ).fetchall()

    n_bloques = len(rows)
    plan_texto = " | ".join(str(p.get("detail", p)) for p in plan)
    usa_indice = "idx_obs_ticker_date" in plan_texto

    if n_bloques > MAX_BLOCK_DISTRIBUTION_ROWS:
        return {
            "query_plan": plan,
            "query_plan_usa_indice_existente": usa_indice,
            "n_bloques": n_bloques,
            "error": (
                f"más de {MAX_BLOCK_DISTRIBUTION_ROWS} bloques -- no se calculan "
                "percentiles/top-N en este request para no arriesgar memoria; "
                "requeriría paginar."
            ),
        }

    counts = [r["n"] for r in rows]
    counts_sorted = sorted(counts)
    top20 = sorted(rows, key=lambda r: r["n"], reverse=True)[:20]
    bottom20 = sorted(rows, key=lambda r: r["n"])[:20]

    return {
        "query_plan": plan,
        "query_plan_usa_indice_existente": usa_indice,
        "n_bloques": n_bloques,
        "min": min(counts) if counts else None,
        "max": max(counts) if counts else None,
        "mediana": statistics.median(counts) if counts else None,
        "percentiles": {
            "p10": _percentile(counts_sorted, 0.10),
            "p25": _percentile(counts_sorted, 0.25),
            "p50": _percentile(counts_sorted, 0.50),
            "p75": _percentile(counts_sorted, 0.75),
            "p90": _percentile(counts_sorted, 0.90),
            "p95": _percentile(counts_sorted, 0.95),
            "p99": _percentile(counts_sorted, 0.99),
        },
        "top_20_bloques_mas_grandes": [
            {"ticker": r["ticker"], "market_date": r["market_date"], "n_observaciones": r["n"]} for r in top20
        ],
        "bottom_20_bloques_mas_chicos": [
            {"ticker": r["ticker"], "market_date": r["market_date"], "n_observaciones": r["n"]} for r in bottom20
        ],
    }


def full_report() -> Dict[str, Any]:
    """Orquesta todo lo de arriba -- aislado por diseño (mismo patrón que
    `u3c3_exclusive_diagnostics.py`/`raw_data_consolidation_pipeline.py`):
    cualquier excepción queda atrapada, el llamador siempre recibe un
    dict, nunca una excepción sin manejar."""
    resultado: Dict[str, Any] = {"ok": False, "error": None}
    try:
        resultado["pragma"] = pragma_diagnostics()
        resultado["file_sizes"] = file_sizes()
        resultado["candidate_observation_total_rows"] = candidate_observation_total_rows()
        resultado["block_distribution"] = block_distribution()
        resultado["ok"] = True
    except Exception as exc:  # este diagnóstico NUNCA puede tumbar al llamador
        resultado["error"] = f"{type(exc).__name__}: {exc}"
        resultado["ok"] = False
    return resultado
