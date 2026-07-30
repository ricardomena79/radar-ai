"""Calibration Advisor: consolida la evidencia de Accuracy Tracker y Pattern Evolution
en propuestas concretas de calibración.

De solo lectura, siempre. No importa `atlas.calibration_manager` -- Learning
Engine no sabe que Calibration Manager existe. Devuelve `CalibrationProposal`,
objetos de datos listos para que un llamador externo (fuera de Learning
Engine) decida entregárselos a Calibration Manager. Esa entrega, y
cualquier aplicación real de un cambio, es responsabilidad exclusiva de
Calibration Manager -- nunca de este módulo.

No propone valores específicos de ajuste (ej. "subir el peso de RVOL a
0.25"): eso requeriría un análisis causal que este módulo no hace. Lo que
sí hace, con evidencia cuantificada, es señalar *dónde* la evidencia
sugiere revisar algo -- una decisión con precisión baja, un patrón que
cambió de vigencia -- dejando el ajuste concreto a criterio humano.

Los valores de `category` ("engine_calibration", "pattern_state_change")
son los mismos literales que usa atlas.calibration_manager, duplicados
aquí a propósito (no importados) para no crear una dependencia entre
Learning Engine y Calibration Manager.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from atlas.learning.accuracy_tracker import AccuracyTracker
from atlas.learning.pattern_evolution import PatternEvolution

# Deben coincidir con atlas.calibration_manager.ENGINE_CALIBRATION / PATTERN_STATE_CHANGE.
ENGINE_CALIBRATION = "engine_calibration"
PATTERN_STATE_CHANGE = "pattern_state_change"

LOW_ACCURACY_THRESHOLD = 0.55  # apenas por encima del azar; por debajo, amerita revisión


@dataclass(frozen=True)
class CalibrationProposal:
    """Candidato a recomendación de calibración.

    No es una escritura: es un objeto de datos. Que un caller externo lo
    entregue a Calibration Manager (o no) es una decisión fuera de este
    módulo.
    """

    recommendation_key: str
    category: str
    target: str
    proposed_by: str
    title: str
    description: str
    evidence: Dict[str, Any]
    sample_size: Optional[int]
    expected_improvement: Optional[str]
    risks: Optional[str]
    proposed_new_state: Optional[str]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class CalibrationAdvisor:
    """Traduce evidencia de Accuracy Tracker y Pattern Evolution en propuestas concretas."""

    def __init__(self, accuracy_tracker: AccuracyTracker, pattern_evolution: PatternEvolution) -> None:
        self._accuracy = accuracy_tracker
        self._patterns = pattern_evolution

    def propose_pattern_transitions(self) -> List[CalibrationProposal]:
        """Una propuesta por cada patrón para el que Pattern Evolution sugiere un cambio."""
        proposals = []
        for report in self._patterns.evaluate_all():
            if report.proposed_state is None:
                continue
            proposals.append(
                CalibrationProposal(
                    recommendation_key=f"pattern.{report.pattern_key}.{report.proposed_state}",
                    category=PATTERN_STATE_CHANGE,
                    target=report.pattern_key,
                    proposed_by="Learning Engine / Pattern Evolution",
                    title=f"Transicionar '{report.pattern_key}': {report.current_state} -> {report.proposed_state}",
                    description=report.reason,
                    evidence=report.evidence,
                    sample_size=report.evidence.get("sample_size"),
                    expected_improvement=None,
                    risks=None,
                    proposed_new_state=report.proposed_state,
                )
            )
        return proposals

    def propose_engine_reviews(self) -> List[CalibrationProposal]:
        """Una propuesta de revisión por cada tipo de decisión con precisión baja y muestra suficiente."""
        report = self._accuracy.accuracy_by_decision()
        proposals = []
        for decision, stats in report.breakdown.items():
            if stats.get("insufficient_sample") or stats.get("accuracy") is None:
                continue
            if stats["accuracy"] >= LOW_ACCURACY_THRESHOLD:
                continue
            proposals.append(
                CalibrationProposal(
                    recommendation_key=f"engine.decision_engine.review_{decision.lower()}",
                    category=ENGINE_CALIBRATION,
                    target="decision_engine",
                    proposed_by="Learning Engine / Calibration Advisor",
                    title=f"Revisar calibración de decisiones {decision}",
                    description=(
                        f"Precisión histórica de {decision} = {stats['accuracy'] * 100:.1f}% "
                        f"sobre {stats['n']} casos, por debajo del umbral de referencia "
                        f"({LOW_ACCURACY_THRESHOLD * 100:.0f}%)."
                    ),
                    evidence=stats,
                    sample_size=stats["n"],
                    expected_improvement="No determinado; requiere identificar qué factor específico ajustar",
                    risks="Evidencia histórica agregada; revisar estacionalidad y condiciones de mercado antes de aplicar cambios",
                    proposed_new_state=None,
                )
            )
        return proposals

    def generate_all_proposals(self) -> List[CalibrationProposal]:
        """Todas las propuestas disponibles: transiciones de patrón + revisiones de motor."""
        return self.propose_pattern_transitions() + self.propose_engine_reviews()
