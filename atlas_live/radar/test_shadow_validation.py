"""Tests de la Fase 2 (SHADOW -> VALIDACIÓN) del circuito LEK -- 2026-08-27,
autorizado explícitamente. DB temporal, sin red. `shadow_decision_log` +
`shadow_validation_report()` -- ver docstrings reales en
`atlas_live/radar/candidate_registry.py`.

Regla que estos tests demuestran, no solo describen: LEK sigue 100% en
Shadow Mode -- `record_shadow_decision`/`shadow_validation_report` son
exclusivamente de lectura/escritura de auditoría, nunca tocan
`candidate_outcome`, nunca cambian `apply_recalibration`, nunca pueden
alterar una decisión real."""

import inspect
import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.core import atlas_decision_core as adc
from atlas_live.radar import candidate_registry as reg
from atlas_live.radar import priority_classifier as pc

_ORIG = reg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_shadow_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None


def _restore():
    reg.DB_PATH = _ORIG


def _record_outcome_final(ticker, market_date, category):
    reg.record_outcome(
        ticker=ticker, market_date=market_date,
        run_up_before_detection_pct=None, max_price_after_detection=10.0,
        max_return_after_detection_pct=5.0, minutes_to_max=30.0,
        reached_20=False, reached_50=False, reached_100=False,
        category=category, is_final=True,
    )


# --- A: registrar correctamente un shadow_differs=True ---------------------

def test_A_registra_shadow_differs_true():
    _fresh()
    try:
        insertado = reg.record_shadow_decision(
            ticker="CRM", market_date="2026-08-27",
            decision="OPORTUNIDAD_PRIORITARIA", decision_shadow="VIGILAR",
            shadow_differs=True, validation_state="VALIDACION_ROBUSTA",
            sample_size=612, wilson_upper_bound_20_pct=0.5, baseline_pct_20=0.8,
        )
        assert insertado is True
        reporte = reg.shadow_validation_report()
        assert reporte["total_eventos_shadow_differs"] == 1
        assert reporte["eventos"][0]["ticker"] == "CRM"
        assert reporte["eventos"][0]["decision"] == "OPORTUNIDAD_PRIORITARIA"
        assert reporte["eventos"][0]["decision_shadow"] == "VIGILAR"
    finally:
        _restore()


# --- B: NO registrar shadow_differs=False -----------------------------------

def test_B_no_registra_shadow_differs_false():
    _fresh()
    try:
        insertado = reg.record_shadow_decision(
            ticker="NSSC", market_date="2026-08-27",
            decision="OPORTUNIDAD_PRIORITARIA", decision_shadow="OPORTUNIDAD_PRIORITARIA",
            shadow_differs=False, validation_state="EN_VALIDACION",
            sample_size=200, wilson_upper_bound_20_pct=5.0, baseline_pct_20=0.8,
        )
        assert insertado is False
        reporte = reg.shadow_validation_report()
        assert reporte["total_eventos_shadow_differs"] == 0
        assert reporte["eventos"] == []
    finally:
        _restore()


# --- C: idempotencia ---------------------------------------------------------

def test_C_idempotencia_no_duplica():
    _fresh()
    try:
        r1 = reg.record_shadow_decision(
            ticker="CRM", market_date="2026-08-27",
            decision="VIGILAR", decision_shadow="PREPARACION",
            shadow_differs=True, validation_state="VALIDACION_ROBUSTA",
            sample_size=550, wilson_upper_bound_20_pct=0.3, baseline_pct_20=0.8,
        )
        # Simula 2 requests distintos a /api/radar-oportunidades el mismo
        # día para la misma candidata -- mismo resultado calculado de nuevo.
        r2 = reg.record_shadow_decision(
            ticker="CRM", market_date="2026-08-27",
            decision="VIGILAR", decision_shadow="PREPARACION",
            shadow_differs=True, validation_state="VALIDACION_ROBUSTA",
            sample_size=551, wilson_upper_bound_20_pct=0.31, baseline_pct_20=0.8,
        )
        assert r1 is True
        assert r2 is False  # ya existía la fila (ticker, market_date) -- INSERT OR IGNORE
        reporte = reg.shadow_validation_report()
        assert reporte["total_eventos_shadow_differs"] == 1
        # Se conserva el valor de la PRIMERA escritura (write-once real).
        assert reporte["eventos"][0]["sample_size"] == 550
    finally:
        _restore()


