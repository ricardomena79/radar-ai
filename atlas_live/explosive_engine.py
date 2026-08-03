"""Radar Explosivo: motor de detección de momentum intradía.

Vive completamente dentro de atlas_live y es independiente de Atlas Core:
no llama a Decision Engine, no usa `display_decision` y no responde "¿Atlas
compraría esto?". Responde una pregunta distinta y más estrecha: ¿qué tan
probable es que este símbolo tenga un movimiento fuerte en los próximos
5-10 minutos? No busca las mejores empresas -- busca las que tienen más
probabilidad de moverse ya, aunque sean pequeñas y de baja calidad.

Reutiliza (sin modificar ni duplicar) datos que Atlas Core ya calculó para
el mismo símbolo en el mismo ciclo de escaneo (`Quote`, `MomentumResult`,
el money flow de su sector) -- no repite ninguna llamada a la red ni
reimplementa ningún indicador (RSI, ATR, VWAP). Los únicos cálculos propios
de este módulo son aritmética trivial sobre datos crudos (gap %, RVOL, $
volumen) que no dependen de ninguna lógica de Atlas Core.

Diseño en 3 etapas:
  A) Filtros de elegibilidad (pasa/no pasa) -- los umbrales están en
     `explosive_config.json`, no en este archivo. Cada símbolo deja un
     rastro (`stage_trace`) de qué etapas superó y en cuál, si acaso,
     quedó descartado -- eso es lo que consume `explosive_diagnostics.py`
     para el modo Diagnóstico. Este rastro es pura instrumentación: no
     cambia qué umbral se evalúa, en qué orden, ni el resultado final de
     ningún símbolo.
  B) Puntaje ponderado a partir del registro de factores "enchufables" de
     `explosive_factors.py` -- agregar un factor nuevo no requiere tocar
     esta etapa.
  C) Ajuste por tamaño: penaliza de forma continua (no binaria) a las
     empresas grandes, con una excepción explícita y justificada en el
     texto de razones cuando el catalizador es extraordinario.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from atlas.data.models.quote import Quote
from atlas.engine.momentum_engine import MomentumResult

from atlas_live.explosive_config import load_config
from atlas_live.explosive_factors import FACTORS, ExplosiveInputs

# Orden real en que se evalúan los filtros de la Etapa A. Es la única
# fuente de verdad sobre el orden del embudo -- tanto `evaluate()` como
# `explosive_diagnostics.py` lo usan, así que no pueden desincronizarse.
STAGE_LABELS: List[tuple] = [
    ("price", "Filtro de precio"),
    ("liquidity", "Filtro de liquidez"),
    ("rvol", "Filtro de volumen relativo (RVOL)"),
    ("movement", "Filtro de gap / cambio %"),
    ("volatility", "Filtro de volatilidad"),
    ("size", "Filtro de tamaño (market cap)"),
]
STAGE_ORDER: List[str] = [key for key, _ in STAGE_LABELS]


@dataclass(frozen=True)
class ExplosiveResult:
    eligible: bool
    score: Optional[float] = None
    reasons: List[str] = field(default_factory=list)
    excluded_reason: Optional[str] = None
    is_size_exception: bool = False
    # --- Instrumentación para el modo Diagnóstico (no afecta la decisión) ---
    failed_stage: Optional[str] = None
    stage_trace: List[str] = field(default_factory=list)
    metrics: Dict[str, Optional[float]] = field(default_factory=dict)


def _gap_pct(quote: Quote) -> Optional[float]:
    if not quote.open or not quote.previous_close:
        return None
    return (quote.open - quote.previous_close) / quote.previous_close * 100


def _relative_volume(quote: Quote) -> Optional[float]:
    if not quote.volume or not quote.average_volume:
        return None
    return quote.volume / quote.average_volume


def _size_label(market_cap: Optional[float]) -> str:
    if market_cap is None:
        return "tamaño desconocido"
    if market_cap < 300_000_000:
        return "microcap"
    if market_cap < 2_000_000_000:
        return "small cap"
    if market_cap < 10_000_000_000:
        return "mid cap"
    if market_cap < 200_000_000_000:
        return "large cap"
    return "mega cap"


def _size_factor(market_cap: Optional[float], cfg: Dict[str, Any]) -> float:
    """Penalización continua por tamaño: entre más grande la empresa, menor el factor.

    Interpola en escala logarítmica entre `small_cap_reference` (factor
    máximo, sin penalización) y `mega_cap_reference` (factor mínimo). No
    es un corte binario: una small cap con señales moderadas puede superar
    a una mega cap con señales fuertes, que es el comportamiento buscado
    (velocidad por encima de calidad de la empresa).
    """
    sf = cfg["size_factor"]
    max_f, min_f = sf["max_factor"], sf["min_factor"]
    if not market_cap or market_cap <= 0:
        return max_f

    low, high = sf["small_cap_reference"], sf["mega_cap_reference"]
    if market_cap <= low:
        return max_f
    if market_cap >= high:
        return min_f

    t = (math.log(market_cap) - math.log(low)) / (math.log(high) - math.log(low))
    return max_f - t * (max_f - min_f)


def evaluate(
    quote: Quote,
    momentum_result: MomentumResult,
    sector_money_flow_score: Optional[float],
    config: Optional[Dict[str, Any]] = None,
) -> ExplosiveResult:
    """Evalúa un símbolo para el Radar Explosivo. No lanza excepciones por
    datos faltantes: cualquier campo ausente resulta en `eligible=False`
    con `excluded_reason` explicando por qué, no en un error."""
    cfg = config or load_config()
    gates = cfg["gates"]

    gap_pct = _gap_pct(quote)
    rvol = _relative_volume(quote)
    dollar_volume = (quote.last_price or 0) * (quote.volume or 0)
    volatility_score = momentum_result.component("atr").score
    market_cap = quote.market_cap
    change_pct = quote.change_percent or 0.0

    # Se calculan siempre, pasen o no los filtros: el modo Diagnóstico los
    # necesita también para las descartadas (para mostrar "Gap +3%, RVOL
    # 1.4x" junto al motivo de exclusión).
    metrics: Dict[str, Any] = {
        "price": quote.last_price,
        "gap_pct": gap_pct,
        "change_pct": change_pct,
        "relative_volume": rvol,
        "dollar_volume": dollar_volume,
        "volatility_score": volatility_score,
        "market_cap": market_cap,
        # Trazabilidad de precio (2026-08-02, ver DATA_FUSION_ENGINE_PROPUESTA.md,
        # addendum) -- puramente informativo para la Cabina del Piloto, no
        # participa en ningún filtro de elegibilidad ni en el Ranking Score.
        "price_type": quote.price_type,
        "source": quote.source,
        "market_state": quote.market_state,
        "price_regular": quote.price_regular,
        "price_premarket": quote.price_premarket,
        "price_afterhours": quote.price_afterhours,
        "price_overnight": quote.price_overnight,
        "price_as_of": quote.timestamp.isoformat() if quote.timestamp else None,
    }

    stage_trace: List[str] = []

    def _fail(stage: str, reason: str) -> ExplosiveResult:
        return ExplosiveResult(
            eligible=False,
            excluded_reason=reason,
            failed_stage=stage,
            stage_trace=list(stage_trace),
            metrics=metrics,
        )

    # --- Etapa A: elegibilidad (mismo orden y mismos umbrales de siempre) ---
    if not quote.last_price or quote.last_price < gates["min_price"]:
        return _fail("price", "Precio por debajo del mínimo operable")
    stage_trace.append("price")

    if dollar_volume < gates["min_dollar_volume"]:
        return _fail("liquidity", "Liquidez insuficiente para operar intradía")
    stage_trace.append("liquidity")

    if rvol is None or rvol < gates["min_rvol"]:
        return _fail("rvol", "Volumen relativo insuficiente (sin interés anómalo)")
    stage_trace.append("rvol")

    if max(abs(gap_pct or 0.0), abs(change_pct)) < gates["min_abs_gap_or_change_pct"]:
        return _fail("movement", "Sin movimiento significativo todavía")
    stage_trace.append("movement")

    if volatility_score < gates["min_volatility_score"]:
        return _fail("volatility", "Volatilidad demasiado baja (instrumento lento)")
    stage_trace.append("volatility")

    is_large_cap = market_cap is not None and market_cap >= gates["large_cap_ceiling"]
    is_size_exception = False
    if is_large_cap:
        meets_exception = (
            abs(gap_pct or 0.0) >= gates["mega_cap_exception_gap_pct"]
            and (rvol or 0) >= gates["mega_cap_exception_rvol"]
        )
        if not meets_exception:
            return _fail(
                "size",
                "Market cap grande sin catalizador extremo suficiente para calificar como excepción",
            )
        is_size_exception = True
    stage_trace.append("size")

    # --- Etapa B: puntaje ponderado (factores enchufables) ---
    inputs = ExplosiveInputs(
        quote=quote,
        momentum_result=momentum_result,
        sector_money_flow_score=sector_money_flow_score,
        gap_pct=gap_pct or 0.0,
        relative_volume=rvol,
        config=cfg,
    )

    weights = cfg["weights"]
    weighted_sum = 0.0
    weight_total = 0.0
    reasons: List[str] = []
    for factor in FACTORS:
        result = factor.compute(inputs)
        if result.score is None:
            continue
        w = weights.get(factor.weight_key, 0.0)
        weighted_sum += result.score * w
        weight_total += w
        if result.reason:
            reasons.append(result.reason)

    base_score = (weighted_sum / weight_total) if weight_total > 0 else 0.0

    # --- Etapa C: ajuste por tamaño ---
    size_factor = _size_factor(market_cap, cfg)
    final_score = round(base_score * size_factor, 1)

    size_label = _size_label(market_cap)
    if is_size_exception:
        reasons.insert(
            0,
            f"Excepción por catalizador extremo: {size_label} con Gap {gap_pct:+.1f}% y "
            f"RVOL {rvol:.1f}x superó el filtro de tamaño (normalmente quedaría descartada)",
        )
    elif size_label in ("microcap", "small cap"):
        reasons.append(f"Market cap {size_label} — favorece movimientos rápidos")

    return ExplosiveResult(
        eligible=True,
        score=final_score,
        reasons=reasons,
        is_size_exception=is_size_exception,
        stage_trace=stage_trace,
        metrics=metrics,
    )
