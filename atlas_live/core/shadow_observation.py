"""Clasificador de observación shadow (Hito 3, Fase 3.4, 2026-09-03,
autorizado explícitamente en Plan Mode).

DECISIÓN BASELINE (`atlas_decision_core.decide()`, sin `learned_evidence`)
+ DECISIÓN SHADOW (`atlas_decision_core.decide()`, con `learned_evidence`)
+ ELEGIBILIDAD (`knowledge_eligibility.classify_eligibility()`, Fase 3.3,
veredicto ya registrado -- nunca recalculado acá) -> OBSERVACIÓN (este
módulo) -> [Fase 3.5, activación controlada -- NO existe todavía, fuera
de alcance de 3.4].

Responde de forma determinista: "si Atlas hubiera seguido el conocimiento
en vez del baseline, ¿qué habría hecho, y ese conocimiento era elegible
según el veredicto YA registrado por 3.3?" -- nunca decide nada, nunca
cambia `estado_final`, nunca activa `apply_recalibration` (ese flag no
aparece en ningún punto ejecutable de este archivo). Puro: sin DB, sin
red -- mismo estilo que `knowledge_eligibility.py`/`atlas_decision_core.py`.

GATE DE OBSERVACIÓN: `observado=True` exige DOS condiciones simultáneas --
`shadow_differs=True` (mismo gate que ya usa `shadow_decision_log`,
preexistente) Y walk-forward seguro (`computed_as_of < market_date`,
estricto). Una violación de walk-forward NUNCA puede terminar persistida
como observación válida, sin importar que `shadow_differs` sea `True` --
corrección 2026-09-03 tras auditoría explícita del usuario: una versión
anterior de este módulo calculaba `walk_forward_violation` pero no lo
usaba para bloquear `observado`, dejando abierta la posibilidad de
registrar una observación construida sobre evidencia temporalmente
inválida. El campo `walk_forward_violation` se sigue devolviendo siempre
(incluso cuando `observado=False` por cualquier motivo) -- nunca se oculta
la razón por la que no se observó.

El gate de `shadow_differs`, a su vez, solo puede cumplirse cuando
`atlas_decision_core._compute_shadow_decision()` ya exigió internamente
`validation_state == "VALIDACION_ROBUSTA"` y un borde superior de Wilson
por debajo del baseline -- estructuralmente el subconjunto más pequeño
posible del universo de candidatas, nunca "todas las candidatas todos los
días" (la causa real del incidente de `candidate_observation`, tenido en
cuenta desde el diseño).

`eligibility_state` SIEMPRE viaja tal cual lo devolvió
`knowledge_eligibility_registry.latest_eligibility_for()` -- este módulo
NUNCA reclasifica ni asume "shadow_differs implica elegible": el gate
interno de `atlas_decision_core.py` y los criterios de integridad de 3.3
pueden divergir en casos raros (Wilson lower>upper, campos ausentes, etc.)
-- por eso siempre se consulta el veredicto real, nunca se infiere.

WALK-FORWARD, reverificado de forma INDEPENDIENTE (mismo criterio que
`decision_outcome_tribunal.py::_build_evento()` y que
`knowledge_eligibility.classify_eligibility()`): `computed_as_of <
market_date`, comparación estricta de strings ISO -- una violación se
MARCA (`walk_forward_violation=True`), nunca se descarta la observación en
silencio."""

from typing import Any, Dict, Optional


def classify_shadow_observation(
    decision: str,
    decision_shadow: Optional[str],
    shadow_differs: bool,
    eligibility_state: Optional[str],
    computed_as_of: Optional[str],
    market_date: str,
) -> Dict[str, Any]:
    """Clasifica un evento de decisión ya calculado (baseline + shadow +
    elegibilidad) en una observación registrable o no. Determinista, sin
    efectos colaterales, no toca ningún objeto de decisión -- solo recibe
    valores primitivos (nunca `AtlasDecision` completo), lo que hace
    estructuralmente imposible que esta función mute el baseline o el
    shadow ya calculados por el llamador.

    Devuelve siempre:
    - `observado`: `True` SOLO si `shadow_differs` Y walk-forward seguro
      (`computed_as_of < market_date`, estricto -- igualdad o posterior
      cuenta como violación e IMPIDE la observación). Si `False`, el resto
      de campos son informativos pero el llamador NO debe persistir nada.
    - `eligibility_state`: tal cual vino del veredicto de 3.3, o
      `"SIN_VEREDICTO_3.3"` si `eligibility_state` es `None` (3.3 nunca
      evaluó esta condición) -- nunca se inventa `"ELEGIBLE"` por defecto.
    - `walk_forward_violation`: `True` si `computed_as_of` es `None`/no
      anterior a `market_date` -- reverificado siempre y siempre devuelto,
      incluso cuando `observado=False` por esta misma razón (nunca se
      oculta por qué no se observó).
    - `decision`/`decision_shadow`: passthrough tal cual."""
    walk_forward_violation = not (computed_as_of is not None and computed_as_of < market_date)

    return {
        "observado": bool(shadow_differs) and not walk_forward_violation,
        "decision": decision,
        "decision_shadow": decision_shadow,
        "eligibility_state": eligibility_state if eligibility_state is not None else "SIN_VEREDICTO_3.3",
        "walk_forward_violation": walk_forward_violation,
        "computed_as_of": computed_as_of,
    }
