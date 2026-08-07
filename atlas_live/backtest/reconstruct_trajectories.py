"""Reconstrucción retroactiva de trayectorias completas -- Motor Predictivo,
Fase 1.1, Camino B (2026-08-06, ver DECISIONES.md).

`historical_scan.py` ya reconstruye, para cada día ya escaneado en
`results_v1/`, un único snapshot (`snapshot_minutes_after_open` minutos
después de la apertura). Este módulo NO reescribe esa lógica -- llama a
`reconstruct_symbol()` sin modificarla, una vez por cada vela de 5 minutos
disponible ese día (premarket + regular + after-hours, gracias a
`prepost=True` ya agregado a `fetch_intraday_bars`), y escribe cada punto
como una fila de `exit_journal.trajectory_samples` -- el mismo esquema que
ya usa el escaneo en vivo, poblado hacia atrás en vez de solo hacia
adelante.

Alcance: no reconstruye los ~2.577 símbolos del universo para cada día --
sería carísimo y no aporta nada a esta fase. Reconstruye trayectoria
completa solo para los símbolos que `classifier.classify_observation()`
(sin modificar, la misma regla que ya usa el Memory Store) clasificó como
EXPLOSION o FALSE_BREAKOUT en el snapshot ya existente -- son exactamente
los casos donde la pregunta "¿cuándo empezó la señal, y cuándo el
movimiento?" tiene sentido. NORMAL/WEAK/LOSER sin señal temprana quedan
fuera de esta reconstrucción (no de Memory Store, que ya los tiene).

Idempotente: si un símbolo/día ya tiene muestras en el Exit Journal
(`exit_journal.get_trajectory()` no vacío), se saltea -- nunca se
sobrescribe una trayectoria ya grabada, sea de esta reconstrucción o del
escaneo en vivo.

Uso:
    python -m atlas_live.backtest.reconstruct_trajectories --source atlas_live/backtest/results_v1
"""

import argparse
import glob
import re
from datetime import date, timezone
from typing import Any, Dict, List, Tuple

from loguru import logger

from atlas_live import explosive_engine
from atlas_live.backtest.historical_scan import (
    fetch_daily_bars,
    fetch_intraday_bars,
    fetch_shares_and_market_cap,
    reconstruct_symbol,
)
from atlas_live.explosive_config import load_config as load_explosive_config
from atlas_live.memory import classifier
from atlas_live.memory import exit_journal as ej

_DAY_FILE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\.json$")

# Categorías cuya trayectoria completa vale la pena reconstruir para
# medir "señal -> movimiento" -- ver docstring del módulo.
_TARGET_CATEGORIES = {"EXPLOSION", "FALSE_BREAKOUT"}

STEP_MINUTES = 5  # mismas velas de 5 minutos que ya usa fetch_intraday_bars


def _iter_day_files(source_dir: str):
    for path in sorted(glob.glob(f"{source_dir}/*.json")):
        if _DAY_FILE_PATTERN.search(path):
            yield path


def _target_symbols_for_day(day_file_path: str) -> Tuple[str, List[str]]:
    """Lee un día ya escaneado y devuelve (fecha, símbolos EXPLOSION/FALSE_BREAKOUT).
    Reutiliza `classifier.classify_observation()` sin modificarla -- misma
    regla que ya clasifica el Memory Store, no una nueva."""
    import json

    with open(day_file_path, "r", encoding="utf-8") as f:
        scan = json.load(f)

    date_str = scan["target_date"]
    symbols = [
        row["symbol"]
        for row in scan["rows"]
        if classifier.classify_observation(row) in _TARGET_CATEGORIES
    ]
    return date_str, symbols


