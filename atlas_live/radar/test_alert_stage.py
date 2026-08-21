"""Tests de alert_stage.py (2026-08-17, Fase 4). Puros, sin DB, sin red --
verifican el orden de prioridad de las 6 ventanas y los umbrales exactos
derivados del estudio histórico."""

from atlas_live.radar import alert_stage as als


def test_agotamiento_gana_sobre_todo_lo_demas_siempre():
    """"agotamiento" YA implica un retroceso real desde un pico (viene de
    `phase_classifier.near_trough_after_peak`, vía la puerta `recuperacion`)
    -- por eso sigue forzando NO_PERSEGUIR sin importar el resto, CON o SIN
    `retroceso_desde_maximo_pct` explícito."""
    assert als.classify_alert_stage(
        relative_volume_hoy=50.0, dias_volumen_elevado=5, aceleracion_volumen=10.0,
        volatility_14d_pct=50.0, timing_deteccion_hoy="agotamiento",
    ) == "NO_PERSEGUIR"
    assert als.classify_alert_stage(
        relative_volume_hoy=50.0, dias_volumen_elevado=5, aceleracion_volumen=10.0,
        volatility_14d_pct=50.0, timing_deteccion_hoy="agotamiento",
        direction="ALCISTA", retroceso_desde_maximo_pct=None,
    ) == "NO_PERSEGUIR"


def test_demasiado_tarde_con_retroceso_real_da_no_perseguir():
    """"demasiado_tarde" CON algún retroceso desde el máximo de hoy (aunque
    sea chico, no necesita cruzar DRAWDOWN_FROM_PEAK_THRESHOLD_PCT) sigue
    dando NO_PERSEGUIR -- ya mostró que se retiró de su propio pico."""
    assert als.classify_alert_stage(
        relative_volume_hoy=50.0, dias_volumen_elevado=5, aceleracion_volumen=10.0,
        volatility_14d_pct=50.0, timing_deteccion_hoy="demasiado_tarde",
        direction="ALCISTA", retroceso_desde_maximo_pct=1.5,
    ) == "NO_PERSEGUIR"


def test_demasiado_tarde_caso_real_mstu_sin_retroceso_da_confirmacion():
    """Caso real MSTU (2026-08-21): detectado a +13%, subió sostenido y
    parejo hasta +19% SIN nunca retroceder desde su máximo de hoy -- quedó
    SIEMPRE en NO_PERSEGUIR porque "demasiado_tarde" se trataba igual que
    "agotamiento", aunque nunca hubo un retroceso real que lo respalde.
    `retroceso_desde_maximo_pct=None` (sigue en su máximo del día) ahora se
    trata igual que "recorrido_significativo_ya_hecho"."""
    assert als.classify_alert_stage(
        relative_volume_hoy=0.11, dias_volumen_elevado=0, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="demasiado_tarde",
        direction="ALCISTA", retroceso_desde_maximo_pct=None,
    ) == "CONFIRMACION"
    assert als.classify_alert_stage(
        relative_volume_hoy=0.11, dias_volumen_elevado=0, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="demasiado_tarde",
        direction="BAJISTA", retroceso_desde_maximo_pct=None,
    ) == "FLUJO_VENDEDOR"


def test_demasiado_tarde_sin_retroceso_ni_direccion_sigue_evaluando_volumen():
    """Sin dirección ALCISTA/BAJISTA confirmada, "demasiado_tarde" sin
    retroceso tampoco alcanza para CONFIRMACION/FLUJO_VENDEDOR -- sigue
    evaluando por volumen/volatilidad, mismo criterio que
    "recorrido_significativo_ya_hecho" ya usa."""
    assert als.classify_alert_stage(
        relative_volume_hoy=50.0, dias_volumen_elevado=5, aceleracion_volumen=10.0,
        volatility_14d_pct=50.0, timing_deteccion_hoy="demasiado_tarde",
    ) == "ALERTA_FUERTE"


def test_recorrido_significativo_da_confirmacion():
    # Fase 7 (2026-08-18): CONFIRMACION exige direccion ALCISTA confirmada
    # -- volumen/timing por si solos ya no alcanzan (ver SEZL, docstring
    # del modulo).
    assert als.classify_alert_stage(
        relative_volume_hoy=1.0, dias_volumen_elevado=0, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="recorrido_significativo_ya_hecho",
        direction="ALCISTA",
    ) == "CONFIRMACION"


def test_al_comienzo_da_inicio():
    assert als.classify_alert_stage(
        relative_volume_hoy=1.0, dias_volumen_elevado=0, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="al_comienzo",
        direction="ALCISTA",
    ) == "INICIO"


def test_recorrido_significativo_bajista_da_flujo_vendedor():
    assert als.classify_alert_stage(
        relative_volume_hoy=1.0, dias_volumen_elevado=0, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="recorrido_significativo_ya_hecho",
        direction="BAJISTA",
    ) == "FLUJO_VENDEDOR"


def test_al_comienzo_bajista_da_flujo_vendedor_no_inicio():
    assert als.classify_alert_stage(
        relative_volume_hoy=1.0, dias_volumen_elevado=0, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="al_comienzo",
        direction="BAJISTA",
    ) == "FLUJO_VENDEDOR"


