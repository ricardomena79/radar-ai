"""Convierte los resultados crudos de `historical_scan.py` (por día) en
informes: por día (top 20 ganadores reales vs qué hizo el Radar Explosivo
con cada uno) y consolidado (promedios de precision/recall a lo largo de
varios días + qué filtros costaron más oportunidades / cuáles parecen
demasiado estrictos o permisivos).

Es una capa de solo lectura sobre resultados ya calculados -- no cambia
ningún umbral de `explosive_config.json` ni ningún resultado del motor.
"""

import json
from collections import Counter
from typing import Any, Dict, List, Optional

TOP_N_GROUND_TRUTH = 20
NEAR_THRESHOLD_MARGIN = 0.25  # 25%: qué tan cerca del umbral cuenta como "cerca"

# Métrica relevante para juzgar qué tan cerca estuvo cada etapa de pasar,
# y contra qué clave de `gates` en explosive_config.json se compara.
STAGE_METRIC = {
    "price": ("price", "min_price", "min"),
    "liquidity": ("dollar_volume", "min_dollar_volume", "min"),
    "rvol": ("relative_volume", "min_rvol", "min"),
    "movement": ("gap_pct", "min_abs_gap_or_change_pct", "min_abs"),
    "volatility": ("volatility_score", "min_volatility_score", "min"),
    "size": ("market_cap", "large_cap_ceiling", "max"),
}


