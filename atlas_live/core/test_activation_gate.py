"""Tests de `activation_gate.py` (Hito 3, Fase 3.5, 2026-09-03,
autorizado explícitamente en Plan Mode). Puro, sin DB."""

import inspect

from atlas_live.core import activation_gate as ag

_MARKET_DATE = "2026-08-24"


def _clasificar(mechanism_state="ON_CONTROLADO", eligibility_state="ELEGIBLE", is_revoked=False,
                 computed_as_of="2026-08-20"):
    return ag.classify_activation(
        mechanism_state=mechanism_state, eligibility_state=eligibility_state,
        is_revoked=is_revoked, computed_as_of=computed_as_of, market_date=_MARKET_DATE,
    )


# --- 1) estado OFF por defecto --------------------------------------------

def test_mecanismo_off_es_no_activo_sin_importar_el_resto():
    r = _clasificar(mechanism_state="OFF", eligibility_state="ELEGIBLE", is_revoked=False)
    assert r["activation_state"] == "NO_ACTIVO"
    assert r["reason"] == "MECANISMO_APAGADO"


def test_mecanismo_off_gana_incluso_con_todo_lo_demas_perfecto():
    r = _clasificar(mechanism_state="OFF", eligibility_state="ELEGIBLE", is_revoked=False,
                     computed_as_of="2026-08-20")
    assert r["activation_state"] == "NO_ACTIVO"


def test_valor_de_mecanismo_desconocido_tambien_es_no_activo():
    r = _clasificar(mechanism_state="ALGO_RARO")
    assert r["activation_state"] == "NO_ACTIVO"


# --- 2) activación controlada válida ---------------------------------------

def test_activacion_controlada_valida():
    r = _clasificar(mechanism_state="ON_CONTROLADO", eligibility_state="ELEGIBLE",
                     is_revoked=False, computed_as_of="2026-08-20")
    assert r["activation_state"] == "ACTIVADO"
    assert r["reason"] == "CONOCIMIENTO_ELEGIBLE_Y_VIGENTE"


# --- 3/4) NO_ELEGIBLE / INSUFICIENTE ---------------------------------------

def test_no_elegible_bloquea():
    r = _clasificar(eligibility_state="NO_ELEGIBLE")
    assert r["activation_state"] == "BLOQUEADO"
    assert "NO_ELEGIBLE" in r["reason"]


def test_insuficiente_bloquea():
    r = _clasificar(eligibility_state="INSUFICIENTE")
    assert r["activation_state"] == "BLOQUEADO"
    assert "INSUFICIENTE" in r["reason"]


# --- 5) walk-forward inválido ----------------------------------------------

def test_walk_forward_igualdad_bloquea_incluso_elegible():
    r = _clasificar(eligibility_state="ELEGIBLE", computed_as_of=_MARKET_DATE)
    assert r["activation_state"] == "BLOQUEADO"
    assert r["reason"] == "WALK_FORWARD_VIOLATION"
    assert r["walk_forward_violation"] is True


def test_walk_forward_posterior_bloquea():
    r = _clasificar(eligibility_state="ELEGIBLE", computed_as_of="2026-08-25")
    assert r["activation_state"] == "BLOQUEADO"
    assert r["reason"] == "WALK_FORWARD_VIOLATION"


def test_walk_forward_ausente_bloquea():
    r = _clasificar(eligibility_state="ELEGIBLE", computed_as_of=None)
    assert r["activation_state"] == "BLOQUEADO"


# --- 6) ausencia de conocimiento --------------------------------------------

def test_ausencia_de_conocimiento_nunca_activa():
    r = _clasificar(eligibility_state=None)
    assert r["activation_state"] == "BLOQUEADO"
    assert r["activation_state"] != "ACTIVADO"


# --- 8/9) revocación, y que gane sobre activación ---------------------------

def test_revocado_bloquea():
    r = _clasificar(is_revoked=True)
    assert r["activation_state"] == "REVOCADO"
    assert r["reason"] == "REVOCACION_ACTIVA"


def test_revocacion_gana_sobre_activacion_que_de_otro_modo_seria_perfecta():
    r = _clasificar(mechanism_state="ON_CONTROLADO", eligibility_state="ELEGIBLE",
                     is_revoked=True, computed_as_of="2026-08-20")
    assert r["activation_state"] == "REVOCADO"
    assert r["activation_state"] != "ACTIVADO"


def test_revocacion_gana_incluso_sobre_no_elegible():
    # Confirma el orden de evaluacion: revocacion se chequea ANTES que
    # elegibilidad -- el motivo reportado debe ser la revocacion, no la
    # falta de elegibilidad.
    r = _clasificar(eligibility_state="NO_ELEGIBLE", is_revoked=True)
    assert r["activation_state"] == "REVOCADO"


# --- 12) baseline/shadow permanecen separados (defensa estructural) --------

def test_firma_solo_acepta_primitivos_nunca_objetos_de_decision():
    firma = str(inspect.signature(ag.classify_activation))
    for tipo_prohibido in ("AtlasDecision", "CandidateSnapshot", "DecisionFeatures", "DecisionScores", "DecisionEvidence"):
        assert tipo_prohibido not in firma


def test_dos_llamadas_no_se_afectan_entre_si():
    r1 = _clasificar(eligibility_state="ELEGIBLE")
    r2 = _clasificar(eligibility_state="NO_ELEGIBLE")
    assert r1["activation_state"] == "ACTIVADO"
    assert r2["activation_state"] == "BLOQUEADO"


# --- 14) apply_recalibration ausente del gate -------------------------------

def test_modulo_nunca_ejecuta_apply_recalibration():
    # El docstring del módulo SÍ menciona "apply_recalibration=True" en
    # prosa (para explicar el alcance) -- lo que nunca debe existir es una
    # ASIGNACIÓN/LLAMADA real dentro del código ejecutable (mismo criterio
    # de falsos positivos ya resuelto para knowledge_eligibility.py).
    fuente_ejecutable = inspect.getsource(ag.classify_activation)
    assert "apply_recalibration" not in fuente_ejecutable
    assert "adc.decide" not in fuente_ejecutable
    assert "decide(" not in fuente_ejecutable


# --- 15) sin vocabulario de ejecución financiera ----------------------------

def test_sin_vocabulario_de_ejecucion_financiera():
    fuente = inspect.getsource(ag).lower()
    for palabra in ("broker", "order", "place_order", "execute_trade", "buy", "sell"):
        assert palabra not in fuente


# --- determinismo ------------------------------------------------------------

def test_misma_entrada_produce_siempre_el_mismo_resultado():
    kwargs = dict(mechanism_state="ON_CONTROLADO", eligibility_state="ELEGIBLE",
                  is_revoked=False, computed_as_of="2026-08-20", market_date=_MARKET_DATE)
    assert ag.classify_activation(**kwargs) == ag.classify_activation(**kwargs)
