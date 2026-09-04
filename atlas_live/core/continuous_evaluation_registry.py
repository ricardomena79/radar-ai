"""Registro de evaluación continua / degradación (Hito 3, Fase 3.6,
2026-09-03, autorizado explícitamente en Plan Mode, revisión corregida
del usuario).

`continuous_evaluation.classify_continuous_evaluation()` (puro, sin DB)
-> este módulo (lectura de ventana reciente + persistencia + disparo
condicional de revocación) -> `GET /api/admin/continuous-evaluation-report`
(solo lectura).

DB propia (`continuous_evaluation.db`), mismo patrón `_connect()`/
`_ro_connect()`/`_db_exists()` de Fases 3.0/3.3/3.4/3.5. Tabla
`continuous_evaluation_log`, append-only, TRANSITION-ONLY por
`(direction, timing_deteccion, methodology_version)`.

DOS CAMINOS, DOS FUENTES DE CONDICIONES, NUNCA SE MODIFICA NINGÚN ARCHIVO
DE FASES 3.0-3.5 NI DE `live_experience_scoring.py`/`live_experience_knowledge.py`:
- **Event-driven (primario)**: `evaluate_conditions_from_experience_table()`,
  llamado desde `live_experience_pipeline.run_experience_learning_cycle()`
  (el único touch de todo 3.6 a un archivo pre-existente, minimal,
  aditivo, fuera del try/except de esa función). La fuente de condiciones
  es `tabla`, YA calculada por ese mismo ciclo -- sin ninguna consulta de
  enumeración nueva. `auto_revoke=True` para cada condición, pero la
  revocación real sigue exigiendo TODOS los guards (DEGRADADO robusto,
  no ya revocada).
- **Manual/on-demand (secundario)**: `evaluate_condition()` llamado
  directamente desde el endpoint admin, para una condición puntual o
  (vía `list_eligible_conditions()`, que reduce
  `knowledge_eligibility_registry.list_eligibility_log()` -- función
  PÚBLICA de Fase 3.3, sin modificarla) para todas las condiciones
  actualmente ELEGIBLES. `auto_revoke=False` por defecto -- un operador
  debe pedirlo explícitamente.

VENTANA RECIENTE -- lectura de solo lectura PROPIA de este módulo
(`_recent_condition_rows()`), mismo join/filtros ya usados por
`live_experience_scoring._load_rows_from_db()` (privada, Fase 2, NUNCA
importada acá) pero acotada a una condición puntual y recortada a las
últimas `n_ventana` filas. El resultado se pasa como `rows=` a
`live_experience_scoring.compute_own_experience_table()` (pública, Fase
2, sin modificar) -- CERO estadística reimplementada, toda la
matemática (Wilson, `validation_state`, `baseline_pct_20`) es la MISMA
función ya probada.

FAIL-SAFE ABSOLUTO: cualquier excepción en `evaluate_condition()`
(consulta rota, DB inaccesible, dato corrupto) -> `evaluation_state=
"NO_EVALUABLE"`, `revocation_requested=False` -- la revocación NUNCA se
intenta en esa rama. La ausencia de evidencia nunca es evidencia de
degradación."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from atlas.config.config import db_path
from atlas_live.core import continuous_evaluation as ce

DB_PATH = db_path("continuous_evaluation.db", default=Path(__file__).parent)

# Deliberadamente igual a candidate_registry.META_MUESTRA_MINIMA -- ver
# docstring de continuous_evaluation.py, sección "VENTANA vs. PISO".
DEFAULT_N_VENTANA = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS continuous_evaluation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    direction TEXT NOT NULL,
    timing_deteccion TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    evaluated_at TEXT NOT NULL,
    market_date TEXT NOT NULL,
    n_ventana INTEGER NOT NULL,
    recent_sample_size INTEGER,
    recent_pct_20 REAL,
    recent_wilson_lower_bound_20_pct REAL,
    recent_wilson_upper_bound_20_pct REAL,
    recent_baseline_pct_20 REAL,
    computed_as_of TEXT,
    walk_forward_ok INTEGER NOT NULL,
    evaluation_state TEXT NOT NULL,
    reason TEXT NOT NULL,
    revocation_requested INTEGER NOT NULL,
    revocation_result TEXT,
    error_detalle TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cel_condition ON continuous_evaluation_log(direction, timing_deteccion, methodology_version);
CREATE INDEX IF NOT EXISTS idx_cel_market_date ON continuous_evaluation_log(market_date);
CREATE INDEX IF NOT EXISTS idx_cel_state ON continuous_evaluation_log(evaluation_state);
"""


