"""Registro de auditoría del experimento shadow bidireccional (2026-09-12,
autorizado explícitamente en Plan Mode -- misión "EXPERIMENTO SHADOW DE
APRENDIZAJE BIDIRECCIONAL", continuación de `7686cd2`).

`bidirectional_shadow.compute_bidirectional_decision()` (puro, sin DB) ->
este módulo (persistencia append-only) -> `learning_safety_summary.py`
(agregado, público, sin detalle por ticker).

Mismo patrón EXACTO que `shadow_observation_registry.py` (Hito 3.4, sin
tocar): DB propia (`bidirectional_shadow.db`), split `_connect()`
(lectura-escritura, solo usado por `record_bidirectional_observation`) /
`_ro_connect()` (lectura real, `mode=ro` + `PRAGMA query_only=ON`, nunca
crea el archivo), `_db_exists()` como guard antes de cualquier lectura,
INMUTABLE (ninguna sentencia `UPDATE`/`DELETE` en todo el archivo).

POR QUÉ UNA TABLA NUEVA Y NO SE EXTIENDE `shadow_observation_log`: ese
mecanismo (Hito 3.4) ya está validado y desplegado en producción --
tocarlo arriesgaría ese sistema ya probado (regla 8 de esta misión, "NO
modificar el sistema de aprendizaje ya validado"). El experimento
bidireccional es conceptualmente distinto (puede reflejar un upgrade
`NO_TOCAR`->`VIGILAR`, que `shadow_observation_log` nunca puede
representar porque se calculó a partir del shadow downgrade-only de
`atlas_decision_core.py`) -- se persiste aparte.

`_evaluate_decision_correctness()`/`_outcome_is_evaluable()` están
REPLICADOS (no importados) de `shadow_observation_registry.py` -- mismo
criterio ya documentado ahí: evitar acoplar un Hito nuevo a un símbolo
interno de otro módulo ya cerrado. Ambas copias están cubiertas por sus
propios tests.

TRANSITION-ONLY por `(ticker, market_date)`: compara `(decision_base,
decision_informada, eligibility_state, computed_as_of, computed_at)`
contra la ÚLTIMA fila -- inserta solo si difiere. Solo se escribe cuando
`decision_informada != decision_base` (no-op en caso contrario, mismo
gate conceptual que 3.4 aplica a `shadow_differs`)."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.config.config import db_path

DB_PATH = db_path("bidirectional_shadow.db", default=Path(__file__).parent)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bidirectional_shadow_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    market_date TEXT NOT NULL,
    decision_timestamp TEXT NOT NULL,
    decision_base TEXT NOT NULL,
    decision_informada TEXT,
    upgrade_aplicado INTEGER NOT NULL,
    eligibility_state TEXT,
    direction TEXT,
    timing_deteccion TEXT,
    methodology_version TEXT,
    validation_state TEXT,
    sample_size INTEGER,
    historical_success_pct_20 REAL,
    baseline_pct_20 REAL,
    lift_20 REAL,
    wilson_lower_bound_20_pct REAL,
    wilson_upper_bound_20_pct REAL,
    computed_as_of TEXT,
    computed_at TEXT,
    core_methodology_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bsl_ticker_date ON bidirectional_shadow_log(ticker, market_date);
CREATE INDEX IF NOT EXISTS idx_bsl_market_date ON bidirectional_shadow_log(market_date);
CREATE INDEX IF NOT EXISTS idx_bsl_condition ON bidirectional_shadow_log(direction, timing_deteccion);
"""


def _connect() -> sqlite3.Connection:
    """Lectura-escritura -- USAR SOLO desde `record_bidirectional_observation()`."""
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(_SCHEMA)  # CREATE TABLE/INDEX IF NOT EXISTS -- nunca DROP, nunca recrea
    return conn


def _db_exists() -> bool:
    return Path(DB_PATH).exists()


