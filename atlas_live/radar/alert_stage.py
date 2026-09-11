"""Capa observacional de ALERTA TEMPRANA (2026-08-17, Fase 4).

Clasifica cada candidata en vivo en una de 6 ventanas -- PREPARACION,
ALERTA_TEMPRANA, ALERTA_FUERTE, INICIO, CONFIRMACION, NO_PERSEGUIR --
basadas en la evidencia real del estudio histórico sobre el universo de
mercado completo (`ALERTA_TEMPRANA_ANALISIS_Y_PROPUESTA.md` y
`PROPUESTA_FINAL_ALGORITMO_ALERTA_TEMPRANA.md`, 2026-08-17): la mediana de
`volatility_14d_pct` en la categoría de movimientos que continúan más allá
de +20% es ~50-65% más alta que en los que se quedan cortos, y la
persistencia de volumen elevado (2+ de los últimos 5 días con
`relative_volume >= 2.0`) separa mejor que un solo pico de volumen.

Los umbrales de acá (`VOLATILITY_ELEVATED_THRESHOLD`, `VOLUME_ELEVATED_THRESHOLD`,
`DIAS_ELEVADOS_PARA_ALERTA_FUERTE`) son los mismos números encontrados en
ese estudio, expuestos como constantes -- no son nuevos ni inventados acá,
y quedan documentados para poder ajustarse cuando haya más evidencia
(ver PROPUESTA_FINAL_ALGORITMO_ALERTA_TEMPRANA.md, sección "Qué falta").

PURAMENTE OBSERVACIONAL: esta clasificación NUNCA se importa desde
`candidate_gates.py`, nunca modifica `evaluate_all_gates()`, el score en
vivo ni `atlas/engine/decision_engine.py`. No bloquea ni prioriza
candidatas -- solo se registra para poder medir su resultado real después
(ver `candidate_registry.record_alert_stage`).
"""

import os
from typing import Optional

from atlas_live.radar import phase_classifier as pc

ALERT_STAGES = ("PREPARACION", "ALERTA_TEMPRANA", "ALERTA_FUERTE", "INICIO", "CONFIRMACION", "NO_PERSEGUIR", "FLUJO_VENDEDOR")

# Umbrales derivados del estudio histórico (ver docstring del módulo) --
# NUNCA se recalculan acá, solo se aplican.
VOLATILITY_ELEVATED_THRESHOLD = 10.0   # volatility_14d_pct -- mediana categoría B (continúa) fue ~10.1-11.4
VOLUME_ELEVATED_THRESHOLD = 2.0        # relative_volume -- mismo umbral usado en el estudio de persistencia
DIAS_ELEVADOS_PARA_ALERTA_FUERTE = 2   # 2+ de los últimos 5 días -- separó B (35% con 2+) de A (7% con 2+)

# Retroceso desde máximo intradía (2026-08-18, pedido explícito del usuario
# -- caso real YYAI: pico $1,57, luego $1,36 -- seguía +13% vs cierre de
# ayer, pero cayendo fuerte desde su propio máximo del día, y Atlas la
# seguía mostrando como oportunidad porque `direction` solo compara contra
# el cierre de AYER, nunca contra el máximo de HOY). A diferencia de
# `VOLATILITY_ELEVATED_THRESHOLD`/`VOLUME_ELEVATED_THRESHOLD` (que salen de
# un estudio histórico con n grande, ver docstring del módulo), este valor
# es un punto de partida razonado, no un backtest con n grande todavía --
# lo suficientemente por encima del ruido normal de una microcap volátil
# (2-5% intradía) como para no disparar por variaciones normales, pero
# comparable al retroceso real medido en YYAI (~12-13% desde el pico).
# Configurable, para poder ajustarlo con más evidencia sin tocar código.
DRAWDOWN_FROM_PEAK_THRESHOLD_PCT = float(os.environ.get("ATLAS_DRAWDOWN_FROM_PEAK_THRESHOLD_PCT", 8.0))