def _connect() -> sqlite3.Connection:
    """Lectura-escritura -- USAR SOLO desde `record_continuous_evaluation()`."""
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(_SCHEMA)
    return conn


def _db_exists() -> bool:
    return Path(DB_PATH).exists()


def _ro_connect() -> sqlite3.Connection:
    """Conexión read-only REAL -- `mode=ro` + `PRAGMA query_only=ON`.
    NUNCA crea el archivo -- SIEMPRE se llama detrás de `_db_exists()`."""
    uri = Path(DB_PATH).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    return dict(r)


# --- lectura de ventana reciente (solo lectura, contra radar_candidates.db) --

def _recent_condition_rows(
    direction: str, timing_deteccion: str, n_ventana: int, as_of_date: str
) -> List[Dict[str, Any]]:
    """Solo lectura REAL (`mode=ro`) contra `candidate_registry.DB_PATH`
    -- mismo join `candidate_detection JOIN candidate_outcome` y mismos
    filtros de calidad (`is_final=1`, `confiable_para_aprendizaje=1`,
    `market_date < as_of_date`) ya usados por
    `live_experience_scoring._load_rows_from_db()` (privada, Fase 2, NUNCA
    importada acá -- esta es una implementación independiente, acotada a
    una condición puntual y recortada a las últimas `n_ventana` filas por
    `market_date`). `[]` si la DB no existe todavía -- nunca crea nada."""
    from atlas_live.radar.candidate_registry import DB_PATH as RADAR_DB_PATH

    if not Path(RADAR_DB_PATH).exists():
        return []
    uri = Path(RADAR_DB_PATH).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        rows = conn.execute(
            """SELECT d.ticker AS ticker, d.market_date AS market_date,
                      d.direction_at_detection AS direction,
                      d.phase_tag AS timing_deteccion,
                      d.volatility_14d_pct_at_detection AS volatility_14d_pct,
                      d.daily_range_pct_at_detection AS daily_range_pct,
                      o.max_return_after_detection_pct AS max_advance_pct
               FROM candidate_detection d
               JOIN candidate_outcome o ON o.ticker = d.ticker AND o.market_date = d.market_date
               WHERE o.is_final = 1 AND o.confiable_para_aprendizaje = 1
                 AND d.market_date < ?
                 AND d.direction_at_detection = ?
                 AND d.phase_tag = ?
               ORDER BY d.market_date DESC
               LIMIT ?""",
            (as_of_date, direction, timing_deteccion, n_ventana),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# --- persistencia (transition-only) ------------------------------------

def _last_evaluation(
    conn: sqlite3.Connection, direction: str, timing_deteccion: str, methodology_version: str
) -> Optional[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM continuous_evaluation_log
           WHERE direction=? AND timing_deteccion=? AND methodology_version=?
           ORDER BY id DESC LIMIT 1""",
        (direction, timing_deteccion, methodology_version),
    ).fetchone()


def _identity_tuple(row: sqlite3.Row) -> tuple:
    return (
        row["evaluation_state"], row["recent_sample_size"],
        row["recent_wilson_upper_bound_20_pct"], row["computed_as_of"], row["revocation_requested"],
    )


def record_continuous_evaluation(snapshot: Dict[str, Any]) -> bool:
    """Persiste UN snapshot completo de evaluación -- transition-only,
    compara `(evaluation_state, recent_sample_size,
    recent_wilson_upper_bound_20_pct, computed_as_of,
    revocation_requested)` contra la ÚLTIMA fila para `(direction,
    timing_deteccion, methodology_version)`, inserta SOLO si difiere."""
    nueva_tupla = (
        snapshot["evaluation_state"], snapshot.get("recent_sample_size"),
        snapshot.get("recent_wilson_upper_bound_20_pct"), snapshot.get("computed_as_of"),
        int(bool(snapshot.get("revocation_requested"))),
    )
    with _connect() as conn:
        anterior = _last_evaluation(conn, snapshot["direction"], snapshot["timing_deteccion"], snapshot["methodology_version"])
        if anterior is not None and _identity_tuple(anterior) == nueva_tupla:
            return False

        now = _now()
        conn.execute(
            """INSERT INTO continuous_evaluation_log
               (direction, timing_deteccion, methodology_version, evaluated_at, market_date,
                n_ventana, recent_sample_size, recent_pct_20, recent_wilson_lower_bound_20_pct,
                recent_wilson_upper_bound_20_pct, recent_baseline_pct_20, computed_as_of,
                walk_forward_ok, evaluation_state, reason, revocation_requested,
                revocation_result, error_detalle, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                snapshot["direction"], snapshot["timing_deteccion"], snapshot["methodology_version"],
                snapshot.get("evaluated_at", now), snapshot["market_date"], snapshot["n_ventana"],
                snapshot.get("recent_sample_size"), snapshot.get("recent_pct_20"),
                snapshot.get("recent_wilson_lower_bound_20_pct"), snapshot.get("recent_wilson_upper_bound_20_pct"),
                snapshot.get("recent_baseline_pct_20"), snapshot.get("computed_as_of"),
                int(bool(snapshot.get("walk_forward_ok"))), snapshot["evaluation_state"], snapshot["reason"],
                int(bool(snapshot.get("revocation_requested"))), snapshot.get("revocation_result"),
                snapshot.get("error_detalle"), now,
            ),
        )
        conn.commit()
        return True


def get_evaluations_for(direction: str, timing_deteccion: str, methodology_version: str) -> List[Dict[str, Any]]:
    if not _db_exists():
        return []
    with _ro_connect() as conn:
        rows = conn.execute(
            """SELECT * FROM continuous_evaluation_log
               WHERE direction=? AND timing_deteccion=? AND methodology_version=?
               ORDER BY id ASC""",
            (direction, timing_deteccion, methodology_version),
        ).fetchall()
    return [_row(r) for r in rows]


def list_evaluations(
    market_date: Optional[str] = None, evaluation_state: Optional[str] = None, limit: int = 5000,
) -> List[Dict[str, Any]]:
    if not _db_exists():
        return []
    query = "SELECT * FROM continuous_evaluation_log WHERE 1=1"
    params: List[Any] = []
    if market_date is not None:
        query += " AND market_date=?"
        params.append(market_date)
    if evaluation_state is not None:
        query += " AND evaluation_state=?"
        params.append(evaluation_state)
    query += " ORDER BY id ASC LIMIT ?"
    params.append(limit)
    with _ro_connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row(r) for r in rows]


