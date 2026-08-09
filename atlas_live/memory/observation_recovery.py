"""Backup y recuperación de las observaciones LIVE del Memory Store (F5,
2026-08-09).

El Memory Store vive en el Volume de Railway (`ATLAS_DATA_DIR`), así que
sobrevive a un REINICIO. Este módulo protege el caso peor -- que el Volume
se PIERDA por completo: entonces las observaciones de aprendizaje generadas
en vivo (source_version="live") se recuperan desde archivos JSONL, igual que
`seed_import.py` recupera el Exit Journal histórico.

  - `export_live_to_jsonl`: vuelca las observaciones live a un JSONL
    (una fila JSON por observación, con TODOS sus campos reales, incluido
    `recorded_at`). Se corre periódicamente / bajo demanda en producción y
    su salida se commitea, para que quede recuperable.
  - `import_all`: al arrancar el servidor, reimporta todos los JSONL de
    `MEMORY_SEED_DIR` al Memory Store, idempotente (INSERT OR IGNORE sobre
    la clave única symbol/date/checkpoint). Nunca sobrescribe una fila viva
    existente ni duplica; preserva el `recorded_at` original.

Estrictamente aditivo. No borra nada, no toca el seed histórico ("v1"), no
inventa timestamps.
"""

import json
from pathlib import Path
from typing import Any, Dict, List

from loguru import logger

from atlas_live.memory import store

# Directorio de JSONL de observaciones live, comiteable (separado del seed
# histórico de Exit Journal en backtest/seeds/).
MEMORY_SEED_DIR = Path(__file__).parent / "live_observations"
SOURCE_LIVE = "live"


def export_live_to_jsonl(out_path: Path) -> Dict[str, Any]:
    """Escribe todas las observaciones live actuales a `out_path` (JSONL).
    Sobrescribe el archivo destino con la foto actual -- es un backup, no un
    append incremental. No toca la base ni las observaciones históricas."""
    live = [o for o in store.get_observations() if o.get("source_version") == SOURCE_LIVE]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for o in live:
            o = dict(o)
            o.pop("id", None)  # el id autoincrement no se exporta (lo asigna cada base)
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    return {"exported": len(live), "path": str(out_path)}


def _import_file(path: Path) -> Dict[str, Any]:
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
                if store.import_observation_row(fila):
                    insertadas += 1
                else:
                    ya_existian += 1
            except Exception as exc:
                errores += 1
                logger.warning(f"observation_recovery: fila inválida en {path.name}: {exc}")
    return {"file": path.name, "insertadas": insertadas, "ya_existian": ya_existian, "errores": errores}


def import_all(seed_dir: Path = MEMORY_SEED_DIR) -> List[Dict[str, Any]]:
    """Reimporta todos los JSONL de observaciones live. Idempotente: reejecutar
    no duplica. Si el directorio no existe todavía (aún no hubo export), no
    hace nada -- no es un error."""
    if not seed_dir.exists():
        return []
    reportes = []
    for path in sorted(seed_dir.glob("*.jsonl")):
        reportes.append(_import_file(path))
    total = sum(r["insertadas"] for r in reportes)
    if total:
        logger.info(f"observation_recovery: {total} observaciones live recuperadas de {len(reportes)} archivo(s).")
    return reportes


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Backup/recuperación de observaciones live del Memory Store.")
    parser.add_argument("--export", action="store_true", help="Exporta las observaciones live a JSONL.")
    parser.add_argument("--out", default=str(MEMORY_SEED_DIR / "live_observations.jsonl"))
    args = parser.parse_args()
    if args.export:
        rep = export_live_to_jsonl(Path(args.out))
        print(f"Exportadas {rep['exported']} observaciones live a {rep['path']}")
    else:
        reps = import_all()
        print(f"Importados {len(reps)} archivo(s):")
        for r in reps:
            print(f"  {r['file']}: insertadas={r['insertadas']} ya_existían={r['ya_existian']} errores={r['errores']}")
