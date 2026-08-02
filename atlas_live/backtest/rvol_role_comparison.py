"""Comparación de escenarios sobre el rol de RVOL en el Radar Explosivo.

Construido para decidir, con evidencia y no por intuición, la base del
Radar Explosivo v2 (ver Propuesta 1 en RADAR_EXPLOSIVO_V2.md). No
implementa ningún cambio en `explosive_engine.py` ni en
`explosive_config.json` -- es una comparación de solo lectura sobre datos
ya guardados por `historical_scan.py`, reutilizando `whatif_simulator.py`
para todo lo que solo cambia gates (Etapa A).

LIMITACIÓN EXPLÍCITA, compartida por todo escenario que recalcula puntaje:
el puntaje real de la Etapa B combina 6-7 factores (relative_volume, gap,
vwap_distance, momentum/rsi, volatility, sector, float), pero solo 3 de
esos factores se pueden reconstruir desde los `metrics` ya guardados
(relative_volume, gap_pct, volatility_score) -- vwap_distance y
momentum/rsi nunca se persistieron, y sector/float son siempre None en
esta validación (ver docstring de `historical_scan.py`). Todo puntaje
"recalculado" aquí es un PUNTAJE PARCIAL de 3 factores: sirve para
comparar escenarios ENTRE SÍ, no es equivalente al puntaje real de
producción. Se marca explícitamente en cada resultado (`partial_score`).

Los 5 escenarios pedidos:
  1. Radar actual -- gate + puntaje real, sin cambios (baseline exacto).
  2. Radar actual sin RVOL -- gate de RVOL desactivado, y RVOL también
     excluido del puntaje parcial (peso 0).
  3. Radar con distintos umbrales de RVOL -- barrido de `min_rvol`, gate
     Etapa A únicamente (no toca el puntaje).
  4. Radar con RVOL con peso menor en el score -- MISMO gate que el
     escenario 1 (el conjunto de elegibles no cambia), pero el ranking
     usa el puntaje parcial con el peso de RVOL reducido.
  5. RVOL como factor de puntuación, no como filtro excluyente -- gate de
     RVOL desactivado (como el escenario 2) pero conservando su peso
     normal en el puntaje parcial (a diferencia del escenario 2, que lo
     pone en 0).
"""

from typing import Any, Dict, List, Optional

from atlas_live.backtest import validation_report as vr
from atlas_live.backtest import whatif_simulator as sim

# Pesos "recuperables" por defecto: la proporción relativa que tenían
# relative_volume / gap / volatility entre sí en el config real (0.25,
# 0.15, 0.15 de los 7 pesos originales), renormalizados a que sumen 1.0
# entre los 3 -- así el escenario 1 (peso "normal") es comparable a los
# demás sin cambiar la proporción relativa real entre estos tres factores.
_DEFAULT_RECOVERABLE_WEIGHTS = {"relative_volume": 0.25, "gap": 0.15, "volatility": 0.15}


def partial_score(metrics: Dict[str, Any], weights: Dict[str, float]) -> Optional[float]:
    """Puntaje parcial (0-100) usando solo los 3 factores reconstruibles
    desde métricas guardadas. Replica la misma normalización que Etapa B de
    explosive_engine.py: solo se cuentan los factores con valor disponible,
    y se divide por la suma de sus pesos (no por 1.0 fijo)."""
    rvol = metrics.get("relative_volume")
    gap = metrics.get("gap_pct")
    volatility = metrics.get("volatility_score")

    weighted_sum = 0.0
    weight_total = 0.0

    if rvol is not None:
        rvol_score = max(0.0, min(100.0, rvol * 12.5))  # misma fórmula que _relative_volume en explosive_factors.py
        weighted_sum += rvol_score * weights.get("relative_volume", 0.0)
        weight_total += weights.get("relative_volume", 0.0)

    if gap is not None:
        gap_score = max(0.0, min(100.0, abs(gap) * 8))  # misma fórmula que _gap en explosive_factors.py
        weighted_sum += gap_score * weights.get("gap", 0.0)
        weight_total += weights.get("gap", 0.0)

    if volatility is not None:
        weighted_sum += volatility * weights.get("volatility", 0.0)
        weight_total += weights.get("volatility", 0.0)

    if weight_total <= 0:
        return None
    return round(weighted_sum / weight_total, 1)


def _apply_scenario(scan: Dict[str, Any], gates: Dict[str, Any], score_weights: Optional[Dict[str, float]]) -> Dict[str, Any]:
    """Aplica gates (Etapa A, vía whatif_simulator) y, si se pide, reemplaza
    el puntaje de las elegibles por el puntaje parcial con los pesos dados."""
    simulated = sim.simulate_scan(scan, gates)
    if score_weights is None:
        return simulated

    new_rows = []
    for row in simulated["rows"]:
        new_row = dict(row)
        exp = dict(row["explosive"])
        if exp["eligible"]:
            exp["score"] = partial_score(exp["metrics"], score_weights)
            exp["score_is_partial"] = True
        new_row["explosive"] = exp
        new_rows.append(new_row)
    new_scan = dict(simulated)
    new_scan["rows"] = new_rows
    return new_scan


