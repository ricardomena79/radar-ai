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


# --- Fallback PM-RVOL (2026-09-11, corrección autorizada explícitamente) ---

def test_pmrvol_no_se_usa_si_legacy_esta_disponible_relative_volume_hoy():
    """Legacy disponible (aunque sea bajo, no None) -> comportamiento
    IDÉNTICO al de antes de este cambio, el fallback PM-RVOL ni se evalúa.
    `relative_volume_hoy=0.1` no cruza VOLUME_ELEVATED_THRESHOLD y
    `dias_volumen_elevado=0` tampoco -- sin el fallback esto daría PREPARACION
    (por volatilidad) o None; con PM-RVOL "VALID" presente, debe seguir
    dando exactamente lo mismo que si premarket_volume_acceleration no
    existiera, porque relative_volume_hoy no es None."""
    sin_pm = als.classify_alert_stage(
        relative_volume_hoy=0.1, dias_volumen_elevado=0, aceleracion_volumen=None,
        volatility_14d_pct=5.0, timing_deteccion_hoy="antes_del_movimiento",
    )
    con_pm = als.classify_alert_stage(
        relative_volume_hoy=0.1, dias_volumen_elevado=0, aceleracion_volumen=None,
        volatility_14d_pct=5.0, timing_deteccion_hoy="antes_del_movimiento",
        premarket_volume_acceleration=14.38, premarket_volume_percentile=88.84,
    )
    assert sin_pm == con_pm == None


def test_pmrvol_no_se_usa_si_legacy_esta_disponible_dias_volumen_elevado():
    """Mismo criterio que el test anterior, pero con `dias_volumen_elevado=0`
    (dato presente, no None) y `relative_volume_hoy=None` -- alcanza con que
    UNA de las dos señales legacy tenga dato real para que el fallback no
    se use."""
    sin_pm = als.classify_alert_stage(
        relative_volume_hoy=None, dias_volumen_elevado=0, aceleracion_volumen=None,
        volatility_14d_pct=5.0, timing_deteccion_hoy="antes_del_movimiento",
    )
    con_pm = als.classify_alert_stage(
        relative_volume_hoy=None, dias_volumen_elevado=0, aceleracion_volumen=None,
        volatility_14d_pct=5.0, timing_deteccion_hoy="antes_del_movimiento",
        premarket_volume_acceleration=14.38,
    )
    assert sin_pm == con_pm == None


def test_pmrvol_valido_con_legacy_none_avanza_a_alerta_temprana_caso_atec():
    """Caso real ATEC (2026-09-11): relative_volume_hoy/dias_volumen_elevado
    en None (RVOL de sesión completa inviable en premarket temprano),
    premarket_volume_acceleration=14.3778 VALID (>= VOLUME_ELEVATED_THRESHOLD),
    session="premarket" (sesión real del sweep). Sin el fallback esto daría
    PREPARACION solo si volatilidad>=10 (ATEC real: volatility=4.713, ni
    siquiera llega a PREPARACION -> None). Con el fallback debe dar
    ALERTA_TEMPRANA."""
    assert als.classify_alert_stage(
        relative_volume_hoy=None, dias_volumen_elevado=None, aceleracion_volumen=None,
        volatility_14d_pct=4.713, timing_deteccion_hoy="antes_del_movimiento",
        premarket_volume_acceleration=14.3778, session="premarket",
    ) == "ALERTA_TEMPRANA"


def test_pmrvol_percentile_solo_no_avanza_sin_aceleracion():
    """`premarket_volume_percentile` por sí solo NO alcanza para avanzar --
    su estado VALID en `candidate_gates.py` solo exige universo suficiente
    (`MIN_UNIVERSE_SIZE_FOR_PM_PERCENTILE`), no crecimiento real de
    volumen -- un percentil VALID puede ser perfectamente bajo (ej. 5).
    Convertirlo en disparador exigiría inventar un umbral de percentil que
    no existe hoy -- explícitamente prohibido. Solo
    `premarket_volume_acceleration` decide, porque su propia validez YA
    exige `MIN_SHARES_PRIOR_WINDOW` de crecimiento real."""
    assert als.classify_alert_stage(
        relative_volume_hoy=None, dias_volumen_elevado=None, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="antes_del_movimiento",
        premarket_volume_percentile=5.0,
    ) is None
    assert als.classify_alert_stage(
        relative_volume_hoy=None, dias_volumen_elevado=None, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="antes_del_movimiento",
        premarket_volume_percentile=88.84,
    ) is None