def _ro_connect() -> sqlite3.Connection:
    """Conexión read-only REAL de SQLite. NUNCA `PRAGMA journal_mode=WAL`,
    NUNCA `executescript(_SCHEMA)`, NUNCA crea el archivo si no existe --
    por eso SIEMPRE se llama detrás de `_db_exists()`, nunca sola."""
    uri = Path(DB_PATH).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    return dict(r)


def _last_row(conn: sqlite3.Connection, ticker: str, market_date: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM bidirectional_shadow_log
           WHERE ticker=? AND market_date=? ORDER BY id DESC LIMIT 1""",
        (ticker, market_date),
    ).fetchone()


def _identity_tuple(row: sqlite3.Row) -> tuple:
    return (row["decision_base"], row["decision_informada"], row["eligibility_state"], row["computed_as_of"], row["computed_at"])


def record_bidirectional_observation(
    ticker: str,
    market_date: str,
    decision_timestamp: str,
    direction: Optional[str],
    timing_deteccion: Optional[str],
    core_methodology_version: str,
    decision_base: str,
    resultado: Dict[str, Any],
    learned_evidence: Optional[Dict[str, Any]] = None,
    eligibility_state: Optional[str] = None,
) -> bool:
    """Persiste UN resultado de
    `bidirectional_shadow.compute_bidirectional_decision()` -- no-op
    (devuelve `False`, no escribe nada) si `resultado["decision_informada"]
    == decision_base` (nada que observar). Si difiere: transition-only,
    compara `(decision_base, decision_informada, eligibility_state,
    computed_as_of, computed_at)` contra la ÚLTIMA fila para `(ticker,
    market_date)`, inserta SOLO si difiere.

    `learned_evidence` es el MISMO dict ya calculado por el llamador
    (nunca recalculado acá) -- se usa solo para copiar los campos de
    auditoría."""
    decision_informada = resultado.get("decision_informada")
    if decision_informada == decision_base:
        return False

    le = learned_evidence or {}
    computed_at = le.get("computed_at")
    nueva_tupla = (decision_base, decision_informada, eligibility_state, le.get("computed_as_of"), computed_at)

    with _connect() as conn:
        anterior = _last_row(conn, ticker, market_date)
        if anterior is not None and _identity_tuple(anterior) == nueva_tupla:
            return False  # misma observación -- request repetido, no duplica

        now = _now()
        conn.execute(
            """INSERT INTO bidirectional_shadow_log
               (ticker, market_date, decision_timestamp, decision_base, decision_informada,
                upgrade_aplicado, eligibility_state, direction, timing_deteccion,
                methodology_version, validation_state, sample_size, historical_success_pct_20,
                baseline_pct_20, lift_20, wilson_lower_bound_20_pct, wilson_upper_bound_20_pct,
                computed_as_of, computed_at, core_methodology_version, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ticker, market_date, decision_timestamp, decision_base, decision_informada,
                int(bool(resultado.get("upgrade_aplicado"))), eligibility_state, direction, timing_deteccion,
                le.get("methodology_version"), le.get("validation_state"), le.get("sample_size"),
                le.get("historical_success_pct_20"), le.get("baseline_pct_20"), le.get("lift_20"),
                le.get("wilson_lower_bound_20_pct"), le.get("wilson_upper_bound_20_pct"),
                le.get("computed_as_of"), computed_at, core_methodology_version, now,
            ),
        )
        conn.commit()
        return True


def get_observations_for(ticker: str, market_date: str) -> List[Dict[str, Any]]:
    """Solo lectura REAL -- todas las transiciones registradas ese día
    para esa candidata, en orden cronológico. `[]` si la DB todavía no
    existe, sin crear nada."""
    if not _db_exists():
        return []
    with _ro_connect() as conn:
        rows = conn.execute(
            """SELECT * FROM bidirectional_shadow_log
               WHERE ticker=? AND market_date=? ORDER BY id ASC""",
            (ticker, market_date),
        ).fetchall()
    return [_row(r) for r in rows]


