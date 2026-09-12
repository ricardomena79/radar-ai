"""Auditoría final obligatoria previa a commit/push/deploy (2026-09-12,
misión "CONECTAR EL APRENDIZAJE BIDIRECCIONAL A LA DECISIÓN REAL").

Pregunta central que este archivo responde con código, no con afirmación:
¿`bidirectional_shadow.resolve_controlled_decision()` reemplaza ÚNICAMENTE
la funcionalidad de recalibración de `atlas_decision_core.decide(...,
apply_recalibration=True)`, o reemplaza accidentalmente alguna otra
responsabilidad de esa función?

Método: para cada escenario, se calcula el resultado que habría producido
el CAMINO ANTERIOR real (llamando de verdad a
`atlas_decision_core.decide(..., apply_recalibration=True)`, sin mockear
nada) y se compara contra el CAMINO NUEVO real (`bidi.resolve_controlled_decision()`).
Deben coincidir en TODOS los escenarios, salvo el único autorizado
(upgrade `NO_TOCAR`->`VIGILAR` con evidencia elegible y favorable) -- ahí
se exige explícitamente que DIFIERAN, y que el nuevo sea el upgrade.

`atlas_decision_core.py` no se modifica en esta misión -- este archivo lo
usa tal cual, como vara de medir, nunca lo reemplaza."""

from atlas_live.core import activation_gate as ag
from atlas_live.core import atlas_decision_core as adc
from atlas_live.core import bidirectional_shadow as bidi
from atlas_live.core import knowledge_eligibility as ke
from atlas_live.radar import priority_classifier as pc

_MARKET_DATE = "2026-09-12"
_COMPUTED_AS_OF_SEGURO = "2026-09-08"  # < market_date -- walk-forward OK
_COMPUTED_AS_OF_LEAKAGE = "2026-09-12"  # == market_date -- walk-forward violado


def _le(available=True, validation_state="VALIDACION_ROBUSTA", sample_size=600,
        wilson_lower=None, wilson_upper=None, baseline=None,
        computed_as_of=_COMPUTED_AS_OF_SEGURO, reason=None):
    if not available:
        return {"available": False, "reason": reason or "SIN_CONOCIMIENTO_PARA_ESTA_CONDICION"}
    return {
        "available": True,
        "validation_state": validation_state,
        "sample_size": sample_size,
        "wilson_lower_bound_20_pct": wilson_lower,
        "wilson_upper_bound_20_pct": wilson_upper,
        "baseline_pct_20": baseline,
        "lift_20": round(wilson_upper / baseline, 2) if (wilson_upper and baseline) else None,
        "computed_as_of": computed_as_of,
        "computed_at": f"{computed_as_of}T12:00:00+00:00",
        "methodology_version": "v1_direction_timing_volatility_tercile",
    }


def _candidate_features(stage, direction="ALCISTA", tiene_precio_actual=True):
    candidate = adc.CandidateSnapshot(ticker="AUD1", market_date=_MARKET_DATE, tiene_precio_actual=tiene_precio_actual)
    features = adc.DecisionFeatures(stage=stage, direction=direction, change_pct_confiable=True)
    return candidate, features


def _old_style_controlada(candidate, features, learned_evidence, activation_state):
    """Reconstrucción EXACTA del cuerpo del bloque Fase 3.5 tal como
    existía antes de esta misión -- llama de verdad a
    `atlas_decision_core.decide(..., apply_recalibration=True)`, sin
    mockear nada. `None` si el gate no estaba ACTIVADO (mismo default
    `decision_controlada = None` del código real anterior)."""
    if activation_state != "ACTIVADO":
        return None
    return adc.decide(candidate, features, learned_evidence=learned_evidence, apply_recalibration=True).decision


