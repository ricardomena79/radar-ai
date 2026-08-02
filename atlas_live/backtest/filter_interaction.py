"""Análisis de interacción entre los filtros de la Etapa A del Radar Explosivo.

No se limita a medir cada filtro por separado: enumera las 64 combinaciones
posibles de los 6 gates (activo/inactivo), reevalúa las métricas YA
guardadas por `historical_scan.py` contra cada una (reutilizando
`whatif_simulator.py`, sin descargar nada nuevo ni tocar
`explosive_engine.py`/`explosive_config.json`), y deriva de ese conjunto
completo:

  - Qué combinación maximiza Precision@10 / Precision@20 / Recall.
  - Poder predictivo individual de cada filtro (solo, sin los demás).
  - Contribución marginal de cada filtro (con todos los demás activos,
    ablation "leave-one-out").
  - Redundancia: filtros con poder individual alto pero contribución
    marginal baja (su información ya la capturan otros filtros).
  - Interacción par a par: cuánto cambia la contribución marginal de un
    filtro según si otro filtro específico está presente o ausente
    (derivado de la misma malla de 64 combinaciones, sin corridas extra).
  - Combinaciones que más falsos positivos / falsos negativos generan.

"Desactivar" un gate significa fijar su umbral en un valor que siempre se
cumple (ver `_disable()`) -- no se elimina ningún dato, solo se deja de
exigir esa condición para ser elegible.
"""

import itertools
from typing import Any, Dict, FrozenSet, List, Tuple

from atlas_live.backtest import whatif_simulator as sim

STAGES: List[str] = ["price", "liquidity", "rvol", "movement", "volatility", "size"]

_DISABLE_VALUES = {
    "price": ("min_price", 0.0),
    "liquidity": ("min_dollar_volume", 0.0),
    "rvol": ("min_rvol", 0.0),
    "movement": ("min_abs_gap_or_change_pct", 0.0),
    "volatility": ("min_volatility_score", 0.0),
    "size": ("large_cap_ceiling", float("inf")),
}


def make_gates(base_gates: Dict[str, Any], active_stages: FrozenSet[str]) -> Dict[str, Any]:
    """Copia de `base_gates` donde cualquier stage que NO esté en
    `active_stages` queda desactivado (su umbral pasa a cumplirse siempre)."""
    gates = dict(base_gates)
    for stage in STAGES:
        if stage not in active_stages:
            key, value = _DISABLE_VALUES[stage]
            gates[key] = value
    return gates


def all_combinations() -> List[FrozenSet[str]]:
    """Las 64 combinaciones posibles de los 6 gates (incluye el conjunto
    vacío -- ningún filtro activo -- y el conjunto completo -- la
    configuración real de producción)."""
    combos = []
    for r in range(len(STAGES) + 1):
        for subset in itertools.combinations(STAGES, r):
            combos.append(frozenset(subset))
    return combos


def evaluate_combination(scans: List[Dict[str, Any]], base_cfg: Dict[str, Any], active_stages: FrozenSet[str]) -> Dict[str, Any]:
    gates = make_gates(base_cfg["gates"], active_stages)
    result = sim.simulate_and_report(scans, gates, base_cfg)
    cons = result["consolidated"]

    # Falsos negativos totales: suma de las pérdidas por etapa de cada día
    # (ya calculadas por validation_report.build_daily_report).
    fn_total = sum(sum(dr["stage_losses"].values()) for dr in result["daily_reports"])

    # Falsos positivos totales: elegibles bajo esta combinación que NO
    # están en el top-20 real de ganadoras de ese día.
    fp_total = 0
    for scan, dr in zip((sim.simulate_scan(s, gates) for s in scans), result["daily_reports"]):
        ground_truth_symbols = {g["symbol"] for g in dr["ground_truth"]}
        fp_total += sum(
            1 for r in scan["rows"]
            if r["explosive"]["eligible"] and r["symbol"] not in ground_truth_symbols
        )

    return {
        "active_stages": sorted(active_stages),
        "n_active": len(active_stages),
        "precision_at_10": cons["avg_precision_at_10"],
        "precision_at_20": cons["avg_precision_at_20"],
        "recall": cons["avg_recall"],
        "false_positive_total": fp_total,
        "false_negative_total": fn_total,
        "newly_eligible_without_score": result["newly_eligible_without_score"],
    }


