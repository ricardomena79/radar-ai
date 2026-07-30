"""Búsqueda de patrones similares sobre los eventos registrados en EventStore.

Sin IA: la "similitud" es una distancia euclidiana simple sobre un puñado de
features numéricas normalizadas (gap %, RVOL, Atlas Score, Momentum Score,
Money Flow Score). Es determinista, transparente y barata de calcular.

También arma el "ADN" de un símbolo: el promedio de esas mismas features a
lo largo de todo su historial en la base, para poder comparar el
comportamiento típico de dos acciones entre sí.
"""

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from atlas.knowledge.event_store import EventStore, MarketEvent

FEATURE_RANGES: Dict[str, Tuple[float, float]] = {
    "gap_percent": (-20.0, 20.0),
    "rvol": (0.0, 5.0),
    "atlas_score": (0.0, 100.0),
    "momentum_score": (0.0, 100.0),
    "money_flow_score": (0.0, 100.0),
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _normalize(event: MarketEvent) -> Dict[str, float]:
    """Convierte las features numéricas de un evento a [0, 1], omitiendo las que falten."""
    raw = {
        "gap_percent": event.gap_percent,
        "rvol": event.rvol,
        "atlas_score": event.atlas_score,
        "momentum_score": event.momentum_score,
        "money_flow_score": event.money_flow_score,
    }
    features: Dict[str, float] = {}
    for name, value in raw.items():
        if value is None:
            continue
        low, high = FEATURE_RANGES[name]
        features[name] = _clamp((value - low) / (high - low))
    return features


def _similarity(features_a: Dict[str, float], features_b: Dict[str, float]) -> Optional[float]:
    """Similitud 0-100 entre dos vectores de features normalizadas; None si no comparten ninguna."""
    common = set(features_a) & set(features_b)
    if not common:
        return None

    squared_diff = sum((features_a[key] - features_b[key]) ** 2 for key in common)
    distance = math.sqrt(squared_diff / len(common))  # RMS de la diferencia, en [0, 1]
    return round(_clamp(1 - distance) * 100, 1)


@dataclass(frozen=True)
class SymbolDNA:
    """Perfil promedio de comportamiento histórico de un símbolo."""

    ticker: str
    sample_size: int
    features: Dict[str, float]  # promedios normalizados [0, 1]
    event_type_counts: Dict[str, int]


class PatternStore:
    """Busca eventos con patrones similares y compara el "ADN" de dos símbolos."""

    def __init__(self, event_store: EventStore) -> None:
        self._event_store = event_store

    def find_similar(
        self,
        reference: MarketEvent,
        top_n: int = 5,
        event_type: Optional[str] = None,
        candidate_pool: int = 5_000,
    ) -> List[Tuple[MarketEvent, float]]:
        """Devuelve los `top_n` eventos más parecidos a `reference` (evento, similitud 0-100)."""
        reference_features = _normalize(reference)
        if not reference_features:
            return []

        candidates = self._event_store.get_events(event_type=event_type, limit=candidate_pool)

        scored = []
        for candidate in candidates:
            if candidate.id == reference.id:
                continue
            similarity = _similarity(reference_features, _normalize(candidate))
            if similarity is not None:
                scored.append((candidate, similarity))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_n]

    def get_symbol_dna(self, ticker: str, limit: int = 5_000) -> Optional[SymbolDNA]:
        """Perfil promedio (ADN) de un símbolo a partir de todo su historial registrado."""
        events = self._event_store.get_events(ticker=ticker, limit=limit)
        if not events:
            return None

        accumulated: Dict[str, List[float]] = {}
        event_type_counts: Dict[str, int] = {}

        for event in events:
            event_type_counts[event.event_type] = event_type_counts.get(event.event_type, 0) + 1
            for name, value in _normalize(event).items():
                accumulated.setdefault(name, []).append(value)

        features = {name: round(sum(values) / len(values), 4) for name, values in accumulated.items()}

        return SymbolDNA(
            ticker=ticker,
            sample_size=len(events),
            features=features,
            event_type_counts=event_type_counts,
        )

    def compare_dna(self, ticker_a: str, ticker_b: str, limit: int = 5_000) -> Optional[float]:
        """Similitud 0-100 entre el ADN histórico de dos símbolos."""
        dna_a = self.get_symbol_dna(ticker_a, limit=limit)
        dna_b = self.get_symbol_dna(ticker_b, limit=limit)
        if dna_a is None or dna_b is None:
            return None
        return _similarity(dna_a.features, dna_b.features)