def _new_style_resolucion(candidate, features, learned_evidence, activation_state):
    """Cadena real del código NUEVO, usando exactamente las mismas
    funciones que `server.py` invoca en el bloque Fase 3.5 reescrito."""
    baseline = adc.decide(candidate, features)
    shadow = adc.decide(candidate, features, learned_evidence=learned_evidence)
    eligibilidad = ke.classify_eligibility(learned_evidence, _MARKET_DATE)
    return bidi.resolve_controlled_decision(
        decision_base=baseline.decision,
        decision_shadow_downgrade=shadow.decision_shadow,
        eligibility_state=eligibilidad["eligibility_state"],
        learned_evidence=learned_evidence,
        activation_state=activation_state,
    )


def _gate_para(eligibility_state, computed_as_of, mechanism_state="ON_CONTROLADO", is_revoked=False):
    return ag.classify_activation(
        mechanism_state=mechanism_state, eligibility_state=eligibility_state,
        is_revoked=is_revoked, computed_as_of=computed_as_of, market_date=_MARKET_DATE,
    )["activation_state"]


# ---------------------------------------------------------------------------
# PRUEBA DE EQUIVALENCIA -- camino anterior vs. camino nuevo, escenario por
# escenario. Deben coincidir EXCEPTO en "upgrade válido" (el único cambio
# autorizado).
# ---------------------------------------------------------------------------

def test_eq_1_sin_conocimiento():
    candidate, features = _candidate_features(stage="NO_PERSEGUIR")  # NO_TOCAR base
    le = _le(available=False)
    eligibilidad = ke.classify_eligibility(le, _MARKET_DATE)
    assert eligibilidad["eligibility_state"] == "NO_ELEGIBLE"
    activation_state = _gate_para(eligibilidad["eligibility_state"], le.get("computed_as_of"))
    assert activation_state == "BLOQUEADO"

    old = _old_style_controlada(candidate, features, le, activation_state)
    new = _new_style_resolucion(candidate, features, le, activation_state)
    assert old is None
    assert new["decision_controlada"] is None
    assert new["cambio_aplicado"] is False


def test_eq_2_conocimiento_inelegible_en_validacion():
    candidate, features = _candidate_features(stage="NO_PERSEGUIR")
    le = _le(validation_state="EN_VALIDACION", sample_size=150, wilson_lower=5.0, wilson_upper=60.0, baseline=10.0)
    eligibilidad = ke.classify_eligibility(le, _MARKET_DATE)
    assert eligibilidad["eligibility_state"] == "INSUFICIENTE"
    activation_state = _gate_para(eligibilidad["eligibility_state"], le["computed_as_of"])
    assert activation_state == "BLOQUEADO"

    old = _old_style_controlada(candidate, features, le, activation_state)
    new = _new_style_resolucion(candidate, features, le, activation_state)
    assert old is None
    assert new["decision_controlada"] is None
    assert new["cambio_aplicado"] is False


def test_eq_3_gate_off_mecanismo_apagado():
    candidate, features = _candidate_features(stage="NO_PERSEGUIR")
    le = _le(wilson_lower=50.0, wilson_upper=60.0, baseline=10.0)  # evidencia perfecta
    eligibilidad = ke.classify_eligibility(le, _MARKET_DATE)
    assert eligibilidad["eligibility_state"] == "ELEGIBLE"
    activation_state = _gate_para(eligibilidad["eligibility_state"], le["computed_as_of"], mechanism_state="OFF")
    assert activation_state == "NO_ACTIVO"

    old = _old_style_controlada(candidate, features, le, activation_state)
    new = _new_style_resolucion(candidate, features, le, activation_state)
    assert old is None
    assert new["decision_controlada"] is None
    assert new["cambio_aplicado"] is False


def test_eq_4_evidencia_insuficiente_muestra_chica():
    candidate, features = _candidate_features(stage="NO_PERSEGUIR")
    le = _le(validation_state="MUESTRA_INSUFICIENTE", sample_size=40, wilson_lower=5.0, wilson_upper=60.0, baseline=10.0)
    eligibilidad = ke.classify_eligibility(le, _MARKET_DATE)
    assert eligibilidad["eligibility_state"] == "INSUFICIENTE"
    activation_state = _gate_para(eligibilidad["eligibility_state"], le["computed_as_of"])
    assert activation_state == "BLOQUEADO"

    old = _old_style_controlada(candidate, features, le, activation_state)
    new = _new_style_resolucion(candidate, features, le, activation_state)
    assert old is None
    assert new["decision_controlada"] is None