def run_full_grid(scans: List[Dict[str, Any]], base_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Corre las 64 combinaciones (los 6 gates) y devuelve la lista completa de resultados."""
    results = []
    for combo in all_combinations():
        results.append(evaluate_combination(scans, base_cfg, combo))
    return results


def run_grid_excluding(scans: List[Dict[str, Any]], base_cfg: Dict[str, Any], always_disabled: FrozenSet[str]) -> List[Dict[str, Any]]:
    """Igual que `run_full_grid()`, pero fijando `always_disabled` siempre
    desactivado (ej. RVOL congelado tras haberse investigado a fondo) y
    variando únicamente el resto -- para encontrar el siguiente cuello de
    botella entre los filtros que quedan, sin que el filtro dominante siga
    tapando la señal de los demás."""
    variable_stages = [s for s in STAGES if s not in always_disabled]
    results = []
    for r in range(len(variable_stages) + 1):
        for subset in itertools.combinations(variable_stages, r):
            results.append(evaluate_combination(scans, base_cfg, frozenset(subset)))
    return results


def _find(results: List[Dict[str, Any]], stages: FrozenSet[str]) -> Dict[str, Any]:
    for r in results:
        if frozenset(r["active_stages"]) == stages:
            return r
    raise KeyError(f"Combinación no encontrada: {stages}")


def best_by(results: List[Dict[str, Any]], metric: str, top_n: int = 5) -> List[Dict[str, Any]]:
    return sorted(results, key=lambda r: r[metric], reverse=True)[:top_n]


def individual_power(results: List[Dict[str, Any]], stages: List[str] = None) -> Dict[str, Dict[str, Any]]:
    """Poder predictivo de cada filtro SOLO (sin ningún otro activo)."""
    stages = stages or STAGES
    return {stage: _find(results, frozenset({stage})) for stage in stages}


def marginal_contribution(results: List[Dict[str, Any]], all_stages: FrozenSet[str] = None) -> Dict[str, Dict[str, float]]:
    """Contribución marginal de cada filtro dado que TODOS los demás
    considerados (`all_stages`) están activos (ablation leave-one-out):
    métrica(todos) - métrica(todos sin este). `all_stages` puede ser un
    subconjunto de STAGES (ej. los 5 no-RVOL) para congelar un filtro y
    analizar solo el resto."""
    all_stages = all_stages or frozenset(STAGES)
    full = _find(results, all_stages)
    contributions = {}
    for stage in all_stages:
        without = _find(results, all_stages - {stage})
        contributions[stage] = {
            "delta_precision_at_10": full["precision_at_10"] - without["precision_at_10"],
            "delta_precision_at_20": full["precision_at_20"] - without["precision_at_20"],
            "delta_recall": full["recall"] - without["recall"],
            "delta_false_positive": full["false_positive_total"] - without["false_positive_total"],
            "delta_false_negative": full["false_negative_total"] - without["false_negative_total"],
        }
    return contributions


def pairwise_interaction(results: List[Dict[str, Any]], stages: List[str] = None) -> List[Dict[str, Any]]:
    """Para cada par (g, h) dentro de `stages`: compara la contribución
    marginal de g cuando h está presente vs. cuando h está ausente (ambas
    veces con los demás fijos en "activos"). Si la contribución marginal de
    g colapsa cuando h ya está presente, g y h se solapan (redundancia
    parcial). `stages` puede ser un subconjunto de STAGES."""
    stages = stages or STAGES
    all_stages = frozenset(stages)
    interactions = []
    for g, h in itertools.combinations(stages, 2):
        full = _find(results, all_stages)
        without_g = _find(results, all_stages - {g})
        without_h = _find(results, all_stages - {h})
        without_both = _find(results, all_stages - {g, h})

        # Contribución de g con h presente (los otros 4 + h activos, solo falta g vs todos)
        contrib_g_with_h = full["precision_at_10"] - without_g["precision_at_10"]
        # Contribución de g con h ausente (los otros 4 activos, ni g ni h)
        contrib_g_without_h = without_h["precision_at_10"] - without_both["precision_at_10"]

        contrib_h_with_g = full["precision_at_10"] - without_h["precision_at_10"]
        contrib_h_without_g = without_g["precision_at_10"] - without_both["precision_at_10"]

        interactions.append({
            "pair": (g, h),
            "contrib_g_with_h_present": contrib_g_with_h,
            "contrib_g_with_h_absent": contrib_g_without_h,
            "contrib_h_with_g_present": contrib_h_with_g,
            "contrib_h_with_g_absent": contrib_h_without_g,
            "overlap_g_on_h": contrib_g_without_h - contrib_g_with_h,
            "overlap_h_on_g": contrib_h_without_g - contrib_h_with_g,
        })
    return interactions


def redundancy_report(results: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Un filtro es candidato a redundante si tiene poder individual alto
    (aislado) pero contribución marginal baja o negativa (dado el resto)."""
    solo = individual_power(results)
    marginal = marginal_contribution(results)
    report = {}
    for stage in STAGES:
        report[stage] = {
            "solo_precision_at_10": solo[stage]["precision_at_10"],
            "solo_recall": solo[stage]["recall"],
            "marginal_delta_precision_at_10": marginal[stage]["delta_precision_at_10"],
            "marginal_delta_recall": marginal[stage]["delta_recall"],
            "possibly_redundant": solo[stage]["precision_at_10"] > 0 and marginal[stage]["delta_precision_at_10"] <= 0,
        }
    return report
