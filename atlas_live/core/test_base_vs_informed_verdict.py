"""Tests de `base_vs_informed_verdict.py` (Fase 3-4/7 de la misión
"HACER OPERATIVO EL APRENDIZAJE REAL", 2026-09-11)."""

import inspect

from atlas_live.core import base_vs_informed_verdict as bvi


def _evento(baseline_v, shadow_v):
    return {
        "ticker": "TEST",
        "market_date": "2026-09-10",
        "eligibility_state": "ELEGIBLE",
        "decision_baseline": "OPORTUNIDAD_PRIORITARIA",
        "decision_shadow": "VIGILAR",
        "shadow_differs": True,
        "outcome_evaluable": baseline_v != "PENDIENTE",
        "decision_baseline_veredicto": baseline_v,
        "decision_shadow_veredicto": shadow_v,
    }


def _universo(eventos_a=0, eventos_b=0, eventos_c=None):
    eventos_c = eventos_c or []
    return {
        "A_sin_elegible": {"n_eventos": eventos_a, "eventos": [{} for _ in range(eventos_a)]},
        "B_elegible_sin_divergencia": {"n_eventos": eventos_b, "eventos": [{} for _ in range(eventos_b)]},
        "C_elegible_con_divergencia": {"n_eventos": len(eventos_c), "eventos": eventos_c},
    }


def test_1_grupo_c_vacio_conocimiento_consultado_sin_efecto():
    universo = _universo(eventos_a=10, eventos_b=5, eventos_c=[])
    resultado = bvi.build_verdict(universo, piso_muestra_minima=5)
    assert resultado["veredicto"] == "CONOCIMIENTO_CONSULTADO_SIN_EFECTO_DECISIONAL"
    assert resultado["universo"]["n_C_elegible_con_divergencia"] == 0


def test_2_grupo_c_todo_pendiente_no_evaluable():
    eventos_c = [_evento("PENDIENTE", "PENDIENTE") for _ in range(10)]
    universo = _universo(eventos_c=eventos_c)
    resultado = bvi.build_verdict(universo, piso_muestra_minima=5)
    assert resultado["veredicto"] == "NO_EVALUABLE"
    assert resultado["grupo_C_detalle"]["n_evaluable"] == 0
    assert resultado["grupo_C_detalle"]["n_pendiente"] == 10


def test_3_muestra_evaluable_bajo_el_piso_no_evaluable():
    eventos_c = [_evento("ERROR", "ACIERTO") for _ in range(3)]
    universo = _universo(eventos_c=eventos_c)
    resultado = bvi.build_verdict(universo, piso_muestra_minima=500)
    assert resultado["veredicto"] == "NO_EVALUABLE"
    assert resultado["grupo_C_detalle"]["n_evaluable"] == 3


def test_4_discordantes_balanceados_cambio_sin_mejora_demostrada():
    eventos_c = (
        [_evento("ERROR", "ACIERTO") for _ in range(5)]
        + [_evento("ACIERTO", "ERROR") for _ in range(5)]
        + [_evento("ACIERTO", "ACIERTO") for _ in range(10)]
    )
    universo = _universo(eventos_c=eventos_c)
    resultado = bvi.build_verdict(universo, piso_muestra_minima=5)
    assert resultado["veredicto"] == "CAMBIO_OBSERVADO_MEJORA_NO_DEMOSTRADA"
    assert resultado["grupo_C_detalle"]["mejoras"] == 5
    assert resultado["grupo_C_detalle"]["empeoramientos"] == 5


def test_4b_cero_pares_discordantes_cambio_sin_mejora_demostrada():
    eventos_c = [_evento("ACIERTO", "ACIERTO") for _ in range(20)]
    universo = _universo(eventos_c=eventos_c)
    resultado = bvi.build_verdict(universo, piso_muestra_minima=5)
    assert resultado["veredicto"] == "CAMBIO_OBSERVADO_MEJORA_NO_DEMOSTRADA"
    assert resultado["grupo_C_detalle"]["discordantes"] == 0


def test_5_diferencia_apareada_robusta_favorece_shadow_aprendizaje_demostrado():
    eventos_c = (
        [_evento("ERROR", "ACIERTO") for _ in range(45)]
        + [_evento("ACIERTO", "ERROR") for _ in range(5)]
        + [_evento("ACIERTO", "ACIERTO") for _ in range(50)]
    )
    universo = _universo(eventos_c=eventos_c)
    resultado = bvi.build_verdict(universo, piso_muestra_minima=100)
    assert resultado["veredicto"] == "APRENDIZAJE_OPERATIVO_DEMOSTRADO"
    ci = resultado["wilson_ci_diferencia_apareada_pct_favorable_a_shadow"]
    assert ci is not None and ci[0] > 50.0


def test_6_determinismo_misma_entrada_mismo_resultado():
    eventos_c = [_evento("ERROR", "ACIERTO") for _ in range(10)] + [_evento("ACIERTO", "ACIERTO") for _ in range(10)]
    universo = _universo(eventos_c=eventos_c)
    r1 = bvi.build_verdict(universo, piso_muestra_minima=5)
    r2 = bvi.build_verdict(universo, piso_muestra_minima=5)
    assert r1 == r2


def test_7_estructural_sin_apply_recalibration_ni_vocabulario_financiero():
    src = inspect.getsource(bvi)
    prohibidos = [
        "apply_recalibration", "ON_CONTROLADO", "broker", "place_order",
        "execute_trade", "buy(", "sell(",
    ]
    for palabra in prohibidos:
        assert palabra not in src, f"vocabulario prohibido encontrado: {palabra}"


