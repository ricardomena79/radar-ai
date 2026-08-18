"""Momentum Engine: calcula el Momentum Score de un instrumento.

Complementa al Atlas Score; no lo reemplaza ni lo modifica. Es un segundo
puntaje objetivo (0-100), independiente, centrado específicamente en la
estrategia de momentum intradía de Atlas: Relative Volume, Gap %, distancia
al VWAP, EMA(9) vs EMA(21), RSI, liquidez, ATR, cambio porcentual del día y
capitalización de mercado.

Reutiliza (sin modificarlos) los componentes ya construidos en
atlas.engine.score_engine para los factores que comparte con el Atlas Score,
y define únicamente los dos que le son propios (Gap % y cambio porcentual),
ambos sobre la biblioteca de indicadores existente.

No genera señales de compra/venta, no usa IA y no decide nada: solo mide.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

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
from atlas.indicators import gap_percent as calc_gap_percent

# Deben sumar 1.0. Pesan más los factores que gatillan movimientos explosivos
# intradía (volumen relativo, gap, distancia a VWAP, RSI) que los de contexto
# (tendencia EMA, ATR, cambio del día, capitalización).
WEIGHTS: Dict[str, float] = {
    "relative_volume": 0.20,
    "gap_pct": 0.15,
    "vwap_distance": 0.15,
    "rsi": 0.15,
    "ema_trend": 0.10,
    "liquidity": 0.10,
    "atr": 0.05,
    "change_percent": 0.05,
    "market_cap": 0.05,
}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def score_gap_pct(open_price: float, previous_close: float) -> ComponentScore:
    """Gap % entre la apertura y el cierre anterior. 0% = score 50 (neutro)."""
    gap = calc_gap_percent(open_price, previous_close)
    score = _clamp(50 + gap * 5)
    return ComponentScore(
        name="gap_pct",
        score=score,
        explanation=f"Gap = {gap:+.2f}% respecto al cierre anterior",
    )


def score_change_percent(change_percent: float) -> ComponentScore:
    """Cambio porcentual del día. 0% = score 50 (neutro)."""
    score = _clamp(50 + change_percent * 5)
    return ComponentScore(
        name="change_percent",
        score=score,
        explanation=f"Cambio del día = {change_percent:+.2f}%",
    )


def score_rsi(close, period: int = 14) -> ComponentScore:
    """RSI como componente propio del Momentum Engine.

    Reutiliza el cálculo de score_momentum() de score_engine (mismo RSI que
    usa el Atlas Score) pero lo expone bajo el nombre "rsi", que es como se
    identifica este factor dentro del Momentum Engine.
    """
    base = score_momentum(close, period=period)
    return ComponentScore(name="rsi", score=base.score, explanation=base.explanation)


@dataclass(frozen=True)
class ScoredComponent:
    """Componente individual del Momentum Score, ya combinado con su peso."""

    name: str
    score: float
    weight: float
    weighted_score: float
    explanation: str


@dataclass(frozen=True)
class MomentumResult:
    """Resultado del Momentum Engine para un símbolo: score total + desglose."""

    symbol: str
    momentum_score: float
    components: List[ScoredComponent]

    def component(self, name: str) -> ScoredComponent:
        """Devuelve el componente por nombre (ej. 'relative_volume')."""
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


def calculate_momentum_score(symbol: str, collector: DataCollector) -> MomentumResult:
    """Calcula el Momentum Score de un símbolo a partir del Data Collector."""
    quote = collector.get_quote(symbol)
    daily = collector.get_history(symbol, period="6mo", interval="1d")

    try:
        intraday = collector.get_history(symbol, period="1d", interval="5m")
    except (ProviderError, QuoteNotFoundError):
        intraday = None

    raw_components = [
        score_relative_volume(quote.volume, quote.average_volume),
        # `quote.open` puede ser `None` (2026-08-18, caso real "0 ciclos con
        # datos" en premarket): Tradier no reporta apertura de la sesión
        # REGULAR mientras esa sesión todavía no abrió hoy -- antes esto
        # hacía explotar gap_percent() con un ValueError sin capturar, que
        # tumbaba TODO el símbolo (no solo este componente). Mismo patrón
        # de degradación honesta que ya usa vwap_distance 3 líneas abajo:
        # sin dato -> score neutro (50) con explicación explícita, nunca
        # una excepción ni un valor inventado.
        score_gap_pct(quote.open, quote.previous_close)
        if quote.open is not None and quote.previous_close is not None
        else ComponentScore(name="gap_pct", score=50.0, explanation="Apertura de sesión regular no disponible todavía"),
        score_vwap_distance(quote.last_price, intraday["High"], intraday["Low"], intraday["Close"], intraday["Volume"])
        if intraday is not None
        else ComponentScore(name="vwap_distance", score=50.0, explanation="Datos intradía no disponibles"),
        score_rsi(daily["Close"]),
        score_ema_trend(daily["Close"]),
        score_liquidity(quote.last_price, quote.volume),
        score_atr(daily["High"], daily["Low"], daily["Close"], quote.last_price),
        score_change_percent(quote.change_percent),
        score_market_cap(quote.market_cap),
    ]

    components = _weighted(raw_components, WEIGHTS)
    momentum_score = round(sum(c.weighted_score for c in components), 2)

    return MomentumResult(symbol=symbol, momentum_score=momentum_score, components=components)