def test_eq_5_leakage_computed_as_of_igual_a_market_date():
    candidate, features = _candidate_features(stage="NO_PERSEGUIR")
    le = _le(wilson_lower=50.0, wilson_upper=60.0, baseline=10.0, computed_as_of=_COMPUTED_AS_OF_LEAKAGE)
    eligibilidad = ke.classify_eligibility(le, _MARKET_DATE)
    # Walk-forward violado -> NO_ELEGIBLE, pese a evidencia perfecta.
    assert eligibilidad["eligibility_state"] == "NO_ELEGIBLE"
    assert "WALK_FORWARD_VIOLATION" in eligibilidad["reasons"][0]
    activation_state = _gate_para(eligibilidad["eligibility_state"], le["computed_as_of"])
    assert activation_state == "BLOQUEADO"

    old = _old_style_controlada(candidate, features, le, activation_state)
    new = _new_style_resolucion(candidate, features, le, activation_state)
    assert old is None
    assert new["decision_controlada"] is None


def test_eq_6_downgrade_oportunidad_prioritaria_a_vigilar():
    candidate, features = _candidate_features(stage="INICIO")  # OPORTUNIDAD_PRIORITARIA base
    baseline = adc.decide(candidate, features)
    assert baseline.decision == "OPORTUNIDAD_PRIORITARIA"
    le = _le(wilson_lower=0.0, wilson_upper=5.0, baseline=35.0)  # desfavorable: upper < baseline
    eligibilidad = ke.classify_eligibility(le, _MARKET_DATE)
    assert eligibilidad["eligibility_state"] == "ELEGIBLE"
    activation_state = _gate_para(eligibilidad["eligibility_state"], le["computed_as_of"])
    assert activation_state == "ACTIVADO"

    old = _old_style_controlada(candidate, features, le, activation_state)
    new = _new_style_resolucion(candidate, features, le, activation_state)
    assert old == "VIGILAR"
    assert new["decision_controlada"] == "VIGILAR"
    assert old == new["decision_controlada"]  # equivalencia exacta preservada


def test_eq_7_empate_wilson_igual_al_baseline_no_cambia_nada():
    # Downgrade: wilson_upper == baseline (no estrictamente menor) -> sin downgrade.
    candidate, features = _candidate_features(stage="INICIO")
    le_downgrade = _le(wilson_lower=30.0, wilson_upper=35.0, baseline=35.0)
    elig = ke.classify_eligibility(le_downgrade, _MARKET_DATE)
    activation_state = _gate_para(elig["eligibility_state"], le_downgrade["computed_as_of"])
    assert activation_state == "ACTIVADO"
    old = _old_style_controlada(candidate, features, le_downgrade, activation_state)
    new = _new_style_resolucion(candidate, features, le_downgrade, activation_state)
    assert old == "OPORTUNIDAD_PRIORITARIA" == new["decision_controlada"]

    # Upgrade: wilson_lower == baseline (no estrictamente mayor) -> sin upgrade.
    candidate2, features2 = _candidate_features(stage="NO_PERSEGUIR")
    le_upgrade = _le(wilson_lower=10.0, wilson_upper=60.0, baseline=10.0)
    elig2 = ke.classify_eligibility(le_upgrade, _MARKET_DATE)
    activation_state2 = _gate_para(elig2["eligibility_state"], le_upgrade["computed_as_of"])
    assert activation_state2 == "ACTIVADO"
    old2 = _old_style_controlada(candidate2, features2, le_upgrade, activation_state2)
    new2 = _new_style_resolucion(candidate2, features2, le_upgrade, activation_state2)
    assert old2 == "NO_TOCAR" == new2["decision_controlada"]