def test_8_estructural_nunca_reevalua_fecha():
    src = inspect.getsource(bvi)
    assert "datetime.now(" not in src
    assert "date.today(" not in src


def test_9_entrada_malformada_nunca_lanza():
    resultado = bvi.build_verdict({}, piso_muestra_minima=5)
    assert resultado["veredicto"] == "CONOCIMIENTO_CONSULTADO_SIN_EFECTO_DECISIONAL"
    resultado2 = bvi.build_verdict(None, piso_muestra_minima=5)  # type: ignore[arg-type]
    assert resultado2["veredicto"] == "CONOCIMIENTO_CONSULTADO_SIN_EFECTO_DECISIONAL"


def test_10_veredictos_conocidos_son_exactamente_los_4():
    assert bvi.VERDICTS == (
        "APRENDIZAJE_OPERATIVO_DEMOSTRADO",
        "CAMBIO_OBSERVADO_MEJORA_NO_DEMOSTRADA",
        "CONOCIMIENTO_CONSULTADO_SIN_EFECTO_DECISIONAL",
        "NO_EVALUABLE",
    )


# --- build_bidirectional_verdict() (2026-09-12, experimento bidireccional) --

def _evento_bidi(base_v, informada_v):
    return {
        "ticker": "TEST", "market_date": "2026-09-10", "eligibility_state": "ELEGIBLE",
        "decision_base": "NO_TOCAR", "decision_informada": "VIGILAR", "upgrade_aplicado": True,
        "outcome_evaluable": base_v != "PENDIENTE",
        "decision_base_veredicto": base_v, "decision_informada_veredicto": informada_v,
    }


def test_b1_grupo_c_vacio_sin_diferencia():
    universo = _universo(eventos_c=[])
    r = bvi.build_bidirectional_verdict(universo, piso_muestra_minima=5)
    assert r["veredicto"] == "SIN_DIFERENCIA"


def test_b2_grupo_c_todo_pendiente_no_evaluable():
    eventos_c = [_evento_bidi("PENDIENTE", "PENDIENTE") for _ in range(10)]
    universo = _universo(eventos_c=eventos_c)
    r = bvi.build_bidirectional_verdict(universo, piso_muestra_minima=5)
    assert r["veredicto"] == "NO_EVALUABLE"


def test_b3_evaluable_bajo_el_piso_evidencia_insuficiente():
    eventos_c = [_evento_bidi("ERROR", "ACIERTO") for _ in range(3)]
    universo = _universo(eventos_c=eventos_c)
    r = bvi.build_bidirectional_verdict(universo, piso_muestra_minima=500)
    assert r["veredicto"] == "EVIDENCIA_INSUFICIENTE"
    assert bvi.MENSAJE_MUESTRA_INSUFICIENTE in r["motivo"]


def test_b4_discordantes_balanceados_sin_diferencia():
    eventos_c = (
        [_evento_bidi("ERROR", "ACIERTO") for _ in range(5)]
        + [_evento_bidi("ACIERTO", "ERROR") for _ in range(5)]
        + [_evento_bidi("ACIERTO", "ACIERTO") for _ in range(10)]
    )
    universo = _universo(eventos_c=eventos_c)
    r = bvi.build_bidirectional_verdict(universo, piso_muestra_minima=5)
    assert r["veredicto"] == "SIN_DIFERENCIA"


def test_b5_mejora_robusta():
    eventos_c = (
        [_evento_bidi("ERROR", "ACIERTO") for _ in range(45)]
        + [_evento_bidi("ACIERTO", "ERROR") for _ in range(5)]
        + [_evento_bidi("ACIERTO", "ACIERTO") for _ in range(50)]
    )
    universo = _universo(eventos_c=eventos_c)
    r = bvi.build_bidirectional_verdict(universo, piso_muestra_minima=100)
    assert r["veredicto"] == "MEJORA"


def test_b6_empeora_robusto():
    eventos_c = (
        [_evento_bidi("ACIERTO", "ERROR") for _ in range(45)]
        + [_evento_bidi("ERROR", "ACIERTO") for _ in range(5)]
        + [_evento_bidi("ACIERTO", "ACIERTO") for _ in range(50)]
    )
    universo = _universo(eventos_c=eventos_c)
    r = bvi.build_bidirectional_verdict(universo, piso_muestra_minima=100)
    assert r["veredicto"] == "EMPEORA"


def test_b7_veredictos_bidireccionales_son_exactamente_los_5():
    assert bvi.VERDICTS_BIDIRECCIONAL == ("MEJORA", "EMPEORA", "SIN_DIFERENCIA", "EVIDENCIA_INSUFICIENTE", "NO_EVALUABLE")


def test_b8_build_verdict_original_no_cambio_de_comportamiento():
    # Mismo caso que test_5 del veredicto original -- confirma que agregar
    # build_bidirectional_verdict() no tocó build_verdict() en absoluto.
    eventos_c = (
        [_evento("ERROR", "ACIERTO") for _ in range(45)]
        + [_evento("ACIERTO", "ERROR") for _ in range(5)]
        + [_evento("ACIERTO", "ACIERTO") for _ in range(50)]
    )
    universo = _universo(eventos_c=eventos_c)
    r = bvi.build_verdict(universo, piso_muestra_minima=100)
    assert r["veredicto"] == "APRENDIZAJE_OPERATIVO_DEMOSTRADO"
