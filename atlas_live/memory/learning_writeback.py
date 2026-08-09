"""Write-back de aprendizaje (F3, 2026-08-09) -- el eslabón que faltaba.

Cierra el circuito del ciclo de aprendizaje de Atlas: cuando una trayectoria
REAL se cierra (Exit Journal `close_exit_summary`, con un `final_return_pct`
observado), este módulo construye UNA observación nueva y la incorpora al
Memory Store, para que la evidencia (tasas base + condiciones confiables)
recalcule y el nivel de aprendizaje pueda cambiar con datos nuevos.

Principios, alineados con la orden del usuario:
  - **Cero mock / cero dato inventado**: la observación se arma con el
    resultado real (`final_return_pct`) y el snapshot de métricas de
    detección que quedó guardado al sellar (F2). Si falta el resultado, NO
    se crea observación (se devuelve None) -- nunca se rellena con un valor.
  - **Solo evidencia suficiente**: una observación entra únicamente cuando la
    trayectoria ya cerró y se pudo clasificar. Una simple lectura de precio o
    un polling NO generan aprendizaje.
  - **Idempotencia**: se apoya en `store.record_observation` (INSERT OR
    IGNORE sobre la clave única symbol/date/checkpoint). Un reintento o un
    reinicio que reprocese el mismo cierre no duplica la observación.
  - **Histórico separado de nuevo**: las observaciones de este flujo llevan
    `source_version="live"`; el seed histórico conserva su `source_version`
    ("v1"). Nunca se cuentan juntas como "nuevas".
  - **Acierto**: se usa exactamente la definición existente del proyecto
    (`classifier` -> categoría; EXPLOSION = acierto en performance_panel),
    no se inventa una nueva.
"""

from typing import Any, Dict, Optional

from atlas_live.memory import classifier, store

# Checkpoint dedicado a la observación de CIERRE de trayectoria. Negativo a
# propósito para no colisionar con los checkpoints del seed/intradía (que son
# "minutos después de la apertura", siempre >= 0). Es un valor FIJO -- no el
# largo real de la ventana -- para que la clave de idempotencia
# (symbol, date, checkpoint) sea estable ante reintentos.
CLOSE_CHECKPOINT_MINUTES = -1

SOURCE_LIVE = "live"


def build_observation(sealed_prediction: Dict[str, Any], exit_summary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Arma los kwargs de `store.record_observation` desde una predicción
    sellada + el resumen de su trayectoria cerrada. Devuelve None si no hay
    resultado real para clasificar (sin inventar nada)."""
    final_return_pct = exit_summary.get("final_return_pct")
    if final_return_pct is None:
        return None

    # Misma señal de elegibilidad que ya usa `_grade_pending`: Radar Explosivo
    # solo calcula `score` para símbolos elegibles, así que score is not None
    # equivale a "era elegible al momento de la predicción".
    eligible = sealed_prediction.get("score") is not None

    category = classifier.classify_observation(
        {"ground_truth_change_pct": final_return_pct, "explosive": {"eligible": eligible}}
    )
    if category is None:  # no debería pasar (ya validamos final_return_pct), pero no se fuerza
        return None

    # Métricas de detección guardadas al sellar (F2). Si una fila vieja no las
    # tiene, se pasa {} -> los campos quedan None (honesto), sin inventar.
    metrics = sealed_prediction.get("metrics_snapshot") or {}

    return {
        "symbol": sealed_prediction["symbol"],
        "date": sealed_prediction["date"],
        "checkpoint_minutes": CLOSE_CHECKPOINT_MINUTES,
        "category": category,
        "metrics": metrics,
        "sector": None,
        "industry": None,
        "market_cap_bucket": None,
        "session": None,
        "source_version": SOURCE_LIVE,
        "market_context": None,
    }


def record_from_closed_trajectory(sealed_prediction: Dict[str, Any], exit_summary: Dict[str, Any]) -> bool:
    """Incorpora la observación al Memory Store. Devuelve True solo si se
    insertó una observación NUEVA (False si no había resultado clasificable o
    si ya existía -- idempotente)."""
    obs = build_observation(sealed_prediction, exit_summary)
    if obs is None:
        return False
    return store.record_observation(**obs)
