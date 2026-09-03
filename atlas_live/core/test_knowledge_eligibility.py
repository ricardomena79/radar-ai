"""Tests de `knowledge_eligibility.py` (Hito 3, Fase 3.3, 2026-09-03,
autorizado explícitamente en Plan Mode). Puro, sin DB."""

from atlas_live.core import knowledge_eligibility as ke

_LE_ELEGIBLE_ROBUSTA = {
    "available": True,
    "validation_state": "VALIDACION_ROBUSTA",
    "sample_size": 600,
    "historical_success_pct_20": 42.0,
    "baseline_pct_20": 30.0,
    "lift_20": 12.0,
    "wilson_lower_bound_20_pct": 38.0,
    "wilson_upper_bound_20_pct": 46.0,
    "computed_as_of": "2026-08-20",
    "computed_at": "2026-08-21T00:00:00+00:00",
    "methodology_version": "v1_direction_timing_volatility_tercile",
}

_MARKET_DATE = "2026-08-24"


def _le(**overrides):
    base = dict(_LE_ELEGIBLE_ROBUSTA)
    base.update(overrides)
    return base


# --- 1) conocimiento elegible ------------------------------------------

def test_elegible_con_validacion_robusta():
    r = ke.classify_eligibility(_le(validation_state="VALIDACION_ROBUSTA", sample_size=600), _MARKET_DATE)
    assert r["eligibility_state"] == "ELEGIBLE"
    assert r["validation_state"] == "VALIDACION_ROBUSTA"
    assert "ELEGIBLE" in r["reasons"][0]


def test_en_validacion_es_insuficiente_no_elegible():
    # Corrección 2026-09-03 (auditoría explícita del usuario): ELEGIBLE
    # exige VALIDACION_ROBUSTA exclusivamente -- EN_VALIDACION (100-499
    # muestras) ya no cuenta como elegible, sin importar qué tan cerca
    # esté del piso robusto.
    r = ke.classify_eligibility(_le(validation_state="EN_VALIDACION", sample_size=250), _MARKET_DATE)
    assert r["eligibility_state"] == "INSUFICIENTE"
    assert r["eligibility_state"] != "ELEGIBLE"
    assert r["validation_state"] == "EN_VALIDACION"
    assert "EN_VALIDACION" in r["reasons"][0]


def test_en_validacion_cerca_del_piso_robusto_sigue_siendo_insuficiente():
    # Caso límite explícito: n=499 (un paso antes del piso de 500) --
    # sigue siendo INSUFICIENTE, la regla es sobre validation_state, no
    # sobre "qué tan cerca" está la muestra del piso robusto.
    r = ke.classify_eligibility(_le(validation_state="EN_VALIDACION", sample_size=499), _MARKET_DATE)
    assert r["eligibility_state"] == "INSUFICIENTE"


def test_validacion_robusta_es_elegible():
    # Caso límite explícito: n=500 exacto (piso de VALIDACION_ROBUSTA) --
    # ELEGIBLE.
    r = ke.classify_eligibility(_le(validation_state="VALIDACION_ROBUSTA", sample_size=500), _MARKET_DATE)
    assert r["eligibility_state"] == "ELEGIBLE"
    assert r["validation_state"] == "VALIDACION_ROBUSTA"


def test_elegible_conserva_todos_los_campos_de_evidencia():
    r = ke.classify_eligibility(_le(), _MARKET_DATE)
    assert r["sample_size"] == 600
    assert r["baseline_pct_20"] == 30.0
    assert r["lift_20"] == 12.0
    assert r["wilson_lower_bound_20_pct"] == 38.0
    assert r["wilson_upper_bound_20_pct"] == 46.0
    assert r["computed_as_of"] == "2026-08-20"
    assert r["computed_at"] == "2026-08-21T00:00:00+00:00"
    assert r["methodology_version"] == "v1_direction_timing_volatility_tercile"


# --- 2) muestra insuficiente ---------------------------------------------

def test_muestra_insuficiente_nunca_es_elegible():
    r = ke.classify_eligibility(_le(validation_state="MUESTRA_INSUFICIENTE", sample_size=42), _MARKET_DATE)
    assert r["eligibility_state"] == "INSUFICIENTE"
    assert r["eligibility_state"] != "ELEGIBLE"
    assert "MUESTRA_INSUFICIENTE" in r["reasons"][0]
    assert r["sample_size"] == 42


# --- 3) validación fallida (distinta de "insuficiente") -----------------

def test_validation_state_desconocido_es_no_elegible_no_insuficiente():
    r = ke.classify_eligibility(_le(validation_state="ALGO_QUE_NO_EXISTE"), _MARKET_DATE)
    assert r["eligibility_state"] == "NO_ELEGIBLE"
    assert r["eligibility_state"] != "INSUFICIENTE"
    assert "INTEGRIDAD_ROTA" in r["reasons"][0]
    assert "validation_state desconocido" in r["reasons"][0]


def test_wilson_lower_mayor_que_upper_es_no_elegible():
    r = ke.classify_eligibility(_le(wilson_lower_bound_20_pct=50.0, wilson_upper_bound_20_pct=10.0), _MARKET_DATE)
    assert r["eligibility_state"] == "NO_ELEGIBLE"
    assert "intervalo Wilson inconsistente" in r["reasons"][0]


