"""Ensambla los dos bloques que la Cabina necesita mostrar SEPARADOS
(2026-08-15, ver `PROPUESTA_MADUREZ_APRENDIZAJE.md`):

  - `get_live_learning_summary()` -- Aprendizaje en Vivo: SOLO datos de
    `atlas_live.radar.candidate_registry` (CAPA 2, arranca en cero tras el
    reset). Nunca lee la Base Histórica.
  - `get_historical_reference_summary()` -- Base Histórica de Referencia:
    SOLO datos de `atlas_live.reference.reference_registry`. Nunca
    contribuye a precisión, aciertos ni madurez.

Cada número de precisión viaja siempre con numerador y denominador
explícitos -- nunca un porcentaje aislado (pedido explícito del usuario).
"""

from typing import Any, Dict, Optional

from atlas_live.learning import maturity as mat
from atlas_live.memory import market_hours
from atlas_live.radar import candidate_registry as reg


def _precision_str(aciertos: Optional[int], evaluables: Optional[int]) -> Optional[str]:
    if not evaluables:
        return None
    pct = round(100 * (aciertos or 0) / evaluables, 1)
    return f"{aciertos}/{evaluables} = {pct}%"


def get_live_learning_summary(market_date: Optional[str] = None) -> Dict[str, Any]:
    """Aprendizaje en Vivo -- arranca en cero el lunes, crece solo con
    observaciones nuevas evaluadas mientras Atlas está en funcionamiento."""
    market_date = market_date or market_hours.market_date()

    hoy = reg.get_daily_summary(market_date)
    acumulada = reg.cumulative_precision()
    reciente = reg.recent_precision()
    reporte_madurez = mat.compute_maturity()

    hoy_evaluables = hoy.get("n_evaluables") if hoy else 0
    hoy_aciertos = hoy.get("n_aciertos") if hoy else 0

    return {
        "market_date": market_date,
        "hoy": {
            "estudiadas": (hoy or {}).get("n_estudiadas"),
            "candidatas": (hoy or {}).get("n_candidatas") or 0,
            "senales": (hoy or {}).get("n_senales") or 0,
            "evaluables": hoy_evaluables or 0,
            "aciertos": hoy_aciertos or 0,
            "fallos": (hoy_evaluables or 0) - (hoy_aciertos or 0) if hoy_evaluables else 0,
            "tardias": (hoy or {}).get("n_tardias") or 0,
            "falsos_positivos": (hoy or {}).get("n_falsos_positivos") or 0,
            "precision": _precision_str(hoy_aciertos, hoy_evaluables),
        },
        "acumulada": {
            "dias": acumulada.get("n_dias") or 0,
            "estudiadas": acumulada.get("estudiadas") or 0,
            "candidatas": acumulada.get("candidatas") or 0,
            "senales": acumulada.get("senales") or 0,
            "evaluables": acumulada.get("evaluables") or 0,
            "aciertos": acumulada.get("aciertos") or 0,
            "tardias": acumulada.get("tardias") or 0,
            "reached_20": acumulada.get("reached_20") or 0,
            "reached_50": acumulada.get("reached_50") or 0,
            "reached_100": acumulada.get("reached_100") or 0,
            "precision": _precision_str(acumulada.get("aciertos"), acumulada.get("evaluables")),
        },
        "reciente": {
            "dias_incluidos": reciente.get("dias_incluidos") or 0,
            "desde": reciente.get("desde"), "hasta": reciente.get("hasta"),
            "precision": _precision_str(reciente.get("aciertos"), reciente.get("evaluables")),
        },
        "madurez": {
            "estado": reporte_madurez.global_level_label,
            "nivel": reporte_madurez.global_level,
            "eje_limitante": reporte_madurez.limiting_axis.label,
            "explicacion": reporte_madurez.limiting_explanation,
            "ejes": [
                {
                    "clave": a.key, "nombre": a.label, "estado": a.level_label, "nivel": a.level,
                    "evidencia": a.evidence, "explicacion": a.explanation,
                }
                for a in reporte_madurez.axes
            ],
        },
    }


def get_historical_reference_summary() -> Dict[str, Any]:
    """Base Histórica de Referencia -- NUNCA se presenta como aprendizaje de
    Atlas, solo como contexto para comparar patrones."""
    from atlas_live.reference import reference_registry as ref

    counts = ref.counts()
    meta = ref.get_meta()
    fases = ref.historical_phase_stats()

    return {
        "es_aprendizaje_de_atlas": False,
        "nota": "Referencia histórica (backtest), no precisión en vivo -- ver PROPUESTA_MADUREZ_APRENDIZAJE.md.",
        "simbolos_procesados": counts.get("simbolos_procesados") or 0,
        "universo_total": meta.get("universe_total"),
        # Evaluables reales = filas con features Y outcome para el mismo
        # symbol+date (nunca el conteo crudo de daily_outcome -- infla el
        # número con días que no tienen timing_deteccion clasificado).
        "observaciones_evaluables": counts.get("evaluables_features_y_outcome") or 0,
        "patrones_por_timing": fases,
    }
