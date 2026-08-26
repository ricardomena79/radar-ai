"""Lectura CONTROLADA del conocimiento propio de Atlas para una candidata EN
VIVO (2026-08-25, Fase 4/5 del circuito de aprendizaje, autorizado
explícitamente).

    CONOCIMIENTO HISTÓRICO (live_experience_knowledge.db, Fase 2)
            ↓
    LECTURA DEL CONOCIMIENTO RELEVANTE (este módulo)
            ↓
    EVIDENCIA PARA LA EVALUACIÓN (`learned_evidence`, dict adjunto)
            ↓
    [DECISIÓN -- NO EN ESTA FASE, NO CONECTADO]

Puramente observacional: `get_learned_evidence()` SOLO lee y empaqueta --
nunca escribe, nunca decide, nunca genera una señal de compra/venta. Ninguna
de las 7 puertas, `priority_classifier.py`, el score ni el ranking importan
ni leen nada de este módulo (confirmado por test, ver
`test_learned_evidence.py::test_O_...`/`test_J_...` de este mismo paquete).

Matching: SOLO el bucket `"poblacion_total"` (nunca `"alto"/"medio"/"bajo"`)
-- limitación deliberada y declarada, no un descuido: `live_experience_knowledge`
persiste las ESTADÍSTICAS de cada tercil, pero no los CORTES (lo/hi) que
los definieron en el momento del cálculo -- sin esos cortes, no hay forma
de determinar a qué tercil pertenecería la volatilidad de una candidata de
HOY sin inventarlo. `volatility_14d_pct` se acepta como parámetro (para
cumplir el contrato pedido, "solo condiciones que existan de verdad") pero
todavía no participa en la selección del bucket -- persistir los cortes es
un cambio de esquema que le corresponde a una fase posterior, con su
propia autorización, no a esta.

Anti-look-ahead reforzado (2026-08-25, pedido explícito -- "no aceptes
simplemente computed_as_of <= date sin analizar el problema temporal"):
el filtro acá es ESTRICTO, `computed_as_of < market_date` (nunca `<=`).
Motivo: el disparo automático (Fase 3) solo genera `computed_as_of=D`
DESPUÉS del cierre de D, así que para D nunca existe a tiempo -- pero el
endpoint MANUAL de recálculo (`/api/admin/generate-experience-knowledge`)
permite pasar `as_of_date=D` en CUALQUIER momento del día D, incluso a
media sesión. Con `<=`, dos candidatas detectadas el MISMO día D podrían
recibir evidencia distinta según si un recálculo manual ya corrió esa
mañana -- no es leakage de información futura en el sentido estricto (los
datos usados siguen siendo `market_date < D`), pero SÍ es una
inconsistencia intradía evitable. El `<` estricto la elimina por completo:
conocimiento marcado `computed_as_of=D` solo puede usarse desde D+1 en
adelante, nunca el mismo día D, sin importar a qué hora se haya calculado.
Esto es deliberadamente MÁS estricto que `live_experience_knowledge.
get_knowledge_for()`/`latest_knowledge_as_of()` (Fase 2, que usan `<=`
por diseño, para sus propios tests de verificación) -- este módulo no
modifica esas funciones, define su propia consulta más conservadora."""

from typing import Any, Dict, Optional

from atlas_live.learning import live_experience_knowledge as lek

DIRECTIONS_VALIDAS = ("ALCISTA", "BAJISTA", "NEUTRAL")

# Único bucket consultado en esta fase -- ver docstring del módulo.
BUCKET_CONSULTA = "poblacion_total"


def get_learned_evidence(
    direction: Optional[str],
    timing_deteccion: Optional[str],
    market_date: str,
    volatility_14d_pct: Optional[float] = None,  # aceptado, no usado todavía (ver docstring)
    methodology_version: str = lek.METHODOLOGY_VERSION,
) -> Dict[str, Any]:
    """Evidencia histórica REAL de la experiencia propia de Atlas para la
    condición `(direction, timing_deteccion)` de una candidata detectada en
    `market_date` -- nunca inventa nada: si la condición no está
    disponible, o no hay conocimiento para ella, devuelve `available=False`
    con un `reason` explícito, nunca una evidencia fabricada."""
    if direction not in DIRECTIONS_VALIDAS or not timing_deteccion:
        return {"available": False, "reason": "CONDICION_NO_DISPONIBLE"}

    try:
        with lek._connect() as conn:
            row = conn.execute(
                """SELECT * FROM live_experience_knowledge
                   WHERE direction = ? AND timing_deteccion = ? AND bucket = ?
                         AND methodology_version = ? AND computed_as_of < ?
                   ORDER BY computed_at DESC LIMIT 1""",
                (direction, timing_deteccion, BUCKET_CONSULTA, methodology_version, market_date),
            ).fetchone()
    except Exception as exc:  # la capa de conocimiento nunca puede tumbar al llamador
        return {"available": False, "reason": f"ERROR_CONSULTA: {type(exc).__name__}"}

    if row is None:
        return {"available": False, "reason": "SIN_CONOCIMIENTO_PARA_ESTA_CONDICION"}

    d = dict(row)
    return {
        "available": True,
        "validation_state": d["validation_state"],
        "sample_size": d["n_evaluables"],
        "historical_success_pct_20": d["pct_20"],
        "baseline_pct_20": d["baseline_pct_20"],
        "lift_20": d["lift_20"],
        "wilson_lower_bound_20_pct": d["wilson_lower_bound_20_pct"],
        "wilson_upper_bound_20_pct": d["wilson_upper_bound_20_pct"],
        "computed_as_of": d["computed_as_of"],
        "computed_at": d["computed_at"],
        "methodology_version": d["methodology_version"],
    }
