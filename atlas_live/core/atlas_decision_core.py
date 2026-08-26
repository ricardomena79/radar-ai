"""ATLAS DECISION CORE (2026-08-26, U3-A -- autorizado explícitamente).

Cerebro único de decisión de Atlas. Envuelve
`atlas_live.radar.priority_classifier.classify_final_priority()` -- NUNCA la
modifica, NUNCA duplica su lógica -- y agrega, por encima, la capacidad de
recibir `learned_evidence` (Fase 4/5) para calcular una `decision_shadow`
SIN que esa evidencia toque todavía la decisión real.

Puro por diseño: sin DB, sin red, sin Yahoo, sin Tradier, sin llamadas a
`explosive_engine.py`, Memory Engine ni `atlas.engine.decision_engine`. Todo
eso debe entregar sus datos YA reducidos a `DecisionFeatures`/
`DecisionScores`/`DecisionEvidence` antes de llegar acá (confirmado por
tests estructurales, ver `test_atlas_decision_core.py::test_I_.../test_J_...`).

`AtlasDecision.decision` es SIEMPRE uno de
`atlas_live.radar.priority_classifier.FINAL_STATES` -- nunca
COMPRAR/DESCARTAR (`decision_engine.py`) ni eligible/rojo/amarillo/verde
(`explosive_engine.py`/Memory Engine). Esos conceptos sobreviven únicamente
dentro de `DecisionFeatures`/`DecisionEvidence` como insumos, nunca como el
valor de `decision` en sí.

Shadow Mode (U2, aprobado): la MISMA llamada a `decide()` calcula, cuando se
provee `learned_evidence`, qué habría propuesto una política de
recalibración conservadora -- SOLO downgrade de un escalón
(OPORTUNIDAD_PRIORITARIA->VIGILAR, VIGILAR->PREPARACION), SOLO cuando
`validation_state=="VALIDACION_ROBUSTA"` (umbral ya existente, >=500, sin
inventar uno nuevo) Y el intervalo de Wilson COMPLETO queda por debajo del
baseline (`wilson_upper_bound_20_pct < baseline_pct_20` -- nunca una regla
de lift puntual). Mientras `apply_recalibration=False` (default hardcodeado
en esta fase, sin ninguna vía de configuración que lo cambie), `decision`
es SIEMPRE la salida cruda de `classify_final_priority()` -- `decision_shadow`
queda disponible en el resultado, pero nunca se filtra hacia `decision`."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from atlas_live.radar import priority_classifier as pc

CORE_METHODOLOGY_VERSION = "v1_wraps_priority_classifier"

# Política de recalibración shadow (U2) -- solo downgrade, un escalón, nunca
# NO_TOCAR directo, nunca upgrade.
_SHADOW_DOWNGRADE_ONE_TIER = {
    "OPORTUNIDAD_PRIORITARIA": "VIGILAR",
    "VIGILAR": "PREPARACION",
}

_LEARNED_EVIDENCE_CONFIDENCE = {
    "VALIDACION_ROBUSTA": "ALTA",
    "EN_VALIDACION": "MEDIA",
    "MUESTRA_INSUFICIENTE": "BAJA",
}


@dataclass(frozen=True)
class CandidateSnapshot:
    """Identidad + estado de mercado de la candidata en el momento de decidir."""

    ticker: str
    market_date: str
    tiene_precio_actual: bool
    estado_validacion: str = pc.VALIDACION_OK


@dataclass(frozen=True)
class DecisionFeatures:
    """Señales reducidas -- nunca un `Quote` crudo, nunca I/O detrás de estos campos."""

    stage: Optional[str]
    direction: Optional[str]
    change_pct_confiable: Optional[bool]
    sector_flow_active: Optional[bool] = None
    # Ex-decisión de `explosive_engine.py` (`eligible`/`excluded_reason`),
    # reclasificada como feature -- ver plan de unificación U1/U2.
    explosive_eligible: Optional[bool] = None
    explosive_excluded_reason: Optional[str] = None


@dataclass(frozen=True)
class DecisionScores:
    """Todos opcionales -- ninguno puede convertirse en un segundo decisor
    (ninguno es del tipo/vocabulario de `AtlasDecision.decision`)."""

    atlas_score: Optional[float] = None
    momentum_score: Optional[float] = None
    money_flow_score: Optional[float] = None
    # Ex-score de `explosive_engine.py`, reclasificado como score de entrada.
    explosive_score: Optional[float] = None
    catalyst_score: Optional[float] = None
    catalyst_opportunity_score: Optional[float] = None
    mrna_similarity_score: Optional[float] = None


@dataclass(frozen=True)
class DecisionEvidence:
    """`historical_evidence` mantiene la forma exacta que ya espera
    `classify_final_priority()` (de `historical_scoring.score_candidate()`).
    `memory_engine_semaforo`/`memory_engine_probability_pct` son la
    ex-decisión de Memory Engine, reclasificada como evidencia informativa."""

    historical_evidence: Optional[Dict[str, Any]] = None
    memory_engine_semaforo: Optional[str] = None
    memory_engine_probability_pct: Optional[float] = None
    catalyst_technical_alignment: Optional[bool] = None


@dataclass(frozen=True)
class AtlasDecision:
    decision: str
    decision_shadow: Optional[str]
    shadow_differs: bool
    reason: str
    confidence: str
    methodology_version: str
    decision_timestamp: datetime
    learned_evidence_used: bool
    features_snapshot: Dict[str, Any]
    scores_snapshot: Dict[str, Any]
    evidence_snapshot: Dict[str, Any]


def _compute_shadow_decision(decision: str, learned_evidence: Dict[str, Any]) -> str:
    """`decision` si no corresponde downgrade, o el escalón inmediatamente
    inferior si la evidencia lo justifica con Wilson + validación robusta.
    Nunca upgrade, nunca salta directo a NO_TOCAR, nunca usa el lift puntual
    como regla directa."""
    if decision not in _SHADOW_DOWNGRADE_ONE_TIER:
        return decision
    if not learned_evidence.get("available"):
        return decision
    if learned_evidence.get("validation_state") != "VALIDACION_ROBUSTA":
        return decision
    wilson_upper = learned_evidence.get("wilson_upper_bound_20_pct")
    baseline = learned_evidence.get("baseline_pct_20")
    if wilson_upper is None or baseline is None:
        return decision
    if wilson_upper < baseline:
        return _SHADOW_DOWNGRADE_ONE_TIER[decision]
    return decision


def _compute_confidence(
    learned_evidence: Optional[Dict[str, Any]],
    historical_evidence: Optional[Dict[str, Any]],
) -> str:
    """Reutiliza los niveles de validación ya existentes (Fase 4/5) -- nunca
    inventa un umbral nuevo. Puramente informativo, nunca afecta `decision`."""
    if learned_evidence and learned_evidence.get("available"):
        return _LEARNED_EVIDENCE_CONFIDENCE.get(learned_evidence.get("validation_state"), "SIN_EVIDENCIA")
    if historical_evidence and historical_evidence.get("grupo_existe"):
        return "MEDIA"
    return "SIN_EVIDENCIA"


def decide(
    candidate: CandidateSnapshot,
    features: DecisionFeatures,
    scores: Optional[DecisionScores] = None,
    evidence: Optional[DecisionEvidence] = None,
    learned_evidence: Optional[Dict[str, Any]] = None,
    *,
    apply_recalibration: bool = False,
    methodology_version: str = CORE_METHODOLOGY_VERSION,
) -> AtlasDecision:
    """Única función de decisión de Atlas. `decision` sale exclusivamente de
    `priority_classifier.classify_final_priority()` -- este core nunca
    reimplementa esa lógica. Mientras `apply_recalibration=False` (default
    de esta fase), `decision` es idéntica se provea o no `learned_evidence`
    -- la única diferencia observable es que `decision_shadow`/`shadow_differs`
    quedan poblados."""
    scores = scores or DecisionScores()
    evidence = evidence or DecisionEvidence()

    decision, reason = pc.classify_final_priority(
        stage=features.stage,
        direction=features.direction,
        change_pct_confiable=features.change_pct_confiable,
        tiene_precio_actual=candidate.tiene_precio_actual,
        sector_flow_active=features.sector_flow_active,
        historical_evidence=evidence.historical_evidence,
        estado_validacion=candidate.estado_validacion,
    )

    decision_shadow: Optional[str] = None
    shadow_differs = False
    if learned_evidence is not None:
        decision_shadow = _compute_shadow_decision(decision, learned_evidence)
        shadow_differs = decision_shadow != decision

    final_decision = decision
    if apply_recalibration and decision_shadow is not None and shadow_differs:
        final_decision = decision_shadow

    return AtlasDecision(
        decision=final_decision,
        decision_shadow=decision_shadow,
        shadow_differs=shadow_differs,
        reason=reason,
        confidence=_compute_confidence(learned_evidence, evidence.historical_evidence),
        methodology_version=methodology_version,
        decision_timestamp=datetime.now(timezone.utc),
        learned_evidence_used=learned_evidence is not None,
        features_snapshot=asdict(features),
        scores_snapshot=asdict(scores),
        evidence_snapshot={**asdict(evidence), "learned_evidence": learned_evidence},
    )
