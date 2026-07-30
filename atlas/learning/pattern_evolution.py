"""Pattern Evolution: mide si los patrones conocidos siguen siendo confiables o
perdieron vigencia, y propone transiciones de estado -- nunca las aplica.

De solo lectura, siempre. Lee patrones de PatternRegistry (Knowledge Base)
vía sus métodos de lectura únicamente (`get_pattern`, `list_patterns`,
`get_transition_history`). Nunca llama a `register_pattern()` ni a
`transition_state()`: esa aplicación es responsabilidad exclusiva de
Calibration Manager, una vez que un humano aprueba la propuesta.

Contrato de evidencia: cada Pattern acumula, en su campo `evidence`, las
claves que este módulo necesita para evaluarlo:
  - sample_size (int): muestra histórica acumulada.
  - win_rate (float, 0-1): tasa de éxito histórica acumulada.
  - recent_sample_size (int, opcional): muestra de la ventana reciente.
  - recent_win_rate (float, opcional): tasa de éxito de la ventana reciente.
  - baseline_win_rate (float, opcional): tasa de éxito de referencia contra
    la que se compara (si no está, se usa DEFAULT_BASELINE_WIN_RATE).
Si estas claves no están presentes, el patrón se reporta como evidencia
insuficiente -- nunca se inventa un valor.

Validación de confiabilidad estadística (tres condiciones, las tres a la
vez): tamaño de muestra, significancia (el límite inferior del intervalo
de Wilson debe superar el baseline -- no alcanza con "ser distinto", tiene
que ser mejor incluso en el escenario pesimista), y consistencia temporal
(evidencia en más de una ventana: histórica y reciente, ambas con muestra
suficiente).

Detección de decadencia: la ventana reciente cae por debajo del umbral
respecto al histórico, o ya no se distingue del baseline.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from atlas.knowledge.pattern_store import (
    PATTERN_ACTIVE,
    PATTERN_DECAYING,
    PATTERN_INACTIVE,
    PATTERN_OBSERVATION,
    PATTERN_REACTIVATED,
    Pattern,
    PatternRegistry,
)

MIN_SAMPLE_SIZE = 10
DEFAULT_BASELINE_WIN_RATE = 0.50
DECAY_DROP_THRESHOLD = 0.15  # caída de 15 puntos porcentuales o más = decadencia
WILSON_Z = 1.96  # ~95% de confianza


def _wilson_lower_bound(successes: int, n: int, z: float = WILSON_Z) -> float:
    """Límite inferior del intervalo de Wilson para una proporción. Sin librerías externas."""
    if n == 0:
        return 0.0
    phat = successes / n
    denominator = 1 + (z ** 2) / n
    center = phat + (z ** 2) / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + (z ** 2) / (4 * n)) / n)
    return max(0.0, (center - margin) / denominator)


def is_statistically_reliable(pattern: Pattern) -> Tuple[bool, str]:
    """Aplica las tres condiciones de confiabilidad. Nunca concluye con muestra chica."""
    evidence = pattern.evidence
    sample_size = evidence.get("sample_size")
    win_rate = evidence.get("win_rate")

    if sample_size is None or win_rate is None:
        return False, "Sin sample_size/win_rate en la evidencia acumulada"
    if sample_size < MIN_SAMPLE_SIZE:
        return False, f"Muestra histórica insuficiente ({sample_size} < {MIN_SAMPLE_SIZE})"

    baseline = evidence.get("baseline_win_rate", DEFAULT_BASELINE_WIN_RATE)
    successes = round(win_rate * sample_size)
    lower_bound = _wilson_lower_bound(successes, sample_size)
    if lower_bound <= baseline:
        return False, (
            f"No supera el baseline con confianza estadística "
            f"(límite inferior {lower_bound:.3f} <= baseline {baseline:.3f})"
        )

    recent_sample_size = evidence.get("recent_sample_size")
    if recent_sample_size is None or recent_sample_size < MIN_SAMPLE_SIZE:
        return False, "Sin evidencia distribuida en más de una ventana temporal (falta muestra reciente)"

    return True, (
        f"Cumple las tres condiciones: muestra={sample_size}, "
        f"límite inferior de Wilson={lower_bound:.3f} > baseline={baseline:.3f}, "
        f"y evidencia reciente disponible (n={recent_sample_size})"
    )


def has_decayed(pattern: Pattern) -> Tuple[bool, str]:
    """Compara la ventana reciente contra el histórico acumulado."""
    evidence = pattern.evidence
    win_rate = evidence.get("win_rate")
    recent_win_rate = evidence.get("recent_win_rate")
    recent_sample_size = evidence.get("recent_sample_size")

    if win_rate is None or recent_win_rate is None or recent_sample_size is None:
        return False, "Evidencia insuficiente para evaluar decadencia"
    if recent_sample_size < MIN_SAMPLE_SIZE:
        return False, f"Muestra reciente insuficiente ({recent_sample_size} < {MIN_SAMPLE_SIZE})"

    drop = win_rate - recent_win_rate
    if drop >= DECAY_DROP_THRESHOLD:
        return True, f"La ventana reciente cae {drop * 100:.1f} puntos porcentuales respecto al histórico"

    baseline = evidence.get("baseline_win_rate", DEFAULT_BASELINE_WIN_RATE)
    recent_successes = round(recent_win_rate * recent_sample_size)
    recent_lower_bound = _wilson_lower_bound(recent_successes, recent_sample_size)
    if recent_lower_bound <= baseline:
        return True, (
            f"La ventana reciente ya no se distingue del baseline con confianza estadística "
            f"(límite inferior {recent_lower_bound:.3f} <= baseline {baseline:.3f})"
        )

    return False, "Sin evidencia de decadencia"


@dataclass(frozen=True)
class PatternEvolutionReport:
    """Propuesta de transición para un patrón. Nunca se aplica desde acá."""

    pattern_key: str
    current_state: str
    proposed_state: Optional[str]  # None = no se propone ningún cambio
    reason: str
    evidence: Dict[str, Any]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PatternEvolution:
    """Evalúa la vigencia de los patrones conocidos y propone transiciones de estado."""

    def __init__(self, pattern_registry: PatternRegistry) -> None:
        self._registry = pattern_registry

    def evaluate_pattern(self, pattern_key: str) -> PatternEvolutionReport:
        pattern = self._registry.get_pattern(pattern_key)
        if pattern is None:
            raise KeyError(f"No existe un patrón registrado con pattern_key='{pattern_key}'")
        return self._evaluate(pattern)

    def evaluate_all(self, state: Optional[str] = None) -> List[PatternEvolutionReport]:
        """Evalúa todos los patrones (opcionalmente filtrados por estado actual)."""
        return [self._evaluate(pattern) for pattern in self._registry.list_patterns(state=state)]

    def _evaluate(self, pattern: Pattern) -> PatternEvolutionReport:
        if pattern.state == PATTERN_OBSERVATION:
            reliable, reason = is_statistically_reliable(pattern)
            proposed = PATTERN_ACTIVE if reliable else None
            if not proposed:
                reason = f"Sigue en observación: {reason}"

        elif pattern.state == PATTERN_ACTIVE:
            decayed, reason = has_decayed(pattern)
            proposed = PATTERN_DECAYING if decayed else None
            if not proposed:
                reason = f"Sigue activo: {reason}"

        elif pattern.state == PATTERN_DECAYING:
            reliable, reliable_reason = is_statistically_reliable(pattern)
            if reliable:
                proposed, reason = PATTERN_REACTIVATED, f"Evidencia reciente recupera confiabilidad: {reliable_reason}"
            else:
                decayed, decay_reason = has_decayed(pattern)
                if decayed:
                    proposed, reason = PATTERN_INACTIVE, f"Decadencia sostenida: {decay_reason}"
                else:
                    proposed, reason = None, f"Sigue en decadencia, sin definición aún: {decay_reason}"

        elif pattern.state == PATTERN_INACTIVE:
            reliable, reason = is_statistically_reliable(pattern)
            proposed = PATTERN_REACTIVATED if reliable else None
            if not proposed:
                reason = f"Sigue inactivo: {reason}"

        elif pattern.state == PATTERN_REACTIVATED:
            decayed, reason = has_decayed(pattern)
            proposed = PATTERN_DECAYING if decayed else None
            if not proposed:
                reason = f"Reactivado, sin señales de decadencia: {reason}"

        else:
            proposed, reason = None, f"Estado '{pattern.state}' sin regla de transición definida"

        return PatternEvolutionReport(
            pattern_key=pattern.pattern_key,
            current_state=pattern.state,
            proposed_state=proposed,
            reason=reason,
            evidence=pattern.evidence,
        )
