"""Simulador de umbrales alternativos para la Etapa A del Radar Explosivo.

Reevalúa los 6 filtros de elegibilidad (Etapa A de `explosive_engine.py`)
sobre las métricas YA guardadas por `historical_scan.py`, contra una
configuración alternativa -- sin volver a descargar ningún dato, sin tocar
`explosive_config.json` y sin tocar `explosive_engine.py`. Sirve para medir
en segundos el efecto de una propuesta de cambio de umbral, en vez de tener
que correr una validación histórica completa (horas) por cada propuesta.

LIMITACIÓN EXPLÍCITA (no oculta): solo puede simular cambios en los 6 gates
de la Etapa A, porque esos umbrales operan exclusivamente sobre campos que
ya están guardados en `metrics` (price, gap_pct, relative_volume,
market_cap, volatility_score, dollar_volume, change_pct). NO puede simular
cambios de PESOS de la Etapa B (el puntaje de ranking), porque los scores
de momentum y VWAP por símbolo no se persisten hoy -- ver la propuesta
correspondiente en RADAR_EXPLOSIVO_V2.md. Consecuencia práctica: para un
símbolo que NO era elegible en la corrida real pero SÍ lo es bajo los gates
alternativos, no existe un `score` real (nunca se calculó, porque
`evaluate()` corta antes de llegar a la Etapa B) -- ese símbolo se marca
con `score=None` y se excluye del ranking de Precision@10/@20 simulado en
vez de inventarle un puntaje aproximado.
"""

from typing import Any, Dict, List


def _passes_gates(metrics: Dict[str, Any], gates: Dict[str, Any]) -> Dict[str, Any]:
    """Replica EXACTAMENTE la lógica y el orden de la Etapa A de
    `explosive_engine.evaluate()` -- no reimplementa ningún indicador, solo
    repite las mismas comparaciones contra umbrales (potencialmente
    distintos) sobre métricas ya calculadas."""
    price = metrics.get("price")
    dollar_volume = metrics.get("dollar_volume")
    rvol = metrics.get("relative_volume")
    gap_pct = metrics.get("gap_pct")
    change_pct = metrics.get("change_pct")
    volatility_score = metrics.get("volatility_score")
    market_cap = metrics.get("market_cap")

    if not price or price < gates["min_price"]:
        return {"eligible": False, "failed_stage": "price"}
    if dollar_volume is None or dollar_volume < gates["min_dollar_volume"]:
        return {"eligible": False, "failed_stage": "liquidity"}
    if rvol is None or rvol < gates["min_rvol"]:
        return {"eligible": False, "failed_stage": "rvol"}
    if max(abs(gap_pct or 0.0), abs(change_pct or 0.0)) < gates["min_abs_gap_or_change_pct"]:
        return {"eligible": False, "failed_stage": "movement"}
    if volatility_score is None or volatility_score < gates["min_volatility_score"]:
        return {"eligible": False, "failed_stage": "volatility"}

    is_large_cap = market_cap is not None and market_cap >= gates["large_cap_ceiling"]
    if is_large_cap:
        meets_exception = (
            abs(gap_pct or 0.0) >= gates["mega_cap_exception_gap_pct"]
            and (rvol or 0) >= gates["mega_cap_exception_rvol"]
        )
        if not meets_exception:
            return {"eligible": False, "failed_stage": "size"}

    return {"eligible": True, "failed_stage": None}


def simulate_scan(scan: Dict[str, Any], alternative_gates: Dict[str, Any]) -> Dict[str, Any]:
    """Reevalúa un día ya escaneado (mismo formato que guarda
    `historical_scan.py`) contra gates alternativos. Devuelve el mismo
    shape que el original para que `validation_report.py` lo procese sin
    cambios -- con la salvedad del `score` descrita en el docstring del
    módulo."""
    new_rows = []
    for row in scan["rows"]:
        original_explosive = row["explosive"]
        result = _passes_gates(original_explosive["metrics"], alternative_gates)

        new_explosive = dict(original_explosive)
        new_explosive["failed_stage"] = result["failed_stage"]

        if result["eligible"] and original_explosive["eligible"]:
            # Elegible en ambas configuraciones: se conserva el score y las
            # razones reales, ya calculados por la corrida original.
            new_explosive["eligible"] = True
        elif result["eligible"] and not original_explosive["eligible"]:
            # Elegible solo bajo los gates alternativos: nunca se calculó
            # un score real para este símbolo (evaluate() corta en la
            # Etapa A). Se marca explícitamente en vez de aproximar uno.
            new_explosive["eligible"] = True
            new_explosive["score"] = None
            new_explosive["reasons"] = []
            new_explosive["score_unavailable_reason"] = "No elegible en la corrida real -- la Etapa B nunca se calculó para este símbolo"
        else:
            new_explosive["eligible"] = False
            new_explosive["score"] = None

        new_row = dict(row)
        new_row["explosive"] = new_explosive
        new_rows.append(new_row)

    new_scan = dict(scan)
    new_scan["rows"] = new_rows
    return new_scan


def simulate_and_report(scans: List[Dict[str, Any]], alternative_gates: Dict[str, Any], base_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Corre `simulate_scan()` sobre todos los días y arma el mismo tipo de
    reporte consolidado que usa la validación real (mismas funciones de
    `validation_report.py`, sin duplicar esa lógica), para poder comparar
    métricas directamente contra el informe original."""
    from atlas_live.backtest import validation_report as vr

    sim_cfg = dict(base_cfg)
    sim_cfg["gates"] = alternative_gates

    simulated_scans = [simulate_scan(s, alternative_gates) for s in scans]
    daily_reports = [vr.build_daily_report(s, sim_cfg) for s in simulated_scans]
    consolidated = vr.consolidate_reports(daily_reports, simulated_scans, sim_cfg)

    newly_eligible_unscored = sum(
        1 for s in simulated_scans for r in s["rows"]
        if r["explosive"]["eligible"] and r["explosive"].get("score") is None
    )

    return {
        "daily_reports": daily_reports,
        "consolidated": consolidated,
        "newly_eligible_without_score": newly_eligible_unscored,
    }
