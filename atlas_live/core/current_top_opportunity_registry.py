"""Memoria persistente de CURRENT TOP OPPORTUNITY (2026-08-26, FASE 2/5,
autorizado explícitamente).

DB propia (`current_top_opportunity.db`, vía `db_path()`, mismo patrón de
aislamiento que `shadow_detector_registry.py`/`live_experience_knowledge.py`)
-- NUNCA reutiliza `candidate_detection`/`candidate_outcome`/
`magnitud_prediction`/`alert_stage_log`/Prediction Journal, tal como pediste
explícitamente: esos sistemas tienen otros propósitos.

REGLA FUNDAMENTAL (pedido explícito): esta tabla NUNCA decide quién es
Top-1 -- solo registra la decisión que ya tomó
`current_top_opportunity.select_current_top_opportunity()`. Ningún import
de `atlas_decision_core.py`/`priority_classifier.py`/gates/scoring en este
archivo -- confirmado por test estructural.

CASO A/B/C (idempotencia, pedido explícito):
    A. Sin Top-1 abierto para la sesión -> INSERT nuevo, `previous_ticker=NULL`.
    B. El Top-1 abierto ya es el mismo ticker -> NO se escribe nada
       (ni siquiera un UPDATE de `score`/`score_components` -- quedan
       CONGELADOS al momento de la selección original, pedido explícito).
    C. Aparece otro ticker -> UPDATE del registro anterior SOLO en
       `deselected_at` (la ÚNICA actualización permitida) + INSERT del
       nuevo con `previous_ticker`/`replacement_reason`/
       `selection_sequence` incrementado.

Campos EXACTOS pedidos, sin agregar columnas nuevas sin proponerlas antes
-- `score_components` (JSON) es donde vive todo el detalle adicional
(criterio_decisivo, candidatos_considerados, ranking_score/atlas_score/
momentum_score del ganador) para no ampliar el schema."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.config.config import db_path
from atlas_live.core.current_top_opportunity import TopOpportunitySelection

DB_PATH = db_path("current_top_opportunity.db", default=Path(__file__).parent)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS current_top_opportunity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    selected_at TEXT NOT NULL,
    deselected_at TEXT,
    selection_sequence INTEGER NOT NULL,
    previous_ticker TEXT,
    replacement_reason TEXT,
    score REAL,
    runner_up_ticker TEXT,
    runner_up_score REAL,
    score_components TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ctop_market_date ON current_top_opportunity_log(market_date, selection_sequence);
-- Salvaguarda de integridad (punto 2, "no debe crear dos selecciones
-- idénticas por accidente"): a lo sumo UN registro abierto
-- (deselected_at IS NULL) por market_date -- índice único parcial,
-- soportado por SQLite desde 3.8.0.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ctop_un_abierto_por_dia
    ON current_top_opportunity_log(market_date) WHERE deselected_at IS NULL;

-- Estado de la capa de estabilidad (2026-08-27, Fase 4/5, autorizado
-- explícitamente): "no crees otro sistema paralelo de historial" -- esto
-- NO es un historial, es la memoria mínima de trabajo del algoritmo de
-- confirmación consecutiva (candidato pendiente + cuántos ciclos lleva),
-- necesaria para sobrevivir un restart sin perder el conteo a mitad de
-- camino. Vive en la MISMA DB que `current_top_opportunity_log`, una sola
-- fila por `market_date`.
CREATE TABLE IF NOT EXISTS current_top_opportunity_pending (
    market_date TEXT PRIMARY KEY,
    pending_ticker TEXT,
    pending_streak INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(_SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _score_components_json(selection: TopOpportunitySelection) -> str:
    return json.dumps({
        "decision": selection.componentes_utilizados["decision"],
        "ranking_score": list(selection.componentes_utilizados["ranking_score"]),
        "atlas_score": selection.componentes_utilizados["atlas_score"],
        "momentum_score": selection.componentes_utilizados["momentum_score"],
        "criterio_decisivo": selection.criterio_decisivo,
        "candidatos_considerados": selection.candidatos_considerados,
    })


def _deserialize(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["score_components"] = json.loads(d["score_components"])
    return d


def register_top_opportunity(
    selection: Optional[TopOpportunitySelection], market_date: str,
) -> Dict[str, Any]:
    """Aplica CASO A/B/C sobre el resultado YA decidido por
    `select_current_top_opportunity()`. `selection=None` (sin candidatos)
    -> no escribe nada, nunca inventa un Top-1. Devuelve un resumen de qué
    acción se tomó (`"SIN_SELECCION"`/`"CREADO"`/`"SIN_CAMBIOS"`/`"REEMPLAZADO"`)."""
    if selection is None:
        return {"action": "SIN_SELECCION"}
    if not market_date or not selection.ticker:
        return {"action": "DATOS_INVALIDOS"}

    with _connect() as conn:
        abierto = conn.execute(
            """SELECT * FROM current_top_opportunity_log
               WHERE market_date=? AND deselected_at IS NULL
               ORDER BY selection_sequence DESC LIMIT 1""",
            (market_date,),
        ).fetchone()

        if abierto is not None and abierto["ticker"] == selection.ticker:
            # CASO B -- mismo Top-1: NO se escribe nada, ni un UPDATE.
            return {"action": "SIN_CAMBIOS", "ticker": selection.ticker,
                    "selection_sequence": abierto["selection_sequence"]}

        now = _now()
        previous_ticker: Optional[str] = None
        next_sequence = 1

        if abierto is not None:
            # CASO C -- cambio real: cerrar el anterior (única actualización
            # permitida), nunca UPDATE de ningún otro campo/fila.
            conn.execute(
                "UPDATE current_top_opportunity_log SET deselected_at=? WHERE id=?",
                (now, abierto["id"]),
            )
            previous_ticker = abierto["ticker"]
            next_sequence = abierto["selection_sequence"] + 1

        conn.execute(
            """INSERT INTO current_top_opportunity_log
               (market_date, ticker, selected_at, deselected_at, selection_sequence,
                previous_ticker, replacement_reason, score, runner_up_ticker, runner_up_score,
                score_components, methodology_version, created_at)
               VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                market_date, selection.ticker, now, next_sequence, previous_ticker,
                selection.motivo_seleccion if abierto is not None else None,
                selection.score_final, selection.runner_up_ticker, selection.runner_up_score,
                _score_components_json(selection), selection.methodology_version, now,
            ),
        )
        return {
            "action": "CREADO" if abierto is None else "REEMPLAZADO",
            "ticker": selection.ticker, "previous_ticker": previous_ticker,
            "selection_sequence": next_sequence,
        }


