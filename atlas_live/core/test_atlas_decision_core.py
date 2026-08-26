"""Tests de `atlas_decision_core.py` (2026-08-26, U3-A). Puros -- sin DB, sin
red, sin fakes de infraestructura -- exactamente lo que el propio módulo
promete ser."""

import dataclasses
import inspect

from atlas_live.core import atlas_decision_core as core
from atlas_live.radar import priority_classifier as pc

_CANDIDATE = core.CandidateSnapshot(ticker="NSSC", market_date="2026-08-26", tiene_precio_actual=True)
_FEATURES_OPORTUNIDAD = core.DecisionFeatures(stage="INICIO", direction="ALCISTA", change_pct_confiable=True)
_FEATURES_VIGILAR = core.DecisionFeatures(stage="ALERTA_TEMPRANA", direction="ALCISTA", change_pct_confiable=True)
_FEATURES_PREPARACION = core.DecisionFeatures(stage="PREPARACION", direction=None, change_pct_confiable=True)
_FEATURES_NO_TOCAR = core.DecisionFeatures(stage="NO_PERSEGUIR", direction="BAJISTA", change_pct_confiable=True)

_LEARNED_EVIDENCE_ROBUSTA_NEGATIVA = {
    "available": True,
    "validation_state": "VALIDACION_ROBUSTA",
    "sample_size": 612,
    "historical_success_pct_20": 12.0,
    "baseline_pct_20": 23.1,
    "lift_20": 0.52,
    "wilson_lower_bound_20_pct": 9.0,
    "wilson_upper_bound_20_pct": 15.5,  # < baseline_pct_20 -- dispara downgrade
    "computed_as_of": "2026-08-25",
    "computed_at": "2026-08-25T20:00:00+00:00",
    "methodology_version": "v1_direction_timing_volatility_tercile",
}

_LEARNED_EVIDENCE_INSUFICIENTE = {
    "available": True,
    "validation_state": "MUESTRA_INSUFICIENTE",
    "sample_size": 12,
    "historical_success_pct_20": 5.0,
    "baseline_pct_20": 23.1,
    "lift_20": 0.22,
    "wilson_lower_bound_20_pct": 0.0,
    "wilson_upper_bound_20_pct": 20.0,
    "computed_as_of": "2026-08-25",
    "computed_at": "2026-08-25T20:00:00+00:00",
    "methodology_version": "v1_direction_timing_volatility_tercile",
}


# --- A: determinismo ---------------------------------------------------------

def test_A_mismo_input_misma_decision():
    r1 = core.decide(_CANDIDATE, _FEATURES_OPORTUNIDAD)
    r2 = core.decide(_CANDIDATE, _FEATURES_OPORTUNIDAD)
    assert r1.decision == r2.decision == "OPORTUNIDAD_PRIORITARIA"
    assert r1.reason == r2.reason


# --- B: priority_classifier sigue siendo la fuente --------------------------

def test_B_priority_classifier_sigue_siendo_la_fuente():
    for features in (_FEATURES_OPORTUNIDAD, _FEATURES_VIGILAR, _FEATURES_PREPARACION, _FEATURES_NO_TOCAR):
        esperado, _ = pc.classify_final_priority(
            stage=features.stage,
            direction=features.direction,
            change_pct_confiable=features.change_pct_confiable,
            tiene_precio_actual=_CANDIDATE.tiene_precio_actual,
            sector_flow_active=features.sector_flow_active,
            historical_evidence=None,
            estado_validacion=_CANDIDATE.estado_validacion,
        )
        resultado = core.decide(_CANDIDATE, features)
        assert resultado.decision == esperado


# --- C: ningun estado fuera de los 4 -----------------------------------------