def list_bidirectional_observations(market_date: Optional[str] = None, limit: int = 5000) -> List[Dict[str, Any]]:
    """Solo lectura REAL, paginado con un límite explícito. `[]` si la DB
    todavía no existe, sin abrir ni crear nada."""
    if not _db_exists():
        return []
    query = "SELECT * FROM bidirectional_shadow_log WHERE 1=1"
    params: List[Any] = []
    if market_date is not None:
        query += " AND market_date=?"
        params.append(market_date)
    query += " ORDER BY id ASC LIMIT ?"
    params.append(limit)
    with _ro_connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row(r) for r in rows]


# --- reporte offline -- BASE vs INFORMADA (bidireccional) contra el outcome real ---

_POSITIVE_STATES = ("OPORTUNIDAD_PRIORITARIA", "VIGILAR")
_NEGATIVE_STATES = ("NO_TOCAR",)
_GOOD_OUTCOME_CATEGORIES = ("mejor_oportunidad", "buena_oportunidad")
_BAD_OUTCOME_CATEGORY = "falsa_senal"

NOTA_ALCANCE = (
    "Reporte offline de solo lectura -- experimento shadow BIDIRECCIONAL "
    "(NO_TOCAR puede elevarse a VIGILAR en esta rama, exclusivamente). "
    "mechanism_state y apply_recalibration no aparecen en ningun punto de "
    "este modulo -- este experimento nunca cambia una decision real."
)


def _evaluate_decision_correctness(decision_value: Optional[str], category: Optional[str]) -> str:
    """Réplica deliberada de `shadow_observation_registry._evaluate_decision_correctness()`
    (misma lógica exacta que `decision_outcome_tribunal.py`) -- ver
    docstring del módulo para por qué se replica en vez de importar."""
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
    return "AMBIGUO"


def _outcome_is_evaluable(outcome: Optional[Dict[str, Any]]) -> bool:
    if not outcome:
        return False
    return bool(outcome.get("is_final")) and bool(outcome.get("confiable_para_aprendizaje"))


def _veredictos_contra_outcome(reg, ticker: str, market_date: str, decision_base: str, decision_informada: Optional[str]) -> Dict[str, Any]:
    outcome = reg.get_outcome(ticker, market_date)
    evaluable = _outcome_is_evaluable(outcome)
    category = outcome.get("category") if evaluable else None
    if evaluable:
        baseline_veredicto = _evaluate_decision_correctness(decision_base, category)
        informada_veredicto = (
            _evaluate_decision_correctness(decision_informada, category) if decision_informada else "SIN_INFORMADA"
        )
    else:
        baseline_veredicto = "PENDIENTE"
        informada_veredicto = "PENDIENTE"
    return {
        "outcome_evaluable": evaluable,
        "decision_base_veredicto": baseline_veredicto,
        "decision_informada_veredicto": informada_veredicto,
    }


