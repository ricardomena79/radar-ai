"""Registro de auditoría de observación shadow (Hito 3, Fase 3.4,
2026-09-03, autorizado explícitamente en Plan Mode).

`shadow_observation.classify_shadow_observation()` (puro, sin DB) -> este
módulo (persistencia append-only) -> `GET /api/admin/shadow-observation-report`
(solo lectura).

Mismo patrón EXACTO que `decision_knowledge_registry.py` (Hito 3.0/3.1) y
`knowledge_eligibility_registry.py` (Hito 3.3, ambos sin tocar): DB propia
(`shadow_observation.db`), split `_connect()` (lectura-escritura, solo
usado por `record_shadow_observation`) / `_ro_connect()` (lectura real,
`mode=ro` + `PRAGMA query_only=ON`, nunca crea el archivo), `_db_exists()`
como guard antes de cualquier lectura, INMUTABLE (ninguna sentencia
`UPDATE`/`DELETE` en todo el archivo).

POR QUÉ UNA TABLA NUEVA Y NO SE EXTIENDE `shadow_decision_log`
(`atlas_live/radar/candidate_registry.py`, preexistente, sin tocar): esa
tabla tiene `UNIQUE(ticker, market_date)` + `INSERT OR IGNORE` --
write-once por día, ya consumida en producción por
`shadow_validation_report()`. Cambiarle la semántica a transition-only
(necesario para representar más de una transición por día) sería una
modificación de comportamiento a una tabla ya en producción con un
consumidor existente -- más riesgoso que una tabla nueva con el patrón ya
probado dos veces en este Hito.

TRANSITION-ONLY por `(ticker, market_date)`: compara
`(decision, decision_shadow, eligibility_state, computed_as_of,
computed_at)` contra la ÚLTIMA fila -- inserta solo si difiere. Solo se
escribe cuando `observation["observado"]` es `True` (gate ya aplicado por
`classify_shadow_observation()`: `shadow_differs=True`, que a su vez solo
ocurre cuando `atlas_decision_core.py` ya exigió internamente
`validation_state=="VALIDACION_ROBUSTA"` -- el subconjunto más chico
posible del universo de candidatas, no "todas las candidatas todos los
días")."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.config.config import db_path

DB_PATH = db_path("shadow_observation.db", default=Path(__file__).parent)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_observation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    market_date TEXT NOT NULL,
    decision_timestamp TEXT NOT NULL,
    decision TEXT NOT NULL,
    decision_shadow TEXT,
    eligibility_state TEXT NOT NULL,
    walk_forward_violation INTEGER NOT NULL,
    direction TEXT,
    timing_deteccion TEXT,
    methodology_version TEXT,
    validation_state TEXT,
    sample_size INTEGER,
    wilson_lower_bound_20_pct REAL,
    wilson_upper_bound_20_pct REAL,
    baseline_pct_20 REAL,
    lift_20 REAL,
    computed_as_of TEXT,
    computed_at TEXT,
    core_methodology_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sol_ticker_date ON shadow_observation_log(ticker, market_date);
CREATE INDEX IF NOT EXISTS idx_sol_market_date ON shadow_observation_log(market_date);
CREATE INDEX IF NOT EXISTS idx_sol_eligibility ON shadow_observation_log(eligibility_state);
CREATE INDEX IF NOT EXISTS idx_sol_condition ON shadow_observation_log(direction, timing_deteccion);
"""


def _connect() -> sqlite3.Connection:
    """Lectura-escritura -- USAR SOLO desde `record_shadow_observation()`.
    Las funciones de lectura usan `_ro_connect()` (corrección ya aplicada
    desde el diseño, mismo criterio que `decision_knowledge_registry.py`
    tras el incidente real de disco lleno de esa fase: una conexión de
    lectura nunca debe intentar `PRAGMA journal_mode=WAL` +
    `CREATE TABLE/INDEX IF NOT EXISTS`)."""
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(_SCHEMA)  # CREATE TABLE/INDEX IF NOT EXISTS -- nunca DROP, nunca recrea
    return conn


def _db_exists() -> bool:
    return Path(DB_PATH).exists()


def _ro_connect() -> sqlite3.Connection:
    """Conexión read-only REAL de SQLite -- `mode=ro` + `PRAGMA
    query_only=ON`. NUNCA `PRAGMA journal_mode=WAL`, NUNCA
    `executescript(_SCHEMA)`, NUNCA crea el archivo si no existe -- por eso
    SIEMPRE se llama detrás de `_db_exists()`, nunca sola."""
    uri = Path(DB_PATH).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    return dict(r)


