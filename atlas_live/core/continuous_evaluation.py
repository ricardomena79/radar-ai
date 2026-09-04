"""Clasificador de evaluación continua / degradación (Hito 3, Fase 3.6,
2026-09-03, autorizado explícitamente en Plan Mode, revisión corregida
del usuario).

CONOCIMIENTO ACUMULADO (`knowledge_eligibility.py`, Fase 3.3 -- nunca
decrece, solo crece) + VENTANA RECIENTE (últimas evaluaciones reales de
una condición) -> DEGRADACIÓN (este módulo) -> [`continuous_evaluation_registry.py`,
único punto que puede llamar a `activation_registry.revoke()`, Fase 3.5,
sin modificarla].

3.3 responde "¿es elegible este conocimiento, en general?" (acumulado,
nunca decrece). 3.6 responde una pregunta DISTINTA: "¿el desempeño
RECIENTE de esa misma condición sigue respaldando esa elegibilidad, o ya
se degradó?" -- una condición puede seguir siendo `VALIDACION_ROBUSTA`
para 3.3 para siempre (la muestra acumulada solo crece) mientras su
desempeño reciente ya dejó de superar su propio baseline.

Reutiliza ÍNTEGRAMENTE la métrica oficial ya usada por
`atlas_decision_core._compute_shadow_decision()` desde Fase U3-B:
`wilson_upper_bound_20_pct < baseline_pct_20` -- nunca se inventa una
métrica nueva, solo se recalcula esa MISMA comparación sobre una ventana
reciente en vez de sobre el acumulado histórico completo.

VENTANA vs. PISO -- aclaración explícita (pedido del usuario, para no
mezclar dos conceptos ya existentes en el proyecto pero para métricas
distintas):
- `candidate_registry.META_MUESTRA_MINIMA = 500` es, en sus DOS usos
  oficiales ya existentes (`precision_validation_state()`,
  `meta_confirmada()`), un PISO DE MUESTRA -- nunca un tamaño de ventana.
  Acá se reutiliza con el mismo significado exacto.
- Los tiers `50/100/250/500` de `magnitud_precision_rolling()` son 4
  ventanas fijas PARALELAS, pensadas para un panel de observabilidad
  humano sobre una métrica DISTINTA (precisión de magnitud). Este módulo
  NO los reutiliza -- 3.6 es un gate binario (revocar o no), no un
  dashboard multi-vista.
- 3.6 usa UNA sola ventana (`n_ventana`, ver `continuous_evaluation_registry.py`),
  con valor por defecto igual a `META_MUESTRA_MINIMA` (500) -- deliberado,
  no casualidad: así "la ventana está completamente llena" y "hay muestra
  robusta" son la MISMA condición. El piso se aplica sobre
  `recent_sample_size` (el tamaño REAL encontrado, que puede ser menor
  que `n_ventana` si todavía no hay suficiente evidencia).

WALK-FORWARD, reverificado de forma INDEPENDIENTE (sexta implementación
del mismo patrón ya usado en `decision_outcome_tribunal.py`,
`knowledge_eligibility.py`, `shadow_observation.py`, `activation_gate.py`
-- nunca confía en que la capa de arriba ya lo haya filtrado):
`computed_as_of < market_date`, comparación estricta.

Puro: sin DB, sin red, sin `apply_recalibration` ni vocabulario de
ejecución financiera en ningún punto de este archivo -- ese flag nunca
aparece acá, y este módulo NUNCA llama a `activation_registry.revoke()`
directamente (eso vive exclusivamente en el registry, condicionado a
guards adicionales de idempotencia que este clasificador no conoce).

Cuatro estados, el primero que aplica gana (árbol determinista):
- `NO_EVALUABLE`: datos faltantes (cualquier campo crítico ausente) o
  walk-forward violado -- nunca se asume degradación por ausencia de
  evidencia o por un dato inválido.
- `INSUFICIENTE`: evidencia íntegra y walk-forward-segura, pero
  `recent_sample_size < META_MUESTRA_MINIMA` -- nunca dispara
  revocación, sin importar qué tan "mal" luzcan las métricas parciales.
- `DEGRADADO`: `recent_sample_size >= META_MUESTRA_MINIMA` Y
  `recent_wilson_upper_bound_20_pct >= recent_baseline_pct_20` --
  evidencia estadística suficiente Y negativa. Único estado que marca
  `revocation_requested=True`.
- `VALIDO`: pasa todos los chequeos, la ventana reciente sigue superando
  su propio baseline.

`REVOCADO` NO es un 5to estado de este módulo -- es el estado operacional
de `activation_registry` (Fase 3.5, sin tocar), causado (entre otras
razones posibles) por una evaluación `DEGRADADO` que además disparó
`revoke()` exitosamente. Este archivo nunca escribe esa palabra como
`evaluation_state`."""

