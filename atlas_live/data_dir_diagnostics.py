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
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from atlas.config.config import data_dir
from atlas_live.catalyst.catalyst_registry import DB_PATH as CATALYST_EVENTS_DB_PATH
from atlas_live.memory.prediction_journal import DB_PATH as PREDICTION_JOURNAL_DB_PATH
from atlas_live.memory.store import DB_PATH as MEMORY_STORE_DB_PATH
from atlas_live.radar.candidate_registry import DB_PATH as RADAR_CANDIDATES_DB_PATH
from atlas_live.reference.reference_registry import DB_PATH as HISTORICAL_REFERENCE_DB_PATH

MARKER_FILENAME = "persistence_marker.json"

# Los 5 archivos SQLite bajo diagnóstico (2026-09-01, ampliación autorizada
# explícitamente) -- se reutiliza el `DB_PATH` YA calculado por cada módulo
# (importado arriba, ya resuelto una sola vez al arrancar el proceso) en vez
# de volver a llamar a `db_path()` acá, que podría disparar la migración
# automática de una base que todavía no existe en el destino -- este
# diagnóstico nunca debe escribir ni mover ningún archivo de datos.
_DATABASES_UNDER_DIAGNOSIS = {
    "memory_store.db": MEMORY_STORE_DB_PATH,
    "radar_candidates.db": RADAR_CANDIDATES_DB_PATH,
    "catalyst_events.db": CATALYST_EVENTS_DB_PATH,
    "prediction_journal.db": PREDICTION_JOURNAL_DB_PATH,
    "historical_reference.db": HISTORICAL_REFERENCE_DB_PATH,
}


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


def filesystem_write_test() -> Dict[str, Any]:
    """Prueba de infraestructura aislada de SQLite (2026-09-01, autorizado
    explícitamente): abre/escribe/fsync/lee/borra un archivo temporal
    minúsculo dentro de `ATLAS_DATA_DIR` -- nunca toca ninguna `.db`.
    Distingue "el filesystem realmente permite I/O" de
    "`os.access(path, os.W_OK)` dice que sí" -- ese chequeo solo lee el
    bit de permiso, nunca ejercita un write/fsync real contra el disco.
    Nombre único por corrida, cleanup garantizado en `finally` incluso si
    la escritura o la lectura fallan a mitad de camino."""
    dd = _data_dir_path()
    tmp_path = dd / f"_fs_write_test_{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(b"atlas_fs_check")
            f.flush()
            os.fsync(f.fileno())
        with open(tmp_path, "rb") as f:
            content = f.read()
        return {"passed": content == b"atlas_fs_check"}
    except OSError as exc:
        return {"passed": False, "error_type": type(exc).__name__, "error_message": str(exc)}
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass  # el cleanup nunca debe convertirse en una causa nueva de fallo


def _file_stat_info(path: Path) -> Dict[str, Any]:
    """Metadatos de un archivo vía `os.stat`/`os.access` puro -- nunca lo
    abre. Incluye sus compañeros `-wal`/`-shm` (existencia + tamaño, sin
    leer contenido) para poder comparar bases entre sí (punto 5/6 del
    pedido: WAL/SHM anómalos)."""
    info: Dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if info["exists"]:
        try:
            stat = path.stat()
            info["size_bytes"] = stat.st_size
            info["modified_at"] = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        except OSError as exc:
            info["stat_error"] = f"{type(exc).__name__}: {exc}"
        info["readable"] = os.access(path, os.R_OK)
        info["writable"] = os.access(path, os.W_OK)
    for suffix, key in ((("-wal"), "wal"), (("-shm"), "shm")):
        side_path = path.parent / (path.name + suffix)
        side: Dict[str, Any] = {"exists": side_path.exists()}
        if side["exists"]:
            try:
                side["size_bytes"] = side_path.stat().st_size
            except OSError as exc:
                side["stat_error"] = f"{type(exc).__name__}: {exc}"
        info[key] = side
    return info


def _sqlite_read_only_test(path: Path) -> Dict[str, Any]:
    """`connect()` + `PRAGMA query_only=ON` + `SELECT name FROM
    sqlite_master LIMIT 1` + `close()` -- explícitamente de solo lectura,
    nunca CREATE/INSERT/UPDATE/DELETE/VACUUM/REINDEX, nunca cambia
    `journal_mode`. Si el archivo no existe, se salta (nunca lo crea --
    `sqlite3.connect()` sobre una ruta inexistente crearía un archivo
    nuevo, exactamente lo que este diagnóstico debe evitar)."""
    if not path.exists():
        return {"connect": "SKIPPED", "read_test": "SKIPPED", "error": "archivo no existe"}
    conn = None
    try:
        conn = sqlite3.connect(str(path), timeout=3)
        conn.execute("PRAGMA query_only=ON")
        conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        return {"connect": "OK", "read_test": "OK", "error": None}
    except Exception as exc:
        return {
            "connect": "OK" if conn is not None else "FAIL",
            "read_test": "FAIL",
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def diagnostics() -> Dict[str, Any]:
    """Todo lo necesario para confirmar, desde afuera, donde esta
    escribiendo realmente este proceso -- sin adivinar nada por tipo de
    montaje (ver decision del usuario, 2026-08-17): la unica prueba
    aceptada es marcador persistente + redeploy, no `st_dev` ni heuristicas
    de filesystem.

    Ampliado (2026-09-01, autorizado explícitamente): además del marcador
    de persistencia, agrega una prueba de escritura real de filesystem
    (aislada de SQLite) y, para cada una de las 5 bases SQLite del
    proyecto, sus metadatos (`stat`) + una prueba de lectura explícitamente
    read-only -- nunca modifica ninguna `.db`."""
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

    databases: Dict[str, Any] = {}
    for name, p in _DATABASES_UNDER_DIAGNOSIS.items():
        entry = _file_stat_info(p)
        entry.update(_sqlite_read_only_test(p))
        databases[name] = entry

    return {
        "atlas_data_dir_env_raw": raw_env,
        "atlas_data_dir_resolved": str(dd.resolve()),
        "atlas_data_dir_exists": dd.exists(),
        "atlas_data_dir_writable": os.access(dd, os.W_OK),
        "historical_reference_db": db_info,
        "persistence_marker": marker_info,
        "filesystem_write_test": filesystem_write_test(),
        "databases": databases,
    }
