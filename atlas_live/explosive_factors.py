"""Registro de factores del Radar Explosivo.

Cada factor es una función independiente y "enchufable": recibe
`ExplosiveInputs` y devuelve un `FactorScore` (puntaje 0-100 o `None` si no
se pudo calcular, más una razón en lenguaje natural o `None` si no aporta
nada que valga la pena mostrar). El motor (`explosive_engine.py`) solo
itera `FACTORS` y pondera -- no conoce el detalle de ningún factor.

Para agregar un factor nuevo en el futuro (noticias, opciones, short
interest, etc.):
  1. Escribir una función `compute(inputs: ExplosiveInputs) -> FactorScore`.
  2. Agregarla a `FACTORS` con un `weight_key`.
  3. Agregar ese `weight_key` a la sección "weights" de
     `explosive_config.json`.
Nada más en este archivo ni en `explosive_engine.py` necesita cambiar.

Nota de honestidad: los textos de "razón" solo describen valores que
realmente se calcularon a partir del dato de este ciclo de escaneo (un
snapshot). No hay serie histórica intradía disponible en este nivel, así
que ningún factor afirma "acelerando" ni "creciendo" (eso implicaría
comparar contra un punto anterior en el tiempo que no se tiene) -- se
describe la magnitud actual tal cual es.
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from atlas.data.models.quote import Quote
from atlas.engine.momentum_engine import MomentumResult


@dataclass(frozen=True)
class ExplosiveInputs:
    """Todo lo que un factor puede necesitar. `config` da acceso a cualquier
    referencia adicional que un factor futuro necesite, sin tener que
    extender esta clase cada vez."""

    quote: Quote
    momentum_result: MomentumResult
    sector_money_flow_score: Optional[float]
    gap_pct: float
    relative_volume: Optional[float]
    config: Dict[str, Any]


@dataclass(frozen=True)
class FactorScore:
    score: Optional[float]  # 0-100, o None si el factor no se pudo calcular para este símbolo
    reason: Optional[str]   # explicación en lenguaje natural, o None si no aporta nada relevante


@dataclass(frozen=True)
class Factor:
    name: str
    weight_key: str
    compute: Callable[[ExplosiveInputs], FactorScore]


def _relative_volume(inputs: ExplosiveInputs) -> FactorScore:
    rvol = inputs.relative_volume
    if rvol is None:
        return FactorScore(score=None, reason=None)
    score = max(0.0, min(100.0, rvol * 12.5))
    reason = f"RVOL {rvol:.1f}x (volumen muy por encima de lo normal)" if rvol >= 2 else None
    return FactorScore(score=score, reason=reason)


def _volatility(inputs: ExplosiveInputs) -> FactorScore:
    score = inputs.momentum_result.component("atr").score
    if score >= 70:
        reason = "Alta volatilidad"
    elif score >= 50:
        reason = "Volatilidad por encima del promedio"
    else:
        reason = None
    return FactorScore(score=score, reason=reason)


def _momentum(inputs: ExplosiveInputs) -> FactorScore:
    score = inputs.momentum_result.component("rsi").score
    if score >= 70:
        reason = "Momentum fuerte"
    elif score >= 55:
        reason = "Momentum en marcha"
    else:
        reason = None
    return FactorScore(score=score, reason=reason)


def _gap(inputs: ExplosiveInputs) -> FactorScore:
    gap = inputs.gap_pct
    score = max(0.0, min(100.0, abs(gap) * 8))
    reason = f"Gap {gap:+.1f}%" if abs(gap) >= 2 else None
    return FactorScore(score=score, reason=reason)


def _vwap_distance(inputs: ExplosiveInputs) -> FactorScore:
    score = inputs.momentum_result.component("vwap_distance").score
    reason = "Sostenido sobre el precio promedio del día" if score >= 60 else None
    return FactorScore(score=score, reason=reason)


def _sector_money_flow(inputs: ExplosiveInputs) -> FactorScore:
    score = inputs.sector_money_flow_score
    if score is None:
        return FactorScore(score=None, reason=None)
    reason = "Sector con entrada de dinero fuerte" if score >= 65 else None
    return FactorScore(score=score, reason=reason)


def _float(inputs: ExplosiveInputs) -> FactorScore:
    float_shares = inputs.quote.float_shares
    if not float_shares:
        return FactorScore(score=None, reason=None)

    ref = inputs.config.get("float_factor", {})
    low = ref.get("low_float_reference", 20_000_000)
    high = ref.get("high_float_reference", 500_000_000)

    if float_shares <= low:
        score = 100.0
    elif float_shares >= high:
        score = 0.0
    else:
        score = 100.0 * (1 - (float_shares - low) / (high - low))

    reason = f"Float bajo ({float_shares / 1e6:.0f}M acciones en circulación)" if float_shares <= low * 2 else None
    return FactorScore(score=score, reason=reason)


FACTORS: List[Factor] = [
    Factor(name="relative_volume", weight_key="relative_volume", compute=_relative_volume),
    Factor(name="volatility", weight_key="volatility", compute=_volatility),
    Factor(name="momentum", weight_key="momentum", compute=_momentum),
    Factor(name="gap", weight_key="gap", compute=_gap),
    Factor(name="vwap_distance", weight_key="vwap_distance", compute=_vwap_distance),
    Factor(name="sector_money_flow", weight_key="sector_money_flow", compute=_sector_money_flow),
    Factor(name="float", weight_key="float", compute=_float),
]
