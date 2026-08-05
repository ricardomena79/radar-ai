"""Diagnóstico del Radar Explosivo.

Toma los `ExplosiveResult` que ya calculó `explosive_engine.py` para TODO
el universo escaneado en el ciclo más reciente (no solo el top_n que se
muestra en el ranking) y arma un resumen auditable: cuántos símbolos
superaron cada etapa del embudo, y el detalle de las aprobadas junto con
las descartadas que más cerca estuvieron de calificar.

No recalcula nada ni cambia ningún umbral -- es una capa de solo lectura
sobre resultados que ya existen. Vive en atlas_live, no en Atlas Core.
"""

from typing import Any, Dict, List

from atlas_live.explosive_engine import STAGE_LABELS


def build_diagnostics(rows: List[Dict[str, Any]], data_errors: int) -> Dict[str, Any]:
    """`rows` son los dicts que arma `_score_symbol()` para cada símbolo del
    universo escaneado (antes de truncar a TOP_N), cada uno con su
    `row["explosive"]`. `data_errors` son los símbolos que ni siquiera se
    pudieron puntuar (fallo de datos, no un filtro del Radar Explosivo)."""

    total_universe = len(rows) + data_errors

    funnel = []
    for stage_key, label in STAGE_LABELS:
        # `stage_trace` ya es acumulativo (contiene, en orden, cada etapa
        # que el símbolo superó), así que contar cuántas filas incluyen
        # esta etapa da directamente el conteo acumulado del embudo.
        count = sum(1 for r in rows if stage_key in r["explosive"]["stage_trace"])
        funnel.append({"stage": stage_key, "label": label, "count": count})

    final_eligible = [r for r in rows if r["explosive"]["eligible"]]

    return {
        "total_universe": total_universe,
        "data_errors": data_errors,
        "funnel": funnel,
        "final_eligible_count": len(final_eligible),
        "table": _build_table(rows),
    }


def _format_reason(row: Dict[str, Any]) -> str:
    exp = row["explosive"]
    if exp["eligible"]:
        return "; ".join(exp["reasons"][:3]) if exp["reasons"] else "Cumple todos los filtros"
    return exp["excluded_reason"] or "Descartada"


def _to_table_row(row: Dict[str, Any], status: str) -> Dict[str, Any]:
    exp = row["explosive"]
    metrics = exp.get("metrics") or {}
    return {
        "symbol": row["symbol"],
        "name": row.get("name"),
        "status": status,
        "reason": _format_reason(row),
        "score": exp.get("score"),
        "market_cap": metrics.get("market_cap"),
        "gap_pct": metrics.get("gap_pct"),
        "relative_volume": metrics.get("relative_volume"),
        "failed_stage": exp.get("failed_stage"),
    }


def _build_table(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Todas las aprobadas (ordenadas por puntaje) + las descartadas que
    llegaron más lejos en el embudo antes de quedar fuera (las más
    "cerca" de haber calificado, no un muestreo al azar)."""
    approved = [r for r in rows if r["explosive"]["eligible"]]
    excluded = [r for r in rows if not r["explosive"]["eligible"]]

    near_misses = sorted(excluded, key=lambda r: len(r["explosive"]["stage_trace"]), reverse=True)[:15]

    table = [
        _to_table_row(r, "Aprobada")
        for r in sorted(approved, key=lambda r: r["explosive"]["score"] or 0, reverse=True)
    ]
    table += [_to_table_row(r, "Excluida") for r in near_misses]
    return table