def test_al_comienzo_sin_direccion_confirmada_nunca_da_inicio():
    """Sin ALCISTA/BAJISTA confirmado (None, NEUTRAL o INDEFINIDA), el
    timing solo no alcanza para anunciar una senal de compra -- sigue
    evaluando por volumen/volatilidad en vez de asumir INICIO."""
    for direction in (None, "NEUTRAL", "INDEFINIDA"):
        resultado = als.classify_alert_stage(
            relative_volume_hoy=0.5, dias_volumen_elevado=0, aceleracion_volumen=None,
            volatility_14d_pct=3.0, timing_deteccion_hoy="al_comienzo",
            direction=direction,
        )
        assert resultado is None, f"direction={direction!r} no deberia dar INICIO ni FLUJO_VENDEDOR (dio {resultado!r})"


def test_caso_real_sezl_rvol_alto_bajista_da_flujo_vendedor_no_alerta_temprana():
    """Caso real de la sesion 2026-08-17: SEZL detectada con RVOL 8.6x
    (ALERTA_TEMPRANA con la logica vieja), cerro el dia en -5.26%. Con la
    misma evidencia de volumen pero direccion BAJISTA ya confirmada
    (distinto del momento exacto de deteccion, donde change_pct=0.0 no era
    confiable -- ver test_phase_classifier.py), debe leerse como
    FLUJO_VENDEDOR, no como una alerta de sabor alcista."""
    assert als.classify_alert_stage(
        relative_volume_hoy=8.5789, dias_volumen_elevado=1, aceleracion_volumen=0.586,
        volatility_14d_pct=7.43, timing_deteccion_hoy="antes_del_movimiento",
        direction="BAJISTA",
    ) == "FLUJO_VENDEDOR"


def test_alerta_fuerte_bajista_da_flujo_vendedor():
    assert als.classify_alert_stage(
        relative_volume_hoy=3.0, dias_volumen_elevado=2, aceleracion_volumen=1.5,
        volatility_14d_pct=12.0, timing_deteccion_hoy="antes_del_movimiento",
        direction="BAJISTA",
    ) == "FLUJO_VENDEDOR"


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


# --- Retroceso desde máximo intradía (2026-08-18, caso real YYAI) ---

def test_retroceso_fuerte_gana_sobre_confirmacion():
    """Caso real YYAI (2026-08-18): pico $1,57, cayó a ~$1,36-1,38 --
    seguía +13% vs cierre de ayer (direction=ALCISTA, timing habría dado
    CONFIRMACION), pero retrocedió ~12-13% desde su propio máximo de hoy.
    Debe ganar NO_PERSEGUIR sin importar que el timing diga lo contrario."""
    assert als.classify_alert_stage(
        relative_volume_hoy=11.7, dias_volumen_elevado=1, aceleracion_volumen=1.0,
        volatility_14d_pct=64.6, timing_deteccion_hoy="recorrido_significativo_ya_hecho",
        direction="ALCISTA", retroceso_desde_maximo_pct=12.7,
    ) == "NO_PERSEGUIR"


def test_retroceso_fuerte_gana_sobre_inicio():
    assert als.classify_alert_stage(
        relative_volume_hoy=5.0, dias_volumen_elevado=1, aceleracion_volumen=1.0,
        volatility_14d_pct=20.0, timing_deteccion_hoy="al_comienzo",
        direction="ALCISTA", retroceso_desde_maximo_pct=15.0,
    ) == "NO_PERSEGUIR"


def test_retroceso_justo_en_el_umbral_dispara():
    assert als.classify_alert_stage(
        relative_volume_hoy=5.0, dias_volumen_elevado=0, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="al_comienzo",
        direction="ALCISTA", retroceso_desde_maximo_pct=als.DRAWDOWN_FROM_PEAK_THRESHOLD_PCT,
    ) == "NO_PERSEGUIR"


def test_retroceso_debajo_del_umbral_no_dispara_sigue_logica_normal():
    """Un retroceso chico (ruido normal de una microcap volátil) NO debe
    forzar NO_PERSEGUIR -- la clasificación sigue como si no existiera."""
    assert als.classify_alert_stage(
        relative_volume_hoy=5.0, dias_volumen_elevado=0, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="al_comienzo",
        direction="ALCISTA", retroceso_desde_maximo_pct=3.5,
    ) == "INICIO"


def test_retroceso_none_no_cambia_el_comportamiento_existente():
    """Compatibilidad hacia atrás: sin pasar el parámetro nuevo, el
    resultado es idéntico al de antes de este cambio."""
    assert als.classify_alert_stage(
        relative_volume_hoy=1.0, dias_volumen_elevado=0, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="al_comienzo", direction="ALCISTA",
    ) == "INICIO"
    assert als.classify_alert_stage(
        relative_volume_hoy=1.0, dias_volumen_elevado=0, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="al_comienzo", direction="ALCISTA",
        retroceso_desde_maximo_pct=None,
    ) == "INICIO"


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
