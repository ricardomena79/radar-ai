"""Umbrales de la escalera de 7 estados x 11 ejes (2026-08-15).

Baja a números concretos la sección 8 de `PROPUESTA_MADUREZ_APRENDIZAJE.md`
(aprobada por el usuario como base de esta implementación). Cada constante
de acá está documentada en esa sección con su justificación estadística o
de diseño -- este módulo NO repite esa justificación, solo la codifica.

Son datos, no lógica de detección: no importa nada de `candidate_gates.py`
ni `phase_classifier.py`, y no los modifica ni los usa.
"""

from dataclasses import dataclass
from typing import List, Tuple

# Los 7 estados, en orden. Índice 0 = "Sin evidencia" (peor), 6 = "Madurez
# alta" (mejor) -- mismo orden que propuso el usuario, solo 0-indexado para
# que sea directamente el índice de una lista.
STATE_KEYS: List[str] = [
    "sin_evidencia",
    "evidencia_inicial",
    "aprendizaje_emergente",
    "aprendizaje_en_desarrollo",
    "aprendizaje_consistente",
    "aprendizaje_validado",
    "madurez_alta",
]

STATE_LABELS: List[str] = [
    "Sin evidencia",
    "Evidencia inicial",
    "Aprendizaje emergente",
    "Aprendizaje en desarrollo",
    "Aprendizaje consistente",
    "Aprendizaje validado",
    "Madurez alta",
]

MIN_STATE = 0
MAX_STATE = len(STATE_LABELS) - 1  # 6


def state_label(level: int) -> str:
    level = max(MIN_STATE, min(MAX_STATE, level))
    return STATE_LABELS[level]


def state_key(level: int) -> str:
    level = max(MIN_STATE, min(MAX_STATE, level))
    return STATE_KEYS[level]


# ---------------------------------------------------------------------------
# Piso estadístico compartido (real, ya usado en el proyecto -- ver
# atlas_live/memory/base_rates.py). Reutilizado acá para el intervalo de
# Wilson de los ejes que lo necesitan (9, 10).
# ---------------------------------------------------------------------------
MIN_SAMPLE_SIZE = 10
WILSON_Z = 1.96


def level_from_breakpoints(value: float, breakpoints: List[float]) -> int:
    """`breakpoints` son los 6 pisos mínimos para alcanzar los niveles L2..L7
    (índices 1..6) -- si `value` no alcanza ni el primer piso, el nivel es 0
    (Sin evidencia). Cada breakpoint debe ser estrictamente mayor al
    anterior; `value >= breakpoints[i]` sube un nivel."""
    level = 0
    for bp in breakpoints:
        if value >= bp:
            level += 1
        else:
            break
    return level


# ---------------------------------------------------------------------------
# Eje 1 -- Volumen (casos CERRADOS y evaluados)
# ---------------------------------------------------------------------------
EJE1_VOLUMEN_BREAKPOINTS = [1, 10, 30, 75, 150, 300]

# ---------------------------------------------------------------------------
# Eje 2 -- Días distintos de mercado (calendario bursátil real: ~5/sem, ~21/mes, ~63/trim)
# ---------------------------------------------------------------------------
EJE2_DIAS_BREAKPOINTS = [1, 5, 10, 21, 42, 63]

# ---------------------------------------------------------------------------
# Eje 3 -- Símbolos distintos + concentración (top-3 símbolos, share máximo permitido)
# ---------------------------------------------------------------------------
EJE3_SIMBOLOS_BREAKPOINTS = [1, 10, 25, 50, 100, 200]
# Cap de concentración por nivel (solo aplica desde "en_desarrollo" en adelante,
# índice 3): {índice: share_maximo_permitido_top3}
EJE3_CONCENTRACION_MAX_TOP3 = {3: 0.30, 4: 0.30, 5: 0.20, 6: 0.15}

# ---------------------------------------------------------------------------
# Eje 4 -- Regímenes de mercado distintos (de los hasta 9 posibles: 3 terciles
# de volatilidad x 3 terciles de sesgo direccional, derivados del propio
# universo escaneado por Atlas, sin fuente externa)
# ---------------------------------------------------------------------------
EJE4_REGIMENES_BREAKPOINTS = [1, 2, 4, 6, 8, 9]
EJE4_MAX_REGIMENES = 9
EJE4_MIN_DIAS_PARA_TERCILES = 3  # con menos días, no tiene sentido partir en terciles

