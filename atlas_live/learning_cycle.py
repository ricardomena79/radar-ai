"""Ciclo de aprendizaje de Atlas Live.

Despierta a Learning Engine (Accuracy Tracker + Pattern Evolution +
Calibration Advisor, ya unificados detrás de la fachada LearningEngine)
después del cierre de la sesión regular, usando exclusivamente los datos
que Decision Recorder ya escribió durante el día en la Knowledge Base.

No modifica ningún algoritmo de aprendizaje: solo construye los objetos que
Learning Engine ya espera recibir, llama a generate_learning_report() una
vez, y persiste el resultado en atlas.knowledge.learning_report_store.

Las propuestas de calibración que resulten se envían a Calibration Manager
como recomendaciones "Pendiente" -- exactamente el mismo flujo de
aprobación humana que ya existe (review -> approve/reject -> implement).
Este módulo nunca aprueba ni aplica ningún cambio por sí solo.
"""

import dataclasses
from datetime import datetime, timezone
from typing import Any, Dict

from atlas.calibration_manager.calibration_manager import CalibrationManager
from atlas.knowledge import KnowledgeEngine
from atlas.knowledge.learning_report_store import LearningReportRecord
from atlas.knowledge.pattern_store import PATTERN_ACTIVE, PatternRegistry
from atlas.learning.learning_engine import LearningEngine


def _proposal_to_kwargs(proposal: Any) -> Dict[str, Any]:
    """CalibrationProposal y CalibrationManager.submit_recommendation() comparten
    los mismos campos, salvo `generated_at` (que submit_recommendation no acepta)."""
    data = dataclasses.asdict(proposal)
    data.pop("generated_at", None)
    return data


def _overall_accuracy_pct(report) -> "float | None":
    todas = report.overall_accuracy.breakdown.get("todas", {})
    accuracy = todas.get("accuracy")
    return round(accuracy * 100, 1) if accuracy is not None else None


def _is_data_sufficient(report) -> bool:
    """Lee el propio umbral de Accuracy Tracker (MIN_SAMPLE_SIZE) -- no inventa uno nuevo.
    Si no hay ni una sola predicción, se considera insuficiente aunque
    'todas' ni siquiera exista en el breakdown."""
    todas = report.overall_accuracy.breakdown.get("todas")
    if todas is None:
        return False
    return not todas.get("insufficient_sample", True)


def _build_executive_summary(
    report, events_analyzed: int, patterns_analyzed: int, patterns_confirmed: int,
    proposals_count: int, data_sufficient: bool,
) -> str:
    accuracy_pct = _overall_accuracy_pct(report)
    accuracy_txt = f"{accuracy_pct}%" if accuracy_pct is not None else "sin calcular (muestra insuficiente)"

    prefix = (
        "⚠ Datos insuficientes todavía para medir precisión con confianza -- esto no es un error, "
        "es un día con pocos eventos registrados. "
        if not data_sufficient else ""
    )

    return (
        f"{prefix}"
        f"Atlas evaluó {events_analyzed} predicciones con resultado conocido. "
        f"Precisión general: {accuracy_txt}. "
        f"Se revisaron {patterns_analyzed} patrones, de los cuales {patterns_confirmed} están confirmados "
        f"(estado \"{PATTERN_ACTIVE}\"). "
        f"Se generaron {proposals_count} propuesta(s) de calibración, enviadas a revisión humana."
    )


def run_learning_cycle() -> LearningReportRecord:
    """Ejecuta un ciclo completo de aprendizaje y devuelve el reporte ya persistido."""
    knowledge = KnowledgeEngine()
    pattern_registry = PatternRegistry()
    calibration_manager = CalibrationManager()

    try:
        learning_engine = LearningEngine(knowledge_engine=knowledge, pattern_registry=pattern_registry)
        report = learning_engine.generate_learning_report()

        for proposal in report.calibration_proposals:
            try:
                calibration_manager.submit_recommendation(**_proposal_to_kwargs(proposal))
            except Exception:
                # Una propuesta individual mal formada no debe tumbar el resto del ciclo.
                continue

        patterns_confirmed = sum(1 for p in report.pattern_reports if p.current_state == PATTERN_ACTIVE)
        data_sufficient = _is_data_sufficient(report)

        record = LearningReportRecord(
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            generated_at=report.generated_at,
            session_evaluated="REGULAR",
            overall_accuracy=dataclasses.asdict(report.overall_accuracy),
            accuracy_by_decision=dataclasses.asdict(report.accuracy_by_decision),
            pattern_reports=[dataclasses.asdict(p) for p in report.pattern_reports],
            calibration_proposals=[
                {**dataclasses.asdict(p), "status_at_generation": "PENDIENTE"} for p in report.calibration_proposals
            ],
            events_analyzed_count=report.overall_accuracy.sample_size,
            patterns_analyzed_count=len(report.pattern_reports),
            patterns_confirmed_count=patterns_confirmed,
            calibration_proposals_count=len(report.calibration_proposals),
            executive_summary=_build_executive_summary(
                report,
                events_analyzed=report.overall_accuracy.sample_size,
                patterns_analyzed=len(report.pattern_reports),
                patterns_confirmed=patterns_confirmed,
                proposals_count=len(report.calibration_proposals),
                data_sufficient=data_sufficient,
            ),
            data_sufficient=data_sufficient,
        )
        knowledge.learning_reports.record_report(record)
        return record
    finally:
        knowledge.close()
        calibration_manager.close()
