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
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

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

# Nombres reales de las 10 tablas de radar_candidates.db (2026-09-01,
# copiados de `_SCHEMA` en candidate_registry.py -- lista fija, nunca
# construida a partir de un valor externo, para no arriesgar ningún tipo
# de inyección en el `SELECT COUNT(*)` de solo lectura de más abajo).
_RADAR_CANDIDATES_TABLE_NAMES = (
    "candidate_detection",
    "candidate_observation",
    "candidate_intraday_metrics",
    "candidate_outcome",
    "radar_meta",
    "alert_stage_log",
    "daily_summary",
    "missed_mover",
    "magnitud_prediction",
    "shadow_decision_log",
)


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


def disk_usage_info(path: Path) -> Dict[str, Any]:
    """Equivalente de `df -h`/`df -i` sobre el filesystem que contiene
    `path`, con librerías estándar de Python -- sin `subprocess`, sin
    shell. `shutil.disk_usage()` funciona en cualquier plataforma;
    `os.statvfs()` (conteo de inodos) es exclusivo de POSIX -- en Linux
    (producción real) siempre está disponible; en Windows (este entorno
    de desarrollo) no existe, así que se reporta explícitamente en vez de
    fallar."""
    info: Dict[str, Any] = {}
    try:
        usage = shutil.disk_usage(str(path))
        info["total_bytes"] = usage.total
        info["used_bytes"] = usage.used
        info["free_bytes"] = usage.free
    except OSError as exc:
        info["disk_usage_error"] = f"{type(exc).__name__}: {exc}"
    statvfs = getattr(os, "statvfs", None)
    if statvfs is None:
        info["inode_info_unavailable"] = "os.statvfs no existe en esta plataforma"
    else:
        try:
            vfs = statvfs(str(path))
            info["inodes_total"] = vfs.f_files
            info["inodes_free"] = vfs.f_ffree
            info["inodes_available"] = vfs.f_favail
        except OSError as exc:
            info["inode_info_unavailable"] = f"{type(exc).__name__}: {exc}"
    return info


def directory_inventory(path: Path, top_n: int = 50) -> Dict[str, Any]:
    """Equivalente de `du -h` -- recorre `path` con `os.walk` (nunca abre
    ningún archivo, solo `stat`) y devuelve las `top_n` entradas más
    grandes, ordenadas descendente, más el total de bytes contabilizados y
    la cantidad total de archivos encontrados. Recursivo, así que también
    cubre cualquier subdirectorio (caches, exports, etc.) bajo
    `ATLAS_DATA_DIR`, no solo el nivel superior."""
    entries: List[Dict[str, Any]] = []
    total_accounted_bytes = 0
    try:
        for root, _dirs, files in os.walk(path):
            for fname in files:
                fpath = Path(root) / fname
                try:
                    size = fpath.stat().st_size
                except OSError:
                    continue
                total_accounted_bytes += size
                try:
                    rel = str(fpath.relative_to(path))
                except ValueError:
                    rel = str(fpath)
                entries.append({"path": rel, "size_bytes": size})
    except OSError as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "entries": [], "total_accounted_bytes": 0}
    entries.sort(key=lambda e: e["size_bytes"], reverse=True)
    return {
        "entries": entries[:top_n],
        "total_files_found": len(entries),
        "total_accounted_bytes": total_accounted_bytes,
    }


def radar_candidates_table_counts(path: Path) -> Dict[str, Any]:
    """`SELECT COUNT(*)` de solo lectura por cada tabla conocida de
    `radar_candidates.db` (2026-09-01) -- ayuda a identificar qué tabla
    concentra el crecimiento, sin `dbstat` (extensión opcional de SQLite,
    no garantizada) y sin abrir la base para escritura. Lista de tablas
    fija (`_RADAR_CANDIDATES_TABLE_NAMES`), nunca construida a partir de
    un valor externo."""
    if not path.exists():
        return {"skipped": "archivo no existe"}
    counts: Dict[str, Any] = {}
    conn = None
    try:
        conn = sqlite3.connect(str(path), timeout=3)
        conn.execute("PRAGMA query_only=ON")
        for table in _RADAR_CANDIDATES_TABLE_NAMES:
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # nosec: nombre fijo, no externo
                counts[table] = row[0] if row else None
            except Exception as exc:
                counts[table] = f"error: {type(exc).__name__}: {exc}"
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return counts


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
        "disk_usage": disk_usage_info(dd),
        "directory_inventory": directory_inventory(dd),
        "radar_candidates_table_counts": radar_candidates_table_counts(RADAR_CANDIDATES_DB_PATH),
    }
