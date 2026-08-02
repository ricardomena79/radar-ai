"""CLI: valida el Radar Explosivo contra los últimos N días reales de
mercado, sobre el Universo Racional completo.

Uso:
    python -m atlas_live.backtest.run_validation --days 30 --results-dir atlas_live/backtest/results

No modifica /atlas, no modifica explosive_engine.py ni explosive_config.json
-- solo mide.
"""

import argparse
import glob
import json
import os
from datetime import date

import yfinance as yf
from loguru import logger

from atlas.data.universe import load_universe
from atlas_live.backtest import historical_scan, validation_report
from atlas_live.explosive_config import load_config


def determine_trading_days(n: int) -> list:
    """Últimos `n` días de mercado REALES (no adivinados por día de la
    semana): se derivan del propio calendario de SPY, que ya excluye fines
    de semana y feriados."""
    df = yf.download("SPY", period="6mo", interval="1d", progress=False)
    dates = [d.date() for d in df.index]
    return dates[-n:]


def main() -> None:
    parser = argparse.ArgumentParser(description="Validación histórica del Radar Explosivo")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--results-dir", type=str, default="atlas_live/backtest/results")
    parser.add_argument("--report-path", type=str, default="atlas_live/backtest/results/consolidated_report.json")
    args = parser.parse_args()

    target_dates = determine_trading_days(args.days)
    logger.info(f"Días de mercado a validar ({len(target_dates)}): {target_dates[0]} .. {target_dates[-1]}")

    universe = list(load_universe().values())
    logger.info(f"Universo Racional completo: {len(universe)} símbolos")

    saved_paths = historical_scan.run_historical_scan_multi(
        target_dates=target_dates,
        results_dir=args.results_dir,
        universe=universe,
    )

    cfg = load_config()

    all_scans = [validation_report.load_scan(p) for p in sorted(saved_paths)]
    daily_reports = [validation_report.build_daily_report(scan, cfg) for scan in all_scans]

    consolidated = validation_report.consolidate_reports(daily_reports, all_scans, cfg)

    output = {
        "generated_from_days": [r["target_date"] for r in daily_reports],
        "daily_reports": daily_reports,
        "consolidated": consolidated,
    }

    os.makedirs(os.path.dirname(args.report_path), exist_ok=True)
    with open(args.report_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    logger.info(f"Informe consolidado guardado en {args.report_path}")
    logger.info(f"Precision@10 promedio: {consolidated['avg_precision_at_10']:.2%}")
    logger.info(f"Precision@20 promedio: {consolidated['avg_precision_at_20']:.2%}")
    logger.info(f"Recall promedio: {consolidated['avg_recall']:.2%}")
    logger.info("VALIDACION COMPLETA")


if __name__ == "__main__":
    main()
