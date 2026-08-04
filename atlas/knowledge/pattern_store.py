"""Búsqueda de patrones similares sobre los eventos registrados en EventStore.

Sin IA: la "similitud" es una distancia euclidiana simple sobre un puñado de
features numéricas normalizadas (gap %, RVOL, Atlas Score, Momentum Score,
Money Flow Score). Es determinista, transparente y barata de calcular.

También arma el "ADN" de un símbolo: el promedio de esas mismas features a
lo largo de todo su historial en la base, para poder comparar el
comportamiento típico de dos acciones entre sí.
"""

import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from atlas.knowledge.event_store import EventStore, MarketEvent, connect

FEATURE_RANGES: Dict[str, Tuple[float, float]] = {
    "gap_percent": (-20.0, 20.0),
    "rvol": (0.0, 5.0),
    "atlas_score": (0.0, 100.0),
    "momentum_score": (0.0, 100.0),
    "money_flow_score": (0.0, 100.0),
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _normalize(event: MarketEvent) -> Dict[str, float]:
    """Convierte las features numéricas de un evento a [0, 1], omitiendo las que falten."""
    raw = {
        "gap_percent": event.gap_percent,
        "rvol": event.rvol,
        "atlas_score": event.atlas_score,
        "momentum_score": event.momentum_score,
        "money_flow_score": event.money_flow_score,
    }
    features: Dict[str, float] = {}
    for name, value in raw.items():
        if value is None:
            continue
        low, high = FEATURE_RANGES[name]
        features[name] = _clamp((value - low) / (high - low))
    return features


def _similarity(features_a: Dict[str, float], features_b: Dict[str, float]) -> Optional[float]:
    """Similitud 0-100 entre dos vectores de features normalizadas; None si no comparten ninguna."""
    common = set(features_a) & set(features_b)
    if not common:
        return None

    squared_diff = sum((features_a[key] - features_b[key]) ** 2 for key in common)
    distance = math.sqrt(squared_diff / len(common))  # RMS de la diferencia, en [0, 1]
    return round(_clamp(1 - distance) * 100, 1)


@dataclass(frozen=True)
class SymbolDNA:
    """Perfil promedio de comportamiento histórico de un símbolo."""

    ticker: str
    sample_size: int
    features: Dict[str, float]  # promedios normalizados [0, 1]
    event_type_counts: Dict[str, int]


class PatternStore:
    """Busca eventos con patrones similares y compara el "ADN" de dos símbolos."""

    def __init__(self, event_store: EventStore) -> None:
        self._event_store = event_store

    def find_similar(
        self,
        reference: MarketEvent,
        top_n: int = 5,
        event_type: Optional[str] = None,
        candidate_pool: int = 5_000,
    ) -> List[Tuple[MarketEvent, float]]:
        """Devuelve los `top_n` eventos más parecidos a `reference` (evento, similitud 0-100)."""
        reference_features = _normalize(reference)
        if not reference_features:
            return []

        candidates = self._event_store.get_events(event_type=event_type, limit=candidate_pool)

        scored = []
        for candidate in candidates:
            if candidate.id == reference.id:
                continue
            similarity = _similarity(reference_features, _normalize(candidate))
            if similarity is not None:
                scored.append((candidate, similarity))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_n]

    def get_symbol_dna(self, ticker: str, limit: int = 5_000) -> Optional[SymbolDNA]:
        """Perfil promedio (ADN) de un símbolo a partir de todo su historial registrado."""
        events = self._event_store.get_events(ticker=ticker, limit=limit)
        if not events:
            return None

        accumulated: Dict[str, List[float]] = {}
        event_type_counts: Dict[str, int] = {}

        for event in events:
            event_type_counts[event.event_type] = event_type_counts.get(event.event_type, 0) + 1
            for name, value in _normalize(event).items():
                accumulated.setdefault(name, []).append(value)

        features = {name: round(sum(values) / len(values), 4) for name, values in accumulated.items()}

        return SymbolDNA(
            ticker=ticker,
            sample_size=len(events),
            features=features,
            event_type_counts=event_type_counts,
        )

    def compare_dna(self, ticker_a: str, ticker_b: str, limit: int = 5_000) -> Optional[float]:
        """Similitud 0-100 entre el ADN histórico de dos símbolos."""
        dna_a = self.get_symbol_dna(ticker_a, limit=limit)
        dna_b = self.get_symbol_dna(ticker_b, limit=limit)
        if dna_a is None or dna_b is None:
            return None
        return _similarity(dna_a.features, dna_b.features)


