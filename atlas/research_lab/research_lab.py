"""Research Lab: investiga la Knowledge Base para descubrir patrones estadísticos.

Solo investiga. El Research Lab NUNCA modifica Atlas Score, Momentum Engine,
Money Flow Engine, Decision Engine ni ningún otro motor automáticamente.
Todo lo que descubre se presenta como una `ResearchFinding` -- una
recomendación para que un humano la revise, apruebe o descarte.

Este archivo define la arquitectura (clases, interfaces, firmas de método)
para las investigaciones descritas en la fase de planificación. La lógica
real de cada investigación (los algoritmos estadísticos que comparan
factores, umbrales y sectores) todavía no está implementada: cada método
lanza NotImplementedError con una descripción de lo que hará, para que sea
evidente qué falta y no se pueda invocar por error creyendo que ya funciona.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from atlas.knowledge.knowledge_engine import KnowledgeEngine

# Estado de una recomendación: siempre empieza pendiente de revisión humana.
# El Research Lab no tiene forma de pasar una recomendación a "aplicada":
# esa decisión es exclusivamente humana, fuera de este módulo.
PENDING_REVIEW = "PENDING_REVIEW"
APPROVED = "APPROVED"
REJECTED = "REJECTED"

FINDING_STATUSES = {PENDING_REVIEW, APPROVED, REJECTED}


@dataclass(frozen=True)
class ResearchFinding:
    """Un hallazgo estadístico, presentado para revisión humana -- nunca autoaplicado."""

    title: str
    category: str  # ej. "combinacion_factores", "umbral", "patron_emergente",
    # "patron_obsoleto", "antipatron", "comparacion_sectorial", "contexto"
    description: str
    evidence: Dict[str, Any] = field(default_factory=dict)  # sample_size, win_rate, etc.
    status: str = PENDING_REVIEW
    discovered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ResearchLab:
    """Investiga la Knowledge Base. Nunca decide, nunca modifica: solo recomienda.

    Cada método corresponde a una de las líneas de investigación definidas
    para Atlas. Reciben la Knowledge Base ya construida (no la modifican) y
    devolverían una lista de ResearchFinding con evidencia de respaldo.
    """

    def __init__(self, knowledge_engine: KnowledgeEngine) -> None:
        self._knowledge = knowledge_engine

    def find_factor_combinations(
        self, factors: List[str], min_sample_size: int = 30
    ) -> List[ResearchFinding]:
        """Busca combinaciones de factores (ej. Gap + RVOL + Float + Money Flow)
        que se correlacionen con explosiones o colapsos."""
        raise NotImplementedError(
            "Research Lab: búsqueda de combinaciones de factores todavía no implementada."
        )

    def compare_thresholds(self, factor: str, thresholds: List[float]) -> List[ResearchFinding]:
        """Compara distintos umbrales de un mismo factor (ej. RVOL > 2 vs RVOL > 3)
        y mide cuál se asocia mejor con resultados favorables."""
        raise NotImplementedError("Research Lab: comparación de umbrales todavía no implementada.")

    def discover_emerging_patterns(self) -> List[ResearchFinding]:
        """Detecta combinaciones que empezaron a repetirse recientemente con éxito."""
        raise NotImplementedError(
            "Research Lab: descubrimiento de patrones emergentes todavía no implementado."
        )

    def detect_decaying_patterns(self) -> List[ResearchFinding]:
        """Detecta patrones que funcionaban antes y dejaron de funcionar."""
        raise NotImplementedError(
            "Research Lab: detección de patrones que dejaron de funcionar todavía no implementada."
        )

    def discover_antipatterns(self) -> List[ResearchFinding]:
        """Detecta combinaciones de factores que se asocian consistentemente con
        malos resultados (colapsos, falsas rupturas)."""
        raise NotImplementedError("Research Lab: descubrimiento de antipatrones todavía no implementado.")

    def compare_sectors(self) -> List[ResearchFinding]:
        """Compara el comportamiento histórico entre sectores e industrias."""
        raise NotImplementedError("Research Lab: comparación de sectores e industrias todavía no implementada.")

    def analyze_market_context_influence(self) -> List[ResearchFinding]:
        """Mide si el contexto general de mercado (VIX, SPY, día de la semana,
        temporada de resultados, etc.) influye en la probabilidad de éxito."""
        raise NotImplementedError(
            "Research Lab: análisis de influencia del contexto de mercado todavía no implementado."
        )

    def run_all(self) -> List[ResearchFinding]:
        """Ejecuta todas las investigaciones disponibles y devuelve sus hallazgos."""
        raise NotImplementedError("Research Lab: orquestación de todas las investigaciones todavía no implementada.")
