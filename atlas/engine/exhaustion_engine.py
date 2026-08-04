"""Motor de Agotamiento: clasifica en qué etapa de su ciclo de vida está un
movimiento ya detectado, en cada revisita (+5m, +15m, +30m, +60m, cierre,
día siguiente).

No inventa indicadores nuevos: compone señales que Atlas Core ya calcula y
ya usa para decidir (`momentum_score` y el score de RVOL, ambos 0-100, y los
mismos umbrales `MOMENTUM_STRONG`/`RVOL_HIGH` que Decision Engine ya usa
para clasificar "Momentum fuerte"/"RVOL > umbral") junto con el retorno
respecto al evento original. Los umbrales propios de este motor (qué tan
cerca del máximo cuenta como "sigue expandiéndose" vs. "ya retrocedió")
son configurables por variable de entorno, para poder ajustarlos con
evidencia real una vez que haya suficientes ciclos completos observados --
hoy son un primer punto de partida razonable, no una verdad estadística
todavía.
"""

from typing import Optional

from atlas.engine.decision_engine import MOMENTUM_STRONG, RVOL_HIGH
from atlas.engine.score_engine import env_float

# --- Estados del ciclo de vida de un movimiento ---
INICIO = "Inicio"  # el evento original (t=0); no lo produce este clasificador
ACELERACION = "Aceleración"
EXPANSION = "Expansión"
DISTRIBUCION = "Distribución"
AGOTAMIENTO = "Agotamiento"
RETROCESO = "Retroceso"
MOVEMENT_STATES = {INICIO, ACELERACION, EXPANSION, DISTRIBUCION, AGOTAMIENTO, RETROCESO}

# Qué fracción del retorno máximo alcanzado hay que conservar para seguir
# contando como "todavía cerca de su máximo" (si cae por debajo, agotamiento).
EXHAUSTION_PEAK_RETENTION_RATIO = env_float("ATLAS_EXHAUSTION_PEAK_RETENTION_RATIO", 0.5)


def classify_movement_state(
    return_percent: float,
    max_return_percent: float,
    momentum_score: Optional[float],
    rvol_score: Optional[float],
    previous_momentum_score: Optional[float] = None,
) -> str:
    """Clasifica el estado de un movimiento en una revisita, usando solo
    señales ya calculadas por Atlas Core en ese mismo checkpoint.

    - `return_percent`: retorno respecto al precio del evento, a este checkpoint.
    - `max_return_percent`: el mejor retorno alcanzado en cualquier momento
      desde el evento hasta este checkpoint (siempre >= return_percent).
    - `momentum_score`, `rvol_score`: los mismos scores 0-100 que Decision
      Engine ya calcula (Momentum Score y el componente `relative_volume`
      del Atlas Score), recalculados en este checkpoint.
    - `previous_momentum_score`: el momentum_score de la revisita anterior
      de este mismo evento, si existe (para distinguir aceleración de
      expansión sostenida). None en la primera revisita.
    """
    if return_percent < 0:
        return RETROCESO

    peak_retention = (
        return_percent / max_return_percent if max_return_percent and max_return_percent > 0 else 1.0
    )
    if peak_retention < EXHAUSTION_PEAK_RETENTION_RATIO:
        # Sigue en terreno positivo respecto al evento, pero ya devolvió más
        # de la mitad de lo mejor que había alcanzado.
        return AGOTAMIENTO

    momentum_score = momentum_score if momentum_score is not None else 0.0
    rvol_score = rvol_score if rvol_score is not None else 0.0
    still_strong = momentum_score >= MOMENTUM_STRONG and rvol_score >= RVOL_HIGH

    if still_strong:
        if previous_momentum_score is not None and momentum_score > previous_momentum_score:
            return ACELERACION
        return EXPANSION

    # Sigue cerca de su máximo, pero la fuerza subyacente (momentum/volumen)
    # ya no acompaña -- el patrón clásico de distribución.
    return DISTRIBUCION