def test_pmrvol_percentile_acompana_aceleracion_sin_cambiar_el_resultado():
    """`premarket_volume_percentile` se recibe junto con
    `premarket_volume_acceleration` (mismo patrón que el caso real ATEC,
    que trae ambas señales VALID) sin alterar el resultado -- confirma que
    pasar el percentil nunca rompe ni cambia el camino que ya decide
    `premarket_volume_acceleration` por sí sola."""
    solo_aceleracion = als.classify_alert_stage(
        relative_volume_hoy=None, dias_volumen_elevado=None, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="antes_del_movimiento",
        premarket_volume_acceleration=14.3778, session="premarket",
    )
    con_percentile_tambien = als.classify_alert_stage(
        relative_volume_hoy=None, dias_volumen_elevado=None, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="antes_del_movimiento",
        premarket_volume_acceleration=14.3778, premarket_volume_percentile=88.84, session="premarket",
    )
    assert solo_aceleracion == con_percentile_tambien == "ALERTA_TEMPRANA"


def test_pmrvol_bajista_con_legacy_none_da_flujo_vendedor():
    """Misma regla de dirección que ya aplica a las señales legacy -- el
    fallback PM-RVOL no crea un camino nuevo de presentación, solo
    alimenta la MISMA condición ya existente."""
    assert als.classify_alert_stage(
        relative_volume_hoy=None, dias_volumen_elevado=None, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="antes_del_movimiento",
        premarket_volume_acceleration=14.3778, direction="BAJISTA", session="premarket",
    ) == "FLUJO_VENDEDOR"


def test_pmrvol_ausente_no_avanza_artificialmente():
    """legacy None Y premarket_volume_acceleration/percentile también None
    (INSUFFICIENT_VOLUME/NOT_PREMARKET/etc. en candidate_gates.py) -> el
    resultado es el mismo de siempre (None, sin alerta), nunca se inventa
    una etapa."""
    assert als.classify_alert_stage(
        relative_volume_hoy=None, dias_volumen_elevado=None, aceleracion_volumen=None,
        volatility_14d_pct=4.713, timing_deteccion_hoy="antes_del_movimiento",
        premarket_volume_acceleration=None, premarket_volume_percentile=None,
    ) is None


def test_pmrvol_nunca_sustituye_persistencia_de_alerta_fuerte():
    """PM-RVOL es una señal de UN solo día -- no debe poder alcanzar
    ALERTA_FUERTE (que exige `dias_elevados>=2`, persistencia entre días)
    aunque venga con volatilidad de régimen elevada y aceleración positiva
    simultáneas. Sin señales legacy de días, el techo es ALERTA_TEMPRANA."""
    assert als.classify_alert_stage(
        relative_volume_hoy=None, dias_volumen_elevado=None, aceleracion_volumen=5.0,
        volatility_14d_pct=50.0, timing_deteccion_hoy="antes_del_movimiento",
        premarket_volume_acceleration=14.3778, session="premarket",
    ) == "ALERTA_TEMPRANA"


def test_pmrvol_sin_ninguna_senal_valida_preserva_comportamiento_previo():
    """Sin legacy y sin PM-RVOL (ninguna señal en absoluto) -> mismo
    resultado exacto que ya daba la función antes de este cambio (None)."""
    assert als.classify_alert_stage(
        relative_volume_hoy=None, dias_volumen_elevado=None, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy=None,
        premarket_volume_acceleration=None, premarket_volume_percentile=None,
    ) is None


def test_pmrvol_parametros_por_defecto_no_rompen_llamadas_existentes():
    """Compatibilidad hacia atrás: llamar sin pasar los 2 parámetros nuevos
    da exactamente el mismo resultado que pasarlos explícitamente en None
    -- ningún caller existente necesita cambiar."""
    sin_kwargs_nuevos = als.classify_alert_stage(
        relative_volume_hoy=3.0, dias_volumen_elevado=2, aceleracion_volumen=1.5,
        volatility_14d_pct=12.0, timing_deteccion_hoy="antes_del_movimiento",
    )
    con_none_explicito = als.classify_alert_stage(
        relative_volume_hoy=3.0, dias_volumen_elevado=2, aceleracion_volumen=1.5,
        volatility_14d_pct=12.0, timing_deteccion_hoy="antes_del_movimiento",
        premarket_volume_acceleration=None, premarket_volume_percentile=None,
    )
    assert sin_kwargs_nuevos == con_none_explicito == "ALERTA_FUERTE"


# --- Corrección PM-RVOL en premarket (2026-09-11, casos A-G autorizados) ---

def test_pmrvol_bajo_el_piso_no_avanza_caso_real_pbr():
    """Caso A -- caso real PBR (2026-09-11, snapshot de producción):
    relative_volume_hoy no es None pero está por debajo del piso de
    informatividad en premarket (0.04 < CHANGE_PCT_MIN_RVOL_TO_TRUST_ZERO),
    y premarket_volume_acceleration=0.1044 es un cociente VALID pero por
    debajo de VOLUME_ELEVATED_THRESHOLD (2.0) -- de hecho indica
    DESACELERACIÓN (la ventana reciente negoció solo el 10% del ritmo de
    la anterior), no aceleración. NO debe avanzar a ALERTA_TEMPRANA."""
    assert als.classify_alert_stage(
        relative_volume_hoy=0.04, dias_volumen_elevado=None, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="antes_del_movimiento",
        premarket_volume_acceleration=0.1044, session="premarket",
    ) is None