# --- evaluación de UNA condición (usada por ambos caminos) ---------------

def evaluate_condition(
    direction: str,
    timing_deteccion: str,
    methodology_version: str,
    as_of_date: str,
    n_ventana: int = DEFAULT_N_VENTANA,
    auto_revoke: bool = False,
) -> Dict[str, Any]:
    """Evalúa UNA condición de punta a punta: lee su ventana reciente
    (solo lectura), recalcula Wilson/baseline con
    `live_experience_scoring.compute_own_experience_table()` (Fase 2, sin
    modificar), clasifica con
    `continuous_evaluation.classify_continuous_evaluation()`, persiste el
    snapshot completo (transition-only), y -- SOLO si
    `evaluation_state=="DEGRADADO"` Y `auto_revoke=True` Y la condición NO
    estaba ya revocada -- llama a `activation_registry.revoke()` (Fase
    3.5, sin modificarla). Nunca lanza -- cualquier excepción termina en
    `NO_EVALUABLE`, sin revocar, con el detalle real del error persistido
    en `error_detalle`."""
    from atlas_live.core import activation_registry as areg
    from atlas_live.learning import live_experience_scoring as les

    market_date = as_of_date
    campos_vacios = {
        "recent_sample_size": None, "recent_pct_20": None,
        "recent_wilson_lower_bound_20_pct": None, "recent_wilson_upper_bound_20_pct": None,
        "recent_baseline_pct_20": None, "computed_as_of": None,
    }

    try:
        ventana = _recent_condition_rows(direction, timing_deteccion, n_ventana, as_of_date)

        if not ventana:
            clasificacion = {
                "evaluation_state": "NO_EVALUABLE", "reason": "SIN_EVIDENCIA_RECIENTE",
                "walk_forward_ok": False, "revocation_requested": False,
            }
            metricas = dict(campos_vacios)
        else:
            tabla = les.compute_own_experience_table(as_of_date, rows=ventana)
            fila = next(
                (f for f in tabla if f["direction"] == direction and f["timing_deteccion"] == timing_deteccion
                 and f["bucket"] == "poblacion_total"),
                None,
            )
            if fila is None:
                clasificacion = {
                    "evaluation_state": "NO_EVALUABLE", "reason": "SIN_GRUPO_POBLACION_TOTAL",
                    "walk_forward_ok": False, "revocation_requested": False,
                }
                metricas = dict(campos_vacios)
            else:
                metricas = {
                    "recent_sample_size": fila["n_evaluables"],
                    "recent_pct_20": fila["pct_20"],
                    "recent_wilson_lower_bound_20_pct": fila["wilson_lower_bound_20_pct"],
                    "recent_wilson_upper_bound_20_pct": fila["wilson_upper_bound_20_pct"],
                    "recent_baseline_pct_20": fila["baseline_pct_20"],
                    "computed_as_of": fila["computed_as_of"],
                }
                clasificacion = ce.classify_continuous_evaluation(
                    recent_sample_size=metricas["recent_sample_size"],
                    recent_wilson_upper_bound_20_pct=metricas["recent_wilson_upper_bound_20_pct"],
                    recent_baseline_pct_20=metricas["recent_baseline_pct_20"],
                    computed_as_of=metricas["computed_as_of"],
                    market_date=market_date,
                )

        revocation_requested = bool(clasificacion["revocation_requested"])
        revocation_result = "NO_SOLICITADA"
        if revocation_requested and auto_revoke:
            if areg.is_revoked(direction, timing_deteccion, methodology_version):
                revocation_result = "YA_REVOCADA_PREVIAMENTE"
            else:
                try:
                    areg.revoke(
                        scope="CONDICION", reason=clasificacion["reason"],
                        direction=direction, timing_deteccion=timing_deteccion,
                        methodology_version=methodology_version,
                    )
                    revocation_result = "OK"
                except Exception as exc:
                    revocation_result = f"ERROR: {type(exc).__name__}: {exc}"

        snapshot = {
            "direction": direction, "timing_deteccion": timing_deteccion,
            "methodology_version": methodology_version, "market_date": market_date,
            "n_ventana": n_ventana, "evaluated_at": _now(),
            **metricas,
            "walk_forward_ok": clasificacion["walk_forward_ok"],
            "evaluation_state": clasificacion["evaluation_state"],
            "reason": clasificacion["reason"],
            "revocation_requested": revocation_requested,
            "revocation_result": revocation_result,
            "error_detalle": None,
        }
    except Exception as exc:
        snapshot = {
            "direction": direction, "timing_deteccion": timing_deteccion,
            "methodology_version": methodology_version, "market_date": market_date,
            "n_ventana": n_ventana, "evaluated_at": _now(),
            **campos_vacios,
            "walk_forward_ok": False, "evaluation_state": "NO_EVALUABLE",
            "reason": f"ERROR_EVALUACION: {type(exc).__name__}: {exc}",
            "revocation_requested": False, "revocation_result": "NO_SOLICITADA",
            "error_detalle": f"{type(exc).__name__}: {exc}",
        }

    record_continuous_evaluation(snapshot)
    return snapshot


