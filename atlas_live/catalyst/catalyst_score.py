"""CATALYST_SCORE y MRNA_SIMILARITY_SCORE (2026-08-23, Fases 5/6 del Motor
de Catalizadores) -- funciones puras, sin DB/red, formulas explicitas y
documentadas (NUNCA lenguaje vago de IA, pedido explicito del usuario).

`mrna_similarity_score()` mide SIMILITUD ESTRUCTURAL contra el vector real
de MRNA (`mrna_pattern.MRNA_PATTERN`) -- aclaracion explicita del usuario:
NUNCA es "probabilidad de subir", es "que tan parecida es la configuracion
actual a la que produjo el movimiento real de MRNA"."""

from typing import Optional

from atlas_live.catalyst.mrna_pattern import (
    MRNA_PATTERN,
    PARTIAL_TRANSFORMATIONAL_TYPES,
    TRANSFORMATIONAL_TYPES,
)

# ---------------------------------------------------------------------------
# CATALYST_SCORE (0-100)
# ---------------------------------------------------------------------------

_IMPORTANCE_SCORE = {"alta": 100.0, "media": 55.0, "baja": 20.0}

_LIFECYCLE_SCORE = {
    "INMINENTE": 100.0,
    "EN_ANTICIPACION": 80.0,
    "FUTURO": 60.0,
    "OCURRIDO": 35.0,
    "EXTENDIDA": 5.0,   # ya corrió la mayor parte -- no perseguir
}

W_IMPORTANCE = 0.30
W_LIFECYCLE = 0.30
W_TECHNICAL = 0.30
W_DIRECTION_ALIGNMENT = 0.10
FINANCING_DILUTION_PENALTY = 25.0


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def importance_score(importance: str) -> float:
    return _IMPORTANCE_SCORE.get(importance, 20.0)


def lifecycle_score(lifecycle_state: str) -> float:
    return _LIFECYCLE_SCORE.get(lifecycle_state, 35.0)


def technical_confirmation_score(
    gates_fired_count: int,
    relative_volume_at_detection: Optional[float],
    change_pct_at_detection: Optional[float],
) -> float:
    """Confirmacion tecnica real -- reusa exactamente lo que el radar YA
    calcula en la deteccion (`candidate_detection.gates_fired`,
    `relative_volume_at_detection`, `change_pct_at_detection`), nunca pide
    un dato nuevo. 60 pts por gates disparadas (piso practico en 4, mismo
    numero que la deteccion real de MRNA), 25 pts por RVOL >= 2.0x (mismo
    umbral que `alert_stage.py::VOLUME_ELEVATED_THRESHOLD`), 15 pts por
    `change_pct_at_detection` ya en la direccion de un movimiento real
    (>= 3.0%, mismo piso que `phase_classifier.MOVEMENT_FLOOR_PCT`)."""
    puntos_gates = min(gates_fired_count, 4) / 4.0 * 60.0
    puntos_rvol = 25.0 if (relative_volume_at_detection is not None and relative_volume_at_detection >= 2.0) else 0.0
    puntos_cambio = 15.0 if (change_pct_at_detection is not None and abs(change_pct_at_detection) >= 3.0) else 0.0
    return _clamp(puntos_gates + puntos_rvol + puntos_cambio)


def direction_alignment_score(
    direction: str,
    price_change_since_published_pct: Optional[float],
) -> float:
    """100 si el precio ya se movio en la direccion que sugiere el
    catalizador, 0 si se movio en contra, 50 si no hay dato de precio
    todavia (neutral -- no se penaliza la falta de dato) o si la direccion
    del catalizador es NEUTRAL/INDEFINIDA (no hay con que alinear)."""
    if direction not in ("ALCISTA", "BAJISTA") or price_change_since_published_pct is None:
        return 50.0
    alineado = (direction == "ALCISTA" and price_change_since_published_pct > 0) or \
               (direction == "BAJISTA" and price_change_since_published_pct < 0)
    return 100.0 if alineado else 0.0


def catalyst_score(
    catalyst_type: str,
    importance: str,
    lifecycle_state: str,
    direction: str,
    gates_fired_count: int = 0,
    relative_volume_at_detection: Optional[float] = None,
    change_pct_at_detection: Optional[float] = None,
    price_change_since_published_pct: Optional[float] = None,
) -> float:
    puntaje = (
        W_IMPORTANCE * importance_score(importance)
        + W_LIFECYCLE * lifecycle_score(lifecycle_state)
        + W_TECHNICAL * technical_confirmation_score(
            gates_fired_count, relative_volume_at_detection, change_pct_at_detection,
        )
        + W_DIRECTION_ALIGNMENT * direction_alignment_score(direction, price_change_since_published_pct)
    )
    if catalyst_type == "FINANCING_DILUTION":
        puntaje -= FINANCING_DILUTION_PENALTY
    return _clamp(puntaje)


