"""Configuración centralizada de Atlas.

Hoy solo resuelve un punto: dónde vive el directorio de datos persistentes
(las bases SQLite de knowledge/journal/calibration). Centralizado acá para
que un Volume de Railway (o, más adelante, una URL de Postgres) sea un
cambio de una sola variable de entorno, no una búsqueda por todo el código.

Cada store (EventStore, DecisionJournal, CalibrationManager) sigue siendo
responsable de su propio archivo/tabla dentro de este directorio -- este
módulo no abre conexiones ni sabe nada de SQL, solo resuelve la ruta base.
"""

import os
from pathlib import Path

# Si ATLAS_DATA_DIR no está seteada (desarrollo local), se mantiene el
# comportamiento de siempre: atlas/cache/ dentro del repo.
_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "cache"


def data_dir() -> Path:
    """Directorio donde Atlas persiste su conocimiento (SQLite hoy).

    En Railway, ATLAS_DATA_DIR debe apuntar al mount path de un Volume
    persistente (ej. `/app/atlas/cache`) para que sobreviva a los deploys.
    Sin esa variable, cae al directorio local de siempre.
    """
    raw = os.environ.get("ATLAS_DATA_DIR")
    path = Path(raw) if raw else _DEFAULT_DATA_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path
