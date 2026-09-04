"""Tests de `shadow_observation.py` (Hito 3, Fase 3.4, 2026-09-03,
autorizado explícitamente en Plan Mode). Puro, sin DB."""

import inspect

from atlas_live.core import shadow_observation as so

_MARKET_DATE = "2026-08-24"


# --- 1) conocimiento ELEGIBLE observado -----------------------------------

def test_elegible_y_shadow_differs_se_observa():
    r = so.classify_shadow_observation(
        decision="VIGILAR", decision_shadow="PREPARACION", shadow_differs=True,
        eligibility_state="ELEGIBLE", computed_as_of="2026-08-20", market_date=_MARKET_DATE,
    )
    assert r["observado"] is True
    assert r["eligibility_state"] == "ELEGIBLE"
    assert r["decision"] == "VIGILAR"
    assert r["decision_shadow"] == "PREPARACION"


# --- 2) INSUFICIENTE no observado como elegible ---------------------------

def test_insuficiente_se_observa_pero_nunca_como_elegible():
    r = so.classify_shadow_observation(
        decision="VIGILAR", decision_shadow="PREPARACION", shadow_differs=True,
        eligibility_state="INSUFICIENTE", computed_as_of="2026-08-20", market_date=_MARKET_DATE,
    )
    assert r["observado"] is True
    assert r["eligibility_state"] == "INSUFICIENTE"
    assert r["eligibility_state"] != "ELEGIBLE"


# --- 3) NO_ELEGIBLE no observado como elegible ----------------------------

def test_no_elegible_se_observa_pero_nunca_como_elegible():
    r = so.classify_shadow_observation(
        decision="VIGILAR", decision_shadow="PREPARACION", shadow_differs=True,
        eligibility_state="NO_ELEGIBLE", computed_as_of="2026-08-20", market_date=_MARKET_DATE,
    )
    assert r["observado"] is True
    assert r["eligibility_state"] == "NO_ELEGIBLE"
    assert r["eligibility_state"] != "ELEGIBLE"


def test_sin_veredicto_de_3_3_nunca_se_asume_elegible():
    r = so.classify_shadow_observation(
        decision="VIGILAR", decision_shadow="PREPARACION", shadow_differs=True,
        eligibility_state=None, computed_as_of="2026-08-20", market_date=_MARKET_DATE,
    )
    assert r["eligibility_state"] == "SIN_VEREDICTO_3.3"
    assert r["eligibility_state"] != "ELEGIBLE"


def test_shadow_differs_false_no_se_observa():
    r = so.classify_shadow_observation(
        decision="VIGILAR", decision_shadow="VIGILAR", shadow_differs=False,
        eligibility_state="ELEGIBLE", computed_as_of="2026-08-20", market_date=_MARKET_DATE,
    )
    assert r["observado"] is False


# --- 4/5) baseline permanece idéntico / shadow no lo modifica -------------

def test_firma_solo_acepta_primitivos_nunca_objetos_de_decision():
    # Defensa estructural: la funcion no puede mutar AtlasDecision/o[...]
    # porque ni siquiera los recibe -- solo strings/bools/None. Se verifica
    # que ningun tipo de objeto de decision aparezca en la firma real.
    firma = str(inspect.signature(so.classify_shadow_observation))
    for tipo_prohibido in ("AtlasDecision", "CandidateSnapshot", "DecisionFeatures", "DecisionScores", "DecisionEvidence"):
        assert tipo_prohibido not in firma


def test_dos_llamadas_con_distinto_shadow_no_cambian_decision():
    r1 = so.classify_shadow_observation(
        decision="VIGILAR", decision_shadow="PREPARACION", shadow_differs=True,
        eligibility_state="ELEGIBLE", computed_as_of="2026-08-20", market_date=_MARKET_DATE,
    )
    r2 = so.classify_shadow_observation(
        decision="VIGILAR", decision_shadow="NO_TOCAR", shadow_differs=True,
        eligibility_state="ELEGIBLE", computed_as_of="2026-08-20", market_date=_MARKET_DATE,
    )
    assert r1["decision"] == r2["decision"] == "VIGILAR"
    assert r1["decision_shadow"] != r2["decision_shadow"]


# --- 6) apply_recalibration permanece desactivado -------------------------