# --- D: cruce con outcome final favorable al downgrade (falsa_senal) -------

def test_D_cruce_downgrade_correcto():
    _fresh()
    try:
        reg.record_shadow_decision(
            ticker="ZZZ1", market_date="2026-08-26",
            decision="OPORTUNIDAD_PRIORITARIA", decision_shadow="VIGILAR",
            shadow_differs=True, validation_state="VALIDACION_ROBUSTA",
            sample_size=600, wilson_upper_bound_20_pct=0.4, baseline_pct_20=0.8,
        )
        _record_outcome_final("ZZZ1", "2026-08-26", "falsa_senal")

        reporte = reg.shadow_validation_report()
        assert reporte["downgrade_correcto"] == 1
        assert reporte["downgrade_incorrecto"] == 0
        assert reporte["pendientes"] == 0
        assert reporte["con_outcome_final"] == 1
        assert reporte["eventos"][0]["resultado"] == "DOWNGRADE_CORRECTO"
        assert reporte["tasa_acierto_pct"] == 100.0
    finally:
        _restore()


# --- E: cruce con outcome donde el downgrade habría sido incorrecto --------

def test_E_cruce_downgrade_incorrecto():
    _fresh()
    try:
        reg.record_shadow_decision(
            ticker="ZZZ2", market_date="2026-08-26",
            decision="OPORTUNIDAD_PRIORITARIA", decision_shadow="VIGILAR",
            shadow_differs=True, validation_state="VALIDACION_ROBUSTA",
            sample_size=600, wilson_upper_bound_20_pct=0.4, baseline_pct_20=0.8,
        )
        _record_outcome_final("ZZZ2", "2026-08-26", "mejor_oportunidad")

        reporte = reg.shadow_validation_report()
        assert reporte["downgrade_correcto"] == 0
        assert reporte["downgrade_incorrecto"] == 1
        assert reporte["eventos"][0]["resultado"] == "DOWNGRADE_INCORRECTO"
        assert reporte["tasa_acierto_pct"] == 0.0
    finally:
        _restore()


# --- F: outcome todavía no cerrado -> pendiente -----------------------------

def test_F_outcome_no_cerrado_queda_pendiente():
    _fresh()
    try:
        reg.record_shadow_decision(
            ticker="ZZZ3", market_date="2026-08-27",
            decision="VIGILAR", decision_shadow="PREPARACION",
            shadow_differs=True, validation_state="VALIDACION_ROBUSTA",
            sample_size=520, wilson_upper_bound_20_pct=0.2, baseline_pct_20=0.8,
        )
        # Outcome EN_CURSO (is_final=False) -- calculado en vivo, no oficial.
        reg.record_outcome(
            ticker="ZZZ3", market_date="2026-08-27",
            run_up_before_detection_pct=None, max_price_after_detection=10.0,
            max_return_after_detection_pct=3.0, minutes_to_max=10.0,
            reached_20=False, reached_50=False, reached_100=False,
            category="EN_CURSO", is_final=False,
        )
        reporte = reg.shadow_validation_report()
        assert reporte["pendientes"] == 1
        assert reporte["con_outcome_final"] == 0
        assert reporte["eventos"][0]["resultado"] == "PENDIENTE"
        assert reporte["n_evaluables_tasa"] == 0
        assert reporte["tasa_acierto_pct"] is None
    finally:
        _restore()


# --- G: el reporte no modifica ningún outcome -------------------------------

def test_G_reporte_no_modifica_outcome():
    _fresh()
    try:
        reg.record_shadow_decision(
            ticker="ZZZ4", market_date="2026-08-26",
            decision="VIGILAR", decision_shadow="PREPARACION",
            shadow_differs=True, validation_state="VALIDACION_ROBUSTA",
            sample_size=600, wilson_upper_bound_20_pct=0.1, baseline_pct_20=0.8,
        )
        _record_outcome_final("ZZZ4", "2026-08-26", "falsa_senal")
        antes = reg.get_outcome("ZZZ4", "2026-08-26")

        reg.shadow_validation_report()
        reg.shadow_validation_report(market_date="2026-08-26")  # llamado 2 veces, distintos args

        despues = reg.get_outcome("ZZZ4", "2026-08-26")
        assert antes == despues
    finally:
        _restore()


# --- H: el reporte no modifica ninguna decisión (verificación estructural) -