def _last_observation(conn: sqlite3.Connection, ticker: str, market_date: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM shadow_observation_log
           WHERE ticker=? AND market_date=? ORDER BY id DESC LIMIT 1""",
        (ticker, market_date),
    ).fetchone()


def _identity_tuple(row: sqlite3.Row) -> tuple:
    return (row["decision"], row["decision_shadow"], row["eligibility_state"], row["computed_as_of"], row["computed_at"])


def record_shadow_observation(
    ticker: str,
    market_date: str,
    decision_timestamp: str,
    direction: Optional[str],
    timing_deteccion: Optional[str],
    core_methodology_version: str,
    observation: Dict[str, Any],
    learned_evidence: Optional[Dict[str, Any]] = None,
) -> bool:
    """Persiste UN resultado de
    `shadow_observation.classify_shadow_observation()` -- no-op (devuelve
    `False`, no escribe nada) si `observation["observado"]` es falso
    (baseline y shadow coinciden, no hay nada que observar). Si es
    verdadero: transition-only, compara `(decision, decision_shadow,
    eligibility_state, computed_as_of, computed_at)` contra la ÚLTIMA fila
    para `(ticker, market_date)`, inserta SOLO si difiere.

    `learned_evidence` es el MISMO dict ya calculado por el llamador
    (nunca recalculado acá) -- se usa solo para copiar los campos de
    auditoría (`validation_state`/`sample_size`/Wilson/`baseline_pct_20`/
    `lift_20`/`methodology_version`), mismo patrón que
    `decision_knowledge_registry.record_decision_knowledge_snapshot()`."""
    if not observation.get("observado"):
        return False

    le = learned_evidence or {}
    computed_at = le.get("computed_at")
    nueva_tupla = (
        observation.get("decision"),
        observation.get("decision_shadow"),
        observation.get("eligibility_state"),
        observation.get("computed_as_of"),
        computed_at,
    )

    with _connect() as conn:
        anterior = _last_observation(conn, ticker, market_date)
        if anterior is not None and _identity_tuple(anterior) == nueva_tupla:
            return False  # misma observación -- request repetido, no duplica

        now = _now()
        conn.execute(
            """INSERT INTO shadow_observation_log
               (ticker, market_date, decision_timestamp, decision, decision_shadow,
                eligibility_state, walk_forward_violation, direction, timing_deteccion,
                methodology_version, validation_state, sample_size,
                wilson_lower_bound_20_pct, wilson_upper_bound_20_pct, baseline_pct_20,
                lift_20, computed_as_of, computed_at, core_methodology_version, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ticker, market_date, decision_timestamp,
                observation.get("decision"), observation.get("decision_shadow"),
                observation.get("eligibility_state"), int(bool(observation.get("walk_forward_violation"))),
                direction, timing_deteccion,
                le.get("methodology_version"), le.get("validation_state"), le.get("sample_size"),
                le.get("wilson_lower_bound_20_pct"), le.get("wilson_upper_bound_20_pct"),
                le.get("baseline_pct_20"), le.get("lift_20"),
                observation.get("computed_as_of"), computed_at,
                core_methodology_version, now,
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
            """SELECT * FROM shadow_observation_log
               WHERE ticker=? AND market_date=? ORDER BY id ASC""",
            (ticker, market_date),
        ).fetchall()
    return [_row(r) for r in rows]


def list_shadow_observations(
    market_date: Optional[str] = None,
    eligibility_state: Optional[str] = None,
    limit: int = 5000,
) -> List[Dict[str, Any]]:
    """Solo lectura REAL, paginado con un límite explícito. `[]` si la DB
    todavía no existe, sin abrir ni crear nada."""
    if not _db_exists():
        return []
    query = "SELECT * FROM shadow_observation_log WHERE 1=1"
    params: List[Any] = []
    if market_date is not None:
        query += " AND market_date=?"
        params.append(market_date)
    if eligibility_state is not None:
        query += " AND eligibility_state=?"
        params.append(eligibility_state)
    query += " ORDER BY id ASC LIMIT ?"
    params.append(limit)
    with _ro_connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row(r) for r in rows]


# --- reporte offline -- baseline vs shadow contra el outcome real ---------

_POSITIVE_STATES = ("OPORTUNIDAD_PRIORITARIA", "VIGILAR")
_NEGATIVE_STATES = ("NO_TOCAR",)
_GOOD_OUTCOME_CATEGORIES = ("mejor_oportunidad", "buena_oportunidad")
_BAD_OUTCOME_CATEGORY = "falsa_senal"