# --- camino event-driven --------------------------------------------------

def evaluate_conditions_from_experience_table(tabla: List[Dict[str, Any]], as_of_date: str) -> Dict[str, Any]:
    """Punto de entrada EVENT-DRIVEN -- llamado por
    `live_experience_pipeline.run_experience_learning_cycle()` (Fase 2,
    tocada mínimamente) inmediatamente después de que `tabla` ya se
    calculó Y persistió. La fuente de condiciones es `tabla` misma -- sin
    ninguna consulta de enumeración nueva. `auto_revoke=True` para cada
    condición, pero la revocación real sigue exigiendo TODOS los guards
    de `evaluate_condition()` (DEGRADADO robusto, no ya revocada). Nunca
    lanza -- cualquier excepción por condición queda contenida, no
    interrumpe la evaluación del resto."""
    from atlas_live.learning import live_experience_knowledge as lek

    resultado: Dict[str, Any] = {"ok": True, "as_of_date": as_of_date, "n_condiciones": 0, "evaluaciones": [], "error": None}
    try:
        condiciones: Set[Tuple[str, str]] = {
            (f["direction"], f["timing_deteccion"]) for f in tabla if f.get("bucket") == "poblacion_total"
        }
        resultado["n_condiciones"] = len(condiciones)
        for direction, timing_deteccion in condiciones:
            try:
                snapshot = evaluate_condition(
                    direction=direction, timing_deteccion=timing_deteccion,
                    methodology_version=lek.METHODOLOGY_VERSION,
                    as_of_date=as_of_date, n_ventana=DEFAULT_N_VENTANA, auto_revoke=True,
                )
                resultado["evaluaciones"].append(snapshot)
            except Exception as exc:  # defensa adicional -- evaluate_condition ya no debería lanzar
                resultado["evaluaciones"].append({
                    "direction": direction, "timing_deteccion": timing_deteccion,
                    "evaluation_state": "NO_EVALUABLE", "error": f"{type(exc).__name__}: {exc}",
                })
    except Exception as exc:
        resultado["ok"] = False
        resultado["error"] = f"{type(exc).__name__}: {exc}"
    return resultado


