"""Event Status vs Trading Status (2026-08-24, Segunda Fase del Motor de
Catalizadores, punto 7 del pedido del usuario -- separación explícita,
"MUY importante").

**Event Status**: SOLO de cuándo es el evento -- capa de presentación
sobre `dias_al_evento`/`lifecycle_state`, que YA calcula
`catalyst_classifier.classify_catalyst_lifecycle()` (no se toca esa
función, `days_to_event()` acá abajo replica la MISMA fórmula exacta
-- `(date.fromisoformat(event_date) - now.date()).days` -- solo para
poder exponerla sin modificar la firma/lógica de la función original).

**Trading Status**: de si HOY hay evidencia técnica real para actuar.
Nunca se inventa evidencia que no existe -- dos caminos, según si el
ticker es también una candidata técnica detectada por el radar hoy:
  - Sí -> se reutiliza `atlas_live/radar/priority_classifier.classify_final_priority()`
    TAL CUAL, sin modificar (ya da OPORTUNIDAD_PRIORITARIA/VIGILAR/
    PREPARACION/NO_TOCAR con evidencia real de gates).
  - No -> `catalyst_trading_status()` (acá), vocabulario deliberadamente
    DISTINTO (PREPARAR/VIGILAR/CALENDARIO) para nunca aparentar la misma
    certeza que una detección técnica real -- basado solo en lo que sí
    existe para ese caso: proximidad del evento + catalyst_opportunity_score
    + RVOL/gap si el barrido de Tradier los trae."""

from datetime import date
from typing import Optional

EVENT_STATUS_STATES = ("HOY", "MANANA", "DOS_A_TRES_DIAS", "CUATRO_A_SIETE_DIAS", "FUTURO", "OCURRIDO", "EXTENDIDA")

TRADING_STATUS_STATES = ("PREPARAR", "VIGILAR", "CALENDARIO")

# Pisos reutilizados de lo que ya existe en el proyecto (nunca inventados
# acá): mismo umbral de RVOL que `alert_stage.VOLUME_ELEVATED_THRESHOLD`
# (2.0) y de movimiento que `phase_classifier.MOVEMENT_FLOOR_PCT` (3.0).
RVOL_PREPARAR_THRESHOLD = 2.0
GAP_PREPARAR_THRESHOLD_PCT = 3.0
OPPORTUNITY_SCORE_PREPARAR_THRESHOLD = 60.0
DIAS_PREPARAR_MAX = 1  # hoy o mañana


def days_to_event(event_date: Optional[str], now) -> Optional[int]:
    """Misma fórmula EXACTA que `classify_catalyst_lifecycle()` usa
    internamente -- se replica acá (no se importa ese cálculo interno)
    para no tocar la función original ni su firma."""
    if not event_date:
        return None
    try:
        return (date.fromisoformat(event_date) - now.date()).days
    except (ValueError, TypeError):
        return None


def event_status(dias_al_evento: Optional[int], lifecycle_state: str) -> str:
    """Una de `EVENT_STATUS_STATES`. `lifecycle_state` (ya calculado por
    `classify_catalyst_lifecycle()`) tiene prioridad para OCURRIDO/EXTENDIDA
    -- esos dos no dependen de `dias_al_evento` (evento sin fecha propia,
    ej. una noticia). El resto se deriva de `dias_al_evento`."""
    if lifecycle_state == "EXTENDIDA":
        return "EXTENDIDA"
    if lifecycle_state == "OCURRIDO":
        return "OCURRIDO"
    if dias_al_evento is None:
        return "FUTURO"
    if dias_al_evento <= 0:
        return "HOY"
    if dias_al_evento == 1:
        return "MANANA"
    if dias_al_evento <= 3:
        return "DOS_A_TRES_DIAS"
    if dias_al_evento <= 7:
        return "CUATRO_A_SIETE_DIAS"
    return "FUTURO"


def catalyst_trading_status(
    opportunity_score: float,
    dias_al_evento: Optional[int],
    relative_volume: Optional[float],
    gap_pct: Optional[float],
) -> str:
    """SOLO para catalizadores SIN detección técnica de hoy (`technical.disponible=False`
    en `catalyst_market_join.join_market_data()`) -- si el ticker SÍ tiene
    detección técnica hoy, usar `priority_classifier.classify_final_priority()`
    en su lugar, nunca esta función."""
    evento_imminente = dias_al_evento is not None and dias_al_evento <= DIAS_PREPARAR_MAX
    hay_senal_temprana = (
        (relative_volume is not None and relative_volume >= RVOL_PREPARAR_THRESHOLD)
        or (gap_pct is not None and abs(gap_pct) >= GAP_PREPARAR_THRESHOLD_PCT)
    )
    if evento_imminente and (hay_senal_temprana or opportunity_score >= OPPORTUNITY_SCORE_PREPARAR_THRESHOLD):
        return "PREPARAR"
    if evento_imminente or opportunity_score >= OPPORTUNITY_SCORE_PREPARAR_THRESHOLD:
        return "VIGILAR"
    return "CALENDARIO"