def get_top_opportunity_at(market_date: str, at: str) -> Optional[Dict[str, Any]]:
    """¿Quién era el Top-1 en el instante `at` (ISO8601)? El registro
    abierto en ese momento -- `selected_at <= at < deselected_at` (o
    `deselected_at IS NULL` si todavía sigue vigente)."""
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM current_top_opportunity_log
               WHERE market_date=? AND selected_at<=?
                     AND (deselected_at IS NULL OR deselected_at>?)
               ORDER BY selection_sequence DESC LIMIT 1""",
            (market_date, at, at),
        ).fetchone()
        return _deserialize(row) if row else None


def get_open_top_opportunity(market_date: str) -> Optional[Dict[str, Any]]:
    """El registro actualmente ABIERTO (`deselected_at IS NULL`) de ese
    día, o `None` si todavía no hay ninguno -- mismo criterio que usa
    internamente `register_top_opportunity()`, expuesto acá como lectura
    para que la capa de estabilidad (Fase 4/5) pueda consultar "quién está
    confirmado ahora mismo" sin reimplementar la consulta."""
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM current_top_opportunity_log
               WHERE market_date=? AND deselected_at IS NULL
               ORDER BY selection_sequence DESC LIMIT 1""",
            (market_date,),
        ).fetchone()
        return _deserialize(row) if row else None


def get_top_opportunity_sequence(market_date: str) -> List[Dict[str, Any]]:
    """Secuencia COMPLETA de intervalos Top-1 de un día, en orden -- una
    fila por CAMBIO real, nunca una fila por barrido/ciclo."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM current_top_opportunity_log WHERE market_date=? ORDER BY selection_sequence ASC",
            (market_date,),
        ).fetchall()
        return [_deserialize(r) for r in rows]


def get_pending_state(market_date: str) -> Dict[str, Any]:
    """Estado de trabajo de la capa de estabilidad (Fase 4/5) -- sobrevive
    un restart del proceso porque vive en esta misma DB. Default explícito
    (`pending_ticker=None`, `pending_streak=0`) cuando todavía no existe
    fila para ese `market_date` -- nunca se inventa un estado previo."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM current_top_opportunity_pending WHERE market_date=?", (market_date,),
        ).fetchone()
    if row is None:
        return {"pending_ticker": None, "pending_streak": 0}
    return {"pending_ticker": row["pending_ticker"], "pending_streak": row["pending_streak"]}


def set_pending_state(market_date: str, pending_ticker: Optional[str], pending_streak: int) -> None:
    """UPSERT de una única fila por `market_date` -- este SÍ es un estado
    de trabajo mutable (a diferencia de `current_top_opportunity_log`,
    append-only), documentado como excepción deliberada: no es historial,
    es memoria de trabajo del algoritmo de estabilidad."""
    with _connect() as conn:
        conn.execute(
            """INSERT INTO current_top_opportunity_pending (market_date, pending_ticker, pending_streak, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(market_date) DO UPDATE SET
                   pending_ticker=excluded.pending_ticker,
                   pending_streak=excluded.pending_streak,
                   updated_at=excluded.updated_at""",
            (market_date, pending_ticker, pending_streak, _now()),
        )
