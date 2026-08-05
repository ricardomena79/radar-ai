"""Clasificador de Resultado -- Entregable Nº2 del Memory Engine.

Función pura: dada una fila ya existente de `historical_scan.py` (snapshot
a los `snapshot_minutes_after_open` minutos + `ground_truth_change_pct` de
cierre -- datos que YA se descargan y guardan hoy en
`atlas_live/backtest/results_v1/` y `results_v2/`), asigna una de las 5
categorías de resultado real: EXPLOSION, FALSE_BREAKOUT, LOSER, WEAK,
NORMAL. No descarga nada nuevo, no modifica ningún archivo de resultados
existente, no depende del Memory Store (Entregable 1) ni de ningún otro
entregable -- es una función aislada, tal como pide el plan de
MEMORY_ENGINE.md.

Hallazgo de diseño (documentado en MEMORY_ENGINE.md): FALSE_BREAKOUT ya es
clasificable con los datos que existen hoy -- "parecía explosiva en el
snapshot temprano (`explosive.eligible=True`, pasó los 6 gates de
Radar Explosivo) pero no lo sostuvo hacia el cierre" no requiere ningún
checkpoint intermedio nuevo (eso queda para el Entregable 8, como
refinamiento, no como bloqueo).

Reglas de clasificación, evaluadas EN ORDEN (la primera que aplica gana --
sin ambigüedad, sin superposición silenciosa):

  1. EXPLOSION      -- ground_truth_change_pct >= explosion_threshold_pct.
                        Se evalúa primero y sin importar `eligible`: el
                        Memory Engine debe poder estudiar tanto las
                        explosiones que Radar Explosivo detectó como las
                        que no (esas últimas son, literalmente, los falsos
                        negativos que ya audita `validation_report.py`).
  2. FALSE_BREAKOUT  -- eligible=True en el snapshot (pasó los 6 gates,
                        parecía un candidato real) Y
                        ground_truth_change_pct < false_breakout_ceiling_pct.
                        Incluye deliberadamente el caso de una señal
                        temprana que termina en pérdida fuerte -- es el
                        caso más informativo para el Memory Engine, más
                        específico que LOSER, así que tiene prioridad
                        sobre él.
  3. LOSER           -- ground_truth_change_pct <= loser_threshold_pct
                        (y no fue capturada por la regla 2, es decir, no
                        tuvo señal temprana).
  4. WEAK            -- abs(ground_truth_change_pct) < weak_ceiling_pct
                        (movimiento insignificante en cualquier dirección,
                        sin señal temprana).
  5. NORMAL          -- todo lo demás: se movió algo, sin ser explosiva,
                        perdedora fuerte, ni una señal temprana fallida.

Límite de alcance conocido, documentado sin ocultarlo (no es tarea de este
entregable): esta regla NO filtra los artefactos de datos ya identificados
en Validación 1 (splits no ajustados, tickers ilíquidos con variaciones de
+2.000% a +9.500% en un día) -- esos casos clasifican hoy como EXPLOSION
por la regla 1, tal como se reporta explícitamente en las pruebas de este
entregable. Filtrar esos artefactos es responsabilidad de una etapa de
carga de datos (Entregable 3, backfill), no de la regla de clasificación
en sí.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

CATEGORIES = ("EXPLOSION", "FALSE_BREAKOUT", "LOSER", "WEAK", "NORMAL")

_CONFIG_PATH = Path(__file__).parent / "classifier_config.json"

# Valores por defecto, idénticos a los del JSON de fábrica -- mismo patrón
# que `explosive_config.py`: si el archivo falta o está corrupto, el
# clasificador no se cae, usa estos valores.
_DEFAULTS = {
    "explosion_threshold_pct": 10.0,
    "false_breakout_ceiling_pct": 5.0,
    "loser_threshold_pct": -5.0,
    "weak_ceiling_pct": 2.0,
}


def load_config() -> Dict[str, float]:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_DEFAULTS)
    return {key: raw.get(key, default) for key, default in _DEFAULTS.items()}


def classify_observation(row: Dict[str, Any], config: Optional[Dict[str, float]] = None) -> Optional[str]:
    """Clasifica una fila de `historical_scan.py` (tal como viene en
    `results_v1/*.json` / `results_v2/*.json`) en una de las 5 categorías.

    Devuelve `None` si la fila no tiene un resultado real medible (dato
    faltante) -- nunca inventa una categoría sobre datos incompletos; el
    llamador debe tratar `None` como "descartar", no como "NORMAL"."""
    cfg = config or load_config()

    change_pct = row.get("ground_truth_change_pct")
    if change_pct is None:
        return None

    explosive = row.get("explosive") or {}
    eligible = bool(explosive.get("eligible"))

    if change_pct >= cfg["explosion_threshold_pct"]:
        return "EXPLOSION"

    if eligible and change_pct < cfg["false_breakout_ceiling_pct"]:
        return "FALSE_BREAKOUT"

    if change_pct <= cfg["loser_threshold_pct"]:
        return "LOSER"

    if abs(change_pct) < cfg["weak_ceiling_pct"]:
        return "WEAK"

    return "NORMAL"