def test_H_reporte_no_puede_tocar_decisiones():
    """`shadow_validation_report`/`record_shadow_decision` no importan ni
    llaman nada de atlas_decision_core.py, current_top_opportunity.py,
    top_opportunity_stability.py, priority_classifier.py ni
    decision_engine.py -- AST real (Import/ImportFrom/Call), nunca un
    match de substring ingenuo contra el docstring (que sí puede nombrar
    esos módulos como documentación, sin importarlos)."""
    import ast
    import textwrap

    prohibidos = (
        "atlas_decision_core", "current_top_opportunity", "top_opportunity_stability",
        "priority_classifier", "decision_engine", "candidate_gates",
    )
    for fn in (reg.record_shadow_decision, reg.shadow_validation_report):
        arbol = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        nombres_referenciados = set()
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Import):
                nombres_referenciados.update(a.name for a in nodo.names)
            elif isinstance(nodo, ast.ImportFrom):
                nombres_referenciados.add(nodo.module or "")
                nombres_referenciados.update(a.name for a in nodo.names)
            elif isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute):
                nombres_referenciados.add(nodo.func.attr)
            elif isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name):
                nombres_referenciados.add(nodo.func.id)
        for nombre in prohibidos:
            assert not any(nombre in ref for ref in nombres_referenciados), \
                f"{fn.__name__} referencia {nombre} en un import/llamada real, no debería"


# --- I: apply_recalibration=False sigue siendo el default -------------------

def test_I_apply_recalibration_sigue_false_por_defecto():
    firma = inspect.signature(adc.decide)
    assert firma.parameters["apply_recalibration"].default is False

    # Y en vivo: sin pasarlo explícito, decide() nunca usa decision_shadow.
    candidate = adc.CandidateSnapshot(ticker="X", market_date="2026-08-27", tiene_precio_actual=True)
    features = adc.DecisionFeatures(stage="INICIO", direction="ALCISTA", change_pct_confiable=True)
    learned_evidence = {
        "available": True, "validation_state": "VALIDACION_ROBUSTA",
        "wilson_upper_bound_20_pct": 0.1, "baseline_pct_20": 0.8,
    }
    resultado = adc.decide(candidate, features, learned_evidence=learned_evidence)
    decision_cruda, _ = pc.classify_final_priority(
        stage=features.stage, direction=features.direction,
        change_pct_confiable=features.change_pct_confiable,
        tiene_precio_actual=candidate.tiene_precio_actual,
    )
    # `decision` real es SIEMPRE la salida cruda de classify_final_priority()
    # -- aunque shadow_differs=True y learned_evidence esté disponible, la
    # decisión real nunca se reemplaza por el shadow mientras
    # apply_recalibration=False (default).
    assert resultado.decision == decision_cruda
    if resultado.shadow_differs:
        assert resultado.decision != resultado.decision_shadow
    assert resultado.learned_evidence_used is True


# --- J: verificación estructural -- archivos protegidos sin diff -----------

def test_J_archivos_protegidos_sin_diff():
    import subprocess

    protegidos = [
        "atlas_live/core/atlas_decision_core.py",
        "atlas_live/core/current_top_opportunity.py",
        "atlas_live/core/top_opportunity_stability.py",
        "atlas_live/core/current_top_opportunity_registry.py",
        "atlas_live/scan_worker.py",
        "atlas_live/radar/radar_worker.py",
        "atlas_live/radar/candidate_gates.py",
        "atlas_live/radar/priority_classifier.py",
        "atlas/engine/decision_engine.py",
        "atlas_live/learning",
    ]
    # Hito 3, Fase 3.6 (2026-09-03, autorizado explícitamente): único touch
    # permitido dentro de "atlas_live/learning" -- el orquestador event-
    # driven y su propio test. Cualquier OTRO archivo de esa carpeta (o de
    # cualquiera de los demás protegidos) sigue disparando este guard.
    excepciones_hito_3_6 = [
        ":(exclude)atlas_live/learning/live_experience_pipeline.py",
        ":(exclude)atlas_live/learning/test_live_experience_pipeline.py",
    ]
    resultado = subprocess.run(
        ["git", "diff", "--stat", "--"] + protegidos + excepciones_hito_3_6,
        capture_output=True, text=True, cwd=".",
    )
    assert resultado.stdout.strip() == "", f"archivos protegidos con diff pendiente: {resultado.stdout}"


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
            p += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
            traceback.print_exc()
            f += 1
    print(f"--- {p} passed, {f} failed ---")
