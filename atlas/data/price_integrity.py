"""Heurística DEFENSIVA de posible corporate action (split/reverse split) --
Hito 6, Fase 6.2 (2026-09-04, autorizado explícitamente). NUNCA una
confirmación real: Atlas no tiene acceso a ningún feed de eventos
corporativos, solo observa el precio antes/después. NUNCA consumida por
`atlas_live/radar/candidate_gates.py` ni por ninguna lógica de decisión --
puramente informativa/de trazabilidad, expuesta en
`Quote.possible_split_flag`/`Quote.possible_split_ratio` y persistida en
`candidate_detection` para auditoría posterior, nada más.

Limitación declarada explícitamente (no se oculta):
- Falso negativo conocido -- si el proveedor ya ajustó `previous_close`
  antes de que Atlas vea el quote (confirmado que esto ocurrió con el
  caso real de MSTU: `change_pct_at_detection=+0.037%`, prácticamente
  plano), esta heurística no tiene nada que detectar -- por diseño no
  puede rescatar ese caso, ya que depende de ver un salto grande de
  precio, y acá no lo hubo desde el punto de vista de Atlas.
- Falso positivo conocido y aceptado -- una caída/suba genuina de ~40%+
  en un solo día que por coincidencia numérica caiga cerca de una
  fracción limpia (ej. casi exactamente -50%) se marca igual que un
  split real. No hay forma de distinguirlos sin un feed real de eventos
  corporativos, que Atlas no tiene -- se documenta como límite, no se
  inventa una fuente de verdad que no existe.

Calibración verificada contra el caso real MRNA (evidencia ya persistida:
`max_return_after_detection_pct=170.6%`, `total_day_change_pct=49.91%`,
`price_at_detection=$65.605`) antes de fijar las constantes:
- Con un cambio de +170.6%: ratio=2.706 -- el más cercano de la lista es
  3.0, a una distancia de |2.706-3|/3=9.8%. Con RATIO_TOLERANCE_PCT=4.0,
  9.8% > 4% -> NUNCA se marca. Margen de casi 2.5x sobre el umbral.
- Con un cambio de +49.91% (el real de detección, no el máximo):
  ratio≈1.4989 -- no hay ningún ratio limpio cerca de 1.5 en la lista
  (splits 3:2 son mucho menos comunes en microcaps, quedan fuera a
  propósito, documentado como límite) -- tampoco se marca.
- Con un split real 2:1 y un +2% de movimiento genuino encima
  (previous_close=100, last_price=51.0, change_percent=-49%): ratio=0.51,
  distancia a 0.5 = 2% <= 4% -> SÍ se marca, dentro del umbral de extremo
  (49% >= 40%).
"""

from typing import Optional, Tuple

POSSIBLE_SPLIT_FLAG = "POSSIBLE_SPLIT_OR_REVERSE_SPLIT"

# Ratios de split reales más comunes en mercados US (forward: precio baja;
# reverse: precio sube). No exhaustivo a propósito -- ratios menos
# frecuentes (ej. 3:2, 7:1) quedan fuera, documentado como limitación, en
# vez de ampliar la lista sin evidencia real de que haga falta.
_CLEAN_SPLIT_RATIOS: Tuple[float, ...] = (2.0, 3.0, 4.0, 5.0, 10.0, 0.5, 1 / 3, 0.25, 0.2, 0.1)

# Piso de magnitud -- por debajo de esto ni se calcula el ratio. El split
# real más chico posible (2:1 o 1:2) ya produce como mínimo ~50% de
# cambio; 40.0 (no 50.0) deja margen para que un split real con un
# pequeño movimiento genuino residual en la misma dirección (ej. -49%)
# siga entrando a la revisión de ratio, sin acercarse al rango donde vive
# un movimiento grande pero genuino (caso MRNA, ~50-170%).
EXTREME_CHANGE_PCT_THRESHOLD = 40.0

# Tolerancia -- qué tan cerca del ratio limpio debe caer para contar como
# "posible split". Calibrado explícitamente para que el caso real MRNA
# (ratio≈2.706, a 9.8% de 3.0) quede muy por fuera de este umbral.
RATIO_TOLERANCE_PCT = 4.0


def classify_possible_split(
    previous_close: Optional[float],
    last_price: Optional[float],
    change_percent: Optional[float],
    price_is_stale: bool = False,
) -> Tuple[Optional[str], Optional[float]]:
    """Devuelve (flag, ratio). `flag` es `POSSIBLE_SPLIT_FLAG` o `None`,
    nunca otro valor. Nunca lanza -- cualquier dato ausente/inválido/stale
    produce (None, None) explícito, comportamiento seguro por defecto,
    nunca un falso positivo por falta de datos."""
    if price_is_stale:
        return None, None
    if previous_close is None or last_price is None or change_percent is None:
        return None, None
    if previous_close <= 0 or last_price <= 0:
        return None, None
    if abs(change_percent) < EXTREME_CHANGE_PCT_THRESHOLD:
        return None, None

    ratio = last_price / previous_close
    for clean in _CLEAN_SPLIT_RATIOS:
        if abs(ratio - clean) / clean <= RATIO_TOLERANCE_PCT / 100.0:
            return POSSIBLE_SPLIT_FLAG, round(ratio, 4)
    return None, None
