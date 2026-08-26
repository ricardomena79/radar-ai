"""Composición de inputs para `atlas_decision_core.decide()` (2026-08-26,
U3-B). Funciones puras que REDUCEN estructuras ya existentes del pipeline
Tradier (`live_opportunities()`) y del pipeline Yahoo (`_score_symbol()`'s
row dict) a `DecisionFeatures`/`DecisionScores`/`DecisionEvidence` -- nunca
inventan un valor cuando falta un dato, siempre `None` explícito.

Este módulo NO importa `decision_engine`, `explosive_engine`, Memory Engine
ni ningún proveedor -- recibe todo ya calculado, mismo espíritu de pureza
que `atlas_decision_core.py`."""

from __future__ import annotations

from typing import Any, Dict, Optional

from atlas_live.core.atlas_decision_core import CandidateSnapshot, DecisionEvidence, DecisionFeatures, DecisionScores


def candidate_from_radar_row(o: Dict[str, Any], market_date: str, estado_validacion: str) -> CandidateSnapshot:
    """Pipeline Tradier -- `o` es una fila ya enriquecida de
    `/api/radar-oportunidades` (server.py), después de resolver el precio
    en vivo pero ANTES de calcular `estado_final`."""
    return CandidateSnapshot(
        ticker=o["ticker"],
        market_date=market_date,
        tiene_precio_actual=o.get("price_actual") is not None,
        estado_validacion=estado_validacion,
    )


def features_from_radar_row(o: Dict[str, Any]) -> DecisionFeatures:
    return DecisionFeatures(
        stage=o.get("stage"),
        direction=o.get("direction"),
        change_pct_confiable=o.get("change_pct_confiable"),
        sector_flow_active=o.get("dinero_entra_sector"),
        # El pipeline Tradier no pasa por `explosive_engine.py` -- sin dato,
        # nunca inventado.
        explosive_eligible=None,
        explosive_excluded_reason=None,
    )


def scores_from_radar_row(o: Dict[str, Any]) -> DecisionScores:
    # `/api/radar-oportunidades` hoy no cruza catalyst_score/mrna_similarity
    # (eso vive en `/api/catalyst-events`, un endpoint distinto) -- quedan
    # `None` explícito, no un 0 inventado.
    return DecisionScores()


def evidence_from_radar_row(o: Dict[str, Any], historical_evidence: Optional[dict]) -> DecisionEvidence:
    return DecisionEvidence(
        historical_evidence=historical_evidence,
        # Memory Engine/catalyst no participan en el pipeline Tradier.
        memory_engine_semaforo=None,
        memory_engine_probability_pct=None,
        catalyst_technical_alignment=None,
    )


def candidate_from_scan_row(row: Dict[str, Any], market_date: str) -> CandidateSnapshot:
    """Pipeline Yahoo -- `row` es el dict que ya arma `_score_symbol()`/
    `get_symbol_detail()`. `tiene_precio_actual` = hay un precio numérico
    real, mismo criterio que el pipeline Tradier."""
    return CandidateSnapshot(
        ticker=row["symbol"],
        market_date=market_date,
        tiene_precio_actual=row.get("price") is not None,
    )


def features_from_scan_row(row: Dict[str, Any], tradier_row: Optional[Dict[str, Any]]) -> DecisionFeatures:
    """Si el símbolo TAMBIÉN es una candidata real de Tradier hoy
    (`tradier_row` no es `None`), su `stage`/`direction`/`change_pct_confiable`
    reales alimentan la misma clasificación que usaría
    `/api/radar-oportunidades` para ese mismo ticker -- decisión idéntica
    para la misma candidata (punto 8 de U3-B). Si no, `stage=None`: Atlas
    nunca detectó una señal técnica real ahí -- `classify_final_priority()`
    ya resuelve `stage=None` a `NO_TOCAR` por su propia lógica existente,
    sin que este módulo tenga que inventar una regla nueva."""
    explosive = row.get("explosive") or {}
    if tradier_row is not None:
        return DecisionFeatures(
            stage=tradier_row.get("stage"),
            direction=tradier_row.get("direction"),
            change_pct_confiable=tradier_row.get("change_pct_confiable"),
            sector_flow_active=tradier_row.get("dinero_entra_sector"),
            explosive_eligible=explosive.get("eligible"),
            explosive_excluded_reason=explosive.get("excluded_reason"),
        )
    return DecisionFeatures(
        stage=None,
        direction=None,
        change_pct_confiable=None,
        sector_flow_active=None,
        explosive_eligible=explosive.get("eligible"),
        explosive_excluded_reason=explosive.get("excluded_reason"),
    )


def scores_from_scan_row(row: Dict[str, Any]) -> DecisionScores:
    explosive = row.get("explosive") or {}
    return DecisionScores(
        atlas_score=row.get("atlas_score"),
        momentum_score=row.get("momentum_score"),
        money_flow_score=row.get("money_flow_score"),
        explosive_score=explosive.get("score"),
    )


def evidence_from_scan_row(
    row: Dict[str, Any],
    memory_engine_semaforo: Optional[str] = None,
    memory_engine_probability_pct: Optional[float] = None,
) -> DecisionEvidence:
    return DecisionEvidence(
        historical_evidence=None,
        memory_engine_semaforo=memory_engine_semaforo,
        memory_engine_probability_pct=memory_engine_probability_pct,
        catalyst_technical_alignment=None,
    )