# ---------------------------------------------------------------------------
# MRNA_SIMILARITY_SCORE (0-100) -- similitud ESTRUCTURAL, NUNCA probabilidad
# ---------------------------------------------------------------------------

AXIS_WEIGHTS = {
    "tipo_transformacional": 0.25,
    "gates_disparadas": 0.20,
    "sorpresa_bajo_rvol": 0.20,
    "aceleracion_volumen": 0.15,
    "direccion_alcista": 0.10,
    "bajo_retroceso": 0.10,
}


def _axis_tipo_transformacional(catalyst_type: str) -> float:
    if catalyst_type in TRANSFORMATIONAL_TYPES:
        return 1.0
    if catalyst_type in PARTIAL_TRANSFORMATIONAL_TYPES:
        return 0.5
    return 0.0


def _axis_gates_disparadas(gates_fired_count: int) -> float:
    referencia = MRNA_PATTERN["gates_fired_count"]  # 4, real
    return min(gates_fired_count / referencia, 1.0) if referencia else 0.0


def _axis_sorpresa_bajo_rvol(relative_volume_at_detection: Optional[float]) -> float:
    """MRNA real explotó desde RVOL=0.0071 -- casi cero, sin posicionamiento
    previo. Similitud alta cuando el RVOL de detección también es muy bajo;
    decae a 0 en RVOL=1.0 (actividad ya "normal", sin sorpresa)."""
    if relative_volume_at_detection is None:
        return 0.0
    referencia = MRNA_PATTERN["relative_volume_at_detection"]  # 0.0071
    if relative_volume_at_detection <= referencia:
        return 1.0
    if relative_volume_at_detection >= 1.0:
        return 0.0
    return 1.0 - (relative_volume_at_detection - referencia) / (1.0 - referencia)


def _axis_aceleracion_volumen(relative_volume_hoy_peak: Optional[float]) -> float:
    """MRNA real escaló hasta RVOL=24x durante el día -- similitud
    proporcional al pico alcanzado, tope en el propio valor real de MRNA."""
    if relative_volume_hoy_peak is None:
        return 0.0
    referencia = MRNA_PATTERN["relative_volume_hoy_peak"]  # 24.0
    return min(relative_volume_hoy_peak / referencia, 1.0) if referencia else 0.0


def _axis_direccion_alcista(direction: Optional[str]) -> float:
    return 1.0 if direction == MRNA_PATTERN["direction"] else 0.0


def _axis_bajo_retroceso(retroceso_desde_maximo_pct: Optional[float]) -> float:
    """MRNA real nunca retrocedió más de 8% desde su máximo intradía en
    todo el día -- similitud alta cuando el retroceso actual también se
    mantiene bajo ese piso; decae a 0 en 40% de retroceso (mismo orden de
    magnitud que `EXTENDED_MOVE_PCT`, un retroceso de esa escala ya
    describe un movimiento agotado, no sostenido)."""
    if retroceso_desde_maximo_pct is None:
        return 0.0
    referencia = MRNA_PATTERN["retroceso_desde_maximo_pct_max"]  # 8.0
    if retroceso_desde_maximo_pct <= referencia:
        return 1.0
    if retroceso_desde_maximo_pct >= 40.0:
        return 0.0
    return 1.0 - (retroceso_desde_maximo_pct - referencia) / (40.0 - referencia)


def mrna_similarity_score(
    catalyst_type: str,
    gates_fired_count: int = 0,
    relative_volume_at_detection: Optional[float] = None,
    relative_volume_hoy_peak: Optional[float] = None,
    direction: Optional[str] = None,
    retroceso_desde_maximo_pct: Optional[float] = None,
) -> float:
    total = (
        AXIS_WEIGHTS["tipo_transformacional"] * _axis_tipo_transformacional(catalyst_type)
        + AXIS_WEIGHTS["gates_disparadas"] * _axis_gates_disparadas(gates_fired_count)
        + AXIS_WEIGHTS["sorpresa_bajo_rvol"] * _axis_sorpresa_bajo_rvol(relative_volume_at_detection)
        + AXIS_WEIGHTS["aceleracion_volumen"] * _axis_aceleracion_volumen(relative_volume_hoy_peak)
        + AXIS_WEIGHTS["direccion_alcista"] * _axis_direccion_alcista(direction)
        + AXIS_WEIGHTS["bajo_retroceso"] * _axis_bajo_retroceso(retroceso_desde_maximo_pct)
    )
    return _clamp(total * 100.0)


