"""Learning Engine: punto único de entrada al aprendizaje del mercado.

Orquesta Accuracy Tracker, Pattern Evolution y Calibration Advisor detrás
de una única API estable. El resto del sistema debe usar esta fachada --
nadie fuera de este paquete debería instanciar AccuracyTracker,
PatternEvolution o CalibrationAdvisor directamente.

Aprende únicamente del mercado. No importa nada de atlas.decision_journal
ni de atlas.operator_learning: esa es la mitad del conocimiento que este
módulo, por diseño, no toca. No importa atlas.calibration_manager: solo
propone (CalibrationProposal), nunca decide ni aplica.

Sigue siendo, en su totalidad, de solo lectura: ningún método de esta
clase escribe en Knowledge Base, Pattern Store, Decision Journal, ni en
ningún motor.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from atlas.knowledge.knowledge_engine import KnowledgeEngine
from atlas.knowledge.pattern_store import PatternRegistry
from atlas.learning.accuracy_tracker import AccuracyReport, AccuracyTracker
from atlas.learning.calibration_advisor import CalibrationAdvisor, CalibrationProposal
from atlas.learning.pattern_evolution import PatternEvolution, PatternEvolutionReport


@dataclass(frozen=True)
class LearningReport:
    """Reporte consolidado: todo lo que Learning Engine sabe hasta este momento."""

    overall_accuracy: AccuracyReport
    accuracy_by_decision: AccuracyReport
    pattern_reports: List[PatternEvolutionReport]
    calibration_proposals: List[CalibrationProposal]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class LearningEngine:
    """Fachada única del aprendizaje de mercado. Nunca escribe en nada."""

    def __init__(self, knowledge_engine: KnowledgeEngine, pattern_registry: PatternRegistry) -> None:
        self._accuracy_tracker = AccuracyTracker(knowledge_engine)
        self._pattern_evolution = PatternEvolution(pattern_registry)
        self._calibration_advisor = CalibrationAdvisor(self._accuracy_tracker, self._pattern_evolution)

    # --- Accuracy Tracker ---

    def overall_accuracy(self) -> AccuracyReport:
        """Precisión general de todas las decisiones clasificables (COMPRAR/DESCARTAR)."""
        return self._accuracy_tracker.overall_accuracy()

    def accuracy_by_decision(self) -> AccuracyReport:
        """Precisión separada por tipo de decisión."""
        return self._accuracy_tracker.accuracy_by_decision()

    def accuracy_by_confidence_band(self) -> AccuracyReport:
        """Precisión separada por banda de confianza de la predicción."""
        return self._accuracy_tracker.accuracy_by_confidence_band()

    def accuracy_by_sector(self) -> AccuracyReport:
        """Precisión separada por sector del símbolo."""
        return self._accuracy_tracker.accuracy_by_sector()

    def accuracy_by_engine_version(self) -> AccuracyReport:
        """Precisión separada por versión de Decision Engine activa al momento de la predicción."""
        return self._accuracy_tracker.accuracy_by_engine_version()

    # --- Pattern Evolution ---

    def evaluate_pattern(self, pattern_key: str) -> PatternEvolutionReport:
        """Evalúa la vigencia de un patrón y propone (sin aplicar) una transición de estado."""
        return self._pattern_evolution.evaluate_pattern(pattern_key)

    def evaluate_all_patterns(self, state: Optional[str] = None) -> List[PatternEvolutionReport]:
        """Evalúa todos los patrones conocidos, opcionalmente filtrados por estado actual."""
        return self._pattern_evolution.evaluate_all(state=state)

    # --- Calibration Advisor ---

    def generate_calibration_proposals(self) -> List[CalibrationProposal]:
        """Propuestas de calibración (transiciones de patrón + revisiones de motor)."""
        return self._calibration_advisor.generate_all_proposals()

    # --- Reporte consolidado ---

    def generate_learning_report(self) -> LearningReport:
        """Todo lo que Learning Engine puede decir hoy, en un solo objeto."""
        return LearningReport(
            overall_accuracy=self.overall_accuracy(),
            accuracy_by_decision=self.accuracy_by_decision(),
            pattern_reports=self.evaluate_all_patterns(),
            calibration_proposals=self.generate_calibration_proposals(),
        )
