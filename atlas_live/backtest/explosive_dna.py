"""Explosive DNA: caracteriza estadísticamente qué tuvieron en común las
acciones que REALMENTE fueron explosivas durante el período validado.

No optimiza ni cambia el Radar Explosivo -- es puramente descriptivo. Lee
los mismos archivos que ya guardó `historical_scan.py` (uno por día, con
las métricas crudas de TODO el universo reconstruido, no solo de las
elegibles) y calcula, para cada característica (precio, gap, RVOL, market
cap, volatilidad, volumen en $, cambio %):

  - Estadísticas del grupo "explosivo" (las top-N ganadoras reales de cada
    día, agrupadas de todos los días analizados).
  - Las mismas estadísticas del "resto del universo" ese mismo día, como
    grupo de control -- un número solo tiene valor de evidencia si se
    puede comparar contra el comportamiento típico, no aislado.
  - Una medida simple de separación entre ambos grupos, para ver qué tan
    discriminativa es cada característica.

El objetivo declarado es que futuros ajustes de umbrales se apoyen en esta
evidencia en vez de en supuestos -- pero este módulo no ajusta ningún
umbral por sí mismo, solo lo documenta.
"""

import glob
import json
import os
import statistics
from typing import Any, Dict, List, Optional

from atlas_live.explosive_engine import _size_label

TOP_N_EXPLOSIVE = 20

METRICS = ["price", "gap_pct", "relative_volume", "market_cap", "volatility_score", "dollar_volume", "change_pct"]
METRIC_LABELS = {
    "price": "Precio",
    "gap_pct": "Gap %",
    "relative_volume": "RVOL",
    "market_cap": "Market Cap",
    "volatility_score": "Volatilidad (score ATR 0-100)",
    "dollar_volume": "Volumen en $",
    "change_pct": "Cambio % (al momento del snapshot)",
}


def load_all_scans(results_dir: str) -> List[Dict[str, Any]]:
    paths = sorted(glob.glob(os.path.join(results_dir, "*.json")))
    scans = []
    for path in paths:
        if os.path.basename(path) == "consolidated_report.json":
            continue
        with open(path, "r", encoding="utf-8") as f:
            scans.append(json.load(f))
    return scans


def _split_day(scan: Dict[str, Any], top_n: int) -> (List[Dict[str, Any]], List[Dict[str, Any]]):
    rows = scan["rows"]
    ranked = sorted(rows, key=lambda r: r["ground_truth_change_pct"], reverse=True)
    explosive = ranked[:top_n]
    rest = ranked[top_n:]
    return explosive, rest


def collect_observations(scans: List[Dict[str, Any]], top_n: int = TOP_N_EXPLOSIVE) -> Dict[str, List[Dict[str, Any]]]:
    """Junta, de todos los días, las observaciones del grupo explosivo y las
    del grupo de control (resto del universo ese mismo día)."""
    explosive_obs: List[Dict[str, Any]] = []
    control_obs: List[Dict[str, Any]] = []

    for scan in scans:
        explosive, rest = _split_day(scan, top_n)
        for row in explosive:
            explosive_obs.append({
                "symbol": row["symbol"], "target_date": scan["target_date"],
                "ground_truth_change_pct": row["ground_truth_change_pct"],
                "eligible": row["explosive"]["eligible"], "failed_stage": row["explosive"]["failed_stage"],
                **row["explosive"]["metrics"],
            })
        for row in rest:
            control_obs.append({
                "symbol": row["symbol"], "target_date": scan["target_date"],
                "ground_truth_change_pct": row["ground_truth_change_pct"],
                **row["explosive"]["metrics"],
            })

    return {"explosive": explosive_obs, "control": control_obs}