def test_sample_size_no_positivo_es_no_elegible():
    r = ke.classify_eligibility(_le(sample_size=0), _MARKET_DATE)
    assert r["eligibility_state"] == "NO_ELEGIBLE"
    assert "sample_size" in r["reasons"][0]


# --- 4) evidencia temporalmente inválida (malformada, no walk-forward) --

def test_computed_as_of_ausente_es_no_elegible():
    r = ke.classify_eligibility(_le(computed_as_of=None), _MARKET_DATE)
    assert r["eligibility_state"] == "NO_ELEGIBLE"
    assert "INTEGRIDAD_ROTA" in r["reasons"][0]
    assert "computed_as_of" in r["reasons"][0]


def test_computed_as_of_no_parseable_es_no_elegible():
    r = ke.classify_eligibility(_le(computed_as_of="no-es-una-fecha"), _MARKET_DATE)
    assert r["eligibility_state"] == "NO_ELEGIBLE"
    assert "computed_as_of" in r["reasons"][0]


def test_computed_at_ausente_es_no_elegible():
    r = ke.classify_eligibility(_le(computed_at=None), _MARKET_DATE)
    assert r["eligibility_state"] == "NO_ELEGIBLE"
    assert "computed_at" in r["reasons"][0]


# --- 5) walk-forward violation (bien formada, del lado equivocado) ------

def test_walk_forward_violation_computed_as_of_igual_a_market_date():
    r = ke.classify_eligibility(_le(computed_as_of=_MARKET_DATE), _MARKET_DATE)
    assert r["eligibility_state"] == "NO_ELEGIBLE"
    assert "WALK_FORWARD_VIOLATION" in r["reasons"][0]
    assert r["checks"]["walk_forward_seguro"] is False


def test_walk_forward_violation_computed_as_of_posterior():
    r = ke.classify_eligibility(_le(computed_as_of="2026-08-25"), _MARKET_DATE)
    assert r["eligibility_state"] == "NO_ELEGIBLE"
    assert "WALK_FORWARD_VIOLATION" in r["reasons"][0]


def test_walk_forward_seguro_cuando_computed_as_of_es_anterior():
    r = ke.classify_eligibility(_le(computed_as_of="2026-08-01"), _MARKET_DATE)
    assert r["checks"]["walk_forward_seguro"] is True
    assert r["eligibility_state"] != "NO_ELEGIBLE"


def test_walk_forward_es_distinto_de_integridad_rota():
    # Bien formada (pasa integridad) pero del lado equivocado del corte --
    # debe fallar específicamente por WALK_FORWARD_VIOLATION, no por
    # INTEGRIDAD_ROTA (son chequeos distintos, con razones distintas).
    r = ke.classify_eligibility(_le(computed_as_of=_MARKET_DATE), _MARKET_DATE)
    assert r["checks"]["integridad_estructural"] is True
    assert "INTEGRIDAD_ROTA" not in r["reasons"][0]


# --- 6) conocimiento inexistente -----------------------------------------

def test_conocimiento_inexistente_dict_available_false():
    le = {"available": False, "reason": "SIN_CONOCIMIENTO_PARA_ESTA_CONDICION"}
    r = ke.classify_eligibility(le, _MARKET_DATE)
    assert r["eligibility_state"] == "NO_ELEGIBLE"
    assert "SIN_CONOCIMIENTO_PARA_ESTA_CONDICION" in r["reasons"][0]
    assert r["checks"]["knowledge_available"] is False


def test_conocimiento_inexistente_learned_evidence_none():
    r = ke.classify_eligibility(None, _MARKET_DATE)
    assert r["eligibility_state"] == "NO_ELEGIBLE"
    assert "SIN_LEARNED_EVIDENCE" in r["reasons"][0]


def test_conocimiento_inexistente_error_de_consulta():
    le = {"available": False, "reason": "ERROR_CONSULTA: OperationalError"}
    r = ke.classify_eligibility(le, _MARKET_DATE)
    assert r["eligibility_state"] == "NO_ELEGIBLE"
    assert "ERROR_CONSULTA" in r["reasons"][0]


# --- determinismo / reproducibilidad -------------------------------------

def test_misma_entrada_produce_siempre_el_mismo_resultado():
    r1 = ke.classify_eligibility(_le(), _MARKET_DATE)
    r2 = ke.classify_eligibility(_le(), _MARKET_DATE)
    assert r1 == r2


def test_modulo_nunca_pasa_apply_recalibration_true():
    # El módulo no maneja `apply_recalibration` en absoluto (no es parte de
    # su API) -- el docstring lo menciona en prosa para explicar el
    # alcance, así que el escaneo busca específicamente una asignación en
    # True, no la sola presencia del nombre (mismo criterio de falsos
    # positivos ya resuelto para `decision_knowledge_registry.py`).
    import inspect
    fuente = inspect.getsource(ke)
    assert "apply_recalibration=True" not in fuente
    assert "apply_recalibration = True" not in fuente
