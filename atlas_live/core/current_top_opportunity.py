"""CURRENT TOP OPPORTUNITY -- selector puro y determinista (2026-08-26,
FASE 1/5, autorizado explícitamente).

Resuelve el problema auditado: hoy existen al menos DOS productores
independientes de "mejor oportunidad" (`live_integration.build_live_ranking()`
para la Cabina, `explosivoRows()` para el dashboard legacy), ninguno con
memoria entre ciclos, y el desempate real de la mayoría de las candidatas
(`ranking_score` empatado en el sentinel `(-1,0,0,0)` mientras el Memory
Store no tiene evidencia real) termina decidiéndose por el orden de llegada
de red (`as_completed()`/orden de dict) -- ruido, no señal.

Esta FASE 1/5 construye ÚNICAMENTE la función pura de selección. NO se
conecta a ninguna UI, NO persiste nada, NO toca ningún productor legacy --
esos siguen calculando sus scores exactamente igual, ahora como INPUT.

Criterio de orden, en este orden estricto (el primero que no empata,
decide -- nunca se usa un peso/suma inventada):

    1. `atlas_decision.decision` -- OPORTUNIDAD_PRIORITARIA > VIGILAR >
       PREPARACION > NO_TOCAR (el Atlas Decision Core, ya única fuente de
       verdad para la categoría, U3-A/B -- nunca se reimplementa acá).
    2. `ranking_score` (Memory Engine, 4 niveles ya existentes) -- gana
       cuando tiene evidencia real diferenciadora.
    3. `atlas_score` -- confirmado por auditoría (2026-08-26) como el
       score más confiable y siempre disponible hoy (se calcula para TODO
       símbolo escaneado, con datos reales de quote/historial, sin
       depender del Memory Store).
    4. `momentum_score` -- mismo criterio que (3), segundo más confiable.
    5. Ticker alfabético -- ÚLTIMO recurso, determinista, nunca orden de
       red/dict/futures (prohibido explícitamente).

Ningún peso, umbral, histéresis, cooldown ni permanencia mínima se define
acá -- eso queda para una fase posterior, con su propia autorización."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

CORE_METHODOLOGY_VERSION = "v1_decision_then_ranking_then_atlas_then_momentum_then_alpha"

_DECISION_PRIORITY = {
    "OPORTUNIDAD_PRIORITARIA": 3,
    "VIGILAR": 2,
    "PREPARACION": 1,
    "NO_TOCAR": 0,
}


@dataclass(frozen=True)
class CandidateForSelection:
    """Insumo del selector -- ya reducido, ninguna candidata trae más que
    lo que el criterio de orden necesita. `ranking_score` es el mismo
    4-tuple que ya produce `ranking_score.compute_ranking_score()`
    (nivel1_wilson_lower_bound, nivel2_condiciones_adicionales,
    nivel3_percentil_dentro_de_banda, nivel4_score_radar) -- se acepta como
    tupla simple, sin importar `atlas_live.memory`, para no acoplar este
    módulo al productor legacy."""

    ticker: str
    decision: str
    ranking_score: Tuple[float, float, float, float]
    atlas_score: Optional[float] = None
    momentum_score: Optional[float] = None


@dataclass(frozen=True)
class RankedEntry:
    ticker: str
    decision: str
    ranking_score: Tuple[float, float, float, float]
    atlas_score: Optional[float]
    momentum_score: Optional[float]
    posicion: int


@dataclass(frozen=True)
class TopOpportunitySelection:
    ticker: str
    decision: str
    posicion: int
    criterio_decisivo: str  # "atlas_decision" | "ranking_score" | "atlas_score" | "momentum_score" | "alfabetico"
    score_final: Optional[float]
    motivo_seleccion: str
    componentes_utilizados: Dict[str, Any]
    ranking_completo: List[RankedEntry]
    runner_up_ticker: Optional[str]
    runner_up_score: Optional[float]
    candidatos_considerados: int
    methodology_version: str


def _score_or_floor(value: Optional[float]) -> float:
    """Un score ausente nunca puede ganarle a uno real -- se trata como el
    piso más bajo posible, nunca se inventa un valor neutro."""
    return value if value is not None else -1.0


def _sort_key(c: CandidateForSelection) -> Tuple[int, Tuple[float, float, float, float], float, float]:
    return (
        _DECISION_PRIORITY.get(c.decision, -1),
        c.ranking_score,
        _score_or_floor(c.atlas_score),
        _score_or_floor(c.momentum_score),
    )


def _criterio_decisivo(ganador: CandidateForSelection, resto: List[CandidateForSelection]) -> Tuple[str, Optional[float], str]:
    """Determina, comparando al ganador contra el resto, cuál fue el
    primer nivel del criterio que realmente lo diferenció -- para que
    `motivo_seleccion` sea exacto, no un texto genérico."""
    if not resto:
        return "atlas_decision", None, f"Única candidata considerada ({ganador.ticker})."

    prioridad_g = _DECISION_PRIORITY.get(ganador.decision, -1)
    if any(_DECISION_PRIORITY.get(c.decision, -1) < prioridad_g for c in resto):
        return (
            "atlas_decision", None,
            f"Decisión de Atlas ({ganador.decision}) superior a la de al menos una candidata restante.",
        )

    if any(c.ranking_score < ganador.ranking_score for c in resto):
        return (
            "ranking_score", ganador.ranking_score[0],
            f"ranking_score ({ganador.ranking_score}) superior dentro del mismo nivel de decisión ({ganador.decision}).",
        )

    ganador_atlas = _score_or_floor(ganador.atlas_score)
    if any(_score_or_floor(c.atlas_score) < ganador_atlas for c in resto):
        return (
            "atlas_score", ganador.atlas_score,
            f"ranking_score empatado -- desempatado por atlas_score ({ganador.atlas_score}).",
        )

    ganador_momentum = _score_or_floor(ganador.momentum_score)
    if any(_score_or_floor(c.momentum_score) < ganador_momentum for c in resto):
        return (
            "momentum_score", ganador.momentum_score,
            f"ranking_score y atlas_score empatados -- desempatado por momentum_score ({ganador.momentum_score}).",
        )

    return (
        "alfabetico", None,
        f"Empate total en decision/ranking_score/atlas_score/momentum_score -- desempate final por orden alfabético del ticker.",
    )


def select_current_top_opportunity(
    candidates: List[CandidateForSelection],
    methodology_version: str = CORE_METHODOLOGY_VERSION,
) -> Optional[TopOpportunitySelection]:
    """Función PURA -- sin red, sin DB, sin estado global. `candidates` en
    cualquier orden de entrada produce SIEMPRE el mismo resultado (ver
    tests A/B/C). Nunca usa orden de llegada de red/dict/futures.

    `None` si `candidates` está vacío -- nunca se inventa un ganador sin
    candidatas."""
    if not candidates:
        return None

    # Paso 1: orden alfabético ascendente -- sort ESTABLE, sobrevive como
    # desempate de última instancia después del segundo sort (Python
    # preserva el orden relativo entre elementos empatados).
    ordenados = sorted(candidates, key=lambda c: c.ticker)

    # Paso 2: sort estable descendente por el criterio real -- el empate
    # del Paso 1 (alfabético) queda como desempate final automático.
    ordenados.sort(key=_sort_key, reverse=True)

    ganador = ordenados[0]
    resto = ordenados[1:]
    criterio, score_final, motivo = _criterio_decisivo(ganador, resto)

    ranking_completo = [
        RankedEntry(
            ticker=c.ticker, decision=c.decision, ranking_score=c.ranking_score,
            atlas_score=c.atlas_score, momentum_score=c.momentum_score, posicion=i + 1,
        )
        for i, c in enumerate(ordenados)
    ]

    runner_up = ordenados[1] if len(ordenados) > 1 else None
    runner_up_score: Optional[float] = None
    if runner_up is not None:
        if criterio == "ranking_score":
            runner_up_score = runner_up.ranking_score[0]
        elif criterio == "atlas_score":
            runner_up_score = runner_up.atlas_score
        elif criterio == "momentum_score":
            runner_up_score = runner_up.momentum_score

    return TopOpportunitySelection(
        ticker=ganador.ticker,
        decision=ganador.decision,
        posicion=1,
        criterio_decisivo=criterio,
        score_final=score_final,
        motivo_seleccion=motivo,
        componentes_utilizados={
            "decision": ganador.decision,
            "ranking_score": ganador.ranking_score,
            "atlas_score": ganador.atlas_score,
            "momentum_score": ganador.momentum_score,
        },
        ranking_completo=ranking_completo,
        runner_up_ticker=runner_up.ticker if runner_up else None,
        runner_up_score=runner_up_score,
        candidatos_considerados=len(candidates),
        methodology_version=methodology_version,
    )