# ---------------------------------------------------------------------------
# CATALYST_OPPORTUNITY_SCORE (0-100) -- Segunda Fase (2026-08-24), para
# RANKING en vivo (no se congela) -- reutiliza CATALYST_SCORE/MRNA_SIMILARITY
# como componentes, nunca los recalcula con otra fórmula.
# ---------------------------------------------------------------------------

W_OPP_CATALYST_SCORE = 0.30
W_OPP_TECHNICAL_CONFIRMATION = 0.20
W_OPP_RELATIVE_VOLUME = 0.15
W_OPP_GAP = 0.10
W_OPP_EVENT_PROXIMITY = 0.15
W_OPP_MRNA_SIMILARITY = 0.10

EVENT_PROXIMITY_MAX_DAYS = 7.0  # >= a esto, proximidad = 0
EXTENSION_PENALTY_THRESHOLD_PCT = 8.0   # mismo piso real de MRNA (retroceso_desde_maximo_pct_max)
EXTENSION_PENALTY_MAX = 20.0            # tope del castigo por extensión
DILUTION_PENALTY = 15.0                 # además del -25 ya aplicado dentro de CATALYST_SCORE


def _rvol_component(relative_volume: Optional[float]) -> Optional[float]:
    if relative_volume is None:
        return None
    return _clamp(relative_volume * 20.0)  # RVOL=5x -> 100


def _gap_component(gap_pct: Optional[float]) -> Optional[float]:
    if gap_pct is None:
        return None
    return _clamp(abs(gap_pct) * 10.0)  # gap=10% -> 100


def _event_proximity_component(dias_al_evento: Optional[float]) -> Optional[float]:
    if dias_al_evento is None:
        return None
    if dias_al_evento < 0:
        return 0.0  # ya pasó -- no es "próximo"
    return _clamp(100.0 * (1.0 - min(dias_al_evento, EVENT_PROXIMITY_MAX_DAYS) / EVENT_PROXIMITY_MAX_DAYS))


def _extension_penalty(retroceso_desde_maximo_pct: Optional[float]) -> float:
    if retroceso_desde_maximo_pct is None or retroceso_desde_maximo_pct <= EXTENSION_PENALTY_THRESHOLD_PCT:
        return 0.0
    exceso = retroceso_desde_maximo_pct - EXTENSION_PENALTY_THRESHOLD_PCT
    return min(EXTENSION_PENALTY_MAX, exceso)  # 1 punto de castigo por cada 1% de exceso, tope 20


def catalyst_opportunity_score(
    catalyst_score: float,
    technical_confirmation: float,
    catalyst_type: str,
    relative_volume: Optional[float] = None,
    gap_pct: Optional[float] = None,
    dias_al_evento: Optional[float] = None,
    mrna_similarity: Optional[float] = None,
    retroceso_desde_maximo_pct: Optional[float] = None,
) -> float:
    """Score de RANKING (no se congela, se recalcula en cada request sobre
    datos ya unidos -- puro, sin red). Degradación PARCIAL explícita
    (pedido del usuario, punto 5): si falta un componente secundario
    (`relative_volume`/`gap_pct`/`dias_al_evento`/`mrna_similarity`), su
    peso se excluye de la suma y el resto se RENORMALIZA sobre los pesos
    disponibles -- nunca cae a `None`, porque `catalyst_score` y
    `technical_confirmation` (ambos siempre numéricos, ya calculables
    solo con `importance`/`lifecycle_state`/gates) siempre están
    presentes y anclan el cálculo."""
    componentes = [
        (W_OPP_CATALYST_SCORE, catalyst_score),
        (W_OPP_TECHNICAL_CONFIRMATION, technical_confirmation),
        (W_OPP_RELATIVE_VOLUME, _rvol_component(relative_volume)),
        (W_OPP_GAP, _gap_component(gap_pct)),
        (W_OPP_EVENT_PROXIMITY, _event_proximity_component(dias_al_evento)),
        (W_OPP_MRNA_SIMILARITY, mrna_similarity),
    ]
    disponibles = [(w, v) for w, v in componentes if v is not None]
    peso_total = sum(w for w, _ in disponibles)
    if peso_total <= 0:
        base = catalyst_score  # nunca debería pasar (catalyst_score siempre numérico), piso de seguridad
    else:
        base = sum(w * v for w, v in disponibles) / peso_total

    base -= _extension_penalty(retroceso_desde_maximo_pct)
    if catalyst_type == "FINANCING_DILUTION":
        base -= DILUTION_PENALTY

    return _clamp(base)
