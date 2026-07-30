"""Accuracy Tracker: mide qué tan bien las decisiones de Decision Engine predijeron
lo que realmente pasó en el mercado.

Es de solo lectura, siempre. No escribe en Knowledge Base, no escribe en
Pattern Store, no escribe en ningún motor. Solo devuelve reportes.

No existe hoy un vínculo explícito (event_id) entre una predicción y el
evento de mercado que confirmó su resultado -- Decision Recorder registra
la predicción en el momento de la decisión, antes de que el resultado se
conozca. Por eso este módulo empareja cada predicción con el evento de
mercado del mismo símbolo y la misma fecha más cercano en hora: una
correlación temporal de solo lectura, no una invención de datos.

Reglas de acierto, fijas y explícitas (sin IA):
  - COMPRAR es acierto si el evento cerró con resultado positivo
    (close_result_percent > 0) o fue una EXPLOSION.
  - DESCARTAR es acierto si el evento cerró neutro/negativo
    (close_result_percent <= 0) o fue un COLLAPSE/FALSE_BREAKOUT.
  - VIGILAR no se clasifica como acierto/error -- es una decisión de
    espera, no una apuesta direccional. Se reporta aparte.

Ignora cualquier predicción o evento con data_status distinto de OK (un
dato estimado o con timeout no es evidencia de lo que realmente ocurrió),
y cualquier grupo con muestra por debajo de MIN_SAMPLE_SIZE.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from atlas.engine.decision_engine import COMPRAR, DESCARTAR, VIGILAR
from atlas.knowledge.engine_versions import parse_versions_json
from atlas.knowledge.event_store import COLLAPSE, EXPLOSION, FALSE_BREAKOUT, STATUS_OK, MarketEvent
from atlas.knowledge.knowledge_engine import KnowledgeEngine
from atlas.knowledge.prediction_store import PredictionRecord

MIN_SAMPLE_SIZE = 10

CONFIDENCE_BANDS = [(0.0, 35.0, "baja (0-35)"), (35.0, 65.0, "media (35-65)"), (65.0, 100.1, "alta (65-100)")]


def _is_clean(data_status: Optional[str]) -> bool:
    return data_status is None or data_status == STATUS_OK


def _time_to_seconds(value: str) -> int:
    hours, minutes, seconds = (int(part) for part in value.split(":"))
    return hours * 3600 + minutes * 60 + seconds


def _was_correct(decision: str, event: MarketEvent) -> Optional[bool]:
    """True/False si la decisión acertó; None si no se clasifica (ej. VIGILAR)."""
    if decision == COMPRAR:
        if event.close_result_percent is not None:
            return event.close_result_percent > 0
        return event.event_type == EXPLOSION
    if decision == DESCARTAR:
        if event.close_result_percent is not None:
            return event.close_result_percent <= 0
        return event.event_type in (COLLAPSE, FALSE_BREAKOUT)
    return None  # VIGILAR u otro valor: no se clasifica


@dataclass(frozen=True)
class AccuracyReport:
    """Resultado de una medición de precisión, agrupada por alguna dimensión."""

    dimension: str
    breakdown: Dict[str, Dict[str, Any]]
    sample_size: int
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AccuracyTracker:
    """Compara predicciones de Decision Engine contra resultados reales. Solo lectura."""

    def __init__(self, knowledge_engine: KnowledgeEngine) -> None:
        self._knowledge = knowledge_engine

    def _matched_pairs(self) -> List[Tuple[PredictionRecord, MarketEvent]]:
        """Empareja cada predicción con el evento del mismo ticker/fecha más cercano en hora."""
        predictions = [
            p for p in self._knowledge.predictions.get_predictions(limit=1_000_000) if _is_clean(p.data_status)
        ]
        events = [e for e in self._knowledge.events.get_events(limit=1_000_000) if _is_clean(e.data_status)]

        events_by_key: Dict[Tuple[str, str], List[MarketEvent]] = defaultdict(list)
        for event in events:
            events_by_key[(event.ticker, event.date)].append(event)

        pairs: List[Tuple[PredictionRecord, MarketEvent]] = []
        for prediction in predictions:
            candidates = events_by_key.get((prediction.ticker, prediction.date), [])
            if not candidates:
                continue
            closest = min(candidates, key=lambda e: abs(_time_to_seconds(e.time) - _time_to_seconds(prediction.time)))
            pairs.append((prediction, closest))
        return pairs

    def _score(self, pairs: List[Tuple[PredictionRecord, MarketEvent]]) -> Dict[str, Any]:
        classified = [(p, e, _was_correct(p.decision, e)) for p, e in pairs]
        classified = [(p, e, correct) for p, e, correct in classified if correct is not None]

        if len(classified) < MIN_SAMPLE_SIZE:
            return {"n": len(classified), "accuracy": None, "insufficient_sample": True}

        correct_count = sum(1 for _, _, correct in classified if correct)
        return {
            "n": len(classified),
            "accuracy": round(correct_count / len(classified), 4),
            "insufficient_sample": False,
        }

    def overall_accuracy(self) -> AccuracyReport:
        """Precisión general de todas las decisiones clasificables (COMPRAR/DESCARTAR)."""
        pairs = self._matched_pairs()
        return AccuracyReport(dimension="overall", breakdown={"todas": self._score(pairs)}, sample_size=len(pairs))

    def accuracy_by_decision(self) -> AccuracyReport:
        """Precisión separada por tipo de decisión."""
        pairs = self._matched_pairs()
        by_decision: Dict[str, List[Tuple[PredictionRecord, MarketEvent]]] = defaultdict(list)
        for prediction, event in pairs:
            by_decision[prediction.decision].append((prediction, event))

        breakdown = {decision: self._score(group) for decision, group in by_decision.items()}
        return AccuracyReport(dimension="by_decision", breakdown=breakdown, sample_size=len(pairs))

    def accuracy_by_confidence_band(self) -> AccuracyReport:
        """Precisión separada por banda de confianza de la predicción."""
        pairs = self._matched_pairs()
        by_band: Dict[str, List[Tuple[PredictionRecord, MarketEvent]]] = defaultdict(list)
        for prediction, event in pairs:
            for low, high, label in CONFIDENCE_BANDS:
                if low <= prediction.confidence < high:
                    by_band[label].append((prediction, event))
                    break

        breakdown = {label: self._score(group) for label, group in by_band.items()}
        return AccuracyReport(dimension="by_confidence_band", breakdown=breakdown, sample_size=len(pairs))

    def accuracy_by_sector(self) -> AccuracyReport:
        """Precisión separada por sector del símbolo (tomado del evento emparejado)."""
        pairs = self._matched_pairs()
        by_sector: Dict[str, List[Tuple[PredictionRecord, MarketEvent]]] = defaultdict(list)
        for prediction, event in pairs:
            by_sector[event.sector or "Sin clasificar"].append((prediction, event))

        breakdown = {sector: self._score(group) for sector, group in by_sector.items()}
        return AccuracyReport(dimension="by_sector", breakdown=breakdown, sample_size=len(pairs))

    def accuracy_by_engine_version(self) -> AccuracyReport:
        """Precisión separada por versión de Decision Engine activa al momento de la predicción."""
        pairs = self._matched_pairs()
        by_version: Dict[str, List[Tuple[PredictionRecord, MarketEvent]]] = defaultdict(list)
        for prediction, event in pairs:
            versions = parse_versions_json(prediction.engine_versions) if prediction.engine_versions else {}
            version = versions.get("decision_engine", "desconocida")
            by_version[version].append((prediction, event))

        breakdown = {version: self._score(group) for version, group in by_version.items()}
        return AccuracyReport(dimension="by_engine_version", breakdown=breakdown, sample_size=len(pairs))
