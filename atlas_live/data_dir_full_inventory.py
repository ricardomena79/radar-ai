"""Inventario COMPLETO, read-only, de todos los archivos bajo
`ATLAS_DATA_DIR` (2026-09-03, auditoría de espacio de Hito 3.2, autorizado
explícitamente).

Complementa a `data_dir_diagnostics.py::directory_inventory()` (que
devuelve SOLO los 50 archivos más grandes) -- este módulo lista TODOS los
archivos, sin recorte, agrupados por extensión, para poder identificar
cualquier archivo no esencial que haya quedado fuera del top-50. Módulo
nuevo y separado a propósito -- `data_dir_diagnostics.py` no se modifica.

PURAMENTE DE LECTURA: `os.walk` + `Path.stat()` únicamente. NUNCA abre el
contenido de ningún archivo, NUNCA importa/ejecuta SQL sobre ningún `.db`
encontrado, NUNCA escribe absolutamente nada bajo `ATLAS_DATA_DIR` -- ni
siquiera un marcador temporal (a diferencia de `data_dir_diagnostics.py::
filesystem_write_test()`, que sí escribe un archivo de prueba y lo borra;
este módulo no hace ni eso)."""

import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.config.config import data_dir


def _data_dir_path() -> Path:
    """Misma resolución exacta que `data_dir_diagnostics.py::_data_dir_path()`
    -- reutiliza `atlas.config.config.data_dir()`, sin duplicar su lógica."""
    return data_dir(Path(__file__).parent)


def _extension_de(nombre_archivo: str) -> str:
    """Clasifica por extensión -- reconoce `.db-wal`/`.db-shm` (que
    `Path.suffix` mezclaría con `.db` si se usara ingenuamente) y agrupa
    cualquier variante de backup (`.pre_reset_v2_*.bak.db`, etc.) bajo
    `"*.bak*"` sin importar dónde caiga `.bak` en el nombre."""
    lower = nombre_archivo.lower()
    if lower.endswith(".db-wal"):
        return ".db-wal"
    if lower.endswith(".db-shm"):
        return ".db-shm"
    if ".bak" in lower:
        return "*.bak*"
    if lower.endswith(".db"):
        return ".db"
    if lower.endswith(".tmp"):
        return ".tmp"
    if lower.endswith(".json"):
        return ".json"
    if lower.endswith(".log"):
        return ".log"
    if lower.endswith(".txt"):
        return ".txt"
    if "." in nombre_archivo:
        return "." + nombre_archivo.rsplit(".", 1)[-1].lower()
    return "(sin_extension)"


def full_file_inventory(path: Optional[Path] = None) -> Dict[str, Any]:
    """Recorre `path` (por defecto `ATLAS_DATA_DIR`) con `os.walk`, hace
    `stat()` de cada archivo encontrado -- nunca lo abre, nunca lee su
    contenido. Devuelve TODAS las entradas (`path` relativo + `size_bytes`),
    ordenadas de mayor a menor, agrupadas por extensión, y el total general
    -- sin el recorte de top-N que tiene `directory_inventory()`."""
    root = Path(path) if path is not None else _data_dir_path()
    entries: List[Dict[str, Any]] = []
    total_bytes = 0
    errores: List[str] = []

    try:
        for r, _dirs, files in os.walk(root):
            for fname in files:
                fpath = Path(r) / fname
                try:
                    size = fpath.stat().st_size
                except OSError as exc:
                    errores.append(f"{fpath}: {type(exc).__name__}: {exc}")
                    continue
                total_bytes += size
                try:
                    rel = str(fpath.relative_to(root))
                except ValueError:
                    rel = str(fpath)
                entries.append({"path": rel, "size_bytes": size})
    except OSError as exc:
        return {
            "error": f"{type(exc).__name__}: {exc}",
            "root": str(root),
            "entries": [],
            "total_files": 0,
            "total_bytes": 0,
            "por_extension": {},
            "errores": [],
        }

    entries.sort(key=lambda e: e["size_bytes"], reverse=True)

    por_extension: Dict[str, Dict[str, int]] = defaultdict(lambda: {"count": 0, "total_bytes": 0})
    for e in entries:
        ext = _extension_de(Path(e["path"]).name)
        por_extension[ext]["count"] += 1
        por_extension[ext]["total_bytes"] += e["size_bytes"]

    return {
        "root": str(root),
        "entries": entries,
        "total_files": len(entries),
        "total_bytes": total_bytes,
        "por_extension": dict(por_extension),
        "errores": errores,
    }


def full_report() -> Dict[str, Any]:
    """Aislado por diseño (mismo patrón que el resto de diagnósticos de
    este proyecto): cualquier excepción queda atrapada acá adentro -- el
    llamador siempre recibe un dict, nunca una excepción sin manejar."""
    resultado: Dict[str, Any] = {"ok": False, "error": None}
    try:
        resultado.update(full_file_inventory())
        resultado["ok"] = True
    except Exception as exc:  # este diagnóstico NUNCA puede tumbar al llamador
        resultado["error"] = f"{type(exc).__name__}: {exc}"
        resultado["ok"] = False
    return resultado
