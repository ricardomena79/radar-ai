"""Investigación 4 -- Persistencia y sincronización del conocimiento de
Atlas (Etapa 3, 2026-08-06, ver DECISION_LOG.md).

Importa hacia la base oficial (donde sea que `exit_journal.DB_PATH`
resuelva en este proceso -- Volume de Railway en producción) los seeds
JSONL comiteados en `atlas_live/backtest/seeds/`. Corre al arrancar el
servidor (ver `server.py`), con el mismo rol que ya cumple `db_path()`
para la migración de un Volume vacío: si la base oficial se pierde por
completo, arrancar el proceso de nuevo la reconstruye sola, de forma
acumulativa, a partir de todos los seeds ya comiteados hasta la fecha.

**Estrictamente aditivo, nunca sobrescribe** (decisión de arquitectura
de esta investigación): antes de escribir una fila, se verifica que su
`(symbol, date, sampled_at)` todavía no exista en destino -- si un dato
en vivo ya ocupa esa clave exacta, el seed nunca lo toca. Cada fila se
inserta con la misma función pública ya validada de `exit_journal.py`
(`record_trajectory_sample`) -- este módulo no abre ninguna conexión
SQL propia ni conoce el esquema de la tabla.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from loguru import logger

from atlas_live.memory import exit_journal as ej

SEED_DIR = Path(__file__).parent / "seeds"


def _existing_sample_keys(symbol: str, date: str, cache: Dict[Tuple[str, str], Set[str]]) -> Set[str]:
    key = (symbol, date)
    if key not in cache:
        cache[key] = {s["sampled_at"] for s in ej.get_trajectory(symbol, date)}
    return cache[key]


def _import_file(path: Path, cache: Dict[Tuple[str, str], Set[str]]) -> Dict[str, Any]:
    insertadas = 0
    ya_existian = 0
    errores = 0
    with open(path, "r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            try:
                fila = json.loads(linea)
                existentes = _existing_sample_keys(fila["symbol"], fila["date"], cache)
                if fila["sampled_at"] in existentes:
                    ya_existian += 1
                    continue
                ej.record_trajectory_sample(
                    symbol=fila["symbol"], date=fila["date"], sampled_at=fila["sampled_at"],
                    return_pct=fila["return_pct"], score=fila["score"], eligible=fila["eligible"],
                )
                existentes.add(fila["sampled_at"])
                insertadas += 1
            except Exception as exc:
                errores += 1
                logger.warning(f"seed_import: fila inválida en {path.name}: {exc}")
    return {"archivo": path.name, "insertadas": insertadas, "ya_existian": ya_existian, "errores": errores}


def import_all_seeds(seed_dir: Path = SEED_DIR) -> List[Dict[str, Any]]:
    """Punto de entrada único, pensado para llamarse una vez al arrancar
    el servidor. Nunca lanza hacia el llamador -- un seed corrupto o el
    directorio ausente no debe impedir que el servidor arranque (mismo
    principio ya aplicado en `live_integration.run_live_cycle`)."""
    if not seed_dir.exists():
        return []

    cache: Dict[Tuple[str, str], Set[str]] = {}
    reportes = []
    for path in sorted(seed_dir.glob("*.jsonl")):
        try:
            reportes.append(_import_file(path, cache))
        except Exception as exc:
            logger.warning(f"seed_import: no se pudo procesar {path.name}: {exc}")
            reportes.append({"archivo": path.name, "insertadas": 0, "ya_existian": 0, "errores": 1})

    total_insertadas = sum(r["insertadas"] for r in reportes)
    if total_insertadas or reportes:
        logger.info(
            f"seed_import: {len(reportes)} archivo(s) procesado(s), "
            f"{total_insertadas} muestra(s) nueva(s) insertada(s)"
        )
    return reportes
