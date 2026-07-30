"""Strategy Lab: simula estrategias sobre la información histórica de la Knowledge Base.

Compara reglas de entrada/salida (ej. comprar Top 1 vs Top 3, salida +2% vs
+3%, distintos Stop Loss) y mide estadísticamente cuál habría rendido mejor.
El Strategy Lab NUNCA modifica automáticamente las reglas de Atlas Score,
Momentum Engine, Money Flow Engine ni Decision Engine: solo entrega
resultados comparativos para revisión y aprobación humana.

Este archivo define la arquitectura (clases, interfaces, firmas de método).
La lógica real de simulación -- recorrer el historial, aplicar cada regla,
calcular retornos -- todavía no está implementada: cada método lanza
NotImplementedError con una descripción de lo que hará.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from atlas.knowledge.knowledge_engine import KnowledgeEngine


@dataclass(frozen=True)
class StrategyRule:
    """Definición de una estrategia a simular: cómo entra y cómo sale.

    Los campos son deliberadamente opcionales y de texto libre en algunos
    casos (entry_rule) porque el motor de simulación real -- que todavía no
    existe -- es quien deberá definir un vocabulario cerrado de reglas.
    """

    name: str
    entry_rule: str  # ej. "Top 1 por Atlas Score", "Confianza > 90%", "Evidencia A+"
    take_profit_percent: Optional[float] = None
    stop_loss_percent: Optional[float] = None
    min_confidence: Optional[float] = None
    min_evidence_level: Optional[str] = None


@dataclass(frozen=True)
class StrategyResult:
    """Resultado estadístico de simular una StrategyRule sobre datos históricos."""

    strategy: StrategyRule
    trades_count: int
    win_rate: Optional[float] = None
    avg_return_percent: Optional[float] = None
    total_return_percent: Optional[float] = None
    max_drawdown_percent: Optional[float] = None
    evaluated_at: str = ""

    def __post_init__(self) -> None:
        if not self.evaluated_at:
            object.__setattr__(self, "evaluated_at", datetime.now(timezone.utc).isoformat())


class StrategyLab:
    """Simula estrategias de entrada/salida sobre la Knowledge Base. Nunca decide por sí solo."""

    def __init__(self, knowledge_engine: KnowledgeEngine) -> None:
        self._knowledge = knowledge_engine

    def simulate(
        self,
        strategy: StrategyRule,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> StrategyResult:
        """Simula una única estrategia sobre el rango de fechas dado."""
        raise NotImplementedError("Strategy Lab: simulación de una estrategia todavía no implementada.")

    def compare(
        self,
        strategies: List[StrategyRule],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[StrategyResult]:
        """Simula varias estrategias sobre el mismo rango y las deja listas para comparar."""
        raise NotImplementedError("Strategy Lab: comparación de estrategias todavía no implementada.")
