"""Learning Engine: aprende exclusivamente del mercado. Nunca modifica ningún motor ni repositorio."""

from atlas.learning.accuracy_tracker import AccuracyReport, AccuracyTracker
from atlas.learning.accuracy_tracker import MIN_SAMPLE_SIZE as ACCURACY_MIN_SAMPLE_SIZE
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
]
