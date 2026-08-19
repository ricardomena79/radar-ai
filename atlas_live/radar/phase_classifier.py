"""Clasificador de fase del movimiento (2026-08-15) -- reglas explícitas,
NO machine learning. Reutilizable desde el radar en vivo
(`candidate_tracker.py`) y desde el estudio histórico
(`atlas_live/reference/daily_reference.py`).

Tres dimensiones INDEPENDIENTES, nunca mezcladas en un solo enum (pedido
explícito del usuario):

  1. `timing_deteccion`: en qué punto del movimiento se detectó --
     antes_del_movimiento / al_comienzo / expansion_temprana /
     recorrido_significativo_ya_hecho / demasiado_tarde / agotamiento.
  2. `direction`: ALCISTA / BAJISTA / NEUTRAL / INDEFINIDA (ver
     `atlas_live.reference.daily_reference.classify_direction` -- misma
     función, no reimplementada).
  3. `comportamiento_post_apertura`: continua / colapsa / no_aplica --
     SOLO tiene sentido si la detección fue en premarket. Para datos
     históricos diarios (sin granularidad de sesión) siempre es
     `no_aplica`, honestamente: Tradier no da barras de premarket
     separadas en `/v1/markets/history`, así que esta dimensión NO es
     medible retroactivamente con velas diarias -- no se inventa.

Cada regla usa SOLO datos que ya existen (gates disparadas, percentil
histórico del propio símbolo, features diarias) -- ninguna se inventa sin
respaldo. `historical_percentile_90` viene de
`atlas_live.reference.reference_registry.percentile_change_pct(symbol, 0.9)`
-- si el símbolo no tiene historial de referencia todavía, el timing queda
`indeterminado`, nunca se adivina.
"""

from dataclasses import dataclass
from typing import List, Optional

from atlas_live.reference.daily_reference import classify_direction

# Piso mínimo para considerar que un movimiento "ya empezó" -- mismo criterio
# que `candidate_gates.MIN_CHANGE_PCT` (no un número nuevo inventado).
MOVEMENT_FLOOR_PCT = 3.0

# Confiabilidad de change_pct en vivo (Fase 7, 2026-08-18, hallazgo real de
# la sesión 2026-08-17): Tradier puede devolver change_percentage=0.0 en
# premarket muy ilíquido sin que signifique "sin movimiento" -- ZIM,
# detectado con change_pct=0.0 y relative_volume=0.0098 (casi sin
# operaciones), terminó moviéndose de verdad (+2.4%/+4.3% máximo el día).
# Un 0.0%/None con volumen real por debajo de este piso no es un dato
# confiable -- se trata como "no disponible", nunca como "neutral real".
CHANGE_PCT_MIN_RVOL_TO_TRUST_ZERO = 0.05


@dataclass
class PhaseTag:
    timing_deteccion: str
    direction: str
    comportamiento_post_apertura: str
    reason: str
    change_pct_confiable: bool = True


def _classify_timing(
    change_pct: Optional[float], percentile_90: Optional[float],
    accelerating: bool, near_trough_after_peak: bool,
) -> "tuple[str, str]":
    if change_pct is None:
        return "indeterminado", "sin change_pct"
    abs_change = abs(change_pct)

    if near_trough_after_peak:
        return "agotamiento", "veníamos de un pico y estamos cerca de un mínimo posterior (retroceso sostenido)"

    if percentile_90 is None:
        # Sin historial de referencia todavía para este símbolo -- se usa
        # SOLO el piso de movimiento (criterio más pobre, declarado como tal).
        if abs_change < MOVEMENT_FLOOR_PCT:
            return "antes_del_movimiento", "bajo el piso de movimiento, sin percentil histórico propio todavía"
        return "expansion_temprana", "sobre el piso de movimiento, sin percentil histórico propio para comparar"

    if abs_change < MOVEMENT_FLOOR_PCT:
        return "antes_del_movimiento", f"|{change_pct}%| bajo el piso de {MOVEMENT_FLOOR_PCT}%"

    if abs_change < percentile_90:
        if accelerating:
            return "al_comienzo", f"|{change_pct}%| moderado para este símbolo (percentil 90 histórico={percentile_90}%) y acelerando"
        return "expansion_temprana", f"|{change_pct}%| moderado para este símbolo (percentil 90 histórico={percentile_90}%)"

    # abs_change >= percentile_90 -- ya es un movimiento grande PARA ESTE símbolo
    if accelerating:
        return "recorrido_significativo_ya_hecho", f"|{change_pct}%| ya supera el percentil 90 histórico ({percentile_90}%) pero sigue acelerando"
    return "demasiado_tarde", f"|{change_pct}%| ya supera el percentil 90 histórico ({percentile_90}%) y no acelera"


def classify_post_open_behavior(session: str, observations_after_detection: List[dict]) -> str:
    """`observations_after_detection`: filas con `change_pct` de
    observaciones YA ocurridas después de la apertura regular. Solo
    aplicable si la detección fue en premarket."""
    if session != "premarket" or not observations_after_detection:
        return "no_aplica"
    changes = [o.get("change_pct") for o in observations_after_detection if o.get("change_pct") is not None]
    if not changes:
        return "no_aplica"
    return "continua" if changes[-1] >= changes[0] else "colapsa"


