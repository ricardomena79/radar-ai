"""Tribunal de comparación offline (2026-09-03, Hito 3, Fase 3.2,
autorizado explícitamente): responde, EXCLUSIVAMENTE por lectura, qué
decisiones tomó Atlas, qué habría propuesto el conocimiento aprendido, y
cuál fue el resultado real -- para las 8 preguntas exactas pedidas:

1. ¿Qué decisiones habría tomado Atlas baseline?
2. ¿Qué decisión shadow habría producido el conocimiento?
3. ¿Cuál fue la decisión real? (== baseline SIEMPRE en esta fase --
   `apply_recalibration` permanece `False` en todo el sistema, verificado)
4. ¿Qué knowledge snapshot estaba disponible?
5. ¿Cuál fue el outcome final?
6. ¿La decisión baseline fue mejor o peor?
7. ¿La decisión shadow fue mejor o peor?
8. ¿Cuál es el resultado agregado por condición?

PURAMENTE DE LECTURA: importa `decision_knowledge_registry.py` (propio,
lectura) y, de `atlas_live.radar.candidate_registry` (protegido, SIN
MODIFICARLO), solo 3 funciones públicas ya existentes --
`get_outcome()`, `wilson_confidence_interval()`, `precision_validation_state()`
-- nunca abre su propia conexión a `radar_candidates.db`, nunca escribe
ahí. Wilson NO se reimplementa -- se reutiliza la fórmula oficial ya
auditada del proyecto.

WALK-FORWARD verificado de forma INDEPENDIENTE, no confiado ciegamente:
aunque `learned_evidence.py` ya filtra `computed_as_of < market_date` al
capturar el snapshot, este módulo recalcula esa comparación por cada fila
ANTES de incluirla en cualquier agregado, y cuenta cualquier violación
(`walk_forward_violations`) -- debe ser siempre 0.

Solo se consideran outcomes `is_final=1` Y `confiable_para_aprendizaje=1`
-- mismos filtros que ya usa `shadow_validation_report()`
(`candidate_registry.py`), reutilizados, no reinventados.

Clasificación ACIERTO/ERROR/AMBIGUO: reutiliza LITERALMENTE la misma
agrupación de `category` que ya usa `shadow_validation_report()`
(candidate_registry.py, "downgrade correcto/incorrecto/ambiguo"):
`category in (mejor_oportunidad, buena_oportunidad)` = resultado real
bueno; `category == falsa_senal` = resultado real malo; cualquier otro
valor (incluida su ausencia) = AMBIGUO, nunca se fuerza a una de las dos
categorías. `OPORTUNIDAD_PRIORITARIA`/`VIGILAR` son llamadas positivas
(ACIERTO si el resultado fue bueno, ERROR si fue `falsa_senal`);
`NO_TOCAR` es una llamada negativa (ACIERTO si el resultado fue
`falsa_senal`, ERROR si fue bueno); `PREPARACION` es una llamada neutral
por diseño (`priority_classifier.py`, "sin movimiento fuerte todavía") y
nunca recibe veredicto ACIERTO/ERROR -- forzarle uno inventaría una
semántica que ese estado no tiene.

Aislado por diseño (mismo patrón que `u3c3_exclusive_diagnostics.py`/
`raw_data_consolidation_pipeline.py`): cualquier excepción queda atrapada
-- el llamador siempre recibe un dict con `ok=False`, nunca una excepción
sin manejar.

NO declara "Atlas está aprendiendo", NO activa ningún conocimiento en
ninguna decisión real, NO crea ningún estado `active`/elegibilidad. Es
exclusivamente un reporte offline -- cada respuesta lleva la nota
explícita de este alcance."""

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from atlas_live.core import decision_knowledge_registry as registry
from atlas_live.radar import candidate_registry as reg

_POSITIVE_STATES = ("OPORTUNIDAD_PRIORITARIA", "VIGILAR")
_NEGATIVE_STATES = ("NO_TOCAR",)
_GOOD_OUTCOME_CATEGORIES = ("mejor_oportunidad", "buena_oportunidad")
_BAD_OUTCOME_CATEGORY = "falsa_senal"