def test_C_ningun_estado_distinto_de_los_4():
    combinaciones = [
        core.DecisionFeatures(stage=stage, direction=direction, change_pct_confiable=confiable)
        for stage in ("INICIO", "CONFIRMACION", "ALERTA_TEMPRANA", "ALERTA_FUERTE",
                       "PREPARACION", "DETECCION_TEMPRANA", "NO_PERSEGUIR", "FLUJO_VENDEDOR", None, "DESCONOCIDO")
        for direction in ("ALCISTA", "BAJISTA", "NEUTRAL", "INDEFINIDA", None)
        for confiable in (True, False, None)
    ]
    for features in combinaciones:
        resultado = core.decide(_CANDIDATE, features)
        assert resultado.decision in pc.FINAL_STATES
        if resultado.decision_shadow is not None:
            assert resultado.decision_shadow in pc.FINAL_STATES

    # Caso explícito sin precio actual -- también debe caer dentro de los 4.
    sin_precio = core.CandidateSnapshot(ticker="X", market_date="2026-08-26", tiene_precio_actual=False)
    assert core.decide(sin_precio, _FEATURES_OPORTUNIDAD).decision in pc.FINAL_STATES


# --- D: learned_evidence=None ------------------------------------------------

def test_D_learned_evidence_none_funciona():
    r = core.decide(_CANDIDATE, _FEATURES_OPORTUNIDAD, learned_evidence=None)
    assert r.decision == "OPORTUNIDAD_PRIORITARIA"
    assert r.decision_shadow is None
    assert r.shadow_differs is False
    assert r.learned_evidence_used is False
    assert r.evidence_snapshot["learned_evidence"] is None


# --- E: learned_evidence real NO modifica decision ---------------------------

def test_E_learned_evidence_real_no_modifica_decision():
    sin = core.decide(_CANDIDATE, _FEATURES_OPORTUNIDAD, learned_evidence=None)
    con = core.decide(_CANDIDATE, _FEATURES_OPORTUNIDAD, learned_evidence=_LEARNED_EVIDENCE_ROBUSTA_NEGATIVA)
    assert sin.decision == con.decision == "OPORTUNIDAD_PRIORITARIA"
    assert con.learned_evidence_used is True


# --- F: learned_evidence real puede producir decision_shadow ----------------

def test_F_learned_evidence_puede_producir_decision_shadow():
    r = core.decide(_CANDIDATE, _FEATURES_OPORTUNIDAD, learned_evidence=_LEARNED_EVIDENCE_ROBUSTA_NEGATIVA)
    assert r.decision == "OPORTUNIDAD_PRIORITARIA"  # decision real intacta
    assert r.decision_shadow == "VIGILAR"  # un escalon de downgrade
    assert r.shadow_differs is True

    # Muestra insuficiente -- nunca dispara el downgrade, sin importar el signo.
    r_insuf = core.decide(_CANDIDATE, _FEATURES_OPORTUNIDAD, learned_evidence=_LEARNED_EVIDENCE_INSUFICIENTE)
    assert r_insuf.decision_shadow == "OPORTUNIDAD_PRIORITARIA"
    assert r_insuf.shadow_differs is False

    # NO_TOCAR no tiene escalon inferior -- shadow nunca lo cambia.
    r_no_tocar = core.decide(_CANDIDATE, _FEATURES_NO_TOCAR, learned_evidence=_LEARNED_EVIDENCE_ROBUSTA_NEGATIVA)
    assert r_no_tocar.decision_shadow == "NO_TOCAR"
    assert r_no_tocar.shadow_differs is False


# --- G: apply_recalibration=False no puede cambiar decision -----------------

def test_G_apply_recalibration_false_no_puede_cambiar_decision():
    r_default = core.decide(_CANDIDATE, _FEATURES_OPORTUNIDAD, learned_evidence=_LEARNED_EVIDENCE_ROBUSTA_NEGATIVA)
    r_explicito = core.decide(
        _CANDIDATE, _FEATURES_OPORTUNIDAD, learned_evidence=_LEARNED_EVIDENCE_ROBUSTA_NEGATIVA,
        apply_recalibration=False,
    )
    assert r_default.decision == r_explicito.decision == "OPORTUNIDAD_PRIORITARIA"
    assert r_default.decision_shadow == r_explicito.decision_shadow == "VIGILAR"

    # Contraste explicito: el kill-switch realmente hace algo cuando se
    # activa a proposito -- si no, el test de arriba seria trivial.
    r_activado = core.decide(
        _CANDIDATE, _FEATURES_OPORTUNIDAD, learned_evidence=_LEARNED_EVIDENCE_ROBUSTA_NEGATIVA,
        apply_recalibration=True,
    )
    assert r_activado.decision == "VIGILAR"


