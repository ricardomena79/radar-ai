"""Experimento shadow de aprendizaje BIDIRECCIONAL (2026-09-12, autorizado
explícitamente en Plan Mode -- misión "EXPERIMENTO SHADOW DE APRENDIZAJE
BIDIRECCIONAL", continuación del informe `7686cd2`).

El informe `7686cd2` encontró que `atlas_decision_core._compute_shadow_decision()`
solo puede DEGRADAR una decisión accionable (`OPORTUNIDAD_PRIORITARIA`->
`VIGILAR`, `VIGILAR`->`PREPARACION`) -- nunca puede elevar `NO_TOCAR`, que
es la decisión base de la gran mayoría de candidatas. Esto por sí solo
casi garantiza que el conocimiento nunca produzca una decisión distinta a
observar, sea cual sea la calidad de la evidencia.

Este módulo agrega, SIN modificar `atlas_decision_core.py` (evaluado y
descartado explícitamente en el plan -- no hace falta: todo lo necesario
ya sale de ese módulo sin tocarlo), una segunda capa de decisión "B"
(`decision_informada`) que puede, ADEMÁS de heredar el downgrade ya
existente, elevar `NO_TOCAR` -> `VIGILAR` -- solo cuando el conocimiento
es `ELEGIBLE` (veredicto YA persistido por Hito 3.3, walk-forward-seguro,
`validation_state=="VALIDACION_ROBUSTA"` exclusivamente -- nunca
re-derivado acá) Y el intervalo de Wilson completo queda por ENCIMA del
baseline (`wilson_lower_bound_20_pct > baseline_pct_20` -- el mismo
intervalo, el lado opuesto de la misma comparación que ya usa
`_compute_shadow_decision()` para decidir el downgrade, ningún umbral
nuevo).

Puro: sin DB, sin red, y deliberadamente sin ninguna dependencia del
registro/estado del mecanismo de activación de Fase 3.5 -- este
experimento corre siempre, sin condicionarse a si esa otra fase está
encendida o apagada. Tampoco lee reloj ni fecha: el walk-forward ya fue
garantizado, de forma independiente, por Hito 3.3 antes de que
`eligibility_state` llegue acá."""

from __future__ import annotations

from typing import Any, Dict, Optional

UPGRADE_ONE_TIER: Dict[str, str] = {"NO_TOCAR": "VIGILAR"}


def compute_bidirectional_decision(
    decision_base: str,
    decision_shadow_downgrade: Optional[str],
    eligibility_state: Optional[str],
    learned_evidence: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Calcula la decisión INFORMADA bidireccional ("B"). Orden de
    evaluación, el primero que aplica gana:

    1. `decision_base` no es clave de `UPGRADE_ONE_TIER` (no es
       `NO_TOCAR`) -> `decision_informada = decision_shadow_downgrade`
       (el comportamiento downgrade-only YA existente, intacto -- cubre
       `PREPARACION` -- que nunca recibe upgrade, por no ser clave de
       `UPGRADE_ONE_TIER` --, `VIGILAR` y `OPORTUNIDAD_PRIORITARIA`).
    2. `decision_base == "NO_TOCAR"` pero `eligibility_state !=
       "ELEGIBLE"` (cubre `NO_ELEGIBLE`/`INSUFICIENTE`/`None`) -> sin
       upgrade, `decision_informada = decision_base`.
    3. `decision_base == "NO_TOCAR"`, `ELEGIBLE`, pero
       `wilson_lower_bound_20_pct`/`baseline_pct_20` ausentes o
       `wilson_lower_bound_20_pct <= baseline_pct_20` (evidencia neutral,
       negativa, o igual al baseline) -> sin upgrade.
    4. `decision_base == "NO_TOCAR"`, `ELEGIBLE`,
       `wilson_lower_bound_20_pct > baseline_pct_20` (evidencia
       favorable, intervalo completo por encima del baseline) ->
       `decision_informada = "VIGILAR"`, `upgrade_aplicado=True`.

    Nunca produce `PREPARACION`->algo, nunca salta a
    `OPORTUNIDAD_PRIORITARIA` directo desde `NO_TOCAR` (único valor
    posible de `UPGRADE_ONE_TIER`), nunca upgrade sin
    `eligibility_state=="ELEGIBLE"`. `decision_base`/
    `decision_shadow_downgrade` nunca se mutan -- solo se leen."""
    le = learned_evidence or {}

    if decision_base not in UPGRADE_ONE_TIER:
        return {
            "decision_informada": decision_shadow_downgrade,
            "upgrade_aplicado": False,
            "motivo": "DECISION_BASE_NO_ELEGIBLE_PARA_UPGRADE (no es NO_TOCAR) -- se hereda el shadow downgrade-only existente",
        }

    if eligibility_state != "ELEGIBLE":
        return {
            "decision_informada": decision_base,
            "upgrade_aplicado": False,
            "motivo": f"CONOCIMIENTO_{eligibility_state if eligibility_state else 'SIN_VEREDICTO_3.3'}: sin upgrade",
        }

    wilson_lower = le.get("wilson_lower_bound_20_pct")
    baseline = le.get("baseline_pct_20")
    if wilson_lower is None or baseline is None or wilson_lower <= baseline:
        return {
            "decision_informada": decision_base,
            "upgrade_aplicado": False,
            "motivo": (
                "EVIDENCIA_SIN_VENTAJA: wilson_lower_bound_20_pct "
                f"{wilson_lower!r} no supera baseline_pct_20 {baseline!r} -- sin upgrade"
            ),
        }

    return {
        "decision_informada": UPGRADE_ONE_TIER[decision_base],
        "upgrade_aplicado": True,
        "motivo": (
            f"UPGRADE: wilson_lower_bound_20_pct={wilson_lower} > baseline_pct_20={baseline}, "
            "eligibility_state=ELEGIBLE (VALIDACION_ROBUSTA, walk-forward-seguro)"
        ),
    }
