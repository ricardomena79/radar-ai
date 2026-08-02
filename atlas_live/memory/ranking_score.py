"""Ranking Score de desempate -- propuesta aprobada el 2026-08-02 (ver
MEMORY_ENGINE.md, sección "RANKING SCORE DE DESEMPATE").

**Alcance explícito, para que no se confunda con otra cosa**: este módulo
NO reemplaza a Radar Explosivo ni modifica la detección. No cambia qué
símbolo es `eligible`, no cambia ningún gate, no cambia ningún peso de
`explosive_config.json`/`explosive_factors.py`, no agrega ninguna fuente
de datos nueva. Su única responsabilidad es **desempatar** entre
candidatos que ya tienen la misma probabilidad histórica (Nivel 1, el
límite inferior de Wilson de la mejor condición confiable que matchean --
ese criterio no se toca). Todo lo que este módulo agrega es orden dentro
de un empate ya existente, nunca cambia si un candidato tiene o no
evidencia, ni cuál es su probabilidad reportada.

Las 4 variables del Ranking Score, evaluadas en ESTE ORDEN ESTRICTO (no es
una suma ponderada -- cada nivel solo decide los empates que el anterior
no resolvió, evitando inventar coeficientes sin evidencia):

  1. Límite inferior de Wilson de la mejor condición confiable (sin
     cambios -- ya calculado por `base_rates`/`calibration_advisor`).
  2. Cantidad de condiciones confiables ADICIONALES que el mismo símbolo
     matchea a la vez (evidencia convergente ya calculada, hoy
     descartada).
  3. Percentil de la métrica de la condición ganadora, dentro de la
     distribución de esa misma métrica entre los símbolos que ya
     matchearon esa condición en el Memory Store (mismo dato ya
     calculado por Radar Explosivo, solo que hoy no se usa para
     distinguir "raspó el umbral" de "está muy adentro").
  4. Score real de Radar Explosivo (Etapa B), si el símbolo es
     `eligible=True` -- si no lo es, este nivel simplemente no aplica
     (no penaliza, no participa).
"""

import bisect
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from atlas_live.memory import base_rates as br
from atlas_live.memory import calibration_advisor as ca


class RankingScore(NamedTuple):
    """Clave de orden de 4 niveles -- se compara lexicográficamente
    (Python ya lo hace al comparar tuplas), nunca se combina en un solo
    número con pesos inventados."""

    nivel1_wilson_lower_bound: float
    nivel2_condiciones_adicionales: int
    nivel3_percentil_dentro_de_banda: float
    nivel4_score_radar: float


def build_condition_value_cache(
    observations: List[Dict[str, Any]],
    proposals: List[ca.CalibrationProposal],
) -> Dict[str, Dict[str, List[float]]]:
    """Para cada condición confiable, y cada métrica que esa condición
    restringe, los valores de esa métrica (ordenados) entre TODAS las
    observaciones del Memory Store que ya matchean esa condición --
    para poder ubicar a un candidato nuevo dentro de esa misma
    distribución (Nivel 3). Se calcula una sola vez por corrida, no por
    candidato -- todos los candidatos que comparten una condición
    comparten también esta distribución."""
    cache: Dict[str, Dict[str, List[float]]] = {}
    for proposal in proposals:
        condition = proposal.condition
        matched = [o for o in observations if condition.matches(o)]
        por_metrica: Dict[str, List[float]] = {}
        for metric in condition.metric_ranges:
            valores = sorted(o[metric] for o in matched if o.get(metric) is not None)
            por_metrica[metric] = valores
        cache[proposal.condition_label] = por_metrica
    return cache


def _percentile_within(value: Optional[float], sorted_values: List[float]) -> float:
    if value is None or not sorted_values:
        return 0.0
    idx = bisect.bisect_left(sorted_values, value)
    return idx / len(sorted_values)


def compute_ranking_score(
    obs: Dict[str, Any],
    proposals: List[ca.CalibrationProposal],
    condition_value_cache: Dict[str, Dict[str, List[float]]],
    radar_score: Optional[float],
    radar_eligible: bool,
) -> Tuple[RankingScore, Optional[ca.CalibrationProposal]]:
    """Calcula el Ranking Score de un candidato. Devuelve también cuál fue
    la condición ganadora (Nivel 1), para que quien llame pueda seguir
    reportando la Probabilidad/Confianza/Semáforo exactamente como antes
    -- este módulo no cambia esos campos, solo agrega el desempate."""
    matches = [p for p in proposals if p.condition.matches(obs)]

    if not matches:
        return RankingScore(-1.0, 0, 0.0, 0.0), None

    best = max(matches, key=lambda p: p.evidence.wilson_lower_bound or 0.0)
    nivel1 = best.evidence.wilson_lower_bound or 0.0
    nivel2 = len(matches) - 1  # condiciones adicionales, sin contar la ganadora

    valores_por_metrica = condition_value_cache.get(best.condition_label, {})
    percentiles = [
        _percentile_within(obs.get(metric), valores)
        for metric, valores in valores_por_metrica.items()
    ]
    nivel3 = sum(percentiles) / len(percentiles) if percentiles else 0.0

    nivel4 = radar_score if (radar_eligible and radar_score is not None) else 0.0

    return RankingScore(nivel1, nivel2, nivel3, nivel4), best
