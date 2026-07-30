"""Decision Engine: combina Atlas Score, Momentum Score y Money Flow Score en una decisión.

Único responsable de traducir los puntajes ya calculados (todos reutilizados
sin modificarlos) en un estado categórico -- COMPRAR, VIGILAR o DESCARTAR --
junto con una explicación accionable: qué condiciones se cumplen, cuáles
faltan, y qué evento concreto cambiaría el estado.

La decisión se deriva de una CONFIANZA ACUMULATIVA (0-100): cada factor
aporta una fracción continua de esa confianza, proporcional a su propio
puntaje (0-100) y a su peso, no a si "pasó" o no un umbral. Los umbrales
solo se usan para mostrar qué factores están fuertes ("Cumple") y cuáles no
("Falta"); la decisión final compara la confianza acumulada contra dos
umbrales globales. No hay IA ni aprendizaje: los pesos son fijos y visibles.

Incluye dos perfiles de peso ("modes"):
  - "standard": balanceado entre los 8 factores.
  - "market_open": para operar entre 9:30 y 10:30, prioriza Relative
    Volume, Gap, confirmación sobre VWAP y ruptura del máximo intradía
    (juntos concentran el 85% del peso).

No ejecuta operaciones, no envía alertas y no decide nada fuera de esta
clasificación: es el último eslabón de medición antes de que un humano
decida qué hacer.
"""

from dataclasses import dataclass
from typing import Dict, List, NamedTuple, Optional, Tuple

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.providers.base import ProviderError, QuoteNotFoundError
from atlas.data.providers.yahoo_finance import YahooFinanceProvider
from atlas.engine.atlas_score import calculate_atlas_score
from atlas.engine.momentum_engine import calculate_momentum_score
from atlas.engine.money_flow_engine import MoneyFlowEngine

COMPRAR = "COMPRAR"
VIGILAR = "VIGILAR"
DESCARTAR = "DESCARTAR"

STANDARD = "standard"
MARKET_OPEN = "market_open"

# Perfiles de peso por factor. Cada uno debe sumar 1.0. Son fijos y
# explícitos: no hay IA ni aprendizaje en esta calibración.
WEIGHT_PROFILES: Dict[str, Dict[str, float]] = {
    STANDARD: {
        "momentum": 0.20,
        "rvol": 0.15,
        "vwap": 0.15,
        "gap": 0.10,
        "liquidity": 0.10,
        "atlas_score": 0.10,
        "money_flow": 0.10,
        "breakout": 0.10,
    },
    MARKET_OPEN: {
        "rvol": 0.25,
        "gap": 0.20,
        "vwap": 0.20,
        "breakout": 0.20,
        "momentum": 0.05,
        "atlas_score": 0.05,
        "liquidity": 0.025,
        "money_flow": 0.025,
    },
}

# Umbrales de visualización: cuándo un factor cuenta como "cumple" en las
# listas de Cumple/Falta. No afectan el cálculo de la confianza.
MOMENTUM_STRONG = 70.0
RVOL_HIGH = 70.0
VWAP_CONFIRMED = 60.0
GAP_FAVORABLE = 60.0
LIQUIDITY_HIGH = 70.0
ATLAS_GOOD = 60.0
MONEY_FLOW_POSITIVE = 55.0
BREAKOUT_CONFIRMED = 50.0

# Umbrales globales sobre la confianza acumulada (0-100).
CONFIDENCE_BUY_FLOOR = 65.0
CONFIDENCE_DISCARD_CEILING = 35.0


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


class Condition(NamedTuple):
    """Un factor evaluable: su puntaje continuo (0-100) y su estado para mostrar."""

    name: str
    score: float
    met: bool
    met_label: str
    missing_label: str
    next_event: str


@dataclass(frozen=True)
class DecisionResult:
    """Decisión final de Atlas para un símbolo, con su explicación accionable."""

    symbol: str
    mode: str
    decision: str
    confidence: float  # 0-100, confianza acumulada ponderada
    atlas_score: float
    momentum_score: float
    money_flow_score: Optional[float]
    met_conditions: List[str]
    missing_conditions: List[str]
    next_events: List[str]
    unavailable_conditions: List[str]


