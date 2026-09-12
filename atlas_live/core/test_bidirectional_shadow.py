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


# 6) apply_recalibration ausente de compute_bidirectional_decision() (estructural).
# `resolve_controlled_decision()` SÍ acepta `activation_state` como dato de
# entrada por diseño (ver tests dedicados más abajo) -- por eso este check
# se escopea a la función pura original, que sigue siendo 100% independiente.
def test_6_estructural_sin_apply_recalibration_ni_mechanism_state():
    src = inspect.getsource(bidi.compute_bidirectional_decision)
    for prohibido in ("apply_recalibration", "mechanism_state", "ON_CONTROLADO", "activation_registry"):
        assert prohibido not in src, f"referencia prohibida encontrada: {prohibido}"


# 6b) Ninguna de las 4 cadenas prohibidas aparece en TODO el módulo, ni
# siquiera en resolve_controlled_decision() -- solo "activation_state" (el
# nombre del parámetro, dato de entrada) está permitido, nunca el mecanismo
# en sí (mismo grep-test que toda la sesión, aplicado a todo el archivo).
def test_6b_estructural_modulo_completo_sin_mecanismo_de_activacion():
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


# 8b) resolve_controlled_decision() no importa activation_registry ni lee
# mechanism_state por sí mismo -- solo recibe activation_state como dato ya
# calculado por el caller (server.py), nunca lo deriva ni lo consulta.
def test_8b_resolve_controlled_decision_no_importa_activation_registry():
    src = inspect.getsource(bidi.resolve_controlled_decision)
    for prohibido in ("import activation_registry", "areg.", "get_mechanism_state", "activation_registry"):
        assert prohibido not in src, f"referencia prohibida encontrada: {prohibido}"
    sig = inspect.signature(bidi.resolve_controlled_decision)
    assert list(sig.parameters) == [
        "decision_base", "decision_shadow_downgrade", "eligibility_state",
        "learned_evidence", "activation_state",
    ]


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


# 12) mecanismo OFF / irrelevante para compute_bidirectional_decision() --
# esa función pura no depende del mecanismo de activación en absoluto
# (resolve_controlled_decision() sí acepta el veredicto del gate como dato
# de entrada, por diseño -- ver tests 8b y los de la sección siguiente).
def test_12_no_depende_del_mecanismo_de_activacion():
    assert not hasattr(bidi, "get_mechanism_state")
    assert "activation" not in inspect.getsource(bidi.compute_bidirectional_decision).lower()


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


# ---------------------------------------------------------------------------
# resolve_controlled_decision() -- misión "CONECTAR EL APRENDIZAJE
# BIDIRECCIONAL A LA DECISIÓN REAL" (2026-09-12). Reemplaza, en el único call
# site real de Fase 3.5, la llamada al flag histórico de recalibración
# forzada -- estos tests ejercitan la función real que server.py invoca.
# ---------------------------------------------------------------------------

# R1) Gate no ACTIVADO (NO_ACTIVO/BLOQUEADO/REVOCADO) -> nunca cambia nada,
# incluso con evidencia perfectamente elegible y favorable para upgrade.
def test_r1_gate_no_activado_nunca_cambia_nada_pese_a_evidencia_perfecta():
    for estado_gate in ("NO_ACTIVO", "BLOQUEADO", "REVOCADO"):
        r = bidi.resolve_controlled_decision(
            decision_base="NO_TOCAR", decision_shadow_downgrade="NO_TOCAR",
            eligibility_state="ELEGIBLE", learned_evidence=_le(5.0, 0.94),
            activation_state=estado_gate,
        )
        assert r["decision_controlada"] is None
        assert r["cambio_aplicado"] is False
        assert r["upgrade_aplicado"] is False


# R2) ACTIVADO + NO_TOCAR + ELEGIBLE + evidencia favorable -> upgrade real a VIGILAR.
def test_r2_activado_no_tocar_elegible_favorable_produce_upgrade_real():
    r = bidi.resolve_controlled_decision(
        decision_base="NO_TOCAR", decision_shadow_downgrade="NO_TOCAR",
        eligibility_state="ELEGIBLE", learned_evidence=_le(5.0, 0.94),
        activation_state="ACTIVADO",
    )
    assert r["decision_controlada"] == "VIGILAR"
    assert r["cambio_aplicado"] is True
    assert r["upgrade_aplicado"] is True


# R3) ACTIVADO + base degradable con evidencia robusta desfavorable -> el
# downgrade-only preexistente se preserva exactamente igual bajo el nuevo mecanismo.
def test_r3_activado_preserva_downgrade_existente():
    for decision_base, decision_shadow_downgrade in [
        ("OPORTUNIDAD_PRIORITARIA", "VIGILAR"), ("VIGILAR", "PREPARACION"),
    ]:
        r = bidi.resolve_controlled_decision(
            decision_base=decision_base, decision_shadow_downgrade=decision_shadow_downgrade,
            eligibility_state="ELEGIBLE", learned_evidence=_le(0.1, 0.94),
            activation_state="ACTIVADO",
        )
        assert r["decision_controlada"] == decision_shadow_downgrade
        assert r["cambio_aplicado"] is True
        assert r["upgrade_aplicado"] is False


# R4) ACTIVADO pero eligibilidad NO_ELEGIBLE/INSUFICIENTE/None -> sin cambio.
def test_r4_activado_sin_elegibilidad_suficiente_no_cambia():
    for estado in ("NO_ELEGIBLE", "INSUFICIENTE", None):
        r = bidi.resolve_controlled_decision(
            decision_base="NO_TOCAR", decision_shadow_downgrade="NO_TOCAR",
            eligibility_state=estado, learned_evidence=_le(5.0, 0.94),
            activation_state="ACTIVADO",
        )
        assert r["decision_controlada"] == "NO_TOCAR"
        assert r["cambio_aplicado"] is False


# R5) ACTIVADO + PREPARACION como base -> nunca recibe upgrade, cualquiera
# sea la evidencia (PREPARACION no es clave de UPGRADE_ONE_TIER ni de la
# tabla downgrade-only interna de atlas_decision_core).
def test_r5_activado_preparacion_nunca_cambia():
    r = bidi.resolve_controlled_decision(
        decision_base="PREPARACION", decision_shadow_downgrade="PREPARACION",
        eligibility_state="ELEGIBLE", learned_evidence=_le(5.0, 0.94),
        activation_state="ACTIVADO",
    )
    assert r["decision_controlada"] == "PREPARACION"
    assert r["cambio_aplicado"] is False


# R6) determinismo -- misma entrada, mismo resultado.
def test_r6_determinismo():
    kwargs = dict(
        decision_base="NO_TOCAR", decision_shadow_downgrade="NO_TOCAR",
        eligibility_state="ELEGIBLE", learned_evidence=_le(5.0, 0.94),
        activation_state="ACTIVADO",
    )
    assert bidi.resolve_controlled_decision(**kwargs) == bidi.resolve_controlled_decision(**kwargs)
