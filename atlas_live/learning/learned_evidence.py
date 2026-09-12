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

Matching por bucket real (FIX 2026-09-12, misión "HACER QUE EL APRENDIZAJE
REALMENTE FUNCIONE", autorizado explícitamente en Plan Mode): hasta esta
fecha, esta función SIEMPRE consultaba el bucket agregado
`"poblacion_total"`, ignorando `volatility_14d_pct` por completo -- código
muerto confirmado (el parámetro llegaba con el dato real de la candidata
en cada llamada de producción, `server.py`, y nunca se usaba). La causa
declarada en ese momento era real: `live_experience_knowledge` no
persistía los CORTES (lo/hi) de tercil, solo las estadísticas de cada
bucket -- sin esos cortes no había forma de saber a qué tercil pertenecía
la volatilidad de una candidata sin inventarlo. Se corrigió agregando 2
columnas aditivas (`feature_cut_low`/`feature_cut_high`, ver
`live_experience_knowledge.py`) -- ahora, si `volatility_14d_pct` viene
poblado y el grupo tiene cortes calculados, se consulta el bucket real
(`alto`/`medio`/`bajo`); si no hay cortes (grupo con muestra insuficiente
para un corte, mismo piso ya usado en `experiments._tercile_cuts()`) o no
hay dato de volatilidad, se degrada con gracia al agregado
`poblacion_total` -- EXACTAMENTE el comportamiento anterior, nunca un
fallo. Verificado con datos reales de producción (2026-09-12): para las 4
condiciones `VALIDACION_ROBUSTA` de hoy, ningún tercil muestra ventaja
sobre el baseline tampoco -- este fix no cambia ningún veredicto hoy, pero
deja de ignorar evidencia real ya calculada.

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

# Bucket agregado -- siempre se consulta primero (fuente de los cortes de
# tercil y fallback universal). Ver docstring del módulo.
BUCKET_CONSULTA = "poblacion_total"


def _bucket_real(volatility_14d_pct: Optional[float], cut_low: Optional[float], cut_high: Optional[float]) -> Optional[str]:
    """Misma semántica exacta que `experiments._bucket_of_row()` para una
    sola feature: `None` si falta el dato o los cortes (degradar a
    `poblacion_total`, nunca inventar)."""
    if volatility_14d_pct is None or cut_low is None or cut_high is None:
        return None
    if volatility_14d_pct <= cut_low:
        return "bajo"
    if volatility_14d_pct > cut_high:
        return "alto"
    return "medio"


def get_learned_evidence(
    direction: Optional[str],
    timing_deteccion: Optional[str],
    market_date: str,
    volatility_14d_pct: Optional[float] = None,
    methodology_version: str = lek.METHODOLOGY_VERSION,
) -> Dict[str, Any]:
    """Evidencia histórica REAL de la experiencia propia de Atlas para la
    condición `(direction, timing_deteccion)` de una candidata detectada en
    `market_date` -- nunca inventa nada: si la condición no está
    disponible, o no hay conocimiento para ella, devuelve `available=False`
    con un `reason` explícito, nunca una evidencia fabricada.

    Consulta primero `poblacion_total` (siempre necesario: es la fuente de
    los cortes de tercil de ESE cálculo). Si `volatility_14d_pct` está
    disponible y esa fila trae cortes reales, se re-consulta el bucket
    específico (`alto`/`medio`/`bajo`) DENTRO DEL MISMO `computed_at`
    (mismo cálculo, nunca mezcla snapshots de días distintos) -- si esa
    fila específica no existe (no debería pasar, las 4 filas de un grupo
    se insertan juntas, pero se verifica en vez de asumir), se devuelve la
    fila agregada ya obtenida, igual que el comportamiento previo."""
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

            if row is not None:
                bucket_real = _bucket_real(volatility_14d_pct, row["feature_cut_low"], row["feature_cut_high"])
                if bucket_real is not None:
                    fila_bucket = conn.execute(
                        """SELECT * FROM live_experience_knowledge
                           WHERE direction = ? AND timing_deteccion = ? AND bucket = ?
                                 AND methodology_version = ? AND computed_at = ?""",
                        (direction, timing_deteccion, bucket_real, methodology_version, row["computed_at"]),
                    ).fetchone()
                    if fila_bucket is not None:
                        row = fila_bucket
    except Exception as exc:  # la capa de conocimiento nunca puede tumbar al llamador
        return {"available": False, "reason": f"ERROR_CONSULTA: {type(exc).__name__}"}

    if row is None:
        return {"available": False, "reason": "SIN_CONOCIMIENTO_PARA_ESTA_CONDICION"}

    d = dict(row)
    return {
        "available": True,
        "bucket": d["bucket"],
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