NOTA_ALCANCE = (
    "Reporte offline de solo lectura (Hito 3, Fase 3.4). Compara baseline vs "
    "shadow SOLO para el subconjunto de conocimiento ya observado -- nunca "
    "implica activacion en decisiones reales, apply_recalibration permanece "
    "False en todo el sistema. No promueve ninguna observacion a decision "
    "ejecutada."
)


def _evaluate_decision_correctness(decision_value: Optional[str], category: Optional[str]) -> str:
    """Misma lógica exacta que
    `decision_outcome_tribunal._evaluate_decision_correctness()` (Hito 3.2,
    sin tocar) -- replicada acá en vez de importada para no acoplar 3.4 a
    un símbolo de otro módulo; ambas copias están cubiertas por tests
    equivalentes."""
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


def _veredictos_contra_outcome(reg, ticker: str, market_date: str, decision: str, decision_shadow: Optional[str]) -> Dict[str, Any]:
    """Un solo lugar para el patrón "outcome real + veredicto baseline/shadow"
    -- reutilizado tanto por el reporte basado en `shadow_observation_log`
    como por la reconstrucción del universo A/B/C de abajo, para no
    duplicar la lógica de evaluación dos veces en el mismo archivo."""
    outcome = reg.get_outcome(ticker, market_date)
    evaluable = _outcome_is_evaluable(outcome)
    category = outcome.get("category") if evaluable else None
    if evaluable:
        baseline_veredicto = _evaluate_decision_correctness(decision, category)
        shadow_veredicto = _evaluate_decision_correctness(decision_shadow, category) if decision_shadow else "SIN_SHADOW"
    else:
        baseline_veredicto = "PENDIENTE"
        shadow_veredicto = "PENDIENTE"
    return {
        "outcome_evaluable": evaluable,
        "decision_baseline_veredicto": baseline_veredicto,
        "decision_shadow_veredicto": shadow_veredicto,
    }


def _construir_universo_abc(market_date: Optional[str], limit: int) -> Dict[str, Any]:
    """Reconstruye, de forma READ-ONLY, el universo completo de
    oportunidades de observación -- no solo el subconjunto que
    `shadow_observation_log` ya persistió (ese subconjunto, acotado a
    `shadow_differs=True` + walk-forward seguro, sigue siendo la ÚNICA
    escritura nueva de Fase 3.4; acá NO se escribe nada).

    Fuente: `decision_knowledge_registry.list_snapshots()` (Hito 3.0, ya
    existente, sin modificar -- escribe una fila por CADA transición,
    tenga o no divergencia). Para cada fila se reconstruye un dict
    `learned_evidence` a partir de sus propias columnas
    (`validation_state`/`sample_size`/Wilson/`baseline_pct_20`/`lift_20`/
    `computed_as_of`/`computed_at`/`methodology_version`) y se clasifica
    con `knowledge_eligibility.classify_eligibility()` -- la MISMA función
    pura de Fase 3.3, importada y reutilizada tal cual, nunca reimplementada
    ni con reglas propias -- única fuente de verdad para qué es "elegible".

    Tres grupos, mutuamente excluyentes:
    - A: `eligibility_state != "ELEGIBLE"` (sin conocimiento elegible --
      incluye conocimiento no disponible, insuficiente, o no elegible).
    - B: `eligibility_state == "ELEGIBLE"` y `shadow_differs` falso
      (conocimiento elegible, pero Atlas ya coincidía con él).
    - C: `eligibility_state == "ELEGIBLE"` y `shadow_differs` verdadero
      (mismo subconjunto que ya cubre `shadow_observation_log`, acá
      re-derivado desde la fuente cruda para que el universo quede
      completo y consistente en un solo lugar)."""
    from atlas_live.core import decision_knowledge_registry as dkr
    from atlas_live.core import knowledge_eligibility as ke
    from atlas_live.radar import candidate_registry as reg

    snapshots = dkr.list_snapshots(market_date=market_date, limit=limit)

    grupos: Dict[str, List[Dict[str, Any]]] = {"A": [], "B": [], "C": []}
    for s in snapshots:
        le_reconstruido = {
            "available": bool(s.get("knowledge_available")),
            "reason": s.get("knowledge_reason"),
            "validation_state": s.get("validation_state"),
            "sample_size": s.get("sample_size"),
            "wilson_lower_bound_20_pct": s.get("wilson_lower_bound_20_pct"),
            "wilson_upper_bound_20_pct": s.get("wilson_upper_bound_20_pct"),
            "baseline_pct_20": s.get("baseline_pct_20"),
            "lift_20": s.get("lift_20"),
            "computed_as_of": s.get("computed_as_of"),
            "computed_at": s.get("computed_at"),
            "methodology_version": s.get("methodology_version"),
        }
        elegibilidad = ke.classify_eligibility(le_reconstruido, s["market_date"])

        if elegibilidad["eligibility_state"] != "ELEGIBLE":
            grupo = "A"
        elif not s.get("shadow_differs"):
            grupo = "B"
        else:
            grupo = "C"

        veredictos = _veredictos_contra_outcome(reg, s["ticker"], s["market_date"], s["decision"], s.get("decision_shadow"))
        grupos[grupo].append({
            "ticker": s["ticker"],
            "market_date": s["market_date"],
            "eligibility_state": elegibilidad["eligibility_state"],
            "decision_baseline": s["decision"],
            "decision_shadow": s.get("decision_shadow"),
            "shadow_differs": bool(s.get("shadow_differs")),
            **veredictos,
        })

    return {
        "A_sin_elegible": {"n_eventos": len(grupos["A"]), "eventos": grupos["A"]},
        "B_elegible_sin_divergencia": {"n_eventos": len(grupos["B"]), "eventos": grupos["B"]},
        "C_elegible_con_divergencia": {"n_eventos": len(grupos["C"]), "eventos": grupos["C"]},
    }


