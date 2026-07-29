"""Atlas Score v1: puntaje objetivo que combina los componentes de score_engine.

No toma decisiones de compra, venta ni espera. Solo calcula un número
(0-100) a partir de datos del Data Collector y la biblioteca de
indicadores, y expone el desglose por componente para que una capa
posterior de Atlas decida qué hacer con él.
"""

from dataclasses import dataclass
from typing import Dict, List

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.providers.base import ProviderError, QuoteNotFoundError
from atlas.engine.score_engine import (
    ComponentScore,
    score_atr,
    score_ema_trend,
    score_liquidity,
    score_market_cap,
    score_momentum,
    score_relative_volume,
    score_vwap_distance,
)

# Deben sumar 1.0; determinan cuánto pesa cada componente en el score final.
WEIGHTS: Dict[str, float] = {
    "momentum": 0.20,
    "relative_volume": 0.20,
    "ema_trend": 0.15,
    "vwap_distance": 0.15,
    "atr": 0.10,
    "liquidity": 0.10,
    "market_cap": 0.10,
}


@dataclass(frozen=True)
class ScoredComponent:
    """Componente individual ya combinado con su peso en el score final."""

    name: str
    score: float
    weight: float
    weighted_score: float
    explanation: str


@dataclass(frozen=True)
class AtlasScore:
    """Resultado del Atlas Score para un símbolo: total + desglose por componente."""

    symbol: str
    total: float
    components: List[ScoredComponent]

    def component(self, name: str) -> ScoredComponent:
        """Devuelve el componente por nombre (ej. 'momentum')."""
        for component in self.components:
            if component.name == name:
                return component
        raise KeyError(f"Componente desconocido: '{name}'")


def _weighted(components: List[ComponentScore], weights: Dict[str, float]) -> List[ScoredComponent]:
    return [
        ScoredComponent(
            name=c.name,
            score=c.score,
            weight=weights[c.name],
            weighted_score=c.score * weights[c.name],
            explanation=c.explanation,
        )
        for c in components
    ]


def calculate_atlas_score(symbol: str, collector: DataCollector) -> AtlasScore:
    """Calcula el Atlas Score de un símbolo a partir del Data Collector."""
    quote = collector.get_quote(symbol)
    daily = collector.get_history(symbol, period="6mo", interval="1d")

    try:
        intraday = collector.get_history(symbol, period="1d", interval="5m")
    except (ProviderError, QuoteNotFoundError):
        intraday = None

    raw_components = [
        score_momentum(daily["Close"]),
        score_relative_volume(quote.volume, quote.average_volume),
        score_ema_trend(daily["Close"]),
        score_vwap_distance(quote.last_price, intraday["High"], intraday["Low"], intraday["Close"], intraday["Volume"])
        if intraday is not None
        else ComponentScore(name="vwap_distance", score=50.0, explanation="Datos intradía no disponibles"),
        score_atr(daily["High"], daily["Low"], daily["Close"], quote.last_price),
        score_liquidity(quote.last_price, quote.volume),
        score_market_cap(quote.market_cap),
    ]

    components = _weighted(raw_components, WEIGHTS)
    total = round(sum(c.weighted_score for c in components), 2)

    return AtlasScore(symbol=symbol, total=total, components=components)
