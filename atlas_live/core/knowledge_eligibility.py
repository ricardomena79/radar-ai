"""Clasificador de elegibilidad de conocimiento (Hito 3, Fase 3.3, 2026-09-03,
autorizado explícitamente en Plan Mode).

CONOCIMIENTO (`learned_evidence`) -> ELEGIBILIDAD (este módulo) -> [Fase 3.4+,
activación -- NO existe todavía, fuera de alcance de 3.3]

Responde de forma determinista una única pregunta: "¿es elegible esta
evidencia para ser considerada por Atlas en este momento, y por qué?" --
nunca decide nada, nunca cambia `estado_final`, nunca activa
`apply_recalibration` (ese flag no aparece en ningún punto de este
archivo). Puro: sin DB, sin red -- mismo estilo que `atlas_decision_core.py`.

Reutiliza íntegramente `validation_state`/Wilson CI/`sample_size`/
`baseline_pct_20`/`lift_20` ya calculados por
`atlas_live/learning/learned_evidence.py` (que a su vez ya aplica su propio
filtro walk-forward estricto, `computed_as_of < market_date`, al construir
el dict) -- no reimplementa ni Wilson ni los umbrales de
`candidate_registry.precision_validation_state()`
(`VALIDACION_MUESTRA_INSUFICIENTE_MAX=99`, `VALIDACION_EN_VALIDACION_MAX=499`),
que llegan indirectamente vía el propio `validation_state` ya calculado.

WALK-FORWARD, reverificado de forma INDEPENDIENTE (mismo criterio que
`decision_outcome_tribunal.py::_build_evento()`, que tampoco confía
ciegamente en el filtro de la capa anterior): `computed_as_of < market_date`,
comparación estricta de strings ISO. No existe en el repo una función
compartida para esto (4 implementaciones independientes ya confirmadas por
auditoría: `learned_evidence.py`, `live_experience_knowledge.py`,
`experiments.py`, `decision_outcome_tribunal.py`) -- esta es la quinta,
deliberada, siguiendo el mismo patrón defensivo ya establecido.

Tres estados, en `ELIGIBILITY_STATES`, mutuamente excluyentes, el primero
que aplica gana (árbol determinista, sin ambigüedad):
- NO_ELEGIBLE: conocimiento inexistente, estructuralmente inconsistente, o
  que viola walk-forward.
- INSUFICIENTE: conocimiento existe, es walk-forward-seguro y
  estructuralmente íntegro, pero `validation_state` es
  `"MUESTRA_INSUFICIENTE"` (n < 100) **o** `"EN_VALIDACION"` (100 <= n <=
  499) -- ambos pisos ya definidos en `candidate_registry.py`, ningún
  umbral nuevo.
- ELEGIBLE: conocimiento existe, es walk-forward-seguro, íntegro, y
  `validation_state == "VALIDACION_ROBUSTA"` EXCLUSIVAMENTE (n >= 500,
  `candidate_registry.META_MUESTRA_MINIMA`). Corrección 2026-09-03, tras
  auditoría explícita del usuario: una versión anterior de este módulo
  incluía `EN_VALIDACION` en ELEGIBLE, justificado incorrectamente citando
  una instrucción sobre el CIERRE de Hito 3.2 (que decía no exigir n>=500
  para cerrar esa fase) -- esa instrucción no aplica a la definición de
  "elegible" en 3.3, son preguntas distintas. Con datos reales
  (`wilson_confidence_interval()`, sin umbral nuevo): en el piso de
  EN_VALIDACION (n=100) el intervalo Wilson mide ~18-19 puntos porcentuales
  de ancho -- no es evidencia suficiente para considerarse elegible.
  `validation_state` sigue viajando intacto en la salida, así que un
  consumidor futuro puede seguir viendo si una condición INSUFICIENTE
  está cerca del piso robusto o recién empezando."""

from datetime import date as _date
from typing import Any, Dict, List, Optional

ELIGIBILITY_STATES = ("NO_ELEGIBLE", "INSUFICIENTE", "ELEGIBLE")

_VALIDATION_STATES_CONOCIDOS = ("MUESTRA_INSUFICIENTE", "EN_VALIDACION", "VALIDACION_ROBUSTA")


def _es_fecha_iso_valida(valor: Any) -> bool:
    if not isinstance(valor, str) or not valor:
        return False
    try:
        _date.fromisoformat(valor[:10])
        return True
    except ValueError:
        return False