# ---------------------------------------------------------------------------
# PatternRegistry: identidad persistente de un patrón, con estado y su
# historial completo de transiciones. Principio de Conservación del
# Conocimiento: ningún patrón se borra ni se sobrescribe, solo cambia de
# estado. Si un patrón reaparece (incluso años después), reutiliza su
# `pattern_key` y por lo tanto todo su historial acumulado.
#
# Quién escribe acá: por arquitectura, el único llamador previsto de
# `register_pattern()`/`transition_state()` es Calibration Manager, una vez
# que un cambio de estado fue aprobado por un humano. Learning Engine solo
# *propone* transiciones (PatternEvolutionReport); nunca las aplica él
# mismo. Esta clase no impone ese límite en tiempo de ejecución (igual que
# el resto de Atlas no bloquea mecánicamente sus reglas de "único
# escritor"), pero está documentado como el contrato a respetar.
# ---------------------------------------------------------------------------

PATTERN_OBSERVATION = "En observación"
PATTERN_ACTIVE = "Activo"
PATTERN_DECAYING = "En decadencia"
PATTERN_INACTIVE = "Inactivo"
PATTERN_REACTIVATED = "Reactivado"

PATTERN_STATES = {
    PATTERN_OBSERVATION,
    PATTERN_ACTIVE,
    PATTERN_DECAYING,
    PATTERN_INACTIVE,
    PATTERN_REACTIVATED,
}


@dataclass(frozen=True)
class Pattern:
    """Un patrón (o antipatrón) con identidad persistente y estado actual."""

    pattern_key: str  # identidad única y estable; reutilizada si el patrón reaparece
    name: str
    category: str
    state: str
    evidence: Dict[str, Any]
    created_at: str
    updated_at: str
    id: Optional[int] = field(default=None, compare=False)


@dataclass(frozen=True)
class PatternTransition:
    """Una entrada del historial de transiciones de un patrón. Nunca se borra ni se edita."""

    pattern_key: str
    from_state: Optional[str]
    to_state: str
    reason: str
    evidence: Dict[str, Any]
    transitioned_at: str
    id: Optional[int] = field(default=None, compare=False)