# ---------------------------------------------------------------------------
# Eje 5 -- Cobertura por timing de detección (peor de los 6 buckets)
# ---------------------------------------------------------------------------
TIMING_BUCKETS = [
    "antes_del_movimiento", "al_comienzo", "expansion_temprana",
    "recorrido_significativo_ya_hecho", "demasiado_tarde", "agotamiento",
]
EJE5_TIMING_BREAKPOINTS = [1, 10, 20, 30, 50, 75]

# ---------------------------------------------------------------------------
# Eje 6 -- Cobertura por dirección (peor de las 3)
# ---------------------------------------------------------------------------
DIRECTION_BUCKETS = ["ALCISTA", "BAJISTA", "NEUTRAL"]
EJE6_DIRECCION_BREAKPOINTS = [1, 10, 20, 30, 50, 75]

# ---------------------------------------------------------------------------
# Eje 7 -- Comportamiento post-apertura (solo detecciones premarket)
# ---------------------------------------------------------------------------
EJE7_MIN_PREMARKET_PARA_EVALUAR = 5  # por debajo de esto, "no aplicable todavía", no "insuficiente"
EJE7_POST_APERTURA_BREAKPOINTS = [5, 10, 15, 25, 40]  # mapean a niveles 2..6 (Emergente..Madurez alta)

# ---------------------------------------------------------------------------
# Eje 8 -- Evidencia por objetivo (+20% / +50% / +100%) -- positivos REALES
# observados, mismo piso (MIN_SAMPLE_SIZE) para los tres; la dificultad
# creciente sale de que el evento es más raro, no de pisos distintos.
# ---------------------------------------------------------------------------
EJE8_PISO_POSITIVOS = MIN_SAMPLE_SIZE  # 10
EJE8_PISO_INTERMEDIO_50 = 5  # exigencia parcial de +50% en nivel "en_desarrollo"
EJE8_PISO_INTERMEDIO_100 = 5  # exigencia parcial de +100% en nivel "validado"

# ---------------------------------------------------------------------------
# Eje 9 -- Consistencia (ventanas no solapadas, tamaño = 1 semana bursátil)
# ---------------------------------------------------------------------------
EJE9_DIAS_POR_VENTANA = 5
EJE9_MIN_CASOS_POR_VENTANA = MIN_SAMPLE_SIZE  # cada ventana necesita su propio piso de Wilson

# ---------------------------------------------------------------------------
# Eje 10 -- Recencia y estabilidad (ventana reciente ~ 1 mes bursátil)
# ---------------------------------------------------------------------------
EJE10_DIAS_VENTANA_RECIENTE = 21
EJE10_BREAKPOINTS = [1, 10, 20, 30]  # mapean a niveles 2..5; nivel 6 exige además ausencia de alerta

# ---------------------------------------------------------------------------
# Eje 11 -- Validación fuera de muestra (holdout temporal rotante, walk-forward)
# ---------------------------------------------------------------------------
EJE11_DIAS_HOLDOUT = 10  # ~2 semanas bursátiles, ventana más reciente, nunca usada para calibrar
EJE11_MIN_CASOS_L5 = MIN_SAMPLE_SIZE  # 10
EJE11_MIN_CASOS_L6 = 30
EJE11_MIN_BUCKETS_TIMING_L6 = 3  # de los 6 posibles


@dataclass
class WilsonInterval:
    successes: int
    n: int
    low: float
    high: float


def wilson_interval(successes: int, n: int, z: float = WILSON_Z) -> WilsonInterval:
    """Intervalo de Wilson completo (cota inferior Y superior) -- reutiliza
    la misma fórmula que `atlas_live/memory/base_rates.py` (reimplementada
    localmente por el mismo motivo que ese módulo documenta: no depender de
    símbolos privados de otro paquete)."""
    if n <= 0:
        return WilsonInterval(successes, n, 0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    low = max(0.0, (centre - margin) / denom)
    high = min(1.0, (centre + margin) / denom)
    return WilsonInterval(successes, n, low, high)


def intervals_overlap(a: WilsonInterval, b: WilsonInterval) -> bool:
    return a.low <= b.high and b.low <= a.high
