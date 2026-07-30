"""Knowledge Engine: fachada única sobre event_store, prediction_store y pattern_store.

Punto de entrada del núcleo de conocimiento de Atlas: registra eventos y
predicciones, y expone búsqueda de patrones similares, comparación de ADN
entre símbolos y estadísticas agregadas. No genera señales ni decide nada
nuevo -- solo persiste y consulta lo que el resto de Atlas ya calculó.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from atlas.knowledge.event_store import EventStore, MarketEvent
from atlas.knowledge.pattern_store import PatternStore, SymbolDNA
from atlas.knowledge.prediction_store import PredictionRecord, PredictionStore


@dataclass(frozen=True)
class KnowledgeStatistics:
    """Estadísticas agregadas del estado actual de la base de conocimiento."""

    total_events: int
    events_by_type: Dict[str, int]
    events_by_sector: Dict[str, int]
    total_predictions: int
    predictions_by_decision: Dict[str, int]


class KnowledgeEngine:
    """Fachada del núcleo de conocimiento: eventos, predicciones y patrones, en una sola base local."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.events = EventStore(db_path)
        self.predictions = PredictionStore(db_path)
        self.patterns = PatternStore(self.events)

    def record_event(self, event: MarketEvent) -> int:
        """Registra un evento de mercado y devuelve su id."""
        return self.events.record_event(event)

    def record_prediction(self, prediction: PredictionRecord) -> int:
        """Registra una predicción del Decision Engine y devuelve su id."""
        return self.predictions.record_prediction(prediction)

    def find_similar_events(
        self, reference: MarketEvent, top_n: int = 5, event_type: Optional[str] = None
    ) -> List[Tuple[MarketEvent, float]]:
        """Eventos históricos más parecidos a `reference` (evento, similitud 0-100)."""
        return self.patterns.find_similar(reference, top_n=top_n, event_type=event_type)

    def get_symbol_dna(self, ticker: str) -> Optional[SymbolDNA]:
        """Perfil promedio (ADN) del comportamiento histórico de un símbolo."""
        return self.patterns.get_symbol_dna(ticker)

    def compare_dna(self, ticker_a: str, ticker_b: str) -> Optional[float]:
        """Similitud 0-100 entre el ADN histórico de dos símbolos."""
        return self.patterns.compare_dna(ticker_a, ticker_b)

    def get_statistics(self) -> KnowledgeStatistics:
        """Estadísticas agregadas de todo lo registrado hasta ahora."""
        return KnowledgeStatistics(
            total_events=self.events.count(),
            events_by_type=self.events.count_by_type(),
            events_by_sector=self.events.count_by_sector(),
            total_predictions=self.predictions.count(),
            predictions_by_decision=self.predictions.count_by_decision(),
        )

    def close(self) -> None:
        """Cierra las conexiones a la base de datos."""
        self.events.close()
        self.predictions.close()