class DecisionEngine:
    """Combina Atlas Score, Momentum Score y Money Flow Score en una decisión final."""

    def __init__(
        self,
        collector: Optional[DataCollector] = None,
        money_flow_engine: Optional[MoneyFlowEngine] = None,
        mode: str = STANDARD,
    ) -> None:
        if mode not in WEIGHT_PROFILES:
            raise ValueError(f"mode desconocido: '{mode}'. Disponibles: {list(WEIGHT_PROFILES)}")

        self._collector = collector or DataCollector(YahooFinanceProvider())
        # El Money Flow Engine se escanea una vez, externamente, sobre el
        # universo que corresponda, y se inyecta ya calculado: recalcularlo
        # por cada símbolo sería carísimo y no es su responsabilidad.
        self._money_flow_engine = money_flow_engine
        self.mode = mode

    def _money_flow_score_for_sector(self, sector: Optional[str]) -> Optional[float]:
        if self._money_flow_engine is None or not sector:
            return None
        for group in self._money_flow_engine.top(10_000):
            if group.name == sector:
                return group.money_flow_score
        return None

    def _intraday_high(self, symbol: str) -> Optional[float]:
        try:
            intraday = self._collector.get_history(symbol, period="1d", interval="5m")
        except (ProviderError, QuoteNotFoundError):
            return None
        if intraday.empty:
            return None
        return float(intraday["High"].max())

    def _build_conditions(self, symbol: str, quote, atlas, momentum) -> Tuple[List[Condition], List[str]]:
        rvol = momentum.component("relative_volume")
        gap = momentum.component("gap_pct")
        liquidity = momentum.component("liquidity")
        vwap = atlas.component("vwap_distance")

        money_flow_score = self._money_flow_score_for_sector(quote.sector)
        intraday_high = self._intraday_high(symbol)

        conditions = [
            Condition(
                name="momentum",
                score=momentum.momentum_score,
                met=momentum.momentum_score >= MOMENTUM_STRONG,
                met_label="Momentum fuerte",
                missing_label="Momentum fuerte",
                next_event=f"Que el Momentum Score supere {MOMENTUM_STRONG:.0f} (hoy: {momentum.momentum_score:.1f})",
            ),
            Condition(
                name="rvol",
                score=rvol.score,
                met=rvol.score >= RVOL_HIGH,
                met_label="RVOL > umbral",
                missing_label="RVOL > umbral",
                next_event="Que el volumen relativo (RVOL) se dispare por encima del promedio reciente",
            ),
            Condition(
                name="vwap",
                score=vwap.score,
                met=vwap.score >= VWAP_CONFIRMED,
                met_label="Confirmación sobre VWAP",
                missing_label="Confirmación sobre VWAP",
                next_event="Que el precio se sostenga por encima del VWAP intradía",
            ),
            Condition(
                name="gap",
                score=gap.score,
                met=gap.score >= GAP_FAVORABLE,
                met_label="Gap favorable",
                missing_label="Gap favorable",
                next_event="Que el gap respecto al cierre anterior se vuelva favorable",
            ),
            Condition(
                name="liquidity",
                score=liquidity.score,
                met=liquidity.score >= LIQUIDITY_HIGH,
                met_label="Alta liquidez",
                missing_label="Alta liquidez",
                next_event="Que el volumen en dólares negociado aumente",
            ),
            Condition(
                name="atlas_score",
                score=atlas.total,
                met=atlas.total >= ATLAS_GOOD,
                met_label="Buen Atlas Score",
                missing_label="Buen Atlas Score",
                next_event=f"Que el Atlas Score supere {ATLAS_GOOD:.0f} (hoy: {atlas.total:.1f})",
            ),
        ]

        unavailable: List[str] = []

        if money_flow_score is not None:
            conditions.append(
                Condition(
                    name="money_flow",
                    score=money_flow_score,
                    met=money_flow_score >= MONEY_FLOW_POSITIVE,
                    met_label="Money Flow positivo",
                    missing_label="Money Flow positivo",
                    next_event="Que el sector muestre Money Flow positivo",
                )
            )
        else:
            unavailable.append("Money Flow (sin sector clasificado o sin escaneo de sector disponible)")

        if intraday_high is not None:
            # Puntaje continuo: 50 = precio exactamente en el máximo intradía,
            # +25 puntos por cada 1% que el precio lo supere (y viceversa).
            distance_pct = (quote.last_price - intraday_high) / intraday_high * 100
            breakout_score = _clamp(50 + distance_pct * 25)
            conditions.append(
                Condition(
                    name="breakout",
                    score=breakout_score,
                    met=breakout_score >= BREAKOUT_CONFIRMED,
                    met_label="Ruptura del máximo intradía",
                    missing_label="Ruptura del máximo intradía",
                    next_event=f"Que el precio rompa el máximo intradía (hoy: {intraday_high:.2f})",
                )
            )
        else:
            unavailable.append("Ruptura del máximo intradía (sin datos intradía disponibles)")

        return conditions, unavailable

    def decide(self, symbol: str) -> DecisionResult:
        """Calcula la confianza acumulada de un símbolo y produce la decisión final."""
        quote = self._collector.get_quote(symbol)
        atlas = calculate_atlas_score(symbol, self._collector)
        momentum = calculate_momentum_score(symbol, self._collector)
        money_flow_score = self._money_flow_score_for_sector(quote.sector)

        conditions, unavailable = self._build_conditions(symbol, quote, atlas, momentum)
        weights = WEIGHT_PROFILES[self.mode]

        # Confianza acumulativa: cada factor aporta weight * score (0-100),
        # no weight completo o cero según un umbral. Si algún factor no está
        # disponible (money_flow, breakout), su peso se redistribuye
        # proporcionalmente entre los factores sí disponibles.
        available_weight = sum(weights[c.name] for c in conditions)
        confidence = (
            round(sum(weights[c.name] * c.score for c in conditions) / available_weight, 1)
            if available_weight
            else 0.0
        )

        if confidence >= CONFIDENCE_BUY_FLOOR:
            decision = COMPRAR
        elif confidence <= CONFIDENCE_DISCARD_CEILING:
            decision = DESCARTAR
        else:
            decision = VIGILAR

        met_conditions = [c.met_label for c in conditions if c.met]
        missing_conditions = [c.missing_label for c in conditions if not c.met]
        next_events = [c.next_event for c in conditions if not c.met]

        return DecisionResult(
            symbol=symbol,
            mode=self.mode,
            decision=decision,
            confidence=confidence,
            atlas_score=atlas.total,
            momentum_score=momentum.momentum_score,
            money_flow_score=money_flow_score,
            met_conditions=met_conditions,
            missing_conditions=missing_conditions,
            next_events=next_events,
            unavailable_conditions=unavailable,
        )
