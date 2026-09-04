"""Gate determinista de activación controlada (Hito 3, Fase 3.5, 2026-09-03,
autorizado explícitamente en Plan Mode, con decisión funcional confirmada
por el usuario: ejercer `apply_recalibration=True` de forma real, aislada
y controlada -- NUNCA una simulación paralela que evite el flag real).

ELEGIBILIDAD (`knowledge_eligibility.classify_eligibility()`, Fase 3.3) +
MECANISMO (`activation_registry.get_mechanism_state()`, Fase 3.5) +
REVOCACIÓN (`activation_registry.is_revoked()`, Fase 3.5) -> ACTIVACIÓN
(este módulo) -> [`server.py`, ÚNICO punto de todo el repo que ejecuta
`adc.decide(..., apply_recalibration=True)`, solo si este gate dijo
`ACTIVADO`].

3.3 responde "¿es elegible este conocimiento?" (propiedad del
conocimiento). 3.5 responde "¿está permitido usarlo AHORA, dentro del
entorno controlado?" -- una pregunta distinta. Este módulo CONSUME el
veredicto de 3.3 (parámetro `eligibility_state`, ya calculado por el
llamador vía `knowledge_eligibility_registry.latest_eligibility_for()`),
nunca lo recalcula con reglas propias -- única fuente de verdad para
elegibilidad sigue siendo `knowledge_eligibility.py`, sin tocar.

Puro: sin DB, sin red, sin `apply_recalibration` en ningún punto de este
archivo -- ese flag solo se pasa, de forma aislada y condicionada al
resultado de este gate, desde `server.py` (ver docstring de ese bloque).

Orden de evaluación, el primero que aplica gana (árbol determinista):
1. `mechanism_state != "ON_CONTROLADO"` -> `NO_ACTIVO` -- el estado por
   defecto (mecanismo `"OFF"`), sin importar el resto de las condiciones.
2. `is_revoked` -> `REVOCADO` -- gana SIEMPRE frente a cualquier condición
   de activación, checkeado ANTES que elegibilidad/walk-forward: ni la
   mejor evidencia puede pasar por encima de una revocación activa.
3. `eligibility_state != "ELEGIBLE"` -> `BLOQUEADO` (cubre
   `NO_ELEGIBLE`/`INSUFICIENTE`/`None`, ausencia de veredicto de 3.3).
4. Walk-forward violado (`computed_as_of` ausente o no anterior a
   `market_date`), reverificado de forma INDEPENDIENTE -- mismo criterio
   ya usado por `decision_outcome_tribunal.py`/`knowledge_eligibility.py`/
   `shadow_observation.py`, nunca confía en que la capa de arriba ya lo
   haya filtrado -> `BLOQUEADO`.
5. Si todo pasa -> `ACTIVADO`."""

from typing import Any, Dict, Optional

ACTIVATION_STATES = ("NO_ACTIVO", "ACTIVADO", "BLOQUEADO", "REVOCADO")

_MECHANISM_ON = "ON_CONTROLADO"


def classify_activation(
    mechanism_state: str,
    eligibility_state: Optional[str],
    is_revoked: bool,
    computed_as_of: Optional[str],
    market_date: str,
) -> Dict[str, Any]:
    """Clasifica de forma determinista si corresponde activar conocimiento
    elegible dentro del entorno controlado. Solo acepta primitivos
    (strings/bool/None) -- nunca objetos de decisión (`AtlasDecision`,
    `CandidateSnapshot`, etc.) -- estructuralmente imposible que esta
    función mute el baseline o el shadow ya calculados por el llamador.

    Devuelve siempre:
    - `activation_state`: uno de `ACTIVATION_STATES`.
    - `reason`: string explicando exactamente por qué (auditable).
    - `walk_forward_violation`: reverificado siempre, incluso cuando el
      resultado ya es `NO_ACTIVO`/`REVOCADO`/`BLOQUEADO` por otro motivo
      (nunca se oculta el chequeo)."""
    walk_forward_violation = not (computed_as_of is not None and computed_as_of < market_date)

    if mechanism_state != _MECHANISM_ON:
        return {
            "activation_state": "NO_ACTIVO",
            "reason": "MECANISMO_APAGADO",
            "walk_forward_violation": walk_forward_violation,
        }

    if is_revoked:
        return {
            "activation_state": "REVOCADO",
            "reason": "REVOCACION_ACTIVA",
            "walk_forward_violation": walk_forward_violation,
        }

    if eligibility_state != "ELEGIBLE":
        return {
            "activation_state": "BLOQUEADO",
            "reason": f"CONOCIMIENTO_{eligibility_state if eligibility_state else 'SIN_VEREDICTO_3.3'}",
            "walk_forward_violation": walk_forward_violation,
        }

    if walk_forward_violation:
        return {
            "activation_state": "BLOQUEADO",
            "reason": "WALK_FORWARD_VIOLATION",
            "walk_forward_violation": walk_forward_violation,
        }

    return {
        "activation_state": "ACTIVADO",
        "reason": "CONOCIMIENTO_ELEGIBLE_Y_VIGENTE",
        "walk_forward_violation": walk_forward_violation,
    }
