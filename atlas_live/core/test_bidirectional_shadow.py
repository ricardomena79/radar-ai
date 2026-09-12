"""Tests de `bidirectional_shadow.py` (misión "EXPERIMENTO SHADOW DE
APRENDIZAJE BIDIRECCIONAL", 2026-09-12) -- 14 casos negativos pedidos
explícitamente por el usuario, mapeados 1 a 1, más los estructurales."""

import inspect

from atlas_live.core import bidirectional_shadow as bidi


def _le(wilson_lower=None, baseline=None):
    return {"wilson_lower_bound_20_pct": wilson_lower, "baseline_pct_20": baseline}


# 1) NO_TOCAR sin conocimiento -> permanece NO_TOCAR.
def test_1_no_tocar_sin_conocimiento_permanece():
    r = bidi.compute_bidirectional_decision("NO_TOCAR", "NO_TOCAR", None, None)
    assert r["decision_informada"] == "NO_TOCAR"
    assert r["upgrade_aplicado"] is False


# 2) NO_TOCAR con conocimiento no elegible -> permanece NO_TOCAR.
def test_2_no_tocar_con_conocimiento_no_elegible_permanece():
    for estado in ("NO_ELEGIBLE", "INSUFICIENTE"):
        r = bidi.compute_bidirectional_decision(
            "NO_TOCAR", "NO_TOCAR", estado, _le(wilson_lower=50.0, baseline=1.0),
        )
        assert r["decision_informada"] == "NO_TOCAR"
        assert r["upgrade_aplicado"] is False


# 3) NO_TOCAR con conocimiento elegible y favorable -> puede pasar a VIGILAR.
def test_3_no_tocar_elegible_favorable_pasa_a_vigilar():
    r = bidi.compute_bidirectional_decision(
        "NO_TOCAR", "NO_TOCAR", "ELEGIBLE", _le(wilson_lower=5.0, baseline=0.94),
    )
    assert r["decision_informada"] == "VIGILAR"
    assert r["upgrade_aplicado"] is True


# 4) Nunca pasa NO_TOCAR -> OPORTUNIDAD_PRIORITARIA, en ningún caso sintético.
def test_4_nunca_salta_a_oportunidad_prioritaria():
    casos = [
        (None, None), ("NO_ELEGIBLE", _le(50.0, 1.0)), ("INSUFICIENTE", _le(50.0, 1.0)),
        ("ELEGIBLE", _le(5.0, 0.94)), ("ELEGIBLE", _le(0.1, 0.94)), ("ELEGIBLE", None),
    ]
    for eligibility_state, le in casos:
        r = bidi.compute_bidirectional_decision("NO_TOCAR", "NO_TOCAR", eligibility_state, le)
        assert r["decision_informada"] != "OPORTUNIDAD_PRIORITARIA"
    assert set(bidi.UPGRADE_ONE_TIER.values()) == {"VIGILAR"}


# 5) PREPARACION no recibe upgrade.
def test_5_preparacion_no_recibe_upgrade():
    r = bidi.compute_bidirectional_decision(
        "PREPARACION", "PREPARACION", "ELEGIBLE", _le(wilson_lower=5.0, baseline=0.94),
    )
    assert r["decision_informada"] == "PREPARACION"
    assert r["upgrade_aplicado"] is False
    assert "PREPARACION" not in bidi.UPGRADE_ONE_TIER


# 6) apply_recalibration ausente del módulo (estructural).
def test_6_estructural_sin_apply_recalibration_ni_mechanism_state():
    src = inspect.getsource(bidi)
    for prohibido in ("apply_recalibration", "mechanism_state", "ON_CONTROLADO", "activation_registry"):
        assert prohibido not in src, f"referencia prohibida encontrada: {prohibido}"


# 7) decision_base nunca se muta -- solo primitivos entran/salen.
def test_7_solo_acepta_primitivos_nunca_objetos_de_decision():
    sig = inspect.signature(bidi.compute_bidirectional_decision)
    assert list(sig.parameters) == [
        "decision_base", "decision_shadow_downgrade", "eligibility_state", "learned_evidence",
    ]
    r = bidi.compute_bidirectional_decision("NO_TOCAR", "NO_TOCAR", "ELEGIBLE", _le(5.0, 0.94))
    assert isinstance(r["decision_informada"], str)
    assert isinstance(r["upgrade_aplicado"], bool)


