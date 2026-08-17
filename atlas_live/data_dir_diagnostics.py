"""Diagnostico de persistencia del Volume de Railway (2026-08-17).

Prueba minima y definitiva de que ATLAS_DATA_DIR realmente persiste entre
redeploys: un marcador se escribe UNA SOLA VEZ en el directorio de datos y
nunca se sobrescribe -- si despues de un redeploy real el mismo
`marker_id` sigue ahi, el Volume persiste de verdad. No alcanza con que
"exista un archivo con el mismo nombre": un contenedor nuevo sin Volume
real tambien podria recrearlo con contenido distinto -- por eso
`write_marker_once()` jamas reescribe uno ya presente.

Solo lee/escribe este marcador y reporta metadatos (tamano, fecha de
modificacion) de `historical_reference.db` -- no toca ninguna fila de
datos existente, no abre esa base para escritura.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from atlas.config.config import data_dir
from atlas_live.reference.reference_registry import DB_PATH as HISTORICAL_REFERENCE_DB_PATH

MARKER_FILENAME = "persistence_marker.json"


def _data_dir_path() -> Path:
    return data_dir(Path(__file__).parent)


def _marker_path() -> Path:
    return _data_dir_path() / MARKER_FILENAME


def write_marker_once() -> Dict[str, Any]:
    """Crea el marcador SOLO si todavia no existe -- nunca lo pisa, para
    que un redeploy real se pueda confirmar comparando el mismo
    `marker_id` antes y despues, no solo la presencia del archivo."""
    path = _marker_path()
    if path.exists():
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return {"created": False, "already_existed": True,
                     "error": f"marcador existe pero no se pudo leer: {type(exc).__name__}: {exc}"}
        return {"created": False, "already_existed": True, "marker": content}

    content = {
        "marker_id": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    return {"created": True, "already_existed": False, "marker": content}


def diagnostics() -> Dict[str, Any]:
    """Todo lo necesario para confirmar, desde afuera, donde esta
    escribiendo realmente este proceso -- sin adivinar nada por tipo de
    montaje (ver decision del usuario, 2026-08-17): la unica prueba
    aceptada es marcador persistente + redeploy, no `st_dev` ni heuristicas
    de filesystem."""
    raw_env = os.environ.get("ATLAS_DATA_DIR")
    dd = _data_dir_path()
    marker_path = _marker_path()

    marker_info: Dict[str, Any] = {"path": str(marker_path), "exists": marker_path.exists()}
    if marker_info["exists"]:
        try:
            marker_info["content"] = json.loads(marker_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            marker_info["read_error"] = f"{type(exc).__name__}: {exc}"

    db_path = HISTORICAL_REFERENCE_DB_PATH
    db_info: Dict[str, Any] = {"path": str(db_path), "exists": db_path.exists()}
    if db_info["exists"]:
        stat = db_path.stat()
        db_info["size_bytes"] = stat.st_size
        db_info["modified_at"] = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

    return {
        "atlas_data_dir_env_raw": raw_env,
        "atlas_data_dir_resolved": str(dd.resolve()),
        "atlas_data_dir_exists": dd.exists(),
        "atlas_data_dir_writable": os.access(dd, os.W_OK),
        "historical_reference_db": db_info,
        "persistence_marker": marker_info,
    }
