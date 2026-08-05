"""Generador de Propuestas de recalibración -- Entregable Nº5 del Memory Engine.

Corre `base_rates.compute_base_rate` (Entregable 4) sobre una grilla de
condiciones acotada y justificada por la evidencia ya conocida de
`RADAR_EXPLOSIVO_V2.md` (RVOL, Gap% y Volatilidad como señales
individuales fuertes; Precio y Market Cap como señales más débiles),
y devuelve únicamente las condiciones que resultan **confiables** (las
tres condiciones de Entregable 4) como `CalibrationProposal` -- mismo
espíritu que `atlas.learning.calibration_advisor.CalibrationAdvisor` de
Atlas Core: candidatas con evidencia, nunca una recomendación de valor
específico, y **nunca se aplica nada solo**. Aplicar cualquier propuesta a
`explosive_config.json`/`explosive_factors.py` requiere su propia
propuesta formal, fuera de este módulo (ver MEMORY_ENGINE.md).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from atlas_live.memory import base_rates as br

# Grilla de condiciones a evaluar -- acotada a las métricas que ya existen
# en el Memory Store, con bandas informadas por la auditoría previa (no
# arbitrarias): RVOL y Gap% son las señales individuales más fuertes según
# Explosive DNA y el mapa de filtros; Volatilidad tiene separación fuerte
# pero rara vez excluye ganadoras reales; Precio y Market Cap son señales
# más débiles, incluidas como control. No es una grilla exhaustiva de
# combinaciones cruzadas -- eso queda para una iteración futura si esta
# primera ronda resulta útil.
CONDITION_GRID: List[br.Condition] = [
    br.Condition(metric_ranges={"relative_volume": (0.5, 1.0)}, label="relative_volume en [0.5, 1.0)"),
    br.Condition(metric_ranges={"relative_volume": (1.0, 2.5)}, label="relative_volume en [1.0, 2.5)"),
    br.Condition(metric_ranges={"relative_volume": (2.5, 5.0)}, label="relative_volume en [2.5, 5.0)"),
    br.Condition(metric_ranges={"relative_volume": (5.0, 10.0)}, label="relative_volume en [5.0, 10.0)"),
    br.Condition(metric_ranges={"relative_volume": (10.0, None)}, label="relative_volume >= 10.0"),
    br.Condition(metric_ranges={"gap_pct": (2.0, 5.0)}, label="gap_pct en [2.0, 5.0)"),
    br.Condition(metric_ranges={"gap_pct": (5.0, 10.0)}, label="gap_pct en [5.0, 10.0)"),
    br.Condition(metric_ranges={"gap_pct": (10.0, None)}, label="gap_pct >= 10.0"),
    br.Condition(metric_ranges={"volatility_score": (50.0, 70.0)}, label="volatility_score en [50, 70)"),
    br.Condition(metric_ranges={"volatility_score": (70.0, 90.0)}, label="volatility_score en [70, 90)"),
    br.Condition(metric_ranges={"volatility_score": (90.0, None)}, label="volatility_score >= 90"),
    br.Condition(metric_ranges={"price": (None, 5.0)}, label="price <= 5"),
    br.Condition(metric_ranges={"market_cap": (None, 300_000_000)}, label="market_cap < 300M (micro, ref. size_factor)"),
    # Combinación ya sugerida por el hallazgo "las 14 detecciones reales
    # tienen RVOL y gap simultáneamente altos" (VALIDATION_RESULTS.md).
    br.Condition(metric_ranges={"relative_volume": (5.0, None), "gap_pct": (5.0, None)}, label="relative_volume>=5 AND gap_pct>=5"),
]


@dataclass(frozen=True)
class CalibrationProposal:
    """Candidata de recalibración con evidencia adjunta -- nunca se aplica
    desde acá. Formato análogo a `atlas.learning.calibration_advisor.CalibrationProposal`."""

    condition_label: str
    condition: br.Condition
    category: str
    evidence: br.BaseRateResult
    suggested_action: str
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def generate_proposals(
    observations: List[Dict[str, Any]],
    category: str = "EXPLOSION",
    conditions: Optional[List[br.Condition]] = None,
) -> List[CalibrationProposal]:
    """Evalúa la grilla de condiciones (o una provista) contra `observations`
    y devuelve únicamente las que resultaron confiables, ordenadas por
    límite inferior de Wilson descendente (la evidencia más sólida primero)."""
    conditions = conditions if conditions is not None else CONDITION_GRID
    baseline = br.compute_population_base_rate(observations, category)
    resultados = br.compute_base_rates_for_conditions(observations, conditions, category, baseline=baseline)

    propuestas = []
    for condicion, resultado in zip(conditions, resultados):
        if not resultado.reliable:
            continue
        lift = (resultado.win_rate / resultado.baseline_win_rate) if resultado.baseline_win_rate > 0 else float("inf")
        propuestas.append(
            CalibrationProposal(
                condition_label=resultado.condition_label,
                condition=condicion,
                category=category,
                evidence=resultado,
                suggested_action=(
                    f"Candidata a factor/condición de ranking: '{resultado.condition_label}' se asocia con "
                    f"{category} a una tasa {lift:.1f}x superior al baseline poblacional "
                    f"({resultado.win_rate*100:.2f}% vs {resultado.baseline_win_rate*100:.2f}%), "
                    f"con evidencia estadísticamente confiable (n={resultado.sample_size}). "
                    f"No se aplica automáticamente -- requiere propuesta formal aparte."
                ),
            )
        )

    propuestas.sort(key=lambda p: p.evidence.wilson_lower_bound or 0.0, reverse=True)
    return propuestas
