"""Fuente v2 de `predicted_pct` -- historial PROPIO de Atlas, agrupado por
`alert_stage` (2026-09-13, autorizado explícitamente tras validación fuera
de muestra en 3 cortes reales: 56,6%-60,9% de acierto considerando
EXCLUSIVAMENTE grupos con n>=500 -- ver informe de la sesión).

Reemplaza, cuando `ATLAS_MAGNITUD_PREDICTION_SOURCE="v2_own_experience"` Y
el grupo `(direction, alert_stage)` tiene evidencia robusta, la fuente
externa (`historical_scoring.py`, Base Histórica de ~3 meses no
condicionada a lo que el radar realmente detecta) por la propia
experiencia de Atlas (`live_experience_knowledge`,
`methodology_version="v2_direction_alert_stage"`).

NO modifica `historical_scoring.py`, `live_experience_scoring.py` ni
`live_experience_knowledge.py` -- lee directamente de
`live_experience_knowledge.DB_PATH` (constante pública de ese módulo, sin
depender de ninguna función privada) para poder aplicar un filtro
walk-forward ESTRICTO (`computed_as_of < market_date`) explícito en la
propia consulta, en vez de reutilizar `latest_knowledge_as_of()` (que usa
`<=`) -- más conservador y fiel a la metodología exacta ya validada con
los 3 cortes reales (que siempre usó `computed_as_of < market_date de
prueba`, nunca igualdad).

Cuando no hay evidencia suficiente (grupo inexistente o `n` por debajo del
umbral de madurez), `resolve_predicted_pct_v2()` devuelve `None`
explícito -- el llamador (`candidate_tracker._tag_magnitud_prediction`)
cae al fallback externo, exactamente el comportamiento de hoy, sin
ningún cambio.

Criterio de madurez (verificado con datos reales, no inventado): se probó
explícitamente incluir grupos de n=100-499 -- su acierto agregado no era
malo, pero en 2 de los 3 cortes reales dependía de UN SOLO grupo
(evidencia demasiado angosta para producción). Por eso el umbral final es
`UMBRAL_MADUREZ_N = META_MUESTRA_MINIMA` (500, el mismo piso oficial ya
usado en Precisión de Magnitud / Hito 3.3 / Hito 3.6) -- nunca un número
nuevo sin respaldo.
"""

import sqlite3
from typing import Any, Dict, Optional

from atlas_live.learning import live_experience_knowledge as lek
from atlas_live.radar.candidate_registry import META_MUESTRA_MINIMA

FUENTE_V2 = "v2_own_experience"
FUENTE_EXTERNA = "external"

# Mismo piso oficial ya usado en todo el proyecto (Precisión de Magnitud,
# Hito 3.3, Hito 3.6) -- verificado con los 3 cortes reales antes de
# implementar, ver docstring del módulo.
UMBRAL_MADUREZ_N = META_MUESTRA_MINIMA

# Única estratificación validada fuera de muestra -- los 3 cortes reales
# siempre usaron el agregado sin tercil de feature, nunca alto/medio/bajo.
BUCKET_CONSULTA = "poblacion_total"


def get_latest_v2_snapshot(
    direction: str, alert_stage: str, market_date: str,
    methodology_version: str = lek.METHODOLOGY_VERSION_V2,
) -> Optional[Dict[str, Any]]:
    """Snapshot v2 más reciente con `computed_as_of` ESTRICTAMENTE anterior
    a `market_date` -- walk-forward explícito en la propia consulta.
    `None` si no existe ninguna fila para esa condición -- nunca se
    inventa evidencia."""
    conn = sqlite3.connect(str(lek.DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """SELECT * FROM live_experience_knowledge
               WHERE methodology_version=? AND direction=? AND timing_deteccion=?
                     AND bucket=? AND computed_as_of < ?
               ORDER BY computed_as_of DESC, computed_at DESC LIMIT 1""",
            (methodology_version, direction, alert_stage, BUCKET_CONSULTA, market_date),
        ).fetchone()
    except sqlite3.OperationalError:
        # La tabla/archivo todavía no existe (ej. antes de la primera
        # corrida real del hilo de conocimiento v2) -- sin evidencia,
        # nunca un error: mismo criterio "nunca se inventa evidencia".
        row = None
    finally:
        conn.close()
    return dict(row) if row is not None else None


def resolve_predicted_pct_v2(direction: str, alert_stage: str, market_date: str) -> Optional[Dict[str, Any]]:
    """Punto de entrada real para `candidate_tracker.py`. Devuelve
    `{"predicted_pct", "muestra_n", "bucket", "fuente", "computed_as_of"}`
    SOLO si hay un snapshot walk-forward-seguro con `n>=UMBRAL_MADUREZ_N` y
    mediana disponible -- en cualquier otro caso (sin snapshot, `n` por
    debajo del umbral, mediana ausente) devuelve `None` explícito, nunca un
    valor a medias. El llamador debe caer al fallback externo cuando esto
    devuelve `None` -- este módulo nunca decide "no hay predicción", solo
    "no hay evidencia propia suficiente todavía"."""
    snap = get_latest_v2_snapshot(direction, alert_stage, market_date)
    if snap is None:
        return None
    n = snap.get("n_evaluables")
    mediana = snap.get("mediana_max_advance_pct")
    if n is None or n < UMBRAL_MADUREZ_N or mediana is None:
        return None
    return {
        "predicted_pct": mediana,
        "muestra_n": n,
        "bucket": snap.get("bucket"),
        "fuente": FUENTE_V2,
        "computed_as_of": snap.get("computed_as_of"),
    }
