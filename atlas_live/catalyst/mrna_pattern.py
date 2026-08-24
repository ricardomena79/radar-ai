"""Vector de referencia MRNA (2026-08-23, Fase 6 del Motor de Catalizadores).

MRNA_SIMILARITY_SCORE NO significa "probabilidad de subir" -- significa
"qué tan parecida es la estructura actual a la configuración que produjo
el movimiento de MRNA" (aclaración explícita del usuario). Este módulo
guarda ese vector, calculado a partir de datos REALES ya persistidos por
el radar técnico para MRNA, ticker/día real (2026-08-19), nunca inventado:

  - Detectada 2026-08-19T10:45:36 UTC (06:45 ET), premarket, a $65.605.
  - `change_pct_at_detection` = 4.2%.
  - `relative_volume_at_detection` = 0.0071 -- casi cero: explotó DESDE
    prácticamente ningún volumen previo, no desde una base ya activa.
  - 4 gates disparadas simultáneamente en la detección
    (cambio_de_precio + aceleracion + despertar + cambio_de_comportamiento)
    -- señal infrecuente, la mayoría de las detecciones disparan 1.
  - `direction_at_detection` = ALCISTA, confirmada de inmediato.
  - `relative_volume_hoy` escaló de 0.007 a 24+ durante el mismo día
    (visto en `alert_stage_log`).
  - `retroceso_desde_maximo_pct` se mantuvo bajo 8% durante TODO el día
    (nunca hubo una caída fuerte desde el máximo intradía -- corrió la
    sesión completa sin agotarse).
  - Resultado real: `max_return_after_detection_pct` = 170.6%,
    `total_day_change_pct` (cierre) = 49.91%, `minutes_to_max` = 563
    (~9.4 horas -- casi toda la sesión).

Fuente de cada número: `atlas_live/radar/candidate_registry.py::get_detection()`/
`get_outcome()`/`alert_stage_history_for_ticker()` para ticker="MRNA",
market_date="2026-08-19" -- ver el informe de esta sesión, no se
recalcula ni se vuelve a consultar acá (vector congelado a propósito,
mismo criterio "referencia fija" que cualquier constante de threshold en
`alert_stage.py`)."""

from typing import Any, Dict

MRNA_PATTERN: Dict[str, Any] = {
    "catalyst_type_class": "transformational",   # FDA/clínico/M&A, no earnings/analyst-action rutinario
    "gates_fired_count": 4,
    "relative_volume_at_detection": 0.0071,
    "relative_volume_hoy_peak": 24.0,
    "change_pct_at_detection": 4.2,
    "direction": "ALCISTA",
    "lifecycle_state_at_detection": "OCURRIDO",   # la noticia ya había salido, confirmada de inmediato
    "retroceso_desde_maximo_pct_max": 8.0,
    "prior_anticipation": "low",                  # RVOL casi nulo == sin posicionamiento previo
}

# Tipos de catalizador considerados "transformacionales" (peso alto en el
# eje `transformational_axis` de catalyst_score.py) vs. los que solo
# ameritan peso parcial/bajo -- ver catalyst_classifier.py para la
# taxonomía completa.
TRANSFORMATIONAL_TYPES = {"FDA_PDUFA", "CLINICAL_TRIAL", "MA_ACQUISITION"}
PARTIAL_TRANSFORMATIONAL_TYPES = {"CONTRACT_AWARD", "GUIDANCE"}
