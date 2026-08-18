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

from typing import Optional

ALERT_STAGES = ("PREPARACION", "ALERTA_TEMPRANA", "ALERTA_FUERTE", "INICIO", "CONFIRMACION", "NO_PERSEGUIR", "FLUJO_VENDEDOR")

# Umbrales derivados del estudio histórico (ver docstring del módulo) --
# NUNCA se recalculan acá, solo se aplican.
VOLATILITY_ELEVATED_THRESHOLD = 10.0   # volatility_14d_pct -- mediana categoría B (continúa) fue ~10.1-11.4
VOLUME_ELEVATED_THRESHOLD = 2.0        # relative_volume -- mismo umbral usado en el estudio de persistencia
DIAS_ELEVADOS_PARA_ALERTA_FUERTE = 2   # 2+ de los últimos 5 días -- separó B (35% con 2+) de A (7% con 2+)

_TARDIO_O_AGOTAMIENTO = ("demasiado_tarde", "agotamiento")


def classify_alert_stage(
    relative_volume_hoy: Optional[float],
    dias_volumen_elevado: Optional[int],
    aceleracion_volumen: Optional[float],
    volatility_14d_pct: Optional[float],
    timing_deteccion_hoy: Optional[str],
    direction: Optional[str] = None,
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

    Orden de evaluación (el primero que matchea gana):
    1. `timing_deteccion_hoy` ya avanzado (demasiado_tarde/agotamiento) -> NO_PERSEGUIR.
    2. `recorrido_significativo_ya_hecho` -> CONFIRMACION si `direction=="ALCISTA"`,
       FLUJO_VENDEDOR si `direction=="BAJISTA"` -- si no, sigue evaluando abajo.
    3. `al_comienzo` -> INICIO si `direction=="ALCISTA"`, FLUJO_VENDEDOR si
       `direction=="BAJISTA"` -- si no, sigue evaluando abajo.
    4. Volumen persistente (2+ días elevados) + volatilidad de régimen
       elevada + aceleración positiva -> ALERTA_FUERTE (FLUJO_VENDEDOR si
       `direction=="BAJISTA"`).
    5. Al menos 1 día con volumen elevado, o volumen elevado HOY ->
       ALERTA_TEMPRANA (FLUJO_VENDEDOR si `direction=="BAJISTA"`).
    6. Solo volatilidad de régimen elevada, sin nada de volumen todavía ->
       PREPARACION (direccionalmente neutral por diseño -- "hay actividad,
       observando").
    7. Nada de lo anterior -> `None` (sin alerta)."""
    if timing_deteccion_hoy in _TARDIO_O_AGOTAMIENTO:
        return "NO_PERSEGUIR"
    if timing_deteccion_hoy == "recorrido_significativo_ya_hecho":
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

    if dias_elevados >= DIAS_ELEVADOS_PARA_ALERTA_FUERTE and volatilidad_elevada and aceleracion_positiva:
        return "FLUJO_VENDEDOR" if direction == "BAJISTA" else "ALERTA_FUERTE"
    if dias_elevados >= 1 or volumen_hoy_elevado:
        return "FLUJO_VENDEDOR" if direction == "BAJISTA" else "ALERTA_TEMPRANA"
    if volatilidad_elevada:
        return "PREPARACION"
    return None
