"""Seed del Memory Store -- snapshot versionado en Git de las observaciones
de backtest, para que Memory Engine sobreviva a un clon nuevo, un deploy o
un cambio de máquina sin versionar la base SQLite viva (2026-08-05).

Flujo:

    Seed (Git)
         |
         v
    Bootstrap automático (ensure_seeded(), llamado al arrancar Atlas Live
    en atlas_live/server.py, a nivel de módulo -- corre tanto en
    `python -m atlas_live.server` como bajo gunicorn en Railway)
         |
         v
    SQLite local (atlas/cache/memory_store.db -- o el Volume de Railway
    si ATLAS_DATA_DIR apunta ahí, ver atlas/config/config.py)
         |
         v
    Railway Volume (persiste ese SQLite entre deploys)
         |
         v
    Nuevas observaciones (una vez que atlas_live/memory/live_integration.py
    esté enganchado al ciclo de scan_worker.py -- todavía no implementado;
    hoy el Volume solo preserva lo que vino del seed)

El seed es de solo lectura desde la perspectiva de Atlas en producción:
nunca se regenera ni se sobrescribe automáticamente. Se vuelve a exportar
y re-comitear a mano solo si en el futuro se corre un backtest nuevo
mucho más grande que este -- una decisión deliberada (ver `export_seed()`),
no un efecto secundario de correr la app.

Idempotencia y no-sobrescritura -- ambas garantizadas por el mismo guard,
a propósito, para no necesitar un marcador aparte que se pueda
desincronizar del dato real: `ensure_seeded()` solo importa si
`store.count_observations() == 0`. Si el SQLite ya tiene aunque sea una
fila -- de un seed importado en un arranque anterior, o de observaciones
reales ya escritas en producción por `live_integration.py` el día que
exista -- el seed nunca se toca. Llamar `ensure_seeded()` una vez, cien
veces, o en cada arranque del proceso, tiene siempre el mismo resultado.
"""

import csv
import gzip
from pathlib import Path
from typing import Any, Dict, Iterator

from loguru import logger

from atlas_live.memory import store

SEED_PATH = Path(__file__).parent / "seed" / "observations_seed.csv.gz"

# Mismo orden que las columnas relevantes de `observations`. Excluye:
#   - `id`: autoincremental, no tiene sentido preservarlo entre entornos.
#   - `recorded_at`: se regenera al importar, con la hora real de
#     importación -- no la de la exportación original.
#   - `market_context`: siempre NULL en las observaciones actuales
#     (verificado antes de esta implementación, ver DECISIONES.md); si
#     algún backfill futuro la llena, agregar la columna acá también.
SEED_COLUMNS = (
    "symbol", "date", "checkpoint_minutes", "category",
    "price", "gap_pct", "change_pct", "relative_volume",
    "dollar_volume", "volatility_score", "market_cap",
    "sector", "industry", "market_cap_bucket", "session", "source_version",
)

_NUMERIC_COLUMNS = (
    "checkpoint_minutes", "price", "gap_pct", "change_pct",
    "relative_volume", "dollar_volume", "volatility_score", "market_cap",
)
_INT_COLUMNS = ("checkpoint_minutes",)
_TEXT_COLUMNS = ("sector", "industry", "market_cap_bucket", "session", "source_version")


def export_seed(path: Path = SEED_PATH) -> int:
    """Exporta TODAS las observaciones actuales del Memory Store a un CSV
    comprimido -- acción manual y deliberada (ej. después de correr un
    backtest grande nuevo), nunca se llama automáticamente desde la app en
    marcha. Sobrescribe el seed existente en `path`. Devuelve la cantidad
    de filas exportadas."""
    observations = store.get_observations()
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(SEED_COLUMNS)
        for obs in observations:
            writer.writerow([obs.get(col) for col in SEED_COLUMNS])
    return len(observations)


def _read_seed_rows(path: Path) -> Iterator[Dict[str, Any]]:
    with gzip.open(path, "rt", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            row: Dict[str, Any] = dict(raw)
            for col in _NUMERIC_COLUMNS:
                value = row.get(col)
                row[col] = float(value) if value not in (None, "") else None
            for col in _INT_COLUMNS:
                if row.get(col) is not None:
                    row[col] = int(row[col])
            for col in _TEXT_COLUMNS:
                if row.get(col) == "":
                    row[col] = None
            yield row


def import_seed(path: Path = SEED_PATH) -> int:
    """Importa el seed al Memory Store local, sin ninguna verificación de
    si ya hay datos -- esa verificación es responsabilidad exclusiva de
    `ensure_seeded()`. Existe como función separada para poder probarla
    de forma aislada. Devuelve la cantidad de filas importadas."""
    if not path.exists():
        raise FileNotFoundError(f"No existe el seed en {path}")
    rows = list(_read_seed_rows(path))
    return store.bulk_insert_observations(rows)


def ensure_seeded(path: Path = SEED_PATH) -> bool:
    """Bootstrap automático, pensado para llamarse una vez al arrancar
    Atlas Live (ver atlas_live/server.py). Si el Memory Store local ya
    tiene una observación (aunque sea una sola), no hace nada -- ni
    siquiera abre el archivo del seed. Si está vacío y el seed existe, lo
    importa completo. Devuelve True si importó, False si no hizo falta o
    no había seed disponible."""
    if store.count_observations() > 0:
        return False
    if not path.exists():
        logger.warning(f"Memory Engine sin observaciones y sin seed en {path} -- arranca vacío.")
        return False
    imported = import_seed(path)
    logger.info(f"Memory Engine: {imported} observaciones importadas automáticamente desde el seed ({path.name}).")
    return True
