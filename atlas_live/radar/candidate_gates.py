"""Puertas de detección independientes del radar (2026-08-14).

Principio explícito de esta capa (pedido directamente): NO hay un único
filtro agresivo ni un único top-K. Cada puerta es independiente, evalúa un
tipo de comportamiento distinto, y basta con que UNA se dispare para que el
símbolo entre al radar como candidata -- unión, no intersección. El objetivo
es "el comienzo o la reactivación de un movimiento con recorrido todavía
disponible", no perseguir solo lo que ya subió +30/+50%.

Todos los umbrales son deliberadamente modestos/inclusivos y quedan
documentados acá mismo, configurables por entorno -- NO son un veredicto
final ("esto es una oportunidad"), son un piso para que el símbolo entre al
radar y se le empiece a hacer seguimiento. El ajuste fino queda para
después, con evidencia real acumulada (`candidate_outcome`).

Cada puerta usa SOLO datos que ya vienen en el Quote (precio, cambio %,
volumen, average_volume, relative_volume) + el historial de barridos del
propio día (`sweep_history.SweepSnapshot`) -- cero llamadas de red nuevas,
cero historial OHLCV (eso es Etapa B, solo para quien ya es candidata).
"""

import os
import statistics
from dataclasses import dataclass
from typing import List, Optional

from atlas_live.radar.sweep_history import SweepSnapshot


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# --- Umbrales, modestos e inclusivos a propósito (ver docstring) ---
MIN_CHANGE_PCT = _env_float("ATLAS_RADAR_GATE_MIN_CHANGE_PCT", 3.0)
MIN_RVOL = _env_float("ATLAS_RADAR_GATE_MIN_RVOL", 1.5)
MIN_DOLLAR_VOLUME = _env_float("ATLAS_RADAR_GATE_MIN_DOLLAR_VOLUME", 300_000.0)
ACCEL_LOOKBACK_SWEEPS = _env_int("ATLAS_RADAR_GATE_ACCEL_LOOKBACK", 4)
ACCEL_MIN_DELTA_PCT = _env_float("ATLAS_RADAR_GATE_ACCEL_MIN_DELTA_PCT", 2.0)
WAKEUP_BASELINE_RVOL_CEILING = _env_float("ATLAS_RADAR_GATE_WAKEUP_BASELINE_RVOL", 1.2)
WAKEUP_MIN_JUMP_RATIO = _env_float("ATLAS_RADAR_GATE_WAKEUP_JUMP_RATIO", 2.0)
RECOVERY_MIN_DROP_PCT = _env_float("ATLAS_RADAR_GATE_RECOVERY_MIN_DROP_PCT", 3.0)
RECOVERY_MIN_REBOUND_PCT = _env_float("ATLAS_RADAR_GATE_RECOVERY_MIN_REBOUND_PCT", 2.0)
BEHAVIOR_CHANGE_MIN_RATIO = _env_float("ATLAS_RADAR_GATE_BEHAVIOR_CHANGE_RATIO", 2.0)
MIN_HISTORY_FOR_COMPARATIVE_GATES = _env_int("ATLAS_RADAR_GATE_MIN_HISTORY", 3)


@dataclass(frozen=True)
class GateResult:
    fired: bool
    name: str
    reason: str
    value: Optional[float] = None


def gate_price_change(current: SweepSnapshot, history: List[SweepSnapshot], session: str) -> GateResult:
    """Cambio de precio -- la puerta más simple, cambio % absoluto vs. cierre anterior."""
    v = current.change_pct
    fired = v is not None and abs(v) >= MIN_CHANGE_PCT
    return GateResult(fired, "cambio_de_precio", f"|change_pct|={v} vs piso {MIN_CHANGE_PCT}%" if v is not None else "sin dato", v)


def gate_relative_volume(current: SweepSnapshot, history: List[SweepSnapshot], session: str) -> GateResult:
    """Volumen relativo -- actividad inusual aunque el precio todavía no se haya movido mucho."""
    v = current.relative_volume
    fired = v is not None and v >= MIN_RVOL
    return GateResult(fired, "volumen_relativo", f"RVOL={v} vs piso {MIN_RVOL}x" if v is not None else "sin dato", v)