def reconstruct_day_trajectories(date_str: str, symbols: List[str]) -> Dict[str, Any]:
    """Reconstruye y graba la trayectoria completa de `symbols` para `date_str`."""
    target_date = date.fromisoformat(date_str)
    report = {"date": date_str, "symbols_target": len(symbols), "symbols_written": 0,
               "symbols_skipped_ya_existia": 0, "symbols_sin_datos": 0, "points_written": 0}

    if not symbols:
        return report

    daily_bars = fetch_daily_bars(symbols, target_date)
    intraday_bars = fetch_intraday_bars(symbols, target_date)
    caps = fetch_shares_and_market_cap(list(daily_bars.keys()))
    explosive_cfg = load_explosive_config()

    for symbol in symbols:
        if ej.get_trajectory(symbol, date_str):
            report["symbols_skipped_ya_existia"] += 1
            continue

        if symbol not in daily_bars or symbol not in intraday_bars:
            report["symbols_sin_datos"] += 1
            continue

        intraday = intraday_bars[symbol].sort_index()
        n_total_candles = len(intraday)
        if n_total_candles == 0:
            report["symbols_sin_datos"] += 1
            continue

        shares, fallback_cap = caps.get(symbol, (None, None))
        points_this_symbol = 0

        for n in range(1, n_total_candles + 1):
            reconstructed = reconstruct_symbol(
                symbol=symbol, daily=daily_bars[symbol], intraday=intraday_bars[symbol],
                target_date=target_date, snapshot_minutes_after_open=n * STEP_MINUTES,
                shares=shares, fallback_market_cap=fallback_cap,
            )
            if reconstructed is None:
                continue

            explosive_result = explosive_engine.evaluate(
                quote=reconstructed["quote"], momentum_result=reconstructed["momentum_result"],
                sector_money_flow_score=None, config=explosive_cfg,
            )

            # Timestamp real de la vela (no el placeholder de medianoche que
            # usa reconstruct_symbol para otros fines) -- la última vela
            # incluida en este corte de `n` candles.
            sample_ts = intraday.iloc[:n].index[-1]
            sample_ts_utc = sample_ts.tz_convert(timezone.utc) if sample_ts.tzinfo else sample_ts.tz_localize(timezone.utc)

            ej.record_trajectory_sample(
                symbol=symbol, date=date_str, sampled_at=sample_ts_utc.isoformat(),
                return_pct=reconstructed["quote"].change_percent,
                score=explosive_result.score, eligible=explosive_result.eligible,
            )
            points_this_symbol += 1

        if points_this_symbol:
            report["symbols_written"] += 1
            report["points_written"] += points_this_symbol
        else:
            report["symbols_sin_datos"] += 1

    return report


def run(source_dir: str = "atlas_live/backtest/results_v1") -> List[Dict[str, Any]]:
    reportes = []
    for path in _iter_day_files(source_dir):
        date_str, symbols = _target_symbols_for_day(path)
        logger.info(f"{date_str}: {len(symbols)} símbolos EXPLOSION/FALSE_BREAKOUT a reconstruir")
        reporte = reconstruct_day_trajectories(date_str, symbols)
        reportes.append(reporte)
        logger.info(
            f"  -> escritos {reporte['symbols_written']} símbolos "
            f"({reporte['points_written']} puntos), "
            f"{reporte['symbols_skipped_ya_existia']} ya existían, "
            f"{reporte['symbols_sin_datos']} sin datos suficientes"
        )
    return reportes


def _print_summary(reportes: List[Dict[str, Any]]) -> None:
    total_target = sum(r["symbols_target"] for r in reportes)
    total_written = sum(r["symbols_written"] for r in reportes)
    total_points = sum(r["points_written"] for r in reportes)
    total_skipped = sum(r["symbols_skipped_ya_existia"] for r in reportes)
    total_sin_datos = sum(r["symbols_sin_datos"] for r in reportes)
    print(f"\nDías procesados: {len(reportes)}")
    print(f"Símbolos objetivo (EXPLOSION/FALSE_BREAKOUT): {total_target}")
    print(f"Símbolos con trayectoria escrita: {total_written} ({total_points} puntos totales)")
    print(f"Símbolos ya existentes (salteados, nunca sobrescritos): {total_skipped}")
    print(f"Símbolos sin datos suficientes para reconstruir: {total_sin_datos}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="atlas_live/backtest/results_v1")
    args = parser.parse_args()

    resultado = run(args.source)
    _print_summary(resultado)
