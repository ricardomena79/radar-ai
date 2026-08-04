"""Calibration Manager: historial completo de la evolución del algoritmo de Atlas.

No es solo una base de datos. Es la única puerta de entrada para cualquier
modificación del conocimiento permanente de Atlas:

  - Recibe recomendaciones (de Learning Engine, o de cualquier motor futuro),
    las versiona y las guarda -- nunca sobrescribe una versión anterior.
  - Es quien decide (vía revisión humana) si una recomendación se aprueba,
    se rechaza o se pospone.
  - Es el único componente autorizado para aplicar un cambio de estado en
    Pattern Store una vez que ese cambio fue aprobado.
  - Registra el resultado obtenido después de aplicar cada recomendación,
    cerrando el ciclo de trazabilidad: qué cambió, quién lo propuso, qué
    evidencia existía, y qué resultado produjo.

Usa su propia base SQLite (`calibration_manager.db`), independiente de la
Knowledge Base y del Decision Journal -- es su propio dominio: la historia
de la evolución del algoritmo, no del mercado ni del operador.

Principio: Learning Engine descubre. Calibration Manager evalúa, registra y
aplica únicamente los cambios aprobados. Pattern Store conserva el
conocimiento. Learning Engine nunca escribe en Pattern Store directamente
-- solo Calibration Manager lo hace, y solo después de una aprobación.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.config.config import data_dir

DEFAULT_DB_PATH = data_dir() / "calibration_manager.db"

# Categorías de recomendación.
ENGINE_CALIBRATION = "engine_calibration"  # ej. ajustar un peso/umbral de un motor
PATTERN_STATE_CHANGE = "pattern_state_change"  # ej. transición de estado en Pattern Store
RECOMMENDATION_CATEGORIES = {ENGINE_CALIBRATION, PATTERN_STATE_CHANGE}

# Estados posibles de una recomendación.
PENDING = "Pendiente"
REVIEWED = "Revisada"
APPROVED = "Aprobada"
REJECTED = "Rechazada"
IMPLEMENTED = "Implementada"
RECOMMENDATION_STATUSES = {PENDING, REVIEWED, APPROVED, REJECTED, IMPLEMENTED}


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


@dataclass(frozen=True)
class CalibrationRecommendation:
    """Una versión de una recomendación de calibración."""

    recommendation_key: str  # identidad estable; una misma idea revisada crea una nueva versión
    version: int
    category: str
    target: str  # ej. "momentum_engine.rvol_weight" o un pattern_key
    proposed_by: str  # motor/módulo que la generó, ej. "Learning Engine / Calibration Advisor"
    title: str
    description: str
    evidence: Dict[str, Any]
    sample_size: Optional[int]
    expected_improvement: Optional[str]
    risks: Optional[str]
    proposed_new_state: Optional[str]  # solo aplica si category == PATTERN_STATE_CHANGE
    status: str
    created_at: str
    id: Optional[int] = field(default=None, compare=False)


@dataclass(frozen=True)
class StatusChange:
    """Una entrada del historial de estado de una recomendación. Nunca se borra."""

    recommendation_key: str
    version: int
    from_status: Optional[str]
    to_status: str
    notes: Optional[str]
    result: Optional[Dict[str, Any]]
    changed_at: str
    id: Optional[int] = field(default=None, compare=False)


def _row_to_recommendation(row: sqlite3.Row) -> CalibrationRecommendation:
    return CalibrationRecommendation(
        id=row["id"],
        recommendation_key=row["recommendation_key"],
        version=row["version"],
        category=row["category"],
        target=row["target"],
        proposed_by=row["proposed_by"],
        title=row["title"],
        description=row["description"],
        evidence=json.loads(row["evidence"]) if row["evidence"] else {},
        sample_size=row["sample_size"],
        expected_improvement=row["expected_improvement"],
        risks=row["risks"],
        proposed_new_state=row["proposed_new_state"],
        status=row["status"],
        created_at=row["created_at"],
    )


def _row_to_status_change(row: sqlite3.Row) -> StatusChange:
    return StatusChange(
        id=row["id"],
        recommendation_key=row["recommendation_key"],
        version=row["version"],
        from_status=row["from_status"],
        to_status=row["to_status"],
        notes=row["notes"],
        result=json.loads(row["result"]) if row["result"] else None,
        changed_at=row["changed_at"],
    )


class CalibrationManagerStore:
    """Persistencia versionada de recomendaciones de calibración, sobre SQLite local."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._connection = _connect(db_path)
        self._create_schema()

    def _create_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS calibration_recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recommendation_key TEXT NOT NULL,
                version INTEGER NOT NULL,
                category TEXT NOT NULL,
                target TEXT NOT NULL,
                proposed_by TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                evidence TEXT,
                sample_size INTEGER,
                expected_improvement TEXT,
                risks TEXT,
                proposed_new_state TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (recommendation_key, version)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS calibration_status_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recommendation_key TEXT NOT NULL,
                version INTEGER NOT NULL,
                from_status TEXT,
                to_status TEXT NOT NULL,
                notes TEXT,
                result TEXT,
                changed_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_calib_reco_key ON calibration_recommendations(recommendation_key)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_calib_reco_status ON calibration_recommendations(status)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_calib_reco_category ON calibration_recommendations(category)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_calib_hist_key ON calibration_status_history(recommendation_key)"
        )
        self._connection.commit()

    def submit_recommendation(
        self,
        recommendation_key: str,
        category: str,
        target: str,
        proposed_by: str,
        title: str,
        description: str,
        evidence: Optional[Dict[str, Any]] = None,
        sample_size: Optional[int] = None,
        expected_improvement: Optional[str] = None,
        risks: Optional[str] = None,
        proposed_new_state: Optional[str] = None,
    ) -> CalibrationRecommendation:
        """Guarda una nueva versión de la recomendación. Nunca sobrescribe una versión
        anterior: si `recommendation_key` ya existe, esta queda como version+1."""
        if category not in RECOMMENDATION_CATEGORIES:
            raise ValueError(f"category inválida: '{category}'. Válidas: {sorted(RECOMMENDATION_CATEGORIES)}")

        existing_versions = self.get_all_versions(recommendation_key)
        next_version = (max(r.version for r in existing_versions) + 1) if existing_versions else 1
        now = datetime.now(timezone.utc).isoformat()

        self._connection.execute(
            """
            INSERT INTO calibration_recommendations (
                recommendation_key, version, category, target, proposed_by, title, description,
                evidence, sample_size, expected_improvement, risks, proposed_new_state, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recommendation_key, next_version, category, target, proposed_by, title, description,
                json.dumps(evidence or {}), sample_size, expected_improvement, risks, proposed_new_state,
                PENDING, now,
            ),
        )
        self._connection.execute(
            """
            INSERT INTO calibration_status_history (
                recommendation_key, version, from_status, to_status, notes, result, changed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (recommendation_key, next_version, None, PENDING, "Recomendación creada", None, now),
        )
        self._connection.commit()
        return self.get_latest(recommendation_key)

    def get_all_versions(self, recommendation_key: str) -> List[CalibrationRecommendation]:
        """Todas las versiones de una recomendación, en orden ascendente. Ninguna se borra."""
        rows = self._connection.execute(
            "SELECT * FROM calibration_recommendations WHERE recommendation_key = ? ORDER BY version ASC",
            (recommendation_key,),
        ).fetchall()
        return [_row_to_recommendation(row) for row in rows]

    def get_latest(self, recommendation_key: str) -> Optional[CalibrationRecommendation]:
        """Devuelve la versión más reciente de una recomendación, o None si no existe."""
        row = self._connection.execute(
            """
            SELECT * FROM calibration_recommendations
            WHERE recommendation_key = ? ORDER BY version DESC LIMIT 1
            """,
            (recommendation_key,),
        ).fetchone()
        return _row_to_recommendation(row) if row else None

    def update_status(
        self,
        recommendation_key: str,
        new_status: str,
        notes: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
    ) -> CalibrationRecommendation:
        """Cambia el estado de la versión más reciente. Nunca borra el estado anterior:
        agrega una fila al historial de estados."""
        if new_status not in RECOMMENDATION_STATUSES:
            raise ValueError(f"status inválido: '{new_status}'. Válidos: {sorted(RECOMMENDATION_STATUSES)}")

        current = self.get_latest(recommendation_key)
        if current is None:
            raise KeyError(f"No existe una recomendación con recommendation_key='{recommendation_key}'")

        now = datetime.now(timezone.utc).isoformat()
        self._connection.execute(
            "UPDATE calibration_recommendations SET status = ? WHERE recommendation_key = ? AND version = ?",
            (new_status, recommendation_key, current.version),
        )
        self._connection.execute(
            """
            INSERT INTO calibration_status_history (
                recommendation_key, version, from_status, to_status, notes, result, changed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (recommendation_key, current.version, current.status, new_status, notes,
             json.dumps(result) if result is not None else None, now),
        )
        self._connection.commit()
        return self.get_latest(recommendation_key)

    def get_status_history(self, recommendation_key: str) -> List[StatusChange]:
        """Historial completo de estados de una recomendación, a través de todas sus versiones."""
        rows = self._connection.execute(
            "SELECT * FROM calibration_status_history WHERE recommendation_key = ? ORDER BY id ASC",
            (recommendation_key,),
        ).fetchall()
        return [_row_to_status_change(row) for row in rows]

    def list_recommendations(
        self, status: Optional[str] = None, category: Optional[str] = None
    ) -> List[CalibrationRecommendation]:
        """Última versión de cada recomendación, opcionalmente filtrada por estado/categoría."""
        clauses = []
        params: list = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if category is not None:
            clauses.append("category = ?")
            params.append(category)
        where = f"AND {' AND '.join(clauses)}" if clauses else ""

        rows = self._connection.execute(
            f"""
            SELECT r.* FROM calibration_recommendations r
            INNER JOIN (
                SELECT recommendation_key, MAX(version) AS max_version
                FROM calibration_recommendations
                GROUP BY recommendation_key
            ) latest
            ON r.recommendation_key = latest.recommendation_key AND r.version = latest.max_version
            WHERE 1 = 1 {where}
            ORDER BY r.created_at DESC
            """,
            params,
        ).fetchall()
        return [_row_to_recommendation(row) for row in rows]

    def close(self) -> None:
        """Cierra la conexión SQLite."""
        self._connection.close()


class CalibrationManager:
    """Fachada: recibe, versiona, decide y aplica recomendaciones de calibración.

    Única puerta de entrada para modificar el conocimiento permanente de
    Atlas: si una recomendación es sobre un patrón (`PATTERN_STATE_CHANGE`),
    solo esta clase está autorizada a aplicar el cambio de estado real en
    Pattern Store, y solo después de que la recomendación llegue a
    `IMPLEMENTED`. Las recomendaciones sobre motores (`ENGINE_CALIBRATION`)
    nunca se aplican solas: siguen requiriendo que un humano edite el
    código del motor; esta clase solo registra que se hizo y con qué
    resultado.
    """

    def __init__(self, db_path: Optional[Path] = None, pattern_registry=None) -> None:
        self.store = CalibrationManagerStore(db_path)
        self._pattern_registry = pattern_registry

    def submit_recommendation(self, **kwargs) -> CalibrationRecommendation:
        """Recibe una recomendación (de Learning Engine u otro origen) y la versiona."""
        return self.store.submit_recommendation(**kwargs)

    def review(self, recommendation_key: str, notes: Optional[str] = None) -> CalibrationRecommendation:
        """Marca la recomendación como revisada por un humano."""
        return self.store.update_status(recommendation_key, REVIEWED, notes=notes)

    def approve(self, recommendation_key: str, notes: Optional[str] = None) -> CalibrationRecommendation:
        """Un humano aprueba la recomendación. Todavía no la aplica."""
        return self.store.update_status(recommendation_key, APPROVED, notes=notes)

    def reject(self, recommendation_key: str, notes: Optional[str] = None) -> CalibrationRecommendation:
        """Un humano rechaza la recomendación."""
        return self.store.update_status(recommendation_key, REJECTED, notes=notes)

    def implement(
        self, recommendation_key: str, result: Optional[Dict[str, Any]] = None, notes: Optional[str] = None
    ) -> CalibrationRecommendation:
        """Marca la recomendación como implementada.

        Si la recomendación es `PATTERN_STATE_CHANGE`, aplica el cambio de
        estado en Pattern Store (requiere haber recibido un `pattern_registry`
        al construir este CalibrationManager). Si es `ENGINE_CALIBRATION`,
        asume que un humano ya editó el motor manualmente: aquí solo se
        registra el hecho y el resultado obtenido.
        """
        recommendation = self.store.get_latest(recommendation_key)
        if recommendation is None:
            raise KeyError(f"No existe una recomendación con recommendation_key='{recommendation_key}'")

        if recommendation.category == PATTERN_STATE_CHANGE:
            if self._pattern_registry is None:
                raise RuntimeError(
                    "Esta recomendación requiere aplicar un cambio en Pattern Store, pero "
                    "CalibrationManager no recibió un pattern_registry al construirse."
                )
            if not recommendation.proposed_new_state:
                raise ValueError("La recomendación no especifica proposed_new_state.")
            self._pattern_registry.transition_state(
                recommendation.target,
                recommendation.proposed_new_state,
                reason=f"Calibration Manager aplicó la recomendación '{recommendation.title}' "
                f"({recommendation_key} v{recommendation.version})",
                evidence=recommendation.evidence,
            )

        return self.store.update_status(recommendation_key, IMPLEMENTED, notes=notes, result=result)

    def get_latest(self, recommendation_key: str) -> Optional[CalibrationRecommendation]:
        """Devuelve la versión más reciente de una recomendación, o None si no existe."""
        return self.store.get_latest(recommendation_key)

    def get_all_versions(self, recommendation_key: str) -> List[CalibrationRecommendation]:
        """Todas las versiones de una recomendación, en orden ascendente."""
        return self.store.get_all_versions(recommendation_key)

    def get_status_history(self, recommendation_key: str) -> List[StatusChange]:
        """Historial completo de estados de una recomendación, a través de todas sus versiones."""
        return self.store.get_status_history(recommendation_key)

    def list_recommendations(
        self, status: Optional[str] = None, category: Optional[str] = None
    ) -> List[CalibrationRecommendation]:
        """Última versión de cada recomendación, opcionalmente filtrada por estado/categoría."""
        return self.store.list_recommendations(status=status, category=category)

    def close(self) -> None:
        """Cierra la conexión SQLite."""
        self.store.close()