def _report_for(scans: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    daily = [vr.build_daily_report(s, cfg) for s in scans]
    cons = vr.consolidate_reports(daily, scans, cfg)
    fn_total = sum(sum(d["stage_losses"].values()) for d in daily)
    fp_total = 0
    for scan, d in zip(scans, daily):
        gt = {g["symbol"] for g in d["ground_truth"]}
        fp_total += sum(1 for r in scan["rows"] if r["explosive"]["eligible"] and r["symbol"] not in gt)
    return {
        "precision_at_10": cons["avg_precision_at_10"],
        "precision_at_20": cons["avg_precision_at_20"],
        "recall": cons["avg_recall"],
        "false_positives": fp_total,
        "false_negatives": fn_total,
        "eligible_total": sum(1 for s in scans for r in s["rows"] if r["explosive"]["eligible"]),
    }


def run_comparison(scans: List[Dict[str, Any]], base_cfg: Dict[str, Any], rvol_sweep: List[float] = None) -> Dict[str, Any]:
    rvol_sweep = rvol_sweep or [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    results: Dict[str, Any] = {}

    # 1. Radar actual (baseline exacto, puntaje real, sin recalcular nada)
    results["1_radar_actual"] = {
        "descripcion": "Config real, sin cambios. Puntaje real de producción (no parcial).",
        **_report_for(scans, base_cfg),
    }

    # 2. Radar actual sin RVOL (ni gate ni score)
    gates_no_rvol = dict(base_cfg["gates"])
    gates_no_rvol["min_rvol"] = 0.0
    weights_no_rvol = dict(_DEFAULT_RECOVERABLE_WEIGHTS)
    weights_no_rvol["relative_volume"] = 0.0
    scans_2 = [_apply_scenario(s, gates_no_rvol, weights_no_rvol) for s in scans]
    results["2_sin_rvol"] = {
        "descripcion": "Gate de RVOL desactivado y excluido del puntaje (peso 0). Puntaje PARCIAL (3 factores).",
        **_report_for(scans_2, base_cfg),
    }

    # 3. Barrido de umbrales de RVOL (solo gate, puntaje real cuando existe)
    results["3_barrido_umbral_rvol"] = {}
    for threshold in rvol_sweep:
        gates_t = dict(base_cfg["gates"])
        gates_t["min_rvol"] = threshold
        sim_result = sim.simulate_and_report(scans, gates_t, base_cfg)
        cons = sim_result["consolidated"]
        fn_total = sum(sum(d["stage_losses"].values()) for d in sim_result["daily_reports"])
        fp_total = 0
        for scan, d in zip((sim.simulate_scan(s, gates_t) for s in scans), sim_result["daily_reports"]):
            gt = {g["symbol"] for g in d["ground_truth"]}
            fp_total += sum(1 for r in scan["rows"] if r["explosive"]["eligible"] and r["symbol"] not in gt)
        results["3_barrido_umbral_rvol"][f"min_rvol={threshold}"] = {
            "precision_at_10": cons["avg_precision_at_10"],
            "precision_at_20": cons["avg_precision_at_20"],
            "recall": cons["avg_recall"],
            "false_positives": fp_total,
            "false_negatives": fn_total,
            "newly_eligible_without_score": sim_result["newly_eligible_without_score"],
        }

    # 4. Mismo gate que el escenario 1 (elegibles idénticas), RVOL con menos peso en el ranking
    weights_less_rvol = {"relative_volume": 0.10, "gap": 0.225, "volatility": 0.225}  # RVOL baja de 0.25 a 0.10, resto sube proporcional
    scans_4 = [_apply_scenario(s, base_cfg["gates"], weights_less_rvol) for s in scans]
    results["4_rvol_menor_peso"] = {
        "descripcion": "Mismo gate que el escenario 1 (mismas elegibles, mismo Recall). Ranking con RVOL en peso 0.10 en vez de 0.25 (puntaje PARCIAL).",
        **_report_for(scans_4, base_cfg),
    }

    # 5. RVOL como factor, no como filtro (gate desactivado, peso normal conservado)
    scans_5 = [_apply_scenario(s, gates_no_rvol, _DEFAULT_RECOVERABLE_WEIGHTS) for s in scans]
    results["5_rvol_solo_como_factor"] = {
        "descripcion": "Gate de RVOL desactivado (como escenario 2) pero con su peso normal conservado en el puntaje (puntaje PARCIAL).",
        **_report_for(scans_5, base_cfg),
    }

    return results