# 8) walk-forward: el módulo nunca lee fecha/reloj directamente (estructural,
# depende exclusivamente del veredicto YA walk-forward-seguro de Hito 3.3).
def test_8_estructural_nunca_reevalua_fecha_ni_walk_forward():
    src = inspect.getsource(bidi)
    for prohibido in ("datetime.now(", "date.today(", "computed_as_of", "market_date"):
        assert prohibido not in src, f"referencia prohibida encontrada: {prohibido}"


# 9) mismo caso produce A y B apareados -- el dict siempre trae ambos lados
# implícitos (decision_base es un parámetro de entrada, decision_informada
# la salida) -- nunca se pierde la referencia al caso base.
def test_9_devuelve_siempre_decision_informada_junto_al_caso():
    r = bidi.compute_bidirectional_decision("NO_TOCAR", "NO_TOCAR", "ELEGIBLE", _le(5.0, 0.94))
    assert "decision_informada" in r and "upgrade_aplicado" in r and "motivo" in r


# 10) determinismo -- outcome se evaluará idénticamente para A y B en el
# reporte porque ambos comparten el mismo (ticker, market_date); acá se
# confirma que la función en sí es determinista (mismo insumo -> mismo B).
def test_10_determinismo():
    args = ("NO_TOCAR", "NO_TOCAR", "ELEGIBLE", _le(5.0, 0.94))
    assert bidi.compute_bidirectional_decision(*args) == bidi.compute_bidirectional_decision(*args)


# 11) eligibility_state=None (conocimiento nunca evaluado por 3.3) -> sin upgrade.
def test_11_eligibility_state_none_sin_upgrade():
    r = bidi.compute_bidirectional_decision("NO_TOCAR", "NO_TOCAR", None, _le(5.0, 0.94))
    assert r["decision_informada"] == "NO_TOCAR"
    assert r["upgrade_aplicado"] is False


# 12) mecanismo OFF / irrelevante -- el módulo no importa activation_registry
# en absoluto (ya cubierto en el test 6, se re-afirma explícitamente acá).
def test_12_no_depende_del_mecanismo_de_activacion():
    assert not hasattr(bidi, "get_mechanism_state")
    assert "activation" not in inspect.getsource(bidi).lower()


# 13) conocimiento negativo o sin ventaja -> no upgrade (incluye igualdad exacta).
def test_13_conocimiento_negativo_o_igual_sin_ventaja_no_upgrade():
    for wilson_lower, baseline in [(0.5, 0.94), (0.94, 0.94), (0.0, 0.94)]:
        r = bidi.compute_bidirectional_decision(
            "NO_TOCAR", "NO_TOCAR", "ELEGIBLE", _le(wilson_lower, baseline),
        )
        assert r["decision_informada"] == "NO_TOCAR"
        assert r["upgrade_aplicado"] is False


# 14) una condición que empeora respecto del baseline (la misma evidencia que
# ya dispara el downgrade existente, wilson_upper < baseline) nunca se usa
# para elevar -- downgrade y upgrade son mutuamente excluyentes por decision_base:
# el downgrade solo aplica a OPORTUNIDAD_PRIORITARIA/VIGILAR (nunca NO_TOCAR),
# el upgrade solo aplica a NO_TOCAR -- nunca se cruzan.
def test_14_evidencia_desfavorable_nunca_eleva_y_downgrade_upgrade_son_excluyentes():
    # Evidencia desfavorable (la misma que activaría un downgrade si decision_base
    # fuera OPORTUNIDAD_PRIORITARIA/VIGILAR) sobre un caso NO_TOCAR -> sin upgrade.
    r = bidi.compute_bidirectional_decision(
        "NO_TOCAR", "NO_TOCAR", "ELEGIBLE", _le(wilson_lower=0.1, baseline=0.94),
    )
    assert r["decision_informada"] == "NO_TOCAR"
    # decision_base degradable (ya cubierto por el shadow downgrade existente) nunca
    # entra por la rama de upgrade, sin importar la evidencia:
    for decision_base, decision_shadow_downgrade in [
        ("OPORTUNIDAD_PRIORITARIA", "VIGILAR"), ("VIGILAR", "PREPARACION"),
    ]:
        r2 = bidi.compute_bidirectional_decision(
            decision_base, decision_shadow_downgrade, "ELEGIBLE", _le(wilson_lower=5.0, baseline=0.94),
        )
        assert r2["decision_informada"] == decision_shadow_downgrade
        assert r2["upgrade_aplicado"] is False


def test_upgrade_one_tier_es_exactamente_no_tocar_a_vigilar():
    assert bidi.UPGRADE_ONE_TIER == {"NO_TOCAR": "VIGILAR"}