def _latest_snapshots_deduped(dkr, market_date: Optional[str], limit: int) -> List[Dict[str, Any]]:
    """FIX 2026-09-12 (misión "HACER QUE EL APRENDIZAJE REALMENTE
    FUNCIONE", autorizado explícitamente en Plan Mode) -- reemplaza a
    `dkr.list_snapshots(market_date, limit)` como fuente de este universo.

    Bug real confirmado con datos de producción: `list_snapshots()` hace
    `ORDER BY id ASC LIMIT ?` -- con la tabla real en 357.805 filas y
    `limit=5000` (el default), un caller que no pagina explícitamente
    termina viendo SOLO las 5.000 filas MÁS ANTIGUAS (1,4% del total,
    las primeras horas tras el deploy de Hito 3.0, antes de que casi
    ninguna condición hubiera madurado) -- nunca el resto de la historia,
    y la ventana ciega CRECE cada día. Además, esa tabla es
    transition-only POR FILA (cada barrido que recalcula la evidencia de
    una candidata agrega una fila), no por candidata-día -- 357.805 filas
    corresponden a solo 9.778 pares (ticker, market_date) reales
    (~36 filas por candidata-día en promedio); contar cada transición
    intradía como un caso independiente sobrecuenta y viola independencia
    estadística.

    Esta función trae el ÚLTIMO snapshot real (mayor `id`) de cada
    `(ticker, market_date)` -- el estado en el que esa candidata quedó
    ese día, nunca un estado intermedio ya superado por el barrido
    siguiente -- sobre el 100% de la historia (sin `ORDER BY id ASC
    LIMIT` truncando desde el principio). No modifica
    `decision_knowledge_registry.py` -- reutiliza sus propios
    `_db_exists()`/`_ro_connect()` (solo lectura REAL, `mode=ro`, nunca
    crea el archivo si no existe -- mismo guard que ya usa
    `list_snapshots()`), sin escribir nada nuevo ahí. `limit` se aplica
    DESPUÉS del dedup (sobre pares candidata-día, no sobre filas crudas)
    -- con 9.778 pares reales hoy, el default de 5.000 ya no trunca
    silenciosamente el caso común; documentado igual como límite
    explícito, nunca "sin acotar"."""
    if not dkr._db_exists():
        return []

    query = """
        SELECT s.* FROM decision_knowledge_snapshot s
        JOIN (
            SELECT ticker, market_date, MAX(id) AS max_id
            FROM decision_knowledge_snapshot
            {where}
            GROUP BY ticker, market_date
        ) m ON s.id = m.max_id
        ORDER BY s.id ASC LIMIT ?
    """
    if market_date is not None:
        query = query.format(where="WHERE market_date = ?")
        params: List[Any] = [market_date, limit]
    else:
        query = query.format(where="")
        params = [limit]

    with dkr._ro_connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def _construir_universo_bidireccional(market_date: Optional[str], limit: int) -> Dict[str, Any]:
    """Reconstruye, de forma READ-ONLY, el universo completo -- no solo el
    subconjunto que `bidirectional_shadow_log` ya persistió (acotado a
    `decision_informada != decision_base`) -- para poder distinguir "sin
    conocimiento elegible" de "conocimiento elegible pero sin divergencia".

    Fuente: `decision_knowledge_registry.list_snapshots()` (Hito 3.0, ya
    existente, sin modificar -- misma fuente que ya usa
    `shadow_observation_registry._construir_universo_abc()`). Para cada
    fila se reconstruye `learned_evidence` desde sus propias columnas, se
    clasifica con `knowledge_eligibility.classify_eligibility()` (Fase
    3.3, importada y reutilizada tal cual) y se calcula
    `bidirectional_shadow.compute_bidirectional_decision()` (este Hito).

    Tres grupos, mutuamente excluyentes:
    - A: `eligibility_state != "ELEGIBLE"`.
    - B: `eligibility_state == "ELEGIBLE"` y `decision_informada ==
      decision_base` (conocimiento elegible, sin efecto -- incluye tanto
      "no era degradable/elevable" como "era NO_TOCAR pero sin ventaja").
    - C: `eligibility_state == "ELEGIBLE"` y `decision_informada !=
      decision_base` (downgrade O upgrade -- el universo bidireccional
      completo, superset del `C_elegible_con_divergencia` de 3.4, que
      solo veía el downgrade)."""
    from atlas_live.core import bidirectional_shadow as bidi
    from atlas_live.core import decision_knowledge_registry as dkr
    from atlas_live.core import knowledge_eligibility as ke
    from atlas_live.radar import candidate_registry as reg

    snapshots = _latest_snapshots_deduped(dkr, market_date, limit)

    grupos: Dict[str, List[Dict[str, Any]]] = {"A": [], "B": [], "C": []}
    for s in snapshots:
        le_reconstruido = {
            "available": bool(s.get("knowledge_available")),
            "reason": s.get("knowledge_reason"),
            "validation_state": s.get("validation_state"),
            "sample_size": s.get("sample_size"),
            "historical_success_pct_20": s.get("historical_success_pct_20"),
            "wilson_lower_bound_20_pct": s.get("wilson_lower_bound_20_pct"),
            "wilson_upper_bound_20_pct": s.get("wilson_upper_bound_20_pct"),
            "baseline_pct_20": s.get("baseline_pct_20"),
            "lift_20": s.get("lift_20"),
            "computed_as_of": s.get("computed_as_of"),
            "computed_at": s.get("computed_at"),
            "methodology_version": s.get("methodology_version"),
        }
        elegibilidad = ke.classify_eligibility(le_reconstruido, s["market_date"])
        resultado = bidi.compute_bidirectional_decision(
            decision_base=s["decision"],
            decision_shadow_downgrade=s.get("decision_shadow"),
            eligibility_state=elegibilidad["eligibility_state"],
            learned_evidence=le_reconstruido,
        )
        decision_informada = resultado["decision_informada"]

        if elegibilidad["eligibility_state"] != "ELEGIBLE":
            grupo = "A"
        elif decision_informada == s["decision"]:
            grupo = "B"
        else:
            grupo = "C"

        veredictos = _veredictos_contra_outcome(reg, s["ticker"], s["market_date"], s["decision"], decision_informada)
        grupos[grupo].append({
            "ticker": s["ticker"],
            "market_date": s["market_date"],
            "eligibility_state": elegibilidad["eligibility_state"],
            "decision_base": s["decision"],
            "decision_informada": decision_informada,
            "upgrade_aplicado": bool(resultado.get("upgrade_aplicado")),
            **veredictos,
        })

    return {
        "A_sin_elegible": {"n_eventos": len(grupos["A"]), "eventos": grupos["A"]},
        "B_elegible_sin_divergencia": {"n_eventos": len(grupos["B"]), "eventos": grupos["B"]},
        "C_elegible_con_divergencia": {"n_eventos": len(grupos["C"]), "eventos": grupos["C"]},
    }