def gate_dollar_volume(current: SweepSnapshot, history: List[SweepSnapshot], session: str) -> GateResult:
    """Dollar volume -- NO está en `ALL_GATES` (ver nota abajo). Se conserva la
    función y el dato (dollar_volume queda igual en cada detección/observación,
    solo como diagnóstico) para no perder la señal ni la posibilidad de
    rediseñarla más adelante como medida RELATIVA en vez de piso absoluto.

    Motivo de la exclusión, con evidencia real (validación 2026-08-14, barrido
    real de los 2.575 símbolos): el universo Racional ya está pre-filtrado a
    acciones líquidas de EE.UU. -- la mediana real de dollar_volume fue
    ~$36.000.000 y hasta un piso de $10.000.000 seguía sin filtrar el 67.7%
    del universo. Un piso absoluto de dollar_volume no discrimina "hay
    actividad inusual HOY" de "esta acción es grande" -- por eso se sacó de
    la unión de puertas (con este piso, 2.355/2.575 símbolos -- 91.5% --
    disparaban esta puerta sola, vaciando de sentido al radar). `relative_volume`
    sí discrimina (mediana real 0.69x) y ya cubre la intención original."""
    v = current.dollar_volume
    fired = v is not None and v >= MIN_DOLLAR_VOLUME
    return GateResult(fired, "dollar_volume", f"${v:,.0f}" if v is not None else "sin dato", v)


def _same_session(history: List[SweepSnapshot], session: str) -> List[SweepSnapshot]:
    """Prioridad 5 (Fase 6, 2026-08-18): las puertas comparativas nunca
    deben comparar un barrido de HOY contra un barrido de OTRA sesión de
    HOY (premarket vs. regular) -- hallazgo real de la sesión 2026-08-17:
    21 de 25 etiquetas INICIO se dispararon en la misma ventana de 5
    minutos, exactamente en el cambio de las 09:30 ET, con volumen en vivo
    casi nulo -- consistente con que `SweepHistory` guarda un único buffer
    por día (`sweep_history.py`) sin distinguir sesión, y las puertas
    comparaban el cambio % de la apertura regular contra el de un barrido
    de premarket. Ningún umbral cambia acá -- solo la ventana de
    comparación deja de cruzar la frontera de sesión."""
    return [h for h in history if h.session == session]


def gate_acceleration(current: SweepSnapshot, history: List[SweepSnapshot], session: str) -> GateResult:
    """Aceleración -- el cambio % subió bruscamente respecto a unos barridos atrás
    (no el mismo umbral de siempre: mide la VELOCIDAD del movimiento, no el nivel)."""
    history = _same_session(history, session)
    if len(history) < min(ACCEL_LOOKBACK_SWEEPS, MIN_HISTORY_FOR_COMPARATIVE_GATES) or current.change_pct is None:
        return GateResult(False, "aceleracion", "historial insuficiente")
    ref = history[max(0, len(history) - ACCEL_LOOKBACK_SWEEPS)]
    if ref.change_pct is None:
        return GateResult(False, "aceleracion", "referencia sin dato")
    delta = current.change_pct - ref.change_pct
    fired = delta >= ACCEL_MIN_DELTA_PCT
    return GateResult(fired, "aceleracion", f"delta {ACCEL_LOOKBACK_SWEEPS} barridos={delta:+.2f}pp vs piso {ACCEL_MIN_DELTA_PCT}pp", delta)


def gate_wakeup(current: SweepSnapshot, history: List[SweepSnapshot], session: str) -> GateResult:
    """Recién empieza a despertar -- estaba tranquilo (RVOL bajo) toda la
    ventana y ahora da un salto real."""
    history = _same_session(history, session)
    if len(history) < MIN_HISTORY_FOR_COMPARATIVE_GATES or current.relative_volume is None:
        return GateResult(False, "despertar", "historial insuficiente")
    prior_rvols = [h.relative_volume for h in history if h.relative_volume is not None]
    if not prior_rvols:
        return GateResult(False, "despertar", "sin RVOL previo")
    baseline_quiet = all(v < WAKEUP_BASELINE_RVOL_CEILING for v in prior_rvols)
    baseline_max = max(prior_rvols) or 0.0001
    jump_ratio = current.relative_volume / baseline_max if baseline_max else None
    fired = baseline_quiet and jump_ratio is not None and jump_ratio >= WAKEUP_MIN_JUMP_RATIO
    return GateResult(fired, "despertar",
                       f"quieto antes (max RVOL previo={baseline_max:.2f}), salto actual x{jump_ratio:.2f}" if jump_ratio else "sin salto",
                       jump_ratio)