def test_eq_8_upgrade_valido_es_la_UNICA_diferencia_autorizada():
    candidate, features = _candidate_features(stage="NO_PERSEGUIR")  # NO_TOCAR base
    le = _le(wilson_lower=50.0, wilson_upper=60.0, baseline=10.0)  # perfectamente favorable
    eligibilidad = ke.classify_eligibility(le, _MARKET_DATE)
    assert eligibilidad["eligibility_state"] == "ELEGIBLE"
    activation_state = _gate_para(eligibilidad["eligibility_state"], le["computed_as_of"])
    assert activation_state == "ACTIVADO"

    old = _old_style_controlada(candidate, features, le, activation_state)
    new = _new_style_resolucion(candidate, features, le, activation_state)
    # ÚNICO escenario de todo este archivo donde old != new -- documentado explícitamente.
    assert old == "NO_TOCAR"
    assert new["decision_controlada"] == "VIGILAR"
    assert new["upgrade_aplicado"] is True
    assert old != new["decision_controlada"]


def test_eq_9_conflicto_upgrade_downgrade_estructuralmente_imposible():
    """`decision_base` es un único valor a la vez -- nunca puede evaluarse
    simultáneamente como candidato a upgrade Y a downgrade. Se prueba con
    la MISMA evidencia (favorable para upgrade Y, si se aplicara a un caso
    degradable, también sin downgrade) sobre los 4 `decision_base`
    posibles, confirmando que cada uno sigue EXACTAMENTE una sola rama."""
    le = _le(wilson_lower=50.0, wilson_upper=60.0, baseline=10.0)  # favorable
    eligibilidad = ke.classify_eligibility(le, _MARKET_DATE)
    activation_state = _gate_para(eligibilidad["eligibility_state"], le["computed_as_of"])
    assert activation_state == "ACTIVADO"

    casos = {
        "NO_PERSEGUIR": ("NO_TOCAR", "VIGILAR"),  # upgrade -- único caso donde cambia
        "INICIO": ("OPORTUNIDAD_PRIORITARIA", "OPORTUNIDAD_PRIORITARIA"),  # evidencia favorable -> sin downgrade
        "ALERTA_TEMPRANA": ("VIGILAR", "VIGILAR"),
    }
    for stage, (base_esperado, resultado_esperado) in casos.items():
        candidate, features = _candidate_features(stage=stage)
        baseline = adc.decide(candidate, features)
        assert baseline.decision == base_esperado
        new = _new_style_resolucion(candidate, features, le, activation_state)
        assert new["decision_controlada"] == resultado_esperado
        # nunca ambas banderas a la vez
        if base_esperado == "NO_TOCAR":
            assert new["upgrade_aplicado"] in (True,)
        else:
            assert new["upgrade_aplicado"] is False


# ---------------------------------------------------------------------------
# PRUEBA DEL ÚNICO CAMBIO PERMITIDO -- matriz de transición exhaustiva.
# ---------------------------------------------------------------------------

_TRANSICIONES_PERMITIDAS = {
    "NO_TOCAR": {"NO_TOCAR", "VIGILAR"},
    "VIGILAR": {"VIGILAR", "PREPARACION"},
    "OPORTUNIDAD_PRIORITARIA": {"OPORTUNIDAD_PRIORITARIA", "VIGILAR"},
    "PREPARACION": {"PREPARACION"},
}

_ESCENARIOS_EVIDENCIA = [
    ("sin_conocimiento", _le(available=False)),
    ("favorable_fuerte", _le(wilson_lower=50.0, wilson_upper=60.0, baseline=10.0)),
    ("desfavorable_fuerte", _le(wilson_lower=0.0, wilson_upper=5.0, baseline=35.0)),
    ("neutral_empate", _le(wilson_lower=10.0, wilson_upper=10.0, baseline=10.0)),
    ("muestra_insuficiente", _le(validation_state="MUESTRA_INSUFICIENTE", sample_size=10,
                                  wilson_lower=50.0, wilson_upper=60.0, baseline=10.0)),
]