def classify_alert_stage(
    relative_volume_hoy: Optional[float],
    dias_volumen_elevado: Optional[int],
    aceleracion_volumen: Optional[float],
    volatility_14d_pct: Optional[float],
    timing_deteccion_hoy: Optional[str],
    direction: Optional[str] = None,
    retroceso_desde_maximo_pct: Optional[float] = None,
    premarket_volume_acceleration: Optional[float] = None,
    premarket_volume_percentile: Optional[float] = None,
    session: Optional[str] = None,
) -> Optional[str]:
    """Devuelve una de `ALERT_STAGES`, o `None` si la candidata no cumple
    ninguna condición de alerta (no se registra nada en ese caso -- nunca
    se inventa una etapa sin evidencia de respaldo).

    `direction` (Fase 7, 2026-08-18, `ALCISTA`/`BAJISTA`/`NEUTRAL`/
    `INDEFINIDA`/`None` -- ver `phase_classifier.from_live_detection`):
    el volumen/la volatilidad detectan MOVIMIENTO, no DIRECCIÓN -- caso
    real, SEZL (RVOL 8,6x, `ALERTA_TEMPRANA`, cerró el día -5,26%). Con la
    MISMA evidencia que antes (ningún umbral cambia), si `direction` es
    `BAJISTA` la etapa se reporta como `FLUJO_VENDEDOR` en vez de una
    etiqueta que suena alcista. `CONFIRMACION`/`INICIO` además EXIGEN
    `direction == "ALCISTA"` explícito -- sin dirección confirmada
    (`None`/`NEUTRAL`/`INDEFINIDA`), no alcanza el timing solo para
    anunciar una señal de compra; se sigue evaluando por volumen/volatilidad.

    `retroceso_desde_maximo_pct` (2026-08-18, caso real YYAI): % de caída
    desde el precio máximo alcanzado HOY por este símbolo (ver
    `candidate_registry.max_price_today`, calculado sobre observaciones
    persistidas -- nunca memoria efímera). Es una señal DISTINTA de
    `timing_deteccion_hoy` -- el timing compara contra el cierre de AYER;
    esto compara contra el propio máximo de HOY, así que detecta una
    reversión real aunque el símbolo siga positivo en el día.

    "demasiado_tarde" vs "agotamiento" ante un retroceso nulo (2026-08-21,
    caso real MSTU: +13% al detectarlo, subió sostenido y parejo hasta
    +19% sin nunca retroceder, y quedó SIEMPRE en NO_PERSEGUIR): ambos
    valores de `timing_deteccion_hoy` decían "ya se movió mucho", pero
    "agotamiento" YA implica venir de un pico con un retroceso posterior
    (`phase_classifier.near_trough_after_peak`, vía la puerta
    `recuperacion`) -- "demasiado_tarde" NO, solo dice que el cambio % ya
    superó el percentil 90 histórico del símbolo sin estar acelerando en
    ESE barrido puntual (la aceleración se mide en saltos de ~20 min, no
    detecta una subida sostenida y pareja). Por eso "demasiado_tarde"
    SOLO fuerza NO_PERSEGUIR si además hubo algún retroceso real desde el
    máximo de hoy (`retroceso_desde_maximo_pct is not None`) -- si el
    símbolo sigue EN su máximo del día (retroceso `None`), se trata igual
    que `recorrido_significativo_ya_hecho` (mismo criterio ya existente,
    nunca un umbral nuevo). "agotamiento" nunca cambia -- ya implica ese
    retroceso por construcción.

    `premarket_volume_acceleration`/`premarket_volume_percentile`
    (2026-09-11, corrección PM-RVOL autorizada explícitamente -- caso real
    ATEC: cambio fuerte y sostenido, `premarket_volume_acceleration=14.38`
    `VALID` en `candidate_gates.py`, pero `relative_volume_hoy`/
    `dias_volumen_elevado` en `None` porque el RVOL de sesión completa es
    matemáticamente inviable en los primeros minutos de premarket -- ver
    `candidate_gates.py`, docstring "PM-RVOL Fase 1"): SOLO cuando NINGUNA
    señal legacy de volumen (ni `relative_volume_hoy` ni
    `dias_volumen_elevado`) está disponible, `premarket_volume_acceleration`
    puede, por sí sola, satisfacer la MISMA condición que hoy satisface
    `volumen_hoy_elevado`/`dias_elevados>=1` en el nivel `ALERTA_TEMPRANA`
    -- SI su valor no es `None` (por contrato de
    `candidate_gates.PremarketVolumeSignal`: `value=None` siempre implica
    `validation_state != "VALID"`, así que "no es `None`" YA ES la regla de
    validez que usa `candidate_gates.py`, no una nueva). Se usa
    específicamente `premarket_volume_acceleration` y NO
    `premarket_volume_percentile` para decidir solo: la validez de
    `premarket_volume_acceleration` en `candidate_gates.py` YA exige
    crecimiento real de acciones negociadas (`MIN_SHARES_PRIOR_WINDOW`),
    mientras que la validez de `premarket_volume_percentile` solo exige
    universo suficiente (`MIN_UNIVERSE_SIZE_FOR_PM_PERCENTILE`) -- un
    percentil "VALID" puede perfectamente ser bajo (símbolo con poco
    volumen relativo al resto del mercado), así que su sola validez NO
    implica "elevado"; convertirlo en un disparador exigiría inventar un
    umbral de percentil que no existe hoy en ningún lado del código
    (prohibido explícitamente). `premarket_volume_percentile` se recibe
    igual como parámetro -- queda disponible para trazabilidad/uso futuro
    con un umbral propio, cuando exista uno real -- pero por ahora no
    decide nada por sí solo. Nunca sustituye la condición de persistencia
    de `ALERTA_FUERTE` (`dias_elevados>=2`) -- estas señales son de UN
    solo día, no miden persistencia entre días. Si alguna señal legacy SÍ
    está disponible (aunque no alcance el umbral), el comportamiento es
    IDÉNTICO al de antes de este cambio -- el fallback nunca compite con
    datos legacy reales, solo cubre su ausencia total.

    Corrección PM-RVOL en premarket (2026-09-11, autorizada explícitamente
    tras 2 hallazgos con evidencia real de producción):

    1. `relative_volume_hoy is not None` casi nunca ocurre en la práctica
       -- Tradier calcula `volume/average_volume` con el promedio de la
       SESIÓN REGULAR completa, así que en premarket temprano da un número
       casi siempre positivo pero estructuralmente ínfimo (caso real PBR:
       `relative_volume_hoy` nunca fue `None`, pero tampoco informativo).
       Por eso, además de `is None`, ahora también se considera "legacy no
       informativo" cuando `session == "premarket"` Y
       `relative_volume_hoy < phase_classifier.CHANGE_PCT_MIN_RVOL_TO_TRUST_ZERO`
       (0.05) -- el MISMO piso que `phase_classifier.py` ya usa para el
       mismo problema (RVOL demasiado bajo para ser evidencia real), no un
       umbral nuevo. Fuera de premarket este piso NUNCA se aplica --
       `relative_volume_hoy is not None` sigue bastando para considerar el
       legacy disponible, exactamente como antes de este cambio.

    2. `premarket_volume_acceleration` "VALID" (no `None`) NO implica
       aceleración alcista -- es un cociente (`vol_reciente/vol_previo`,
       ver `candidate_gates.premarket_volume_acceleration`) que puede ser
       perfectamente una DESACELERACIÓN (caso real PBR: `pm_accel=0.1044`,
       la ventana reciente negoció solo el 10% del ritmo de la ventana
       anterior). Por eso ahora, además de `is not None`, se exige
       `premarket_volume_acceleration >= VOLUME_ELEVATED_THRESHOLD` (el
       MISMO piso de "elevado" que ya usa `relative_volume_hoy` dos líneas
       más abajo en esta misma función, no un umbral nuevo) -- caso real
       LABD: `pm_accel=3.5759`, sí cruza el piso, sí es aceleración
       genuina.

    3. El fallback completo (incluida esta corrección) SOLO puede activarse
       cuando `session == "premarket"` es la sesión REAL del sweep, recibida
       tal cual por este parámetro -- esta función nunca la recalcula ni la
       lee de ningún dato persistido; es responsabilidad exclusiva del
       llamador (`candidate_tracker._tag_alert_stage()`, que a su vez la
       recibe de `process_sweep()`, que a su vez la recibe de
       `radar_worker.py::market_hours.get_session()` en cada sweep). Fuera
       de premarket (`session in ("regular", None, ...)`), el
       comportamiento es EXACTAMENTE igual al anterior a esta corrección.

    Orden de evaluación (el primero que matchea gana):
    1. Retroceso fuerte desde el máximo de hoy
       (>= `DRAWDOWN_FROM_PEAK_THRESHOLD_PCT`) -> NO_PERSEGUIR, sin importar
       qué diga el resto -- si ya está revirtiendo desde su propio pico, no
       hay timing ni volumen que lo compense.
    2. `agotamiento` -> NO_PERSEGUIR, siempre (ya implica un retroceso real).
    3. `demasiado_tarde` CON algún retroceso desde el máximo de hoy
       (`retroceso_desde_maximo_pct is not None`) -> NO_PERSEGUIR.
    4. `recorrido_significativo_ya_hecho`, o `demasiado_tarde` SIN
       retroceso (sigue en su máximo de hoy) -> CONFIRMACION si
       `direction=="ALCISTA"`, FLUJO_VENDEDOR si `direction=="BAJISTA"` --
       si no, sigue evaluando abajo.
    5. `al_comienzo` -> INICIO si `direction=="ALCISTA"`, FLUJO_VENDEDOR si
       `direction=="BAJISTA"` -- si no, sigue evaluando abajo.
    6. Volumen persistente (2+ días elevados) + volatilidad de régimen
       elevada + aceleración positiva -> ALERTA_FUERTE (FLUJO_VENDEDOR si
       `direction=="BAJISTA"`).
    7. Al menos 1 día con volumen elevado, o volumen elevado HOY ->
       ALERTA_TEMPRANA (FLUJO_VENDEDOR si `direction=="BAJISTA"`).
    8. Solo volatilidad de régimen elevada, sin nada de volumen todavía ->
       PREPARACION (direccionalmente neutral por diseño -- "hay actividad,
       observando").
    9. Nada de lo anterior -> `None` (sin alerta)."""
    if retroceso_desde_maximo_pct is not None and retroceso_desde_maximo_pct >= DRAWDOWN_FROM_PEAK_THRESHOLD_PCT:
        return "NO_PERSEGUIR"
    if timing_deteccion_hoy == "agotamiento":
        return "NO_PERSEGUIR"
    if timing_deteccion_hoy == "demasiado_tarde" and retroceso_desde_maximo_pct is not None:
        return "NO_PERSEGUIR"
    if timing_deteccion_hoy in ("recorrido_significativo_ya_hecho", "demasiado_tarde"):
        if direction == "ALCISTA":
            return "CONFIRMACION"
        if direction == "BAJISTA":
            return "FLUJO_VENDEDOR"
    if timing_deteccion_hoy == "al_comienzo":
        if direction == "ALCISTA":
            return "INICIO"
        if direction == "BAJISTA":
            return "FLUJO_VENDEDOR"

    volatilidad_elevada = volatility_14d_pct is not None and volatility_14d_pct >= VOLATILITY_ELEVATED_THRESHOLD
    dias_elevados = dias_volumen_elevado or 0
    volumen_hoy_elevado = relative_volume_hoy is not None and relative_volume_hoy >= VOLUME_ELEVATED_THRESHOLD
    aceleracion_positiva = aceleracion_volumen is not None and aceleracion_volumen > 0

    # Fallback PM-RVOL (2026-09-11, corregido el mismo día -- ver docstring
    # "Corrección PM-RVOL en premarket"). Solo entra en juego cuando
    # `session == "premarket"` (sesión REAL del sweep, nunca recalculada
    # acá) Y el RVOL legacy no es informativo (ausente, o presente pero por
    # debajo de `phase_classifier.CHANGE_PCT_MIN_RVOL_TO_TRUST_ZERO` --
    # solo en premarket) Y `dias_volumen_elevado` tampoco trae dato; nunca
    # pisa ni compite con ninguna de las dos señales legacy cuando SÍ traen
    # dato real (aunque sea 0 o bajo). Decide SOLO `premarket_volume_acceleration`,
    # y solo si alcanza el mismo piso de "elevado" que ya usa
    # `relative_volume_hoy` (`VOLUME_ELEVATED_THRESHOLD`) --
    # `premarket_volume_percentile` se recibe pero no participa de esta
    # condición (ver docstring: su validez no implica "elevado", solo
    # "universo suficiente").
    legacy_rvol_informativo = relative_volume_hoy is not None
    if (
        session == "premarket"
        and legacy_rvol_informativo
        and relative_volume_hoy < pc.CHANGE_PCT_MIN_RVOL_TO_TRUST_ZERO
    ):
        legacy_rvol_informativo = False
    legacy_volumen_disponible = legacy_rvol_informativo or dias_volumen_elevado is not None
    pm_rvol_valido = (
        session == "premarket"
        and not legacy_volumen_disponible
        and premarket_volume_acceleration is not None
        and premarket_volume_acceleration >= VOLUME_ELEVATED_THRESHOLD
    )
    # premarket_volume_percentile: recibido, documentado, no decide solo (ver docstring).

    if dias_elevados >= DIAS_ELEVADOS_PARA_ALERTA_FUERTE and volatilidad_elevada and aceleracion_positiva:
        return "FLUJO_VENDEDOR" if direction == "BAJISTA" else "ALERTA_FUERTE"
    if dias_elevados >= 1 or volumen_hoy_elevado or pm_rvol_valido:
        return "FLUJO_VENDEDOR" if direction == "BAJISTA" else "ALERTA_TEMPRANA"
    if volatilidad_elevada:
        return "PREPARACION"
    return None