def test_modulo_nunca_pasa_apply_recalibration_true():
    fuente = inspect.getsource(so)
    assert "apply_recalibration=True" not in fuente
    assert "apply_recalibration = True" not in fuente


# --- 7) walk-forward -------------------------------------------------------
# Corrección 2026-09-03 (auditoría explícita del usuario): una violación de
# walk-forward NUNCA puede terminar persistida como observación válida --
# `observado` debe ser `False`, no solo `walk_forward_violation=True`.

def test_walk_forward_violation_igualdad_exacta_impide_la_observacion():
    r = so.classify_shadow_observation(
        decision="VIGILAR", decision_shadow="PREPARACION", shadow_differs=True,
        eligibility_state="ELEGIBLE", computed_as_of=_MARKET_DATE, market_date=_MARKET_DATE,
    )
    assert r["walk_forward_violation"] is True
    assert r["observado"] is False  # computed_as_of == market_date -> BLOQUEA, no solo marca


def test_walk_forward_violation_posterior_impide_la_observacion():
    r = so.classify_shadow_observation(
        decision="VIGILAR", decision_shadow="PREPARACION", shadow_differs=True,
        eligibility_state="ELEGIBLE", computed_as_of="2026-08-25", market_date=_MARKET_DATE,
    )
    assert r["walk_forward_violation"] is True
    assert r["observado"] is False  # computed_as_of > market_date -> BLOQUEA


def test_walk_forward_seguro_permite_observar_cuando_shadow_differs():
    r = so.classify_shadow_observation(
        decision="VIGILAR", decision_shadow="PREPARACION", shadow_differs=True,
        eligibility_state="ELEGIBLE", computed_as_of="2026-08-20", market_date=_MARKET_DATE,
    )
    assert r["walk_forward_violation"] is False
    assert r["observado"] is True  # computed_as_of < market_date -> puede observarse


def test_walk_forward_violation_el_campo_sigue_reportandose_correctamente():
    # walk_forward_violation debe seguir indicando la violacion real
    # incluso cuando observado ya es False por ese mismo motivo -- nunca
    # se oculta la razon.
    casos = [
        (_MARKET_DATE, True),      # igual -> violacion
        ("2026-08-25", True),      # posterior -> violacion
        ("2026-08-20", False),     # anterior -> sin violacion
        (None, True),              # ausente -> violacion
    ]
    for computed_as_of, esperado in casos:
        r = so.classify_shadow_observation(
            decision="VIGILAR", decision_shadow="PREPARACION", shadow_differs=True,
            eligibility_state="ELEGIBLE", computed_as_of=computed_as_of, market_date=_MARKET_DATE,
        )
        assert r["walk_forward_violation"] is esperado


def test_walk_forward_seguro_cuando_es_anterior():
    r = so.classify_shadow_observation(
        decision="VIGILAR", decision_shadow="PREPARACION", shadow_differs=True,
        eligibility_state="ELEGIBLE", computed_as_of="2026-08-20", market_date=_MARKET_DATE,
    )
    assert r["walk_forward_violation"] is False


def test_walk_forward_violation_computed_as_of_none():
    r = so.classify_shadow_observation(
        decision="VIGILAR", decision_shadow="PREPARACION", shadow_differs=True,
        eligibility_state="ELEGIBLE", computed_as_of=None, market_date=_MARKET_DATE,
    )
    assert r["walk_forward_violation"] is True


def test_walk_forward_se_reverifica_incluso_sin_observar():
    # shadow_differs=False -> observado=False, pero walk-forward se sigue
    # calculando (informativo, nunca se omite el chequeo).
    r = so.classify_shadow_observation(
        decision="VIGILAR", decision_shadow="VIGILAR", shadow_differs=False,
        eligibility_state="ELEGIBLE", computed_as_of=_MARKET_DATE, market_date=_MARKET_DATE,
    )
    assert r["observado"] is False
    assert r["walk_forward_violation"] is True


# --- determinismo ----------------------------------------------------------

def test_misma_entrada_produce_siempre_el_mismo_resultado():
    kwargs = dict(
        decision="VIGILAR", decision_shadow="PREPARACION", shadow_differs=True,
        eligibility_state="ELEGIBLE", computed_as_of="2026-08-20", market_date=_MARKET_DATE,
    )
    assert so.classify_shadow_observation(**kwargs) == so.classify_shadow_observation(**kwargs)