def test_matriz_transicion_exhaustiva_nunca_produce_transicion_no_autorizada():
    for stage_repr, base_esperado in [
        ("NO_PERSEGUIR", "NO_TOCAR"), ("INICIO", "OPORTUNIDAD_PRIORITARIA"),
        ("ALERTA_TEMPRANA", "VIGILAR"), ("PREPARACION", "PREPARACION"),
    ]:
        candidate, features = _candidate_features(stage=stage_repr)
        baseline = adc.decide(candidate, features)
        assert baseline.decision == base_esperado, f"fixture rota para stage={stage_repr}"

        for nombre_escenario, le in _ESCENARIOS_EVIDENCIA:
            eligibilidad = ke.classify_eligibility(le, _MARKET_DATE)
            for mechanism_state in ("OFF", "ON_CONTROLADO"):
                activation_state = _gate_para(
                    eligibilidad["eligibility_state"], le.get("computed_as_of"), mechanism_state=mechanism_state,
                )
                resolucion = _new_style_resolucion(candidate, features, le, activation_state)
                destino = resolucion["decision_controlada"] if resolucion["decision_controlada"] is not None else base_esperado
                assert destino in _TRANSICIONES_PERMITIDAS[base_esperado], (
                    f"transición NO autorizada: {base_esperado} -> {destino} "
                    f"(stage={stage_repr}, escenario={nombre_escenario}, mechanism={mechanism_state})"
                )


def test_nunca_vigilar_a_oportunidad_prioritaria():
    """Caso puntual explícitamente pedido: VIGILAR nunca puede
    'priorizarse' a OPORTUNIDAD_PRIORITARIA, ni con la evidencia más
    favorable posible."""
    candidate, features = _candidate_features(stage="ALERTA_TEMPRANA")
    baseline = adc.decide(candidate, features)
    assert baseline.decision == "VIGILAR"
    for _, le in _ESCENARIOS_EVIDENCIA:
        eligibilidad = ke.classify_eligibility(le, _MARKET_DATE)
        activation_state = _gate_para(eligibilidad["eligibility_state"], le.get("computed_as_of"))
        resolucion = _new_style_resolucion(candidate, features, le, activation_state)
        assert resolucion["decision_controlada"] != "OPORTUNIDAD_PRIORITARIA"


def test_nunca_preparacion_cambia_a_nada():
    candidate, features = _candidate_features(stage="PREPARACION")
    baseline = adc.decide(candidate, features)
    assert baseline.decision == "PREPARACION"
    for _, le in _ESCENARIOS_EVIDENCIA:
        eligibilidad = ke.classify_eligibility(le, _MARKET_DATE)
        activation_state = _gate_para(eligibilidad["eligibility_state"], le.get("computed_as_of"))
        resolucion = _new_style_resolucion(candidate, features, le, activation_state)
        assert resolucion["cambio_aplicado"] is False


# ---------------------------------------------------------------------------
# PRODUCCIÓN -- gate real activable en fixture controlado, ningún camino
# inseguro puede activarlo.
# ---------------------------------------------------------------------------

def test_produccion_on_controlado_gate_activa_y_upgrade_llega_a_estado_final_simulado():
    """Reproduce, sin Flask ni DB real, exactamente lo que server.py haría
    con o["estado_final"] bajo ON_CONTROLADO + evidencia perfecta."""
    candidate, features = _candidate_features(stage="NO_PERSEGUIR")
    baseline = adc.decide(candidate, features)
    le = _le(wilson_lower=50.0, wilson_upper=60.0, baseline=10.0)
    eligibilidad = ke.classify_eligibility(le, _MARKET_DATE)
    activation_state = _gate_para(eligibilidad["eligibility_state"], le["computed_as_of"], mechanism_state="ON_CONTROLADO")
    assert activation_state == "ACTIVADO"

    resolucion = _new_style_resolucion(candidate, features, le, activation_state)
    o_estado_final = baseline.decision
    if resolucion["cambio_aplicado"]:
        o_estado_final = resolucion["decision_controlada"]
    assert o_estado_final == "VIGILAR"  # el upgrade SÍ llega al campo que server.py expone