# --- H: snapshots congelados --------------------------------------------------

def test_H_snapshots_congelados():
    scores = core.DecisionScores(atlas_score=71.2, catalyst_score=55.0)
    evidence = core.DecisionEvidence(memory_engine_semaforo="verde")
    r = core.decide(_CANDIDATE, _FEATURES_OPORTUNIDAD, scores=scores, evidence=evidence,
                     learned_evidence=_LEARNED_EVIDENCE_ROBUSTA_NEGATIVA)

    assert r.features_snapshot == dataclasses.asdict(_FEATURES_OPORTUNIDAD)
    assert r.scores_snapshot == dataclasses.asdict(scores)
    assert r.evidence_snapshot["memory_engine_semaforo"] == "verde"
    assert r.evidence_snapshot["learned_evidence"] == _LEARNED_EVIDENCE_ROBUSTA_NEGATIVA

    # AtlasDecision es realmente inmutable.
    try:
        r.decision = "NO_TOCAR"
        raise AssertionError("se esperaba FrozenInstanceError")
    except dataclasses.FrozenInstanceError:
        pass


# --- I: sin imports de decision_engine/explosive_engine/Memory Engine -------

def test_I_sin_imports_prohibidos():
    import ast

    tree = ast.parse(inspect.getsource(core))
    modulos = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modulos.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modulos.add(node.module)
            modulos.update(a.name for a in node.names)

    prohibidos = ("decision_engine", "explosive_engine", "demo_ranking", "live_integration",
                  "scan_worker", "atlas_score", "momentum_engine")
    for m in modulos:
        for p in prohibidos:
            assert p not in m, f"import prohibido encontrado: {m}"

    # El unico import propio de Atlas permitido es priority_classifier.
    assert any("priority_classifier" in m for m in modulos)


# --- J: sin I/O ni DB ---------------------------------------------------------

def test_J_sin_io_ni_db():
    src = inspect.getsource(core)
    prohibidos = ("sqlite3", "requests.", "urllib", "socket.", "open(", "\nimport os",
                  " os.environ", "yfinance", "http.client")
    for p in prohibidos:
        assert p not in src, f"posible I/O encontrado: {p!r}"


# --- K: metodologia registrada -------------------------------------------------

def test_K_metodologia_registrada():
    r = core.decide(_CANDIDATE, _FEATURES_OPORTUNIDAD)
    assert r.methodology_version == core.CORE_METHODOLOGY_VERSION

    r_custom = core.decide(_CANDIDATE, _FEATURES_OPORTUNIDAD, methodology_version="v2_experimental")
    assert r_custom.methodology_version == "v2_experimental"
    # No se inventan multiples versiones por defecto -- solo una constante.
    assert core.CORE_METHODOLOGY_VERSION == "v1_wraps_priority_classifier"


# --- L: shadow usa exactamente los mismos inputs -----------------------------

def test_L_shadow_usa_los_mismos_inputs():
    scores = core.DecisionScores(atlas_score=80.0)
    evidence = core.DecisionEvidence(memory_engine_semaforo="amarillo")

    sin = core.decide(_CANDIDATE, _FEATURES_OPORTUNIDAD, scores=scores, evidence=evidence, learned_evidence=None)
    con = core.decide(_CANDIDATE, _FEATURES_OPORTUNIDAD, scores=scores, evidence=evidence,
                       learned_evidence=_LEARNED_EVIDENCE_ROBUSTA_NEGATIVA)

    assert sin.decision == con.decision  # misma decision real
    assert sin.features_snapshot == con.features_snapshot
    assert sin.scores_snapshot == con.scores_snapshot
    # evidence_snapshot difiere SOLO en el campo learned_evidence -- el resto identico.
    sin_evidence_sans_le = {k: v for k, v in sin.evidence_snapshot.items() if k != "learned_evidence"}
    con_evidence_sans_le = {k: v for k, v in con.evidence_snapshot.items() if k != "learned_evidence"}
    assert sin_evidence_sans_le == con_evidence_sans_le


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