def gate_recovery(current: SweepSnapshot, history: List[SweepSnapshot], session: str) -> GateResult:
    """Pierde fuerza y luego recupera -- hubo una caída local desde un pico
    y el cambio % actual ya está rebotando desde ese mínimo."""
    history = _same_session(history, session)
    if len(history) < MIN_HISTORY_FOR_COMPARATIVE_GATES or current.change_pct is None:
        return GateResult(False, "recuperacion", "historial insuficiente")
    pcts = [h.change_pct for h in history if h.change_pct is not None]
    if len(pcts) < MIN_HISTORY_FOR_COMPARATIVE_GATES:
        return GateResult(False, "recuperacion", "sin suficientes cambios % previos")
    peak = max(pcts)
    trough = min(pcts[pcts.index(peak):]) if peak in pcts else min(pcts)
    drop = peak - trough
    rebound = current.change_pct - trough
    fired = drop >= RECOVERY_MIN_DROP_PCT and rebound >= RECOVERY_MIN_REBOUND_PCT
    return GateResult(fired, "recuperacion", f"caída previa={drop:.2f}pp, rebote actual={rebound:.2f}pp", rebound)


def gate_sustained_premarket_climb(current: SweepSnapshot, history: List[SweepSnapshot], session: str) -> GateResult:
    """Viene subiendo desde el premarket -- cambio % positivo y sostenido a
    lo largo de la sesión de premarket (no un pico aislado)."""
    if session != "premarket":
        return GateResult(False, "sostenido_premarket", "no aplica fuera de premarket")
    if len(history) < MIN_HISTORY_FOR_COMPARATIVE_GATES or current.change_pct is None or current.change_pct <= 0:
        return GateResult(False, "sostenido_premarket", "historial insuficiente o sin alza actual")
    pcts = [h.change_pct for h in history if h.change_pct is not None] + [current.change_pct]
    positivos = sum(1 for p in pcts if p > 0)
    fired = positivos >= max(3, int(0.8 * len(pcts))) and current.change_pct > 0
    return GateResult(fired, "sostenido_premarket", f"{positivos}/{len(pcts)} barridos en positivo", positivos / len(pcts))


def gate_behavior_change(current: SweepSnapshot, history: List[SweepSnapshot], session: str) -> GateResult:
    """Cambio de comportamiento respecto a barridos anteriores -- comparación
    AUTO-relativa (no un piso fijo): el RVOL actual es varias veces la
    mediana de su propio historial de hoy. Complementa las puertas de piso
    fijo captando anomalías incluso en símbolos con rangos normales atípicos."""
    history = _same_session(history, session)
    if len(history) < MIN_HISTORY_FOR_COMPARATIVE_GATES or current.relative_volume is None:
        return GateResult(False, "cambio_de_comportamiento", "historial insuficiente")
    prior = [h.relative_volume for h in history if h.relative_volume is not None]
    if len(prior) < MIN_HISTORY_FOR_COMPARATIVE_GATES:
        return GateResult(False, "cambio_de_comportamiento", "sin suficiente RVOL previo")
    mediana = statistics.median(prior)
    if not mediana:
        return GateResult(False, "cambio_de_comportamiento", "mediana previa en cero")
    ratio = current.relative_volume / mediana
    fired = ratio >= BEHAVIOR_CHANGE_MIN_RATIO
    return GateResult(fired, "cambio_de_comportamiento", f"RVOL actual={current.relative_volume:.2f} vs mediana propia={mediana:.2f} (x{ratio:.2f})", ratio)


# `gate_dollar_volume` deliberadamente NO está acá -- ver docstring de la
# función para la evidencia real (validación 2026-08-14) de por qué un piso
# absoluto no discrimina en el universo Racional (91.5% del universo real lo
# disparaba solo). Las 7 puertas activas miden CAMBIO/RATIO, no nivel
# absoluto -- por diseño, ninguna tiene el mismo sesgo de tamaño.
ALL_GATES = [
    gate_price_change,
    gate_relative_volume,
    gate_acceleration,
    gate_wakeup,
    gate_recovery,
    gate_sustained_premarket_climb,
    gate_behavior_change,
]


def evaluate_all_gates(current: SweepSnapshot, history: List[SweepSnapshot], session: str) -> List[GateResult]:
    """Corre TODAS las puertas (todas se evalúan siempre, no hay short-circuit
    -- así el registro queda con la lista completa de qué disparó y qué no,
    trazable). Devuelve la lista completa; el llamador decide "candidata" si
    len([g for g in resultado if g.fired]) >= 1."""
    return [gate(current, history, session) for gate in ALL_GATES]


def any_gate_fired(results: List[GateResult]) -> bool:
    return any(g.fired for g in results)


def fired_gates(results: List[GateResult]) -> List[GateResult]:
    return [g for g in results if g.fired]