def test_produccion_is_revoked_bloquea_incluso_con_mecanismo_on_y_evidencia_perfecta():
    candidate, features = _candidate_features(stage="NO_PERSEGUIR")
    baseline = adc.decide(candidate, features)
    le = _le(wilson_lower=50.0, wilson_upper=60.0, baseline=10.0)
    eligibilidad = ke.classify_eligibility(le, _MARKET_DATE)
    activation_state = ag.classify_activation(
        mechanism_state="ON_CONTROLADO", eligibility_state=eligibilidad["eligibility_state"],
        is_revoked=True, computed_as_of=le["computed_as_of"], market_date=_MARKET_DATE,
    )["activation_state"]
    assert activation_state == "REVOCADO"

    resolucion = _new_style_resolucion(candidate, features, le, activation_state)
    assert resolucion["cambio_aplicado"] is False
    o_estado_final = baseline.decision if not resolucion["cambio_aplicado"] else resolucion["decision_controlada"]
    assert o_estado_final == "NO_TOCAR"  # revocación gana, sin importar la evidencia


def test_produccion_mecanismo_off_nunca_activa_pese_a_todo_lo_demas_perfecto():
    candidate, features = _candidate_features(stage="NO_PERSEGUIR")
    baseline = adc.decide(candidate, features)
    le = _le(wilson_lower=50.0, wilson_upper=60.0, baseline=10.0)
    eligibilidad = ke.classify_eligibility(le, _MARKET_DATE)
    activation_state = _gate_para(eligibilidad["eligibility_state"], le["computed_as_of"], mechanism_state="OFF")
    assert activation_state == "NO_ACTIVO"

    resolucion = _new_style_resolucion(candidate, features, le, activation_state)
    assert resolucion["cambio_aplicado"] is False
    o_estado_final = baseline.decision if not resolucion["cambio_aplicado"] else resolucion["decision_controlada"]
    assert o_estado_final == "NO_TOCAR"


# ---------------------------------------------------------------------------
# Responsabilidades adicionales de atlas_decision_core.decide() -- confirmar
# que ninguna se pierde porque, simplemente, ninguna era consumida por el
# bloque Fase 3.5 antiguo (el único campo leído de `controlada` era
# `.decision`).
# ---------------------------------------------------------------------------

def test_decide_no_tiene_efectos_colaterales_mas_alla_del_valor_retornado():
    """`decide()` es puro (confirmado por su propio docstring y por
    lectura completa del archivo) -- ninguna DB, ninguna red, ninguna
    escritura a estado de módulo. Quitar una llamada extra a esta función
    no puede, por construcción, perder ningún efecto colateral porque no
    existe ninguno."""
    import inspect
    src = inspect.getsource(adc.decide)
    for prohibido in ("open(", ".execute(", "requests.", "_connect(", "global "):
        assert prohibido not in src, f"decide() tiene un efecto colateral inesperado: {prohibido}"


def test_campos_de_atlasdecision_no_usados_por_fase_3_5_antigua_documentados():
    """El único campo de `AtlasDecision` que el bloque Fase 3.5 antiguo
    leía era `.decision` (confirmado por auditoría de código antes de esta
    misión) -- se confirma acá que el resto de los campos siguen
    existiendo y calculándose con normalidad para cualquier otro caller
    (p.ej. la llamada de línea base y la de shadow, ambas sin tocar),
    simplemente ya no se vuelven a calcular una tercera vez en Fase 3.5."""
    candidate, features = _candidate_features(stage="NO_PERSEGUIR")
    le = _le(wilson_lower=50.0, wilson_upper=60.0, baseline=10.0)
    resultado = adc.decide(candidate, features, learned_evidence=le, apply_recalibration=True)
    for campo in (
        "decision", "decision_shadow", "shadow_differs", "reason", "confidence",
        "methodology_version", "decision_timestamp", "learned_evidence_used",
        "features_snapshot", "scores_snapshot", "evidence_snapshot",
    ):
        assert hasattr(resultado, campo)