# --- enumeración para el camino manual (reutiliza Fase 3.3, sin tocarla) --

def list_eligible_conditions(limit: int = 5000) -> List[Tuple[Optional[str], Optional[str], Optional[str]]]:
    """Reduce `knowledge_eligibility_registry.list_eligibility_log()`
    (Fase 3.3, función pública, sin modificarla) a las condiciones
    DISTINTAS cuyo ÚLTIMO veredicto conocido es `"ELEGIBLE"` -- fuente
    para el camino manual/on-demand únicamente (el event-driven usa
    `tabla` directamente, nunca esto)."""
    from atlas_live.core import knowledge_eligibility_registry as ker

    filas = ker.list_eligibility_log(limit=limit)
    ultimo_por_condicion: Dict[Tuple[Optional[str], Optional[str], Optional[str]], Dict[str, Any]] = {}
    for fila in filas:
        clave = (fila.get("direction"), fila.get("timing_deteccion"), fila.get("methodology_version"))
        ultimo_por_condicion[clave] = fila  # filas en orden ascendente por id -- la última pisa
    return [
        clave for clave, fila in ultimo_por_condicion.items()
        if fila.get("eligibility_state") == "ELEGIBLE" and all(clave)
    ]


# --- reporte offline -------------------------------------------------------

NOTA_ALCANCE = (
    "Reporte offline de solo lectura (Hito 3, Fase 3.6). La revocacion "
    "automatica solo puede ocurrir por evidencia DEGRADADO robusta "
    "(n>=META_MUESTRA_MINIMA y wilson_upper>=baseline) -- nunca por dato "
    "faltante, muestra insuficiente o error. apply_recalibration permanece "
    "sin activarse por este modulo; activation-mechanism-state no se toca."
)


def full_continuous_evaluation_report(
    market_date: Optional[str] = None, evaluation_state: Optional[str] = None, limit: int = 5000,
) -> Dict[str, Any]:
    """Reporte de solo lectura -- mismo estilo que los 4 reportes
    anteriores de este Hito. Nunca lanza."""
    try:
        eventos = list_evaluations(market_date=market_date, evaluation_state=evaluation_state, limit=limit)
        conteos: Dict[str, int] = {estado: 0 for estado in ce.EVALUATION_STATES}
        n_revocaciones_disparadas = 0
        for evento in eventos:
            conteos[evento["evaluation_state"]] = conteos.get(evento["evaluation_state"], 0) + 1
            if evento.get("revocation_result") == "OK":
                n_revocaciones_disparadas += 1
        return {
            "ok": True, "nota": NOTA_ALCANCE, "n_eventos": len(eventos),
            "conteos_por_estado": conteos, "n_revocaciones_disparadas": n_revocaciones_disparadas,
            "eventos": eventos, "error": None,
        }
    except Exception as exc:
        return {
            "ok": False, "nota": NOTA_ALCANCE, "n_eventos": 0, "conteos_por_estado": {},
            "n_revocaciones_disparadas": 0, "eventos": [], "error": str(exc),
        }