from typing import Any, Dict, Optional

from atlas_live.radar.candidate_registry import META_MUESTRA_MINIMA

EVALUATION_STATES = ("VALIDO", "DEGRADADO", "INSUFICIENTE", "NO_EVALUABLE")


def classify_continuous_evaluation(
    recent_sample_size: Optional[int],
    recent_wilson_upper_bound_20_pct: Optional[float],
    recent_baseline_pct_20: Optional[float],
    computed_as_of: Optional[str],
    market_date: str,
) -> Dict[str, Any]:
    """Clasifica de forma determinista si la ventana reciente de una
    condición sigue siendo válida, está degradada, tiene muestra
    insuficiente, o no se puede evaluar con confianza. Solo acepta
    primitivos -- nunca objetos de decisión -- estructuralmente imposible
    que mute baseline/shadow/cualquier decisión real.

    Devuelve siempre:
    - `evaluation_state`: uno de `EVALUATION_STATES`.
    - `reason`: string auditable con los números reales que motivaron el veredicto.
    - `walk_forward_ok`: bool, reverificado siempre, incluso en NO_EVALUABLE.
    - `revocation_requested`: `True` ÚNICAMENTE cuando `evaluation_state=="DEGRADADO"`
      -- el ÚNICO estado que puede pedir una revocación. El llamador
      (`continuous_evaluation_registry.py`) decide, con guards propios
      adicionales (idempotencia, `is_revoked()` previo), si esa solicitud
      efectivamente se traduce en una llamada real a `revoke()`."""
    campos_criticos = {
        "recent_sample_size": recent_sample_size,
        "recent_wilson_upper_bound_20_pct": recent_wilson_upper_bound_20_pct,
        "recent_baseline_pct_20": recent_baseline_pct_20,
        "computed_as_of": computed_as_of,
    }
    faltantes = [nombre for nombre, valor in campos_criticos.items() if valor is None]
    if faltantes:
        return {
            "evaluation_state": "NO_EVALUABLE",
            "reason": f"DATOS_FALTANTES: {', '.join(faltantes)}",
            "walk_forward_ok": False,
            "revocation_requested": False,
        }

    walk_forward_ok = computed_as_of < market_date
    if not walk_forward_ok:
        return {
            "evaluation_state": "NO_EVALUABLE",
            "reason": f"WALK_FORWARD_VIOLATION: computed_as_of={computed_as_of!r} >= market_date={market_date!r}",
            "walk_forward_ok": False,
            "revocation_requested": False,
        }

    if recent_sample_size < META_MUESTRA_MINIMA:
        return {
            "evaluation_state": "INSUFICIENTE",
            "reason": f"MUESTRA_RECIENTE_INSUFICIENTE: recent_sample_size={recent_sample_size} < META_MUESTRA_MINIMA={META_MUESTRA_MINIMA}",
            "walk_forward_ok": True,
            "revocation_requested": False,
        }

    if recent_wilson_upper_bound_20_pct >= recent_baseline_pct_20:
        return {
            "evaluation_state": "DEGRADADO",
            "reason": (
                f"DEGRADACION_DETECTADA: recent_wilson_upper_bound_20_pct={recent_wilson_upper_bound_20_pct} "
                f">= recent_baseline_pct_20={recent_baseline_pct_20}, recent_sample_size={recent_sample_size}"
            ),
            "walk_forward_ok": True,
            "revocation_requested": True,
        }

    return {
        "evaluation_state": "VALIDO",
        "reason": (
            f"VENTANA_RECIENTE_VALIDA: recent_wilson_upper_bound_20_pct={recent_wilson_upper_bound_20_pct} "
            f"< recent_baseline_pct_20={recent_baseline_pct_20}, recent_sample_size={recent_sample_size}"
        ),
        "walk_forward_ok": True,
        "revocation_requested": False,
    }
