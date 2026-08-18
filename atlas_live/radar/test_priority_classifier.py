"""Tests reales (puros, sin DB/red) de `priority_classifier.classify_final_priority`."""

from atlas_live.radar.priority_classifier import (
    FINAL_STATES,
    SIN_PRECIO_ACTUAL_MOTIVO,
    classify_final_priority,
)


def test_sin_precio_actual_es_siempre_no_tocar_sin_importar_la_etapa():
    estado, motivo = classify_final_priority(
        stage="CONFIRMACION", direction="ALCISTA", change_pct_confiable=True,
        tiene_precio_actual=False,
    )
    assert estado == "NO_TOCAR"
    assert motivo == SIN_PRECIO_ACTUAL_MOTIVO


def test_no_perseguir_es_no_tocar():
    estado, motivo = classify_final_priority(
        stage="NO_PERSEGUIR", direction=None, change_pct_confiable=True, tiene_precio_actual=True,
    )
    assert estado == "NO_TOCAR"
    assert "NO_PERSEGUIR" in motivo


def test_flujo_vendedor_es_no_tocar():
    estado, motivo = classify_final_priority(
        stage="FLUJO_VENDEDOR", direction="BAJISTA", change_pct_confiable=True, tiene_precio_actual=True,
    )
    assert estado == "NO_TOCAR"
    assert "FLUJO_VENDEDOR" in motivo


def test_inicio_alcista_con_precio_actual_es_oportunidad_prioritaria():
    estado, motivo = classify_final_priority(
        stage="INICIO", direction="ALCISTA", change_pct_confiable=True, tiene_precio_actual=True,
    )
    assert estado == "OPORTUNIDAD_PRIORITARIA"
    assert "INICIO" in motivo


def test_confirmacion_alcista_es_oportunidad_prioritaria():
    estado, motivo = classify_final_priority(
        stage="CONFIRMACION", direction="ALCISTA", change_pct_confiable=True, tiene_precio_actual=True,
    )
    assert estado == "OPORTUNIDAD_PRIORITARIA"


def test_alerta_temprana_es_vigilar():
    estado, motivo = classify_final_priority(
        stage="ALERTA_TEMPRANA", direction=None, change_pct_confiable=True, tiene_precio_actual=True,
    )
    assert estado == "VIGILAR"


def test_alerta_fuerte_es_vigilar():
    estado, motivo = classify_final_priority(
        stage="ALERTA_FUERTE", direction="NEUTRAL", change_pct_confiable=True, tiene_precio_actual=True,
    )
    assert estado == "VIGILAR"


def test_preparacion_es_preparacion():
    estado, motivo = classify_final_priority(
        stage="PREPARACION", direction=None, change_pct_confiable=True, tiene_precio_actual=True,
    )
    assert estado == "PREPARACION"


def test_deteccion_temprana_es_preparacion():
    estado, motivo = classify_final_priority(
        stage="DETECCION_TEMPRANA", direction=None, change_pct_confiable=None, tiene_precio_actual=True,
    )
    assert estado == "PREPARACION"


def test_sector_flow_active_se_anexa_al_motivo_pero_no_cambia_estado():
    estado_con, motivo_con = classify_final_priority(
        stage="INICIO", direction="ALCISTA", change_pct_confiable=True,
        tiene_precio_actual=True, sector_flow_active=True,
    )
    estado_sin, motivo_sin = classify_final_priority(
        stage="INICIO", direction="ALCISTA", change_pct_confiable=True,
        tiene_precio_actual=True, sector_flow_active=False,
    )
    assert estado_con == estado_sin == "OPORTUNIDAD_PRIORITARIA"
    assert "flujo de dinero activo" in motivo_con
    assert "flujo de dinero activo" not in motivo_sin


def test_evidencia_historica_solo_anota_nunca_cambia_estado_final():
    evidencia = {"grupo_existe": True, "pct_20": 47.0, "n": 312}
    estado_con, motivo_con = classify_final_priority(
        stage="ALERTA_TEMPRANA", direction=None, change_pct_confiable=True,
        tiene_precio_actual=True, historical_evidence=evidencia,
    )
    estado_sin, motivo_sin = classify_final_priority(
        stage="ALERTA_TEMPRANA", direction=None, change_pct_confiable=True,
        tiene_precio_actual=True, historical_evidence=None,
    )
    assert estado_con == estado_sin == "VIGILAR"
    assert "evidencia histórica" in motivo_con
    assert "n=312" in motivo_con
    assert "evidencia histórica" not in motivo_sin


def test_evidencia_historica_sin_grupo_existente_no_se_anexa():
    evidencia = {"grupo_existe": False, "bucket": None, "n": 0}
    estado, motivo = classify_final_priority(
        stage="PREPARACION", direction=None, change_pct_confiable=True,
        tiene_precio_actual=True, historical_evidence=evidencia,
    )
    assert estado == "PREPARACION"
    assert "evidencia histórica" not in motivo


def test_stage_desconocido_o_none_es_no_tocar():
    estado, motivo = classify_final_priority(
        stage=None, direction=None, change_pct_confiable=None, tiene_precio_actual=True,
    )
    assert estado == "NO_TOCAR"


def test_inicio_sin_direccion_alcista_confirmada_nunca_es_prioritaria():
    # No debería ocurrir en la práctica (alert_stage.py ya lo exige), pero
    # esta función nunca marca OPORTUNIDAD_PRIORITARIA sin ALCISTA explícito.
    estado, motivo = classify_final_priority(
        stage="INICIO", direction=None, change_pct_confiable=True, tiene_precio_actual=True,
    )
    assert estado != "OPORTUNIDAD_PRIORITARIA"


def test_todos_los_estados_devueltos_pertenecen_a_final_states():
    casos = [
        ("NO_PERSEGUIR", None), ("FLUJO_VENDEDOR", "BAJISTA"), ("INICIO", "ALCISTA"),
        ("CONFIRMACION", "ALCISTA"), ("ALERTA_TEMPRANA", None), ("ALERTA_FUERTE", None),
        ("PREPARACION", None), ("DETECCION_TEMPRANA", None), (None, None),
    ]
    for stage, direction in casos:
        estado, _ = classify_final_priority(
            stage=stage, direction=direction, change_pct_confiable=True, tiene_precio_actual=True,
        )
        assert estado in FINAL_STATES