def from_live_detection(
    change_pct_at_detection: Optional[float], gates_fired_names: List[str],
    historical_percentile_90: Optional[float], session: str,
    observations_after_open: Optional[List[dict]] = None,
    relative_volume: Optional[float] = None,
    price_basis: Optional[str] = None,
) -> PhaseTag:
    """Adaptador para una candidata del radar en vivo (CAPA 2).

    `relative_volume` (opcional, del mismo Quote de este barrido) permite
    distinguir un 0.0%/None real de un dato no confiable por falta de
    operaciones -- ver `CHANGE_PCT_MIN_RVOL_TO_TRUST_ZERO`. Si no se pasa,
    el comportamiento es el de siempre (se confía en `change_pct_at_detection`
    tal cual, compatibilidad hacia atrás).

    `price_basis` (2026-08-19, caso real KEN -- ver DECISIONES.md): cuando
    el precio viene de `"tradier_bid_ask_mid"` (punto medio entre bid/ask,
    usado por Tradier cuando no hay operación reciente real) Y casi no hay
    volumen real, el `change_pct` resultante puede NO ser 0.0 -- es
    aritmética sobre el spread, no un movimiento de mercado -- y aun así no
    está respaldado por ninguna operación real. El chequeo de arriba
    (`== 0.0`) no cubre este caso porque el número que sale casi nunca es
    exactamente cero."""
    accelerating = "aceleracion" in gates_fired_names or "despertar" in gates_fired_names
    near_trough = "recuperacion" in gates_fired_names

    # `relative_volume=None` (no pasado) preserva el comportamiento de
    # siempre -- solo se marca "no confiable" cuando SÍ hay evidencia de
    # volumen real y esa evidencia dice que casi no hubo operaciones.
    change_pct_confiable = True
    if change_pct_at_detection is None:
        change_pct_confiable = False  # dato faltante -- nunca es "0% real"
    elif price_basis == "tradier_bid_ask_mid" and (relative_volume is None or relative_volume < CHANGE_PCT_MIN_RVOL_TO_TRUST_ZERO):
        change_pct_confiable = False  # precio sintético (mid bid/ask) sin operaciones reales que lo respalden
    elif change_pct_at_detection == 0.0 and relative_volume is not None and relative_volume < CHANGE_PCT_MIN_RVOL_TO_TRUST_ZERO:
        change_pct_confiable = False  # 0.0% sin volumen real que lo respalde -- no se asume "neutral"

    effective_change_pct = change_pct_at_detection if change_pct_confiable else None
    timing, reason = _classify_timing(effective_change_pct, historical_percentile_90, accelerating, near_trough)
    direction = classify_direction(effective_change_pct)
    if not change_pct_confiable:
        reason = f"{reason} -- change_pct no confiable (RVOL={relative_volume}, posible dato de premarket ilíquido)"

    return PhaseTag(
        timing_deteccion=timing,
        direction=direction,
        comportamiento_post_apertura=classify_post_open_behavior(session, observations_after_open or []),
        reason=reason,
        change_pct_confiable=change_pct_confiable,
    )


def from_historical_day(
    change_pct: Optional[float], historical_percentile_90: Optional[float],
    drop_from_peak_10d_pct: Optional[float], rebound_from_trough_pct: Optional[float],
    peak_gain_10d_pct: Optional[float] = None,
) -> PhaseTag:
    """Adaptador para un día del estudio histórico (velas diarias). Sin
    granularidad de sesión -- `comportamiento_post_apertura` siempre
    `no_aplica` para datos diarios (limitación real de Tradier, declarada,
    no inventada).

    REVISIÓN 2026-08-15 (hallazgo del usuario tras la primera corrida real:
    "agotamiento" salía con una tasa de +20% posterior sorprendentemente
    alta -- señal de que la regla original mezclaba agotamiento real con
    simple drift lateral). La regla original solo pedía "bajó >=3pp desde un
    pico de 10 días y no rebotó" -- eso es cierto para CUALQUIER caída leve,
    incluso sin que haya existido nunca una subida real que "agotar". Ahora
    se exige además que ese pico haya venido de un `peak_gain_10d_pct`
    (subida real dentro de la misma ventana) de al menos `MOVEMENT_FLOOR_PCT`
    -- si no hubo una subida real que agotar, no es agotamiento, es
    simplemente una caída o un drift, y se clasifica por las reglas
    normales de abajo. Mantener como HIPÓTESIS a reforzar con más evidencia
    (pedido explícito del usuario), no como parámetro definitivo."""
    near_trough = (
        drop_from_peak_10d_pct is not None and drop_from_peak_10d_pct <= -3.0
        and (rebound_from_trough_pct is None or rebound_from_trough_pct < 2.0)
        and peak_gain_10d_pct is not None and peak_gain_10d_pct >= MOVEMENT_FLOOR_PCT
    )
    accelerating = rebound_from_trough_pct is not None and rebound_from_trough_pct >= 3.0
    timing, reason = _classify_timing(change_pct, historical_percentile_90, accelerating, near_trough)
    return PhaseTag(
        timing_deteccion=timing,
        direction=classify_direction(change_pct),
        comportamiento_post_apertura="no_aplica",
        reason=reason,
    )