def _stats_for(observations: List[Dict[str, Any]], metric: str) -> Optional[Dict[str, Any]]:
    values = [o[metric] for o in observations if o.get(metric) is not None]
    if len(values) < 2:
        return None
    values_sorted = sorted(values)

    def _pct(p: float) -> float:
        idx = min(len(values_sorted) - 1, max(0, round(p * (len(values_sorted) - 1))))
        return values_sorted[idx]

    return {
        "n": len(values),
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
        "min": min(values),
        "max": max(values),
        "p25": _pct(0.25),
        "p75": _pct(0.75),
        "p90": _pct(0.90),
    }


def _separation(explosive_values: List[float], control_values: List[float]) -> Optional[float]:
    """Qué fracción del grupo de control queda POR DEBAJO de la mediana del
    grupo explosivo -- 1.0 = separación perfecta (todo el control por
    debajo), 0.5 = la mediana explosiva no distingue nada del control."""
    if not explosive_values or not control_values:
        return None
    median_explosive = statistics.median(explosive_values)
    below = sum(1 for v in control_values if v < median_explosive)
    return below / len(control_values)


def build_dna_profile(scans: List[Dict[str, Any]], top_n: int = TOP_N_EXPLOSIVE) -> Dict[str, Any]:
    observations = collect_observations(scans, top_n)
    explosive_obs, control_obs = observations["explosive"], observations["control"]

    metrics_report = {}
    for metric in METRICS:
        explosive_stats = _stats_for(explosive_obs, metric)
        control_stats = _stats_for(control_obs, metric)
        explosive_values = [o[metric] for o in explosive_obs if o.get(metric) is not None]
        control_values = [o[metric] for o in control_obs if o.get(metric) is not None]
        metrics_report[metric] = {
            "label": METRIC_LABELS[metric],
            "explosive": explosive_stats,
            "control": control_stats,
            "separation_vs_control": _separation(explosive_values, control_values),
        }

    size_tier_counts: Dict[str, int] = {}
    for o in explosive_obs:
        tier = _size_label(o.get("market_cap"))
        size_tier_counts[tier] = size_tier_counts.get(tier, 0) + 1

    detected = sum(1 for o in explosive_obs if o["eligible"])
    total = len(explosive_obs)

    return {
        "days_analyzed": len(scans),
        "top_n_per_day": top_n,
        "total_explosive_observations": total,
        "total_control_observations": len(control_obs),
        "detected_by_radar": detected,
        "detected_by_radar_pct": detected / total if total else 0.0,
        "size_tier_distribution": size_tier_counts,
        "metrics": metrics_report,
    }


def format_report(profile: Dict[str, Any]) -> str:
    lines = []
    lines.append(f"Explosive DNA -- {profile['days_analyzed']} días, {profile['total_explosive_observations']} observaciones explosivas (top {profile['top_n_per_day']}/día)")
    lines.append(f"El Radar detectó {profile['detected_by_radar']}/{profile['total_explosive_observations']} ({profile['detected_by_radar_pct']:.1%}) de esas observaciones (cualquier rank).")
    lines.append("")
    lines.append("Distribución por tamaño de las explosivas:")
    for tier, count in sorted(profile["size_tier_distribution"].items(), key=lambda x: -x[1]):
        lines.append(f"  {tier}: {count} ({count/profile['total_explosive_observations']:.1%})")
    lines.append("")
    for metric, data in profile["metrics"].items():
        lines.append(f"--- {data['label']} ---")
        if data["explosive"] is None:
            lines.append("  Sin datos suficientes.")
            continue
        e, c = data["explosive"], data["control"]
        lines.append(f"  Explosivas : mediana={e['median']:.2f}  p25={e['p25']:.2f}  p75={e['p75']:.2f}  (n={e['n']})")
        if c:
            lines.append(f"  Resto      : mediana={c['median']:.2f}  p25={c['p25']:.2f}  p75={c['p75']:.2f}  (n={c['n']})")
        if data["separation_vs_control"] is not None:
            lines.append(f"  Separación : {data['separation_vs_control']:.1%} del resto queda por debajo de la mediana explosiva")
        lines.append("")
    return "\n".join(lines)
