"""Panel de Evolución de Atlas (2026-08-07, ver DECISION_LOG.md).

Demuestra, con evidencia real, cómo evolucionan la precisión y el
aprendizaje de Atlas con el paso de los días. Separa 3 conceptos que
nunca se mezclan:
  1. Precisión del modelo (aciertos por período + precisión histórica).
  2. Rendimiento financiero (win rate, profit factor, expectativa, etc.).
  3. Evolución del aprendizaje (trayectorias, muestras, condiciones con
     evidencia, nivel de aprendizaje).

100% de solo lectura y de REUTILIZACIÓN -- no reimplementa ningún cálculo
ni toca la lógica del Radar, el Memory Engine ni el Motor Predictivo:
  - Precisión y métricas financieras salen de `performance_panel` (única
    fuente de verdad; "acierto" = `category == "EXPLOSION"` vía el
    Clasificador del proyecto, no una definición nueva).
  - La evolución del aprendizaje se ensambla con contadores reales ya
    existentes: Exit Journal (`get_all_symbol_dates`, `count_trajectory_samples`),
    Memory Store (`count_observations`) y el resumen del Memory Engine
    (`get_memory_engine_summary`).

Ningún valor se fabrica: si un dato no existe todavía, queda `None` y el
frontend lo muestra como "No disponible".
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from atlas_live import performance_panel as pp
from atlas_live.memory import calibration_advisor as ca
from atlas_live.memory import exit_journal as ej
from atlas_live.memory import live_integration
from atlas_live.memory import market_hours
from atlas_live.memory import store


def _iso_week(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def _cerrados() -> List[Dict[str, Any]]:
    """Trayectorias del Exit Journal que ya cerraron su ventana (tienen
    retorno final real)."""
    return [r for r in ej.get_summaries_between() if r.get("final_return_pct") is not None]


# ---------------------------------------------------------------------------
# 1. Precisión del modelo
# ---------------------------------------------------------------------------

def _precision(now: datetime, global_perf: Dict[str, Any], cerrados: List[Dict[str, Any]]) -> Dict[str, Any]:
    hoy = market_hours.market_date(now)
    semana = _iso_week(hoy)
    mes = hoy[:7]

    # "acierto" = exactamente la misma definición que ya usa el proyecto
    # (Clasificador: EXPLOSION), reutilizando el helper de performance_panel.
    def es_acierto(fila: Dict[str, Any]) -> bool:
        return pp._categoria_real(fila) == "EXPLOSION"

    return {
        "aciertos_hoy": sum(1 for r in cerrados if r["date"] == hoy and es_acierto(r)),
        "aciertos_semana": sum(1 for r in cerrados if _iso_week(r["date"]) == semana and es_acierto(r)),
        "aciertos_mes": sum(1 for r in cerrados if r["date"][:7] == mes and es_acierto(r)),
        "aciertos_historico": sum(1 for r in cerrados if es_acierto(r)),
        "precision_historica_pct": global_perf["precision_del_modelo"]["tasa_acierto_pct"],
        "muestra_historica": global_perf["precision_del_modelo"]["muestra"],
    }


# ---------------------------------------------------------------------------
# 2. Rendimiento financiero (nunca mezclado con la precisión)
# ---------------------------------------------------------------------------

def _financiero(global_perf: Dict[str, Any], cerrados: List[Dict[str, Any]]) -> Dict[str, Any]:
    fin = dict(global_perf["rendimiento_financiero"])  # copia -- no muta el original
    retornos = [r["final_return_pct"] for r in cerrados]
    # mejor/peor operación GLOBAL (performance_panel solo trae las de hoy).
    fin["mejor_operacion_global_pct"] = max(retornos) if retornos else None
    fin["peor_operacion_global_pct"] = min(retornos) if retornos else None
    return fin


# ---------------------------------------------------------------------------
# 3. Evolución del aprendizaje
# ---------------------------------------------------------------------------

def _aprendizaje(now: datetime) -> Dict[str, Any]:
    resumen = live_integration.get_memory_engine_summary(now)
    casos = store.count_observations()
    confiables = resumen.get("reliable_condition_count")
    total_condiciones = len(ca.CONDITION_GRID)

    # Nivel de aprendizaje = fracción de las condiciones evaluadas que ya
    # alcanzaron confiabilidad estadística (límite inferior de Wilson por
    # encima del baseline). Es un dato real, no un compuesto arbitrario.
    # Si todavía no hay observaciones, es "No disponible" -- no se fabrica.
    if casos and total_condiciones > 0 and confiables is not None:
        nivel = round(confiables / total_condiciones * 100, 1)
    else:
        nivel = None

    return {
        "trayectorias_almacenadas": len(ej.get_all_symbol_dates()),
        "muestras_analizadas": ej.count_trajectory_samples(),
        "casos_similares_acumulados": casos,
        "condiciones_evidencia_suficiente": confiables,
        "condiciones_totales_evaluadas": total_condiciones,
        "nivel_aprendizaje_pct": nivel,
        "ultima_actualizacion": resumen.get("last_recalibrated_on"),
    }


def get_evolution(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Ensambla las 3 secciones del Panel de Evolución con datos reales.
    Reutiliza performance_panel para precisión y finanzas (única fuente de
    verdad) y contadores existentes para el aprendizaje."""
    now = now or datetime.now(timezone.utc)
    global_perf = pp.get_global_performance(now)
    cerrados = _cerrados()
    return {
        "date": market_hours.market_date(now),
        "precision_del_modelo": _precision(now, global_perf, cerrados),
        "rendimiento_financiero": _financiero(global_perf, cerrados),
        "evolucion_aprendizaje": _aprendizaje(now),
    }