NOTA_ALCANCE = (
    "Reporte offline de solo lectura (Hito 3, Fase 3.2). No implica "
    "activacion de conocimiento en decisiones reales -- apply_recalibration "
    "permanece False en todo el sistema. No declara que Atlas este "
    "aprendiendo."
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _evaluate_decision_correctness(decision_value: Optional[str], category: Optional[str]) -> str:
    """Pura, sin DB. Ver docstring del módulo para la semántica exacta."""
    if category is None:
        return "SIN_CATEGORIA"
    if decision_value in _POSITIVE_STATES:
        if category in _GOOD_OUTCOME_CATEGORIES:
            return "ACIERTO"
        if category == _BAD_OUTCOME_CATEGORY:
            return "ERROR"
        return "AMBIGUO"
    if decision_value in _NEGATIVE_STATES:
        if category == _BAD_OUTCOME_CATEGORY:
            return "ACIERTO"
        if category in _GOOD_OUTCOME_CATEGORIES:
            return "ERROR"
        return "AMBIGUO"
    return "AMBIGUO"  # PREPARACION, o cualquier valor fuera de FINAL_STATES conocido -- sin veredicto forzado


def _outcome_is_evaluable(outcome: Optional[Dict[str, Any]]) -> bool:
    if not outcome:
        return False
    return bool(outcome.get("is_final")) and bool(outcome.get("confiable_para_aprendizaje"))


def _build_evento(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    walk_forward_violation = False
    if snapshot.get("knowledge_available") and snapshot.get("computed_as_of"):
        walk_forward_violation = not (snapshot["computed_as_of"] < snapshot["market_date"])

    outcome = reg.get_outcome(snapshot["ticker"], snapshot["market_date"])
    outcome_evaluable = _outcome_is_evaluable(outcome)
    category = outcome.get("category") if outcome_evaluable else None

    baseline_veredicto = (
        _evaluate_decision_correctness(snapshot["decision"], category) if outcome_evaluable else "SIN_OUTCOME"
    )
    shadow_veredicto: Optional[str] = None
    if snapshot.get("knowledge_available") and snapshot.get("decision_shadow"):
        shadow_veredicto = (
            _evaluate_decision_correctness(snapshot["decision_shadow"], category) if outcome_evaluable else "SIN_OUTCOME"
        )

    return {
        "ticker": snapshot["ticker"],
        "market_date": snapshot["market_date"],
        "decision_timestamp": snapshot["decision_timestamp"],
        # Pregunta 1
        "decision_baseline": snapshot["decision"],
        # Pregunta 2
        "decision_shadow": snapshot.get("decision_shadow"),
        # Pregunta 3 -- SIEMPRE igual a baseline en esta fase.
        "decision_real": snapshot["decision"],
        "apply_recalibration_active": bool(snapshot.get("apply_recalibration_active")),
        # Pregunta 4
        "knowledge_snapshot": {
            "available": bool(snapshot.get("knowledge_available")),
            "reason": snapshot.get("knowledge_reason"),
            "methodology_version": snapshot.get("methodology_version"),
            "computed_as_of": snapshot.get("computed_as_of"),
            "computed_at": snapshot.get("computed_at"),
            "validation_state": snapshot.get("validation_state"),
            "sample_size": snapshot.get("sample_size"),
            "historical_success_pct_20": snapshot.get("historical_success_pct_20"),
            "baseline_pct_20": snapshot.get("baseline_pct_20"),
            "lift_20": snapshot.get("lift_20"),
            "wilson_lower_bound_20_pct": snapshot.get("wilson_lower_bound_20_pct"),
            "wilson_upper_bound_20_pct": snapshot.get("wilson_upper_bound_20_pct"),
        },
        # Pregunta 5
        "outcome": outcome if outcome_evaluable else None,
        "outcome_evaluable": outcome_evaluable,
        "walk_forward_violation": walk_forward_violation,
        # Preguntas 6/7
        "decision_baseline_veredicto": baseline_veredicto,
        "decision_shadow_veredicto": shadow_veredicto,
    }


def _stats(counts: Counter) -> Dict[str, Any]:
    acierto = counts.get("ACIERTO", 0)
    error = counts.get("ERROR", 0)
    ambiguo = counts.get("AMBIGUO", 0)
    sin_categoria = counts.get("SIN_CATEGORIA", 0)
    n_evaluables = acierto + error
    pct = round(100 * acierto / n_evaluables, 1) if n_evaluables else None
    wilson = reg.wilson_confidence_interval(acierto, n_evaluables) if n_evaluables else None
    return {
        "n_acierto": acierto,
        "n_error": error,
        "n_ambiguo": ambiguo,
        "n_sin_categoria": sin_categoria,
        "n_evaluables": n_evaluables,
        "pct_acierto": pct,
        "wilson_ci_acierto_pct": list(wilson) if wilson else None,
        "validation_state": reg.precision_validation_state(n_evaluables),
    }


def _aggregate_condition(direction: str, timing_deteccion: str, eventos: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pregunta 8 -- agregado por condición, reutilizando
    `wilson_confidence_interval`/`precision_validation_state` (solo
    lectura, sin reimplementar)."""
    baseline_counts = Counter(e["decision_baseline_veredicto"] for e in eventos)
    shadow_eventos = [e for e in eventos if e["decision_shadow_veredicto"] is not None]
    shadow_counts = Counter(e["decision_shadow_veredicto"] for e in shadow_eventos)
    return {
        "direction": direction,
        "timing_deteccion": timing_deteccion,
        "n_eventos": len(eventos),
        "n_eventos_con_shadow": len(shadow_eventos),
        "baseline": _stats(baseline_counts),
        "shadow": _stats(shadow_counts),
    }


def full_tribunal_report(
    market_date: Optional[str] = None,
    direction: Optional[str] = None,
    timing_deteccion: Optional[str] = None,
    limit: int = 5000,
) -> Dict[str, Any]:
    """Orquesta el reporte completo -- responde las 8 preguntas sobre el
    conjunto de snapshots que matchean los filtros opcionales. Nunca
    lanza: cualquier excepción queda atrapada, el llamador siempre recibe
    un dict."""
    resultado: Dict[str, Any] = {
        "generated_at": _now_iso(),
        "ok": False,
        "nota": NOTA_ALCANCE,
        "walk_forward_violations": 0,
        "n_snapshots": 0,
        "n_con_outcome_evaluable": 0,
        "eventos": [],
        "agregado_por_condicion": [],
        "error": None,
    }
    try:
        snapshots = registry.list_snapshots(
            market_date=market_date, direction=direction, timing_deteccion=timing_deteccion, limit=limit,
        )
        resultado["n_snapshots"] = len(snapshots)

        condiciones: Dict[tuple, List[Dict[str, Any]]] = {}
        for s in snapshots:
            evento = _build_evento(s)
            resultado["eventos"].append(evento)
            if evento["walk_forward_violation"]:
                resultado["walk_forward_violations"] += 1
                continue  # nunca se usa una fila con violación de walk-forward para el agregado
            if evento["outcome_evaluable"]:
                resultado["n_con_outcome_evaluable"] += 1
                key = (s.get("direction"), s.get("timing_deteccion"))
                if key[0] is not None and key[1] is not None:
                    condiciones.setdefault(key, []).append(evento)

        resultado["agregado_por_condicion"] = [
            _aggregate_condition(direction=k[0], timing_deteccion=k[1], eventos=v)
            for k, v in condiciones.items()
        ]
        resultado["ok"] = True
    except Exception as exc:  # el tribunal NUNCA puede tumbar al llamador
        resultado["error"] = f"{type(exc).__name__}: {exc}"
        resultado["ok"] = False
    return resultado
