"""CLI: calcula el perfil "Explosive DNA" a partir de los resultados ya
guardados por `run_validation.py`.

Uso:
    python -m atlas_live.backtest.run_explosive_dna --results-dir atlas_live/backtest/results

No modifica /atlas, no modifica explosive_engine.py ni explosive_config.json,
y no cambia ningún resultado de la validación -- solo lee y describe.
"""

import argparse
import json
import os

from loguru import logger

from atlas_live.backtest import explosive_dna


def main() -> None:
    parser = argparse.ArgumentParser(description="Explosive DNA: perfil estadístico de las acciones realmente explosivas")
    parser.add_argument("--results-dir", type=str, default="atlas_live/backtest/results")
    parser.add_argument("--top-n", type=int, default=explosive_dna.TOP_N_EXPLOSIVE)
    parser.add_argument("--output", type=str, default="atlas_live/backtest/results/explosive_dna.json")
    args = parser.parse_args()

    scans = explosive_dna.load_all_scans(args.results_dir)
    if not scans:
        logger.error(f"No hay resultados en {args.results_dir}. Corré primero run_validation.py.")
        return

    logger.info(f"Cargados {len(scans)} días de resultados. Calculando Explosive DNA...")
    profile = explosive_dna.build_dna_profile(scans, top_n=args.top_n)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)

    print(explosive_dna.format_report(profile))
    logger.info(f"Perfil guardado en {args.output}")


if __name__ == "__main__":
    main()
