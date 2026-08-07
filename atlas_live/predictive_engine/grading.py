"""Motor Predictivo -- calificación automática de predicciones (Fase 1.1,
Sprint 5, aprobado 2026-08-06, ver DECISIONES.md).

Cierra el ciclo: cada predicción que `entry_window` ya registró en
`prediction_log` (Sprint 1) se compara, una sola vez y solo cuando el día
de mercado de esa predicción ya terminó (la trayectoria de HOY todavía se
está escribiendo, no es un resultado cerrado), contra el resultado real
observado en el Exit Journal -- reutilizando `derive_movement_start`, la
misma función pura que ya usa `entry_window` para construir la evidencia.
Ningún modelo nuevo, ninguna IA de caja negra: una comparación
determinística de dos marcas de tiempo.

**Criterio de calificación.** Cada recomendación de `entry_window` es, en
el fondo, una afirmación sobre si el movimiento ya había empezado o no en
el momento de predecir:
  - "esperar" / "comprar_ahora" afirman que el movimiento TODAVÍA NO
    había empezado -- correcta si, en los datos reales, el movimiento
    empezó en `predicted_at` o después (o nunca empezó ese día -- también
    consistente con "todavía no empezó").
  - "movimiento_pudo_haber_empezado" afirma que el movimiento YA había
    empezado -- correcta si, en los datos reales, empezó antes de
    `predicted_at`.
Una predicción sin recomendación (`confidence="insuficiente"`) se
califica directamente como "sin_evidencia_suficiente" -- no hay ninguna
afirmación que comparar contra la realidad.

**Append-only respetado.** `prediction_log.grade_prediction()` solo llena
las columnas reservadas desde el Sprint 1 (`graded_at`, `actual_outcome`,
`correct`) -- ningún campo de la predicción original se reescribe.
Protegido contra doble calificación (`AlreadyGradedError`).

**No modifica Mission Control, Memory Engine, Prediction Journal ni Exit
Journal** -- solo lee `exit_journal.get_trajectory()` (ya existente,
solo lectura) y escribe en `prediction_log` (ya existente, Sprint 1).
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from atlas_live.explosive_config import load_config as load_explosive_config
from atlas_live.memory import exit_journal as ej
from atlas_live.memory import market_hours
from atlas_live.predictive_engine import prediction_log as plog

_MOVEMENT_ALREADY_STARTED_OUTCOMES = {"movimiento_pudo_haber_empezado"}
_MOVEMENT_NOT_STARTED_YET_OUTCOMES = {"esperar", "comprar_ahora"}


def _movement_threshold_pct() -> float:
    return load_explosive_config()["gates"]["min_abs_gap_or_change_pct"]


def _movement_already_started(symbol: str, date: str, predicted_at: str) -> bool:
    """`True` si, en la trayectoria real del Exit Journal, el movimiento
    (`derive_movement_start`, mismo umbral que usa Radar Explosivo) ya
    había empezado para cuando se emitió esta predicción. `False` también
    cuando el movimiento nunca ocurrió ese día -- dato real, no un caso
    especial fabricado."""
    trajectory = ej.get_trajectory(symbol, date)
    movement = ej.derive_movement_start(trajectory, _movement_threshold_pct())
    if movement.timestamp is None:
        return False
    return datetime.fromisoformat(movement.timestamp) <= datetime.fromisoformat(predicted_at)


def _grade_one(pred: Dict[str, Any]) -> str:
    """"correcta" | "incorrecta" | "sin_evidencia_suficiente" -- ver
    docstring del módulo para el criterio exacto."""
    if pred["recommendation"] is None:
        return "sin_evidencia_suficiente"

    ya_empezo = _movement_already_started(pred["symbol"], pred["date"], pred["predicted_at"])

    if pred["recommendation"] in _MOVEMENT_ALREADY_STARTED_OUTCOMES:
        return "correcta" if ya_empezo else "incorrecta"
    if pred["recommendation"] in _MOVEMENT_NOT_STARTED_YET_OUTCOMES:
        return "correcta" if not ya_empezo else "incorrecta"
    return "sin_evidencia_suficiente"  # recomendación desconocida -- no se fabrica un veredicto


def grade_pending(now: Optional[datetime] = None, limit: int = 500) -> Dict[str, Any]:
    """Recorre las predicciones sin calificar y califica las que
    pertenecen a un día de mercado YA CERRADO (estrictamente anterior al
    de hoy) -- nunca lanza hacia el llamador, una predicción que falla no
    debe bloquear al resto (mismo principio que
    `live_integration._grade_pending`)."""
    now = now or datetime.now(timezone.utc)
    today = market_hours.market_date(now)
    pendientes = plog.get_ungraded(limit=limit)

    calificadas = 0
    saltadas_dia_no_cerrado = 0
    errores = 0
    for pred in pendientes:
        if pred["date"] >= today:
            saltadas_dia_no_cerrado += 1
            continue
        try:
            resultado = _grade_one(pred)
            plog.grade_prediction(pred["id"], resultado, now.isoformat())
            calificadas += 1
        except Exception:
            errores += 1
            continue

    return {
        "pendientes_totales": len(pendientes),
        "calificadas": calificadas,
        "saltadas_dia_no_cerrado": saltadas_dia_no_cerrado,
        "errores": errores,
    }
