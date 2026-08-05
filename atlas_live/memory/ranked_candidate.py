"""Construcción de un candidato rankeado del Memory Engine, a partir de una
fila de ranking (`row['explosive']['metrics'/'score'/'eligible']` -- el
mismo formato que produce `scan_worker._score_symbol()` en vivo).

Portado desde `demo_ranking.py` de una rama paralela (2026-08-05):
`RankedCandidate` y `build_ranked_candidate()` no son código de demo, son
la lógica real que usa `live_integration.py` en cada ciclo. Lo que sí era
demo y no se portó: `build_ranking()` (leía archivos históricos de
`atlas_live/backtest/results_v1/*.json`, no versionados acá) y el CLI de
`__main__` que dependía de esa función -- ninguno de los dos tiene uso
posible sin esos archivos, así que no tenía sentido mantenerlos.

Combina dos fuentes, cada una de solo lectura, sin modificar nada:
  - Las métricas y el Score real de Radar Explosivo (Etapa B) que ya trae
    la fila (`row['explosive']`).
  - Las propuestas confiables del Memory Engine (`calibration_advisor`) --
    de ahí sale la Probabilidad, la Confianza, el Semáforo y la Evidencia
    histórica de cada símbolo.

Reglas de presentación, documentadas explícitamente (no hay "supuestos"
ocultos):
  - market_cap_bucket se deriva en el momento, sin tocar el Memory Store,
    con los mismos umbrales que Radar Explosivo ya tiene en
    `explosive_config.json` (small_cap_reference=$300M, large_cap_ceiling=
    $10B) -- no son números nuevos inventados acá.
  - Probabilidad = win_rate de la propuesta confiable con mayor límite
    inferior de Wilson que matchea al símbolo (la evidencia más sólida
    entre las que aplican). Si ninguna propuesta confiable matchea, el
    símbolo se marca explícitamente "sin evidencia histórica suficiente"
    -- nunca se le asigna una probabilidad de 0% ni se inventa un número.
  - Nivel de confianza: Alta (muestra >= 500), Media (100-499), Baja
    (10-99) -- todas las propuestas de la grilla ya tienen muestra >= 10
    por construcción (condición 1 de confiabilidad de `base_rates.py`);
    "Sin evidencia" si no matchea ninguna.
  - Semáforo: 🟢 si la mejor evidencia que matchea tiene una tasa >= 10x
    el baseline poblacional; 🟡 si matchea pero con lift menor; 🔴 si no
    matchea ninguna propuesta confiable.

Ranking Score de desempate (ver `ranking_score.py`): cuando varios
candidatos matchean la misma condición ganadora (Nivel 1, sin cambios),
el orden entre ellos usa 3 niveles adicionales de evidencia ya existente.
No reemplaza a Radar Explosivo ni modifica la detección -- solo decide el
orden entre candidatos que ya tenían la misma Probabilidad reportada.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from atlas_live.memory import calibration_advisor as ca
from atlas_live.memory import ranking_score as rs

MICRO_CAP_CEILING = 300_000_000       # explosive_config.json -> size_factor.small_cap_reference
LARGE_CAP_CEILING = 10_000_000_000    # explosive_config.json -> gates.large_cap_ceiling

CONFIDENCE_HIGH_MIN_SAMPLE = 500
CONFIDENCE_MEDIUM_MIN_SAMPLE = 100

SEMAFORO_VERDE_LIFT_MIN = 10.0


def market_cap_bucket(market_cap: Optional[float]) -> str:
    if market_cap is None:
        return "desconocido"
    if market_cap < MICRO_CAP_CEILING:
        return "micro"
    if market_cap < LARGE_CAP_CEILING:
        return "mid"
    return "large"


def _confidence_level(sample_size: int) -> str:
    if sample_size >= CONFIDENCE_HIGH_MIN_SAMPLE:
        return "Alta"
    if sample_size >= CONFIDENCE_MEDIUM_MIN_SAMPLE:
        return "Media"
    return "Baja"


@dataclass
class RankedCandidate:
    symbol: str
    score: Optional[float]
    eligible_radar: bool
    market_cap_bucket: str
    price: Optional[float]
    change_pct: Optional[float]
    # Trazabilidad de precio -- pasante desde `metrics`, igual que
    # `price`/`change_pct` de arriba: no participa en el cálculo del
    # Ranking Score. En esta rama Quote no trae estos campos (Atlas Core
    # sigue congelado, ver explosive_engine.py) así que quedan en None.
    price_type: str
    price_source: str
    market_state: Optional[str]
    price_regular: Optional[float]
    price_premarket: Optional[float]
    price_afterhours: Optional[float]
    price_overnight: Optional[float]
    price_as_of: Optional[str]
    probability_pct: Optional[float]
    confidence: str
    semaforo: str
    explanation: str
    evidence_condition: Optional[str]
    evidence_sample_size: int
    evidence_wilson_lower_bound_pct: Optional[float]
    evidence_baseline_pct: float
    ranking_score: rs.RankingScore
    tie_break_note: Optional[str]
    sort_key: float  # se mantiene por compatibilidad de lectura del Nivel 1; el orden real usa ranking_score
    # Motivo exacto por el que Radar Explosivo rechazó el símbolo, cuando
    # `eligible_radar` es False -- para poder explicar el rechazo real,
    # no solo la falta de evidencia del Memory Engine.
    radar_excluded_reason: Optional[str]


def build_ranked_candidate(
    row: Dict[str, Any],
    proposals: List[ca.CalibrationProposal],
    condition_value_cache: Dict[str, Dict[str, List[float]]],
    baseline: float,
    category: str = "EXPLOSION",
) -> RankedCandidate:
    """Arma UN candidato rankeado a partir de una fila con forma
    `row['explosive']['metrics'/'score'/'eligible']` -- el mismo formato
    que produce `scan_worker._score_symbol()` en cada ciclo en vivo."""
    metrics = row["explosive"]["metrics"]
    obs = dict(metrics)  # mismas claves que Condition.matches espera (relative_volume, gap_pct, etc.)

    score, best = rs.compute_ranking_score(
        obs, proposals, condition_value_cache,
        radar_score=row["explosive"]["score"],
        radar_eligible=bool(row["explosive"]["eligible"]),
    )

    if best is None:
        probability_pct = None
        confidence = "Sin evidencia"
        semaforo = "🔴"
        explanation = (
            "Ninguna condición con evidencia histórica confiable coincide con las métricas de "
            "hoy de este símbolo -- no hay base para asignarle una probabilidad elevada."
        )
        evidence_condition = None
        evidence_sample_size = 0
        evidence_wlb_pct = None
        tie_break_note = None
    else:
        e = best.evidence
        probability_pct = e.win_rate * 100 if e.win_rate is not None else None
        confidence = _confidence_level(e.sample_size)
        lift = (e.win_rate / e.baseline_win_rate) if e.baseline_win_rate > 0 else float("inf")
        semaforo = "🟢" if lift >= SEMAFORO_VERDE_LIFT_MIN else "🟡"
        explanation = (
            f"Coincide con la condición '{best.condition_label}', que tuvo una tasa de {category} "
            f"de {probability_pct:.2f}% ({lift:.1f}x el promedio general de {baseline*100:.2f}%), "
            f"con {e.sample_size} observaciones de respaldo y un límite inferior de Wilson de "
            f"{e.wilson_lower_bound*100:.2f}% (el peor caso estadísticamente razonable, no solo el promedio)."
        )
        evidence_condition = best.condition_label
        evidence_sample_size = e.sample_size
        evidence_wlb_pct = e.wilson_lower_bound * 100 if e.wilson_lower_bound is not None else None
        tie_break_note = (
            f"Desempate (Ranking Score): {score.nivel2_condiciones_adicionales} condición(es) "
            f"confiable(s) adicional(es) matcheada(s); percentil {score.nivel3_percentil_dentro_de_banda*100:.0f} "
            f"dentro de la banda de '{best.condition_label}'; "
            + (f"score real de Radar Explosivo={score.nivel4_score_radar:.1f}."
               if score.nivel4_score_radar else "sin score real de Radar Explosivo (no elegible).")
        )

    return RankedCandidate(
        symbol=row["symbol"],
        score=row["explosive"]["score"],
        eligible_radar=bool(row["explosive"]["eligible"]),
        market_cap_bucket=market_cap_bucket(metrics.get("market_cap")),
        price=metrics.get("price"),
        change_pct=metrics.get("change_pct"),
        price_type=metrics.get("price_type") or "unknown",
        price_source=metrics.get("source") or "yahoo_finance",
        market_state=metrics.get("market_state"),
        price_regular=metrics.get("price_regular"),
        price_premarket=metrics.get("price_premarket"),
        price_afterhours=metrics.get("price_afterhours"),
        price_overnight=metrics.get("price_overnight"),
        price_as_of=metrics.get("price_as_of"),
        probability_pct=probability_pct,
        confidence=confidence,
        semaforo=semaforo,
        explanation=explanation,
        evidence_condition=evidence_condition,
        evidence_sample_size=evidence_sample_size,
        evidence_wilson_lower_bound_pct=evidence_wlb_pct,
        evidence_baseline_pct=baseline * 100,
        ranking_score=score,
        tie_break_note=tie_break_note,
        sort_key=score.nivel1_wilson_lower_bound,
        radar_excluded_reason=row["explosive"].get("excluded_reason"),
    )