def full_shadow_observation_report(
    market_date: Optional[str] = None,
    eligibility_state: Optional[str] = None,
    limit: int = 5000,
) -> Dict[str, Any]:
    """Orquesta el reporte: para cada observación registrada en
    `shadow_observation_log`, joinea contra `candidate_registry.get_outcome()`
    (solo lectura, reutilizado, nunca reimplementado) y clasifica
    ACIERTO/ERROR/AMBIGUO tanto para `decision` (baseline) como para
    `decision_shadow`, agregado por `eligibility_state`. Sin outcome
    evaluable todavía -> `"PENDIENTE"`, nunca inventado.

    Además (corrección 2026-09-03, auditoría explícita del usuario):
    `universo_conocimiento` reconstruye, por LECTURA de
    `decision_knowledge_snapshot` (nunca escribe nada nuevo), el universo
    completo A/B/C -- sin esto, `shadow_observation_log` por sí sola no
    puede distinguir "no había conocimiento elegible" de "había
    conocimiento elegible pero coincidía con el baseline", porque ninguno
    de los dos casos generaba fila propia. `universo_conocimiento` no
    respeta el filtro `eligibility_state` (que sigue aplicando solo a
    `eventos`/`agregado_por_elegibilidad`) -- siempre se calcula completo
    para ese `market_date`.

    Nunca lanza -- cualquier excepción queda atrapada."""
    resultado: Dict[str, Any] = {
        "generated_at": _now(),
        "ok": False,
        "nota": NOTA_ALCANCE,
        "n_observaciones": 0,
        "eventos": [],
        "agregado_por_elegibilidad": {},
        "universo_conocimiento": {},
        "error": None,
    }
    try:
        from atlas_live.radar import candidate_registry as reg

        observaciones = list_shadow_observations(market_date=market_date, eligibility_state=eligibility_state, limit=limit)
        resultado["n_observaciones"] = len(observaciones)

        por_estado: Dict[str, List[Dict[str, Any]]] = {}
        for obs in observaciones:
            veredictos = _veredictos_contra_outcome(reg, obs["ticker"], obs["market_date"], obs["decision"], obs.get("decision_shadow"))

            evento = {
                "ticker": obs["ticker"],
                "market_date": obs["market_date"],
                "eligibility_state": obs["eligibility_state"],
                "decision_baseline": obs["decision"],
                "decision_shadow": obs["decision_shadow"],
                "walk_forward_violation": bool(obs["walk_forward_violation"]),
                **veredictos,
            }
            resultado["eventos"].append(evento)
            por_estado.setdefault(obs["eligibility_state"], []).append(evento)

        resultado["universo_conocimiento"] = _construir_universo_abc(market_date, limit)

        for estado, eventos in por_estado.items():
            baseline_counts: Dict[str, int] = {}
            shadow_counts: Dict[str, int] = {}
            for e in eventos:
                baseline_counts[e["decision_baseline_veredicto"]] = baseline_counts.get(e["decision_baseline_veredicto"], 0) + 1
                shadow_counts[e["decision_shadow_veredicto"]] = shadow_counts.get(e["decision_shadow_veredicto"], 0) + 1
            resultado["agregado_por_elegibilidad"][estado] = {
                "n_eventos": len(eventos),
                "baseline": baseline_counts,
                "shadow": shadow_counts,
            }

        resultado["ok"] = True
    except Exception as exc:  # el reporte nunca puede tumbar al llamador
        resultado["error"] = f"{type(exc).__name__}: {exc}"
        resultado["ok"] = False
    return resultado