def full_bidirectional_report(market_date: Optional[str] = None, limit: int = 200_000) -> Dict[str, Any]:
    """Orquesta el reporte del experimento bidireccional: reconstruye el
    universo completo A/B/C (`_construir_universo_bidireccional()`),
    agrega conteos, y expone `universo_conocimiento` en la MISMA forma
    que ya consume `base_vs_informed_verdict.build_verdict()`/
    `build_bidirectional_verdict()` -- reutilizable sin adaptar nada.

    `limit` default subido de 5.000 a 200.000 (FIX 2026-09-12, misión
    "HACER QUE EL APRENDIZAJE REALMENTE FUNCIONE"): el universo se
    calcula sobre pares (ticker, market_date) DEDUPLICADOS
    (`_latest_snapshots_deduped()`), no sobre filas crudas -- hoy son
    9.778 pares reales, muy por debajo de este límite; sigue siendo un
    límite explícito y documentado, nunca "sin acotar".

    Nunca lanza -- cualquier excepción queda atrapada."""
    resultado: Dict[str, Any] = {
        "generated_at": _now(),
        "ok": False,
        "nota": NOTA_ALCANCE,
        "n_observaciones_persistidas": 0,
        "universo_conocimiento": {},
        "error": None,
    }
    try:
        resultado["n_observaciones_persistidas"] = len(list_bidirectional_observations(market_date=market_date, limit=limit))
        resultado["universo_conocimiento"] = _construir_universo_bidireccional(market_date, limit)
        resultado["ok"] = True
    except Exception as exc:  # el reporte nunca puede tumbar al llamador
        resultado["error"] = f"{type(exc).__name__}: {exc}"
        resultado["ok"] = False
    return resultado
