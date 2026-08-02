"""Motor de tasas base + validación estadística -- Entregable Nº4 del Memory Engine.

Calcula, a partir de las observaciones ya guardadas en el Memory Store
(Entregable 1, pobladas por el backfill del Entregable 3), la tasa base
real de una categoría de resultado (ej. EXPLOSION) para un subconjunto de
condiciones (ej. "relative_volume entre 2.5 y 267.8, sector Technology,
sesión regular"), con la misma validación estadística de tres condiciones
que ya usa `atlas.learning.pattern_evolution` en Atlas Core: tamaño de
muestra, significancia (intervalo de Wilson por encima del baseline) y
consistencia temporal (evidencia en más de una ventana de fechas).

Reimplementa la fórmula de Wilson localmente (no importa
`atlas.learning.pattern_evolution._wilson_lower_bound`, que es privada al
módulo de Core) para que el Memory Engine no dependa de símbolos internos
de un paquete congelado -- misma fórmula exacta, documentada como tal,
consistente con el patrón ya usado en `rvol_role_comparison.py`
("misma fórmula que ... en explosive_factors.py", reimplementada en vez
de importada).

100% de solo lectura sobre el Memory Store: ningún función de este módulo
escribe nada. No aplica ninguna recalibración -- eso es el Entregable 5.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from atlas_live.memory import store

# Mismos valores que atlas.learning.pattern_evolution (MIN_SAMPLE_SIZE=10,
# WILSON_Z=1.96 ~= 95% de confianza) -- mismo criterio de rigor estadístico
# del proyecto, no un umbral nuevo inventado para este módulo.
MIN_SAMPLE_SIZE = 10
WILSON_Z = 1.96


def _wilson_lower_bound(successes: int, n: int, z: float = WILSON_Z) -> float:
    """Límite inferior del intervalo de Wilson para una proporción. Sin
    librerías externas -- misma fórmula que
    `atlas.learning.pattern_evolution._wilson_lower_bound`."""
    if n == 0:
        return 0.0
    phat = successes / n
    denominator = 1 + (z ** 2) / n
    center = phat + (z ** 2) / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + (z ** 2) / (4 * n)) / n)
    return max(0.0, (center - margin) / denominator)


@dataclass(frozen=True)
class Condition:
    """Subconjunto de observaciones por rangos de métrica y/o contexto.
    Todo campo omitido significa "sin restricción en esa dimensión".

    `metric_ranges` usa las mismas claves que `store.METRIC_FIELDS`
    (price, gap_pct, change_pct, relative_volume, dollar_volume,
    volatility_score, market_cap), cada una como (mínimo, máximo) --
    cualquiera de los dos puede ser `None` (sin límite de ese lado)."""

    metric_ranges: Dict[str, Tuple[Optional[float], Optional[float]]] = field(default_factory=dict)
    sector: Optional[str] = None
    market_cap_bucket: Optional[str] = None
    session: Optional[str] = None
    label: str = ""

    def matches(self, obs: Dict[str, Any]) -> bool:
        for metric, (lo, hi) in self.metric_ranges.items():
            value = obs.get(metric)
            if value is None:
                return False
            if lo is not None and value < lo:
                return False
            if hi is not None and value > hi:
                return False
        if self.sector is not None and obs.get("sector") != self.sector:
            return False
        if self.market_cap_bucket is not None and obs.get("market_cap_bucket") != self.market_cap_bucket:
            return False
        if self.session is not None and obs.get("session") != self.session:
            return False
        return True


@dataclass(frozen=True)
class BaseRateResult:
    """Resultado auditable -- nunca se reporta un win_rate sin su muestra
    y su intervalo de confianza."""

    condition_label: str
    category: str
    sample_size: int
    successes: int
    win_rate: Optional[float]
    wilson_lower_bound: Optional[float]
    baseline_win_rate: float
    recent_sample_size: int
    reliable: bool
    reason: str


def compute_population_base_rate(observations: List[Dict[str, Any]], category: str) -> float:
    """Tasa de la categoría sobre TODA la población de observaciones
    provistas, sin ninguna condición -- baseline por defecto contra el que
    se mide si una condición aporta señal real, no solo ruido."""
    if not observations:
        return 0.0
    hits = sum(1 for o in observations if o.get("category") == category)
    return hits / len(observations)


def compute_base_rate(
    observations: List[Dict[str, Any]],
    condition: Condition,
    category: str,
    baseline: Optional[float] = None,
) -> BaseRateResult:
    """Aplica las tres condiciones de confiabilidad (mismo criterio que
    `pattern_evolution.is_statistically_reliable`), sobre el subconjunto
    de `observations` que matchea `condition`."""
    matched = [o for o in observations if condition.matches(o)]
    sample_size = len(matched)
    successes = sum(1 for o in matched if o.get("category") == category)
    win_rate = successes / sample_size if sample_size else None
    baseline_rate = baseline if baseline is not None else compute_population_base_rate(observations, category)

    def _result(recent_sample_size: int, reliable: bool, reason: str, lower_bound: Optional[float] = None) -> BaseRateResult:
        return BaseRateResult(
            condition_label=condition.label,
            category=category,
            sample_size=sample_size,
            successes=successes,
            win_rate=win_rate,
            wilson_lower_bound=lower_bound,
            baseline_win_rate=baseline_rate,
            recent_sample_size=recent_sample_size,
            reliable=reliable,
            reason=reason,
        )

    # Condición 1 -- tamaño de muestra.
    if sample_size < MIN_SAMPLE_SIZE:
        return _result(0, False, f"Muestra insuficiente ({sample_size} < {MIN_SAMPLE_SIZE})")

    # Condición 2 -- significancia: el límite inferior de Wilson debe
    # superar el baseline, no alcanza con que el punto estimado sea mayor.
    lower_bound = _wilson_lower_bound(successes, sample_size)
    if lower_bound <= baseline_rate:
        return _result(
            0, False,
            f"No supera el baseline con confianza estadística "
            f"(límite inferior {lower_bound:.3f} <= baseline {baseline_rate:.3f})",
            lower_bound,
        )

    # Condición 3 -- consistencia temporal: la evidencia debe repartirse en
    # más de una ventana de fechas, no ser un solo grupo de días
    # consecutivos. "Reciente" = la mitad cronológicamente más nueva de las
    # fechas distintas presentes en el subconjunto que matchea la condición.
    fechas = sorted({o["date"] for o in matched if o.get("date")})
    if len(fechas) < 2:
        return _result(0, False, "Sin evidencia distribuida en más de una ventana temporal (una sola fecha)", lower_bound)

    corte = fechas[len(fechas) // 2]
    recientes = [o for o in matched if o.get("date", "") >= corte]
    recent_sample_size = len(recientes)
    if recent_sample_size < MIN_SAMPLE_SIZE:
        return _result(
            recent_sample_size, False,
            f"Muestra reciente insuficiente ({recent_sample_size} < {MIN_SAMPLE_SIZE})",
            lower_bound,
        )

    return _result(
        recent_sample_size, True,
        f"Cumple las tres condiciones: muestra={sample_size}, "
        f"límite inferior de Wilson={lower_bound:.3f} > baseline={baseline_rate:.3f}, "
        f"evidencia reciente disponible (n={recent_sample_size})",
        lower_bound,
    )


def compute_base_rates_for_conditions(
    observations: List[Dict[str, Any]],
    conditions: List[Condition],
    category: str,
    baseline: Optional[float] = None,
) -> List[BaseRateResult]:
    """Corre `compute_base_rate` para una lista de condiciones contra la
    misma población -- mismo baseline poblacional para todas si no se
    provee uno explícito, para que sean comparables entre sí."""
    if baseline is None:
        baseline = compute_population_base_rate(observations, category)
    return [compute_base_rate(observations, c, category, baseline) for c in conditions]


def load_all_observations() -> List[Dict[str, Any]]:
    """Punto de entrada real: carga todas las observaciones del Memory
    Store. Requiere que el backfill (Entregable 3) ya haya corrido --
    sobre una base vacía, cualquier condición devuelve `reliable=False`
    por muestra insuficiente, nunca un resultado inventado."""
    return store.get_observations()
