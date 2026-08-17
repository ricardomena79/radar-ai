"""Tests de alert_stage.py (2026-08-17, Fase 4). Puros, sin DB, sin red --
verifican el orden de prioridad de las 6 ventanas y los umbrales exactos
derivados del estudio histórico."""

from atlas_live.radar import alert_stage as als


def test_timing_tardio_gana_sobre_todo_lo_demas():
    assert als.classify_alert_stage(
        relative_volume_hoy=50.0, dias_volumen_elevado=5, aceleracion_volumen=10.0,
        volatility_14d_pct=50.0, timing_deteccion_hoy="demasiado_tarde",
    ) == "NO_PERSEGUIR"
    assert als.classify_alert_stage(
        relative_volume_hoy=50.0, dias_volumen_elevado=5, aceleracion_volumen=10.0,
        volatility_14d_pct=50.0, timing_deteccion_hoy="agotamiento",
    ) == "NO_PERSEGUIR"


def test_recorrido_significativo_da_confirmacion():
    assert als.classify_alert_stage(
        relative_volume_hoy=1.0, dias_volumen_elevado=0, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="recorrido_significativo_ya_hecho",
    ) == "CONFIRMACION"


def test_al_comienzo_da_inicio():
    assert als.classify_alert_stage(
        relative_volume_hoy=1.0, dias_volumen_elevado=0, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="al_comienzo",
    ) == "INICIO"


def test_alerta_fuerte_exige_los_3_criterios_juntos():
    base = dict(timing_deteccion_hoy="antes_del_movimiento")
    # los 3 juntos -> ALERTA_FUERTE
    assert als.classify_alert_stage(
        relative_volume_hoy=3.0, dias_volumen_elevado=2, aceleracion_volumen=1.5,
        volatility_14d_pct=12.0, **base,
    ) == "ALERTA_FUERTE"
    # falta persistencia (solo 1 día elevado) -> no llega a ALERTA_FUERTE
    assert als.classify_alert_stage(
        relative_volume_hoy=3.0, dias_volumen_elevado=1, aceleracion_volumen=1.5,
        volatility_14d_pct=12.0, **base,
    ) == "ALERTA_TEMPRANA"
    # falta volatilidad de régimen -> no llega a ALERTA_FUERTE
    assert als.classify_alert_stage(
        relative_volume_hoy=3.0, dias_volumen_elevado=2, aceleracion_volumen=1.5,
        volatility_14d_pct=5.0, **base,
    ) == "ALERTA_TEMPRANA"
    # falta aceleración positiva -> no llega a ALERTA_FUERTE
    assert als.classify_alert_stage(
        relative_volume_hoy=3.0, dias_volumen_elevado=2, aceleracion_volumen=-0.5,
        volatility_14d_pct=12.0, **base,
    ) == "ALERTA_TEMPRANA"


def test_alerta_temprana_con_un_dia_elevado_o_volumen_hoy():
    base = dict(timing_deteccion_hoy="antes_del_movimiento", volatility_14d_pct=5.0, aceleracion_volumen=None)
    assert als.classify_alert_stage(relative_volume_hoy=0.5, dias_volumen_elevado=1, **base) == "ALERTA_TEMPRANA"
    assert als.classify_alert_stage(relative_volume_hoy=2.5, dias_volumen_elevado=0, **base) == "ALERTA_TEMPRANA"


def test_preparacion_solo_con_volatilidad_de_regimen():
    assert als.classify_alert_stage(
        relative_volume_hoy=0.5, dias_volumen_elevado=0, aceleracion_volumen=None,
        volatility_14d_pct=15.0, timing_deteccion_hoy="antes_del_movimiento",
    ) == "PREPARACION"


def test_sin_ninguna_condicion_no_hay_alerta():
    assert als.classify_alert_stage(
        relative_volume_hoy=0.5, dias_volumen_elevado=0, aceleracion_volumen=None,
        volatility_14d_pct=3.0, timing_deteccion_hoy="antes_del_movimiento",
    ) is None


def test_valores_none_no_rompen_nada():
    assert als.classify_alert_stage(
        relative_volume_hoy=None, dias_volumen_elevado=None, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy=None,
    ) is None


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
