"""Learning Engine: aprende exclusivamente del mercado. Nunca modifica ningún motor ni repositorio.

API estable para el resto del sistema: `LearningEngine` (y `LearningReport`).
Los submódulos (AccuracyTracker, PatternEvolution, CalibrationAdvisor)
siguen exportados acá para sus propias pruebas unitarias, pero ningún otro
módulo de Atlas debería instanciarlos directamente -- deben pasar por
`LearningEngine`.
"""

from atlas.learning.accuracy_tracker import AccuracyReport, AccuracyTracker
from atlas.learning.accuracy_tracker import MIN_SAMPLE_SIZE as ACCURACY_MIN_SAMPLE_SIZE
from atlas.learning.calibration_advisor import (
    ENGINE_CALIBRATION,
    LOW_ACCURACY_THRESHOLD,
    PATTERN_STATE_CHANGE,
    CalibrationAdvisor,
    CalibrationProposal,
)
from atlas.learning.learning_engine import LearningEngine, LearningReport
from atlas.learning.pattern_evolution import (
    DECAY_DROP_THRESHOLD,
    DEFAULT_BASELINE_WIN_RATE,
)
from atlas.learning.pattern_evolution import MIN_SAMPLE_SIZE as PATTERN_EVOLUTION_MIN_SAMPLE_SIZE
from atlas.learning.pattern_evolution import (
    PatternEvolution,
    PatternEvolutionReport,
    has_decayed,
    is_statistically_reliable,
)

__all__ = [
    "LearningEngine",
    "LearningReport",
    "AccuracyTracker",
    "AccuracyReport",
    "ACCURACY_MIN_SAMPLE_SIZE",
    "PatternEvolution",
    "PatternEvolutionReport",
    "is_statistically_reliable",
    "has_decayed",
    "PATTERN_EVOLUTION_MIN_SAMPLE_SIZE",
    "DEFAULT_BASELINE_WIN_RATE",
    "DECAY_DROP_THRESHOLD",
    "CalibrationAdvisor",
    "CalibrationProposal",
    "ENGINE_CALIBRATION",
    "PATTERN_STATE_CHANGE",
    "LOW_ACCURACY_THRESHOLD",
]