def _row_to_pattern(row: sqlite3.Row) -> Pattern:
    return Pattern(
        id=row["id"],
        pattern_key=row["pattern_key"],
        name=row["name"],
        category=row["category"],
        state=row["state"],
        evidence=json.loads(row["evidence"]) if row["evidence"] else {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_transition(row: sqlite3.Row) -> PatternTransition:
    return PatternTransition(
        id=row["id"],
        pattern_key=row["pattern_key"],
        from_state=row["from_state"],
        to_state=row["to_state"],
        reason=row["reason"],
        evidence=json.loads(row["evidence"]) if row["evidence"] else {},
        transitioned_at=row["transitioned_at"],
    )


class PatternRegistry:
    """Identidad persistente de patrones: estado actual + historial completo de transiciones."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._connection = connect(db_path)
        self._create_schema()

    def _create_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                state TEXT NOT NULL,
                evidence TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pattern_transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_key TEXT NOT NULL,
                from_state TEXT,
                to_state TEXT NOT NULL,
                reason TEXT NOT NULL,
                evidence TEXT,
                transitioned_at TEXT NOT NULL,
                FOREIGN KEY (pattern_key) REFERENCES patterns(pattern_key)
            )
            """
        )
        self._connection.execute("CREATE INDEX IF NOT EXISTS idx_patterns_state ON patterns(state)")
        self._connection.execute("CREATE INDEX IF NOT EXISTS idx_patterns_category ON patterns(category)")
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_pattern_transitions_key ON pattern_transitions(pattern_key)"
        )
        self._connection.commit()

    def register_pattern(
        self,
        pattern_key: str,
        name: str,
        category: str,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> Pattern:
        """Crea un patrón nuevo (estado inicial: En observación), o si `pattern_key` ya
        existe, lo devuelve tal cual está -- nunca duplica ni reinicia su historial."""
        existing = self.get_pattern(pattern_key)
        if existing is not None:
            return existing

        now = datetime.now(timezone.utc).isoformat()
        self._connection.execute(
            """
            INSERT INTO patterns (pattern_key, name, category, state, evidence, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (pattern_key, name, category, PATTERN_OBSERVATION, json.dumps(evidence or {}), now, now),
        )
        self._connection.execute(
            """
            INSERT INTO pattern_transitions (pattern_key, from_state, to_state, reason, evidence, transitioned_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (pattern_key, None, PATTERN_OBSERVATION, "Patrón descubierto por primera vez", json.dumps(evidence or {}), now),
        )
        self._connection.commit()
        return self.get_pattern(pattern_key)

    def record_observation(self, pattern_key: str, ticker: str, observed_at: str) -> Pattern:
        """Enriquece un patrón ya registrado con una nueva observación real.

        Incrementa `sample_size` y actualiza cuándo/en qué símbolo se vio por
        última vez, además de una ventana acotada de los últimos tickers que
        exhibieron este comportamiento (la base de la transferencia de
        conocimiento entre símbolos: el patrón es el mismo sin importar el
        ticker). No cambia `state` ni escribe en `pattern_transitions` -- eso
        es exclusivo de `transition_state`, reservado para cuando Calibration
        Manager aprueba un cambio real de estado. Este método es para
        acumular evidencia en cada escaneo, no para decidir nada.
        """
        current = self.get_pattern(pattern_key)
        if current is None:
            raise KeyError(f"No existe un patrón registrado con pattern_key='{pattern_key}'")

        evidence = dict(current.evidence)
        evidence["sample_size"] = int(evidence.get("sample_size", 0)) + 1
        evidence["last_seen_at"] = observed_at
        evidence["last_seen_ticker"] = ticker
        recent_tickers = list(evidence.get("recent_tickers", []))
        recent_tickers.append(ticker)
        evidence["recent_tickers"] = recent_tickers[-20:]  # ventana acotada, no crece sin límite

        now = datetime.now(timezone.utc).isoformat()
        self._connection.execute(
            "UPDATE patterns SET evidence = ?, updated_at = ? WHERE pattern_key = ?",
            (json.dumps(evidence), now, pattern_key),
        )
        self._connection.commit()
        return self.get_pattern(pattern_key)

    def get_pattern(self, pattern_key: str) -> Optional[Pattern]:
        """Devuelve el patrón por su identidad, o None si nunca se registró."""
        row = self._connection.execute(
            "SELECT * FROM patterns WHERE pattern_key = ?", (pattern_key,)
        ).fetchone()
        return _row_to_pattern(row) if row else None

    def transition_state(
        self,
        pattern_key: str,
        new_state: str,
        reason: str,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> Pattern:
        """Cambia el estado de un patrón existente y agrega una fila al historial.
        Nunca borra ni sobrescribe la fila anterior: el historial es acumulativo."""
        if new_state not in PATTERN_STATES:
            raise ValueError(f"Estado inválido: '{new_state}'. Válidos: {sorted(PATTERN_STATES)}")

        current = self.get_pattern(pattern_key)
        if current is None:
            raise KeyError(f"No existe un patrón registrado con pattern_key='{pattern_key}'")

        now = datetime.now(timezone.utc).isoformat()
        merged_evidence = {**current.evidence, **(evidence or {})}

        self._connection.execute(
            "UPDATE patterns SET state = ?, evidence = ?, updated_at = ? WHERE pattern_key = ?",
            (new_state, json.dumps(merged_evidence), now, pattern_key),
        )
        self._connection.execute(
            """
            INSERT INTO pattern_transitions (pattern_key, from_state, to_state, reason, evidence, transitioned_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (pattern_key, current.state, new_state, reason, json.dumps(evidence or {}), now),
        )
        self._connection.commit()
        return self.get_pattern(pattern_key)

    def get_transition_history(self, pattern_key: str) -> List[PatternTransition]:
        """Historial completo de transiciones de un patrón, en orden cronológico."""
        rows = self._connection.execute(
            "SELECT * FROM pattern_transitions WHERE pattern_key = ? ORDER BY id ASC",
            (pattern_key,),
        ).fetchall()
        return [_row_to_transition(row) for row in rows]

    def list_patterns(self, state: Optional[str] = None, category: Optional[str] = None) -> List[Pattern]:
        """Lista patrones, opcionalmente filtrados por estado y/o categoría."""
        clauses = []
        params: list = []
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        if category is not None:
            clauses.append("category = ?")
            params.append(category)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(f"SELECT * FROM patterns {where} ORDER BY updated_at DESC", params).fetchall()
        return [_row_to_pattern(row) for row in rows]

    def close(self) -> None:
        """Cierra la conexión SQLite."""
        self._connection.close()
