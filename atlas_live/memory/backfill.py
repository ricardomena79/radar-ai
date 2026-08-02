"""Carga histórica retroactiva al Memory Store -- Entregable Nº3 del Memory Engine.

Primera vez que el Memory Store (Entregable 1) recibe datos reales:
recorre los días ya guardados de una corrida de `historical_scan.py`
(`atlas_live/backtest/results_v1/` o `results_v2/`), clasifica cada fila
con el Clasificador de Resultado (Entregable 2) y la persiste.

100% de solo lectura sobre los archivos fuente -- no descarga nada nuevo,
no modifica `results_v1/` ni `results_v2/`, no toca `/atlas` ni la
validación V2 en curso (que sigue incompleta hoy: 15/30 días -- por eso
esta carga usa por defecto `results_v1/`, el único conjunto ya cerrado a
30/30; correr contra `results_v2/` cuando cierre es una ejecución nueva de
este mismo script, con `--source-version v2`, no un cambio de código).

Uso:
    python -m atlas_live.memory.backfill --source atlas_live/backtest/results_v1 --source-version v1

Decisiones de alcance de este entregable, explícitas para no ocultar
límites (no se inventa lo que no está en los datos fuente):
  - `sector`/`industry`: no se capturan en `historical_scan.py` hoy (campo
    `sector_money_flow_score` siempre `None`, según RADAR_EXPLOSIVO_V2.md)
    -- se guardan como `None`.
  - `market_cap_bucket`: no se deriva en este entregable -- requeriría
    definir umbrales de bucket sin evidencia todavía; se guarda `None`
    hasta que exista una regla explícita y aprobada.
  - `session`: la metodología de `historical_scan.py` reconstruye siempre
    el snapshot durante el horario regular de mercado -- se guarda
    `"regular"`, no es un dato inventado, es la metodología documentada.
  - `market_context`: reservado (ver MEMORY_ENGINE.md), no se llena todavía.
"""

import argparse
import glob
import json
import re
from typing import Any, Dict, Iterator, Tuple

from atlas_live.memory import classifier, store

_DAY_FILE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\.json$")


def _iter_day_files(source_dir: str) -> Iterator[str]:
    for path in sorted(glob.glob(f"{source_dir}/*.json")):
        if _DAY_FILE_PATTERN.search(path):
            yield path


def backfill(source_dir: str, source_version: str) -> Dict[str, Any]:
    """Recorre todos los días de `source_dir`, clasifica y persiste cada
    fila. Devuelve un reporte -- nunca falla en silencio."""
    dias_procesados = 0
    filas_totales = 0
    filas_guardadas = 0
    filas_descartadas_sin_resultado = 0
    por_categoria: Dict[str, int] = {c: 0 for c in classifier.CATEGORIES}

    for path in _iter_day_files(source_dir):
        with open(path, "r", encoding="utf-8") as f:
            scan = json.load(f)
        date = scan["target_date"]
        checkpoint_minutes = scan["snapshot_minutes_after_open"]
        dias_procesados += 1

        for row in scan["rows"]:
            filas_totales += 1
            categoria = classifier.classify_observation(row)
            if categoria is None:
                filas_descartadas_sin_resultado += 1
                continue

            store.record_observation(
                symbol=row["symbol"],
                date=date,
                checkpoint_minutes=checkpoint_minutes,
                category=categoria,
                metrics=row["explosive"]["metrics"],
                sector=None,
                industry=None,
                market_cap_bucket=None,
                session="regular",
                source_version=source_version,
                market_context=None,
            )
            filas_guardadas += 1
            por_categoria[categoria] += 1

    return {
        "source_dir": source_dir,
        "source_version": source_version,
        "dias_procesados": dias_procesados,
        "filas_totales": filas_totales,
        "filas_guardadas": filas_guardadas,
        "filas_descartadas_sin_resultado": filas_descartadas_sin_resultado,
        "por_categoria": por_categoria,
    }


def _print_report(reporte: Dict[str, Any]) -> None:
    print(f"Fuente: {reporte['source_dir']} (source_version={reporte['source_version']!r})")
    print(f"Días procesados: {reporte['dias_procesados']}")
    print(f"Filas totales leídas: {reporte['filas_totales']}")
    print(f"Filas guardadas en el Memory Store: {reporte['filas_guardadas']}")
    print(f"Filas descartadas (sin ground_truth_change_pct): {reporte['filas_descartadas_sin_resultado']}")
    print("Por categoría:")
    for categoria, n in reporte["por_categoria"].items():
        print(f"  {categoria:15s} {n}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="atlas_live/backtest/results_v1", help="Directorio con los días ya guardados (results_v1 o results_v2)")
    parser.add_argument("--source-version", default="v1", help="Etiqueta de trazabilidad de qué config generó estos datos")
    args = parser.parse_args()

    reporte = backfill(args.source, args.source_version)
    _print_report(reporte)