def _closeness(row: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[float]:
    """Qué tan cerca estuvo del umbral el filtro que lo descartó, como
    fracción (0 = justo en el umbral, 1 = el doble de lejos que el margen
    de referencia). None si no se puede calcular (dato faltante)."""
    stage = row["explosive"]["failed_stage"]
    if stage is None or stage not in STAGE_METRIC:
        return None
    metric_key, gate_key, mode = STAGE_METRIC[stage]
    value = row["explosive"]["metrics"].get(metric_key)
    threshold = cfg["gates"].get(gate_key)
    if value is None or threshold in (None, 0):
        return None

    if mode == "min":
        gap = (threshold - value) / threshold
    elif mode == "min_abs":
        gap = (threshold - abs(value)) / threshold
    else:  # max
        gap = (value - threshold) / threshold
    return max(0.0, gap)


def judge_exclusion(row: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    """Juicio heurístico y transparente (no una verdad absoluta): compara
    qué tan lejos estaba el valor real del umbral que lo descartó."""
    closeness = _closeness(row, cfg)
    if closeness is None:
        return "No se pudo evaluar (dato faltante)"
    if closeness <= NEAR_THRESHOLD_MARGIN:
        return f"Cerca del umbral (a un {closeness*100:.0f}% de distancia) -- el filtro pudo ser demasiado estricto aquí"
    return f"Lejos del umbral ({closeness*100:.0f}% de distancia) -- descarte parece correcto"


def build_daily_report(scan_result: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    rows = scan_result["rows"]

    ground_truth = sorted(rows, key=lambda r: r["ground_truth_change_pct"], reverse=True)[:TOP_N_GROUND_TRUTH]
    ground_truth_symbols = {r["symbol"] for r in ground_truth}

    eligible = [r for r in rows if r["explosive"]["eligible"]]
    eligible_sorted = sorted(eligible, key=lambda r: r["explosive"]["score"] or 0, reverse=True)
    rank_by_symbol = {r["symbol"]: i + 1 for i, r in enumerate(eligible_sorted)}
    eligible_symbols = set(rank_by_symbol.keys())

    top10_radar_symbols = {r["symbol"] for r in eligible_sorted[:10]}
    top20_radar_symbols = {r["symbol"] for r in eligible_sorted[:20]}

    precision_at_10_hits = len(top10_radar_symbols & ground_truth_symbols)
    precision_at_20_hits = len(top20_radar_symbols & ground_truth_symbols)
    recall_hits = len(ground_truth_symbols & eligible_symbols)

    detail = []
    for r in ground_truth:
        symbol = r["symbol"]
        exp = r["explosive"]
        if exp["eligible"]:
            detail.append({
                "symbol": symbol,
                "ground_truth_change_pct": r["ground_truth_change_pct"],
                "appeared": True,
                "rank": rank_by_symbol[symbol],
                "score": exp["score"],
                "reasons": exp["reasons"],
                "judgment": None,
            })
        else:
            detail.append({
                "symbol": symbol,
                "ground_truth_change_pct": r["ground_truth_change_pct"],
                "appeared": False,
                "failed_stage": exp["failed_stage"],
                "excluded_reason": exp["excluded_reason"],
                "metrics": exp["metrics"],
                "judgment": judge_exclusion(r, cfg),
            })

    excluded_ground_truth = [r for r in ground_truth if not r["explosive"]["eligible"]]
    stage_losses = Counter(r["explosive"]["failed_stage"] for r in excluded_ground_truth)

    return {
        "target_date": scan_result["target_date"],
        "universe_total": scan_result["universe_total"],
        "reconstructed_ok": scan_result["reconstructed_ok"],
        "data_errors": scan_result["data_errors"],
        "eligible_count": len(eligible),
        "ground_truth": [{"symbol": r["symbol"], "change_pct": r["ground_truth_change_pct"]} for r in ground_truth],
        "detail": detail,
        "precision_at_10": {"hits": precision_at_10_hits, "of": 10, "value": precision_at_10_hits / 10},
        "precision_at_20": {"hits": precision_at_20_hits, "of": 20, "value": precision_at_20_hits / 20},
        "recall": {"hits": recall_hits, "of": len(ground_truth), "value": recall_hits / len(ground_truth) if ground_truth else 0.0},
        "stage_losses": dict(stage_losses),
    }


def consolidate_reports(daily_reports: List[Dict[str, Any]], all_scan_results: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    n = len(daily_reports)
    avg_p10 = sum(r["precision_at_10"]["value"] for r in daily_reports) / n
    avg_p20 = sum(r["precision_at_20"]["value"] for r in daily_reports) / n
    avg_recall = sum(r["recall"]["value"] for r in daily_reports) / n

    # Filtros que más oportunidades reales (top-20 ganadores del día) hicieron perder, sumado en todos los días.
    stage_loss_totals: Counter = Counter()
    for r in daily_reports:
        stage_loss_totals.update(r["stage_losses"])

    # De las oportunidades reales que el radar SÍ detectó, qué factores aparecen más seguido en sus razones
    # ("qué aportó más valor" -- qué señal explica mejor los aciertos reales, no solo cualquier aprobación).
    value_factor_counts: Counter = Counter()
    for r in daily_reports:
        for d in r["detail"]:
            if d["appeared"]:
                for reason in d["reasons"]:
                    # Se agrupa por el nombre del factor, no el texto completo (que incluye números variables).
                    key = reason.split(" ")[0] if not reason.startswith("RVOL") else "RVOL"
                    value_factor_counts[key] += 1

    # Umbrales "demasiado estrictos": entre TODAS las oportunidades reales excluidas (no solo top 20 por día,
    # sino cada vez que una acción del top-20-ganador de cualquier día quedó fuera), qué fracción de los
    # descartes por cada etapa estuvo "cerca" del umbral según judge_exclusion().
    near_miss_by_stage: Counter = Counter()
    far_miss_by_stage: Counter = Counter()
    for scan in all_scan_results:
        ground_truth = sorted(scan["rows"], key=lambda r: r["ground_truth_change_pct"], reverse=True)[:TOP_N_GROUND_TRUTH]
        for r in ground_truth:
            if r["explosive"]["eligible"]:
                continue
            stage = r["explosive"]["failed_stage"]
            closeness = _closeness(r, cfg)
            if closeness is None:
                continue
            if closeness <= NEAR_THRESHOLD_MARGIN:
                near_miss_by_stage[stage] += 1
            else:
                far_miss_by_stage[stage] += 1

    too_strict = []
    for stage, near_count in near_miss_by_stage.items():
        far_count = far_miss_by_stage.get(stage, 0)
        total = near_count + far_count
        if total >= 3 and near_count / total >= 0.5:
            too_strict.append({"stage": stage, "near_misses": near_count, "far_misses": far_count, "share_near": near_count / total})

    # Umbrales "demasiado permisivos": entre los símbolos que el radar aprobó pero que NO fueron una
    # oportunidad real ese día (no en el top-20 de ganadores -- falsos positivos), qué tan seguido
    # pasaron CADA UNO de los 6 filtros "raspando" (con el valor apenas por encima -- o, para "size",
    # apenas por debajo del techo -- del umbral configurado). Cubre los 6 gates, no solo RVOL.
    false_positive_near_by_stage: Counter = Counter()
    false_positive_total = 0
    for scan in all_scan_results:
        ground_truth_symbols = {r["symbol"] for r in sorted(scan["rows"], key=lambda r: r["ground_truth_change_pct"], reverse=True)[:TOP_N_GROUND_TRUTH]}
        for r in scan["rows"]:
            if not r["explosive"]["eligible"] or r["symbol"] in ground_truth_symbols:
                continue
            false_positive_total += 1
            metrics = r["explosive"]["metrics"]
            for stage, (metric_key, gate_key, mode) in STAGE_METRIC.items():
                value = metrics.get(metric_key)
                threshold = cfg["gates"].get(gate_key)
                if value is None or threshold in (None, 0):
                    continue
                if mode == "min":
                    margin = (value - threshold) / threshold
                elif mode == "min_abs":
                    margin = (abs(value) - threshold) / threshold
                else:  # max (ej. "size": qué tan cerca del techo de market cap, por debajo)
                    margin = (threshold - value) / threshold
                if 0 <= margin <= NEAR_THRESHOLD_MARGIN:
                    false_positive_near_by_stage[stage] += 1

    too_permissive = []
    if false_positive_total >= 3:
        for stage, near_count in false_positive_near_by_stage.items():
            share = near_count / false_positive_total
            if share >= 0.4:
                too_permissive.append({"gate": stage, "near_count": near_count, "false_positive_total": false_positive_total, "share": share})
    too_permissive.sort(key=lambda x: -x["share"])

    return {
        "days_analyzed": n,
        "avg_precision_at_10": avg_p10,
        "avg_precision_at_20": avg_p20,
        "avg_recall": avg_recall,
        "stage_loss_totals": dict(stage_loss_totals.most_common()),
        "value_factor_counts": dict(value_factor_counts.most_common()),
        "too_strict_candidates": sorted(too_strict, key=lambda x: x["near_misses"], reverse=True),
        "too_permissive_candidates": too_permissive,
    }


def load_scan(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