def test_pmrvol_supera_el_piso_avanza_caso_real_labd():
    """Caso B -- caso real LABD (2026-09-11, snapshot de producción): mismo
    patrón que PBR (RVOL legacy no informativo, <0.05), pero
    premarket_volume_acceleration=3.5759 SÍ cruza VOLUME_ELEVATED_THRESHOLD
    (2.0) -- aceleración genuina. Debe avanzar a ALERTA_TEMPRANA."""
    assert als.classify_alert_stage(
        relative_volume_hoy=0.04, dias_volumen_elevado=None, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="antes_del_movimiento",
        premarket_volume_acceleration=3.5759, session="premarket",
    ) == "ALERTA_TEMPRANA"


def test_pmrvol_legacy_no_informativo_con_pm_accel_en_el_piso_avanza():
    """Caso C -- relative_volume_hoy=0.04 (< 0.05, no informativo en
    premarket) + premarket_volume_acceleration=2.0 (exactamente en el piso,
    inclusivo) -> avanza a ALERTA_TEMPRANA."""
    assert als.classify_alert_stage(
        relative_volume_hoy=0.04, dias_volumen_elevado=None, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="antes_del_movimiento",
        premarket_volume_acceleration=2.0, session="premarket",
    ) == "ALERTA_TEMPRANA"


def test_pmrvol_fuera_de_premarket_preserva_comportamiento_previo():
    """Caso D -- mismo relative_volume_hoy=0.04 y premarket_volume_acceleration
    por encima del piso, pero session="regular": el piso de informatividad
    de RVOL (0.05) NUNCA se aplica fuera de premarket, y el fallback
    PM-RVOL tampoco puede activarse -- comportamiento IDÉNTICO con o sin
    PM-RVOL presente."""
    con_pm = als.classify_alert_stage(
        relative_volume_hoy=0.04, dias_volumen_elevado=None, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="antes_del_movimiento",
        premarket_volume_acceleration=5.0, session="regular",
    )
    sin_pm = als.classify_alert_stage(
        relative_volume_hoy=0.04, dias_volumen_elevado=None, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="antes_del_movimiento",
        session="regular",
    )
    assert con_pm == sin_pm is None


def test_pmrvol_no_sustituye_legacy_informativo_aunque_pm_accel_alto():
    """Caso E -- relative_volume_hoy=1.0 (informativo, aunque no cruce el
    piso "elevado" de 2.0) + premarket + premarket_volume_acceleration=5.0
    (muy por encima del piso) -- el fallback NO debe activarse: el legacy
    SÍ trae dato real (>= 0.05), y el fallback nunca compite con datos
    legacy reales. Resultado idéntico con y sin PM-RVOL."""
    sin_pm = als.classify_alert_stage(
        relative_volume_hoy=1.0, dias_volumen_elevado=None, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="antes_del_movimiento",
        session="premarket",
    )
    con_pm = als.classify_alert_stage(
        relative_volume_hoy=1.0, dias_volumen_elevado=None, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="antes_del_movimiento",
        premarket_volume_acceleration=5.0, session="premarket",
    )
    assert sin_pm == con_pm is None


def test_pmrvol_bloqueado_por_dias_volumen_elevado_presente():
    """Caso F -- dias_volumen_elevado=0 (dato presente, no None) +
    relative_volume_hoy no informativo (0.04, premarket) +
    premarket_volume_acceleration=5.0 (>= piso) -- el fallback sigue
    bloqueado porque ya hay una señal legacy real (días), aunque no esté
    elevada."""
    assert als.classify_alert_stage(
        relative_volume_hoy=0.04, dias_volumen_elevado=0, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="antes_del_movimiento",
        premarket_volume_acceleration=5.0, session="premarket",
    ) is None


def test_pmrvol_percentile_alto_pero_aceleracion_baja_no_dispara():
    """Caso G -- premarket_volume_percentile alto (98.4, caso real UVXY)
    pero premarket_volume_acceleration por debajo del piso (1.968, caso
    real UVXY -- justo debajo de 2.0) -- el percentil nunca decide solo, y
    la aceleración no alcanza el piso: no debe disparar."""
    assert als.classify_alert_stage(
        relative_volume_hoy=None, dias_volumen_elevado=None, aceleracion_volumen=None,
        volatility_14d_pct=None, timing_deteccion_hoy="antes_del_movimiento",
        premarket_volume_acceleration=1.968, premarket_volume_percentile=98.4, session="premarket",
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