def classify_eligibility(learned_evidence: Optional[Dict[str, Any]], market_date: str) -> Dict[str, Any]:
    """Clasifica un dict `learned_evidence` (el mismo que ya devuelve
    `learned_evidence.get_learned_evidence()`) contra `market_date` (la
    fecha de mercado de la decisión que estaría considerando este
    conocimiento). Determinista, sin efectos colaterales.

    Devuelve siempre:
    - `eligibility_state`: uno de `ELIGIBILITY_STATES`.
    - `reasons`: lista de strings (siempre exactamente 1 en esta versión --
      árbol de decisión de "primero que aplica gana", nunca acumula razones
      de ramas no evaluadas).
    - `checks`: dict de booleanos con cada chequeo intermedio evaluado,
      para auditoría (`knowledge_available`, `integridad_estructural`,
      `walk_forward_seguro` -- ausentes si no se llegó a evaluarlos).
    - passthrough de `validation_state`/`sample_size`/
      `wilson_lower_bound_20_pct`/`wilson_upper_bound_20_pct`/
      `baseline_pct_20`/`lift_20`/`computed_as_of`/`computed_at`/
      `methodology_version` -- tal cual venían en `learned_evidence`,
      nunca recalculados."""
    le = learned_evidence or {}
    checks: Dict[str, bool] = {}
    reasons: List[str] = []

    passthrough = {
        "validation_state": le.get("validation_state"),
        "sample_size": le.get("sample_size"),
        "wilson_lower_bound_20_pct": le.get("wilson_lower_bound_20_pct"),
        "wilson_upper_bound_20_pct": le.get("wilson_upper_bound_20_pct"),
        "baseline_pct_20": le.get("baseline_pct_20"),
        "lift_20": le.get("lift_20"),
        "computed_as_of": le.get("computed_as_of"),
        "computed_at": le.get("computed_at"),
        "methodology_version": le.get("methodology_version"),
    }

    # Paso 1 -- conocimiento disponible
    checks["knowledge_available"] = bool(le.get("available"))
    if not checks["knowledge_available"]:
        razon = le.get("reason", "SIN_LEARNED_EVIDENCE") if learned_evidence is not None else "SIN_LEARNED_EVIDENCE"
        reasons.append(f"CONOCIMIENTO_NO_DISPONIBLE: {razon}")
        return {"eligibility_state": "NO_ELEGIBLE", "reasons": reasons, "checks": checks, **passthrough}

    # Paso 2 -- integridad estructural (presencia/consistencia, ningún
    # umbral estadístico nuevo -- solo que el dict sea utilizable).
    integridad_fallas: List[str] = []
    if not _es_fecha_iso_valida(passthrough["computed_as_of"]):
        integridad_fallas.append("computed_as_of ausente o no parseable")
    if not passthrough["computed_at"]:
        integridad_fallas.append("computed_at ausente")
    if not passthrough["methodology_version"]:
        integridad_fallas.append("methodology_version ausente")
    sample_size = passthrough["sample_size"]
    if not isinstance(sample_size, int) or isinstance(sample_size, bool) or sample_size <= 0:
        integridad_fallas.append("sample_size ausente o no positivo")
    if passthrough["validation_state"] not in _VALIDATION_STATES_CONOCIDOS:
        integridad_fallas.append(f"validation_state desconocido: {passthrough['validation_state']!r}")
    lower = passthrough["wilson_lower_bound_20_pct"]
    upper = passthrough["wilson_upper_bound_20_pct"]
    if lower is None or upper is None:
        integridad_fallas.append("intervalo Wilson ausente")
    elif lower > upper:
        integridad_fallas.append(f"intervalo Wilson inconsistente: lower={lower} > upper={upper}")
    if passthrough["baseline_pct_20"] is None:
        integridad_fallas.append("baseline_pct_20 ausente")

    checks["integridad_estructural"] = not integridad_fallas
    if integridad_fallas:
        reasons.append("INTEGRIDAD_ROTA: " + "; ".join(integridad_fallas))
        return {"eligibility_state": "NO_ELEGIBLE", "reasons": reasons, "checks": checks, **passthrough}

    # Paso 3 -- walk-forward, reverificado de forma independiente (nunca
    # confía en que `learned_evidence.py` ya lo haya filtrado).
    checks["walk_forward_seguro"] = passthrough["computed_as_of"] < market_date
    if not checks["walk_forward_seguro"]:
        reasons.append(
            f"WALK_FORWARD_VIOLATION: computed_as_of={passthrough['computed_as_of']!r} "
            f">= market_date={market_date!r}"
        )
        return {"eligibility_state": "NO_ELEGIBLE", "reasons": reasons, "checks": checks, **passthrough}

    # Paso 4 -- mapeo directo desde validation_state, sin umbrales nuevos.
    # ELEGIBLE exige VALIDACION_ROBUSTA exclusivamente (n>=500,
    # candidate_registry.META_MUESTRA_MINIMA) -- MUESTRA_INSUFICIENTE y
    # EN_VALIDACION mapean ambos a INSUFICIENTE (corrección 2026-09-03,
    # ver docstring del módulo).
    if passthrough["validation_state"] == "VALIDACION_ROBUSTA":
        reasons.append(f"ELEGIBLE: validation_state={passthrough['validation_state']!r}, sample_size={sample_size}")
        return {"eligibility_state": "ELEGIBLE", "reasons": reasons, "checks": checks, **passthrough}

    reasons.append(
        f"{passthrough['validation_state']}: sample_size={sample_size} "
        "(solo VALIDACION_ROBUSTA -- n>=candidate_registry.META_MUESTRA_MINIMA -- es ELEGIBLE)"
    )
    return {"eligibility_state": "INSUFICIENTE", "reasons": reasons, "checks": checks, **passthrough}
