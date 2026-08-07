"""Exit Journal -- memoria histórica de la evolución de una oportunidad,
desde su detección hasta el fin de la sesión (propuesta aprobada el
2026-08-02, con la modificación explícita del usuario: **no fija todavía
ningún umbral de pérdida de fuerza (X) ni de fin de impulso (N)**).

**No es un algoritmo de salida.** No genera ninguna recomendación de
vender ni de mantener -- ni una sola línea de este archivo decide nada,
solo registra y, bajo pedido explícito con parámetros explícitos,
describe. El futuro Exit Pattern Engine (fase posterior, no construida
todavía) es quien usará esta memoria para descubrir con evidencia qué
umbrales generalizan -- este módulo no se adelanta a esa decisión.

Diseño de dos niveles, para que ningún número "provisional" pueda
confundirse con un hecho observado:

  1. **Guardado (SQLite, append-only)** -- solo lo objetivamente medible,
     sin ningún umbral:
     - `trajectory_samples`: la serie cruda completa de rendimiento
       observado, un punto por cada ciclo de `scan_worker.py` (~5 min)
       mientras el símbolo esté en el ranking sellado del día. Nunca se
       borra ni se resume -- es la memoria misma.
     - `exit_summary`: UNA fila por símbolo/día, calculada una sola vez al
       cerrar la ventana de observación (`close_exit_summary`, protegida
       contra recálculo por `AlreadyClosedError`, mismo patrón que
       `AlreadySealedError`/`AlreadyGradedError` de `prediction_journal.py`).
       Todos los campos son objetivos: hora de detección, hora de entrada
       (aproximada por el sellado del ranking -- no hay ejecución de
       órdenes real todavía), hora y valor del máximo observado,
       rendimiento final, y duración de la ventana observada. Ninguno
       requiere decidir qué es "perder fuerza" o "terminar el impulso".

  2. **NO guardado -- funciones puras, calculadas bajo demanda** --
     `derive_movement_start`, `derive_weakness_point`, `derive_impulse_end`
     y `derive_movement_duration` reciben el umbral como parámetro
     OBLIGATORIO (sin default) cada vez que se llaman. Como la trayectoria
     cruda se conserva completa, estas funciones se pueden re-ejecutar en
     cualquier momento futuro con distintos umbrales -- nada queda "mal
     grabado" porque nunca se grabó una interpretación, solo el dato crudo.
"""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.config.config import db_path

DB_PATH = db_path("exit_journal.db", default=Path(__file__).parent)


class AlreadyClosedError(Exception):
    """Se intentó cerrar (calcular el resumen objetivo de) un símbolo/día
    que ya tiene un resumen calculado -- no se recalcula."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS trajectory_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    sampled_at TEXT NOT NULL,
    return_pct REAL,
    score REAL,
    eligible INTEGER,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trajectory_symbol_date ON trajectory_samples(symbol, date);
CREATE INDEX IF NOT EXISTS idx_trajectory_date ON trajectory_samples(date);

CREATE TABLE IF NOT EXISTS exit_summary (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    detected_at TEXT,
    entry_at TEXT,
    peak_at TEXT,
    peak_return_pct REAL,
    final_return_pct REAL,
    window_closed_at TEXT NOT NULL,
    total_window_minutes REAL,
    sample_count INTEGER NOT NULL,
    PRIMARY KEY (symbol, date)
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


# ---------------------------------------------------------------------------
# 1. Trayectoria cruda -- append-only, sin ningún umbral
# ---------------------------------------------------------------------------

def record_trajectory_sample(
    symbol: str, date: str, sampled_at: str,
    return_pct: Optional[float], score: Optional[float], eligible: bool,
) -> None:
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO trajectory_samples (symbol, date, sampled_at, return_pct, score, eligible, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (symbol, date, sampled_at, return_pct, score, int(eligible), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def get_all_symbol_dates() -> List[tuple]:
    """Todos los pares (symbol, date) con al menos una muestra de
    trayectoria -- para que capacidades del Motor Predictivo (ej.
    `entry_window`, Fase 1.1, Sprint 3) puedan recorrer toda la base
    histórica sin necesitar una función nueva por cada agrupación futura.
    Solo lectura, no agrega tabla ni mecanismo nuevo."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol, date FROM trajectory_samples ORDER BY date ASC, symbol ASC"
        ).fetchall()
    return [(r["symbol"], r["date"]) for r in rows]


def count_trajectory_samples() -> int:
    """Total de muestras guardadas -- usado como llave de invalidación de
    caché por capacidades que agregan sobre toda la base histórica (ej.
    `entry_window.gather_evidence`): cuando este número crece (reconstrucción
    retroactiva en curso o nuevas trayectorias en vivo), la caché se
    recalcula sola, sin ningún cambio de código."""
    with closing(_connect()) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM trajectory_samples").fetchone()
    return row["n"]


def get_trajectory(symbol: str, date: str) -> List[Dict[str, Any]]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM trajectory_samples WHERE symbol = ? AND date = ? ORDER BY sampled_at ASC, id ASC",
            (symbol, date),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 2. Resumen objetivo -- una sola vez, sin ningún umbral
# ---------------------------------------------------------------------------

def is_closed(symbol: str, date: str) -> bool:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT 1 FROM exit_summary WHERE symbol = ? AND date = ?", (symbol, date)
        ).fetchone()
    return row is not None


def close_exit_summary(symbol: str, date: str, entry_at: Optional[str], window_closed_at: str) -> Dict[str, Any]:
    """Calcula y guarda el resumen objetivo de la trayectoria observada --
    una sola vez por símbolo/día. `entry_at` es la hora de sellado del
    ranking oficial (aproximación documentada de "entrada", no una orden
    real -- ver docstring del módulo). Ningún campo calculado acá depende
    de un umbral de pérdida de fuerza o fin de impulso."""
    if is_closed(symbol, date):
        raise AlreadyClosedError(f"{symbol!r} en {date!r} ya tiene un resumen calculado -- no se recalcula.")

    trayectoria = get_trajectory(symbol, date)
    con_valor = [t for t in trayectoria if t["return_pct"] is not None]

    if not con_valor:
        detected_at = None
        peak_at = None
        peak_return_pct = None
        final_return_pct = None
        total_window_minutes = None
    else:
        detected_at = trayectoria[0]["sampled_at"]
        pico = max(con_valor, key=lambda t: t["return_pct"])
        peak_at = pico["sampled_at"]
        peak_return_pct = pico["return_pct"]
        final_return_pct = con_valor[-1]["return_pct"]
        t0 = datetime.fromisoformat(detected_at)
        t1 = datetime.fromisoformat(window_closed_at)
        total_window_minutes = (t1 - t0).total_seconds() / 60.0

    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO exit_summary (symbol, date, detected_at, entry_at, peak_at, peak_return_pct, "
            "final_return_pct, window_closed_at, total_window_minutes, sample_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, date, detected_at, entry_at, peak_at, peak_return_pct,
             final_return_pct, window_closed_at, total_window_minutes, len(trayectoria)),
        )
        conn.commit()

    return get_exit_summary(symbol, date)


def get_exit_summary(symbol: str, date: str) -> Optional[Dict[str, Any]]:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT * FROM exit_summary WHERE symbol = ? AND date = ?", (symbol, date)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_recent_summaries(limit: int = 20) -> List[Dict[str, Any]]:
    """Últimos resúmenes objetivos cerrados, más recientes primero (Cabina
    del Piloto, Panel 11). Solo lectura -- ningún umbral involucrado, son
    los mismos campos objetivos que guarda `close_exit_summary`."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM exit_summary ORDER BY date DESC, symbol ASC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_summaries_between(start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """Todos los resúmenes objetivos cerrados en un rango de fechas
    (inclusive) -- Panel de Desempeño (2026-08-07, ver DECISION_LOG.md).
    `None` en cualquiera de los dos extremos significa "sin límite" de ese
    lado. Solo lectura, mismos campos objetivos de siempre -- no agrega
    ningún umbral ni interpretación nueva."""
    query = "SELECT * FROM exit_summary WHERE 1=1"
    params: List[Any] = []
    if start_date is not None:
        query += " AND date >= ?"
        params.append(start_date)
    if end_date is not None:
        query += " AND date <= ?"
        params.append(end_date)
    query += " ORDER BY date ASC, symbol ASC"
    with closing(_connect()) as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 3. NO se guarda -- funciones puras, umbral SIEMPRE explícito, nunca un
#    default silencioso. Resultado siempre marcado como regla temporal.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProvisionalExitPoint:
    """Resultado de aplicar una regla temporal (no una regla definitiva del
    proyecto) sobre la trayectoria observada. `regla_aplicada` documenta
    exactamente qué parámetro se usó, para que nunca se confunda con un
    hecho medido."""

    timestamp: Optional[str]
    regla_aplicada: str
    es_provisional: bool = True


def derive_movement_start(trajectory: List[Dict[str, Any]], movement_threshold_pct: float) -> ProvisionalExitPoint:
    """Primer muestreo donde |rendimiento| supera `movement_threshold_pct`.
    Sin default -- quien llama debe elegir el umbral explícitamente (ej.
    reutilizar `min_abs_gap_or_change_pct` de `explosive_config.json`, o
    cualquier otro que quiera probar)."""
    for muestra in trajectory:
        valor = muestra.get("return_pct")
        if valor is not None and abs(valor) >= movement_threshold_pct:
            return ProvisionalExitPoint(
                timestamp=muestra["sampled_at"],
                regla_aplicada=f"primer muestreo con |rendimiento| >= {movement_threshold_pct}%",
            )
    return ProvisionalExitPoint(timestamp=None, regla_aplicada=f"ningún muestreo superó {movement_threshold_pct}%")


def derive_signal_start(trajectory: List[Dict[str, Any]]) -> ProvisionalExitPoint:
    """Primer muestreo donde el símbolo cumplió la condición de elegibilidad
    de Radar Explosivo (`eligible=1`) -- el momento en que Atlas ya lo
    hubiera mostrado como candidato, no cuando el precio ya se movió (ver
    `derive_movement_start`). Motor Predictivo, Fase 1.1 (2026-08-06, ver
    DECISIONES.md): capacidad `entry_window` mide la ventana entre este
    punto y `derive_movement_start`, no entre la detección y el movimiento
    usando la misma marca de tiempo -- son dos hechos distintos.
    Sin parámetro de umbral: `eligible` ya es un hecho binario grabado en
    la trayectoria, no algo a derivar con un umbral elegido acá."""
    for muestra in trajectory:
        if muestra.get("eligible"):
            return ProvisionalExitPoint(
                timestamp=muestra["sampled_at"],
                regla_aplicada="primer muestreo con eligible=1 (condición de Radar Explosivo cumplida)",
            )
    return ProvisionalExitPoint(timestamp=None, regla_aplicada="ningún muestreo tuvo eligible=1")


def derive_weakness_point(trajectory: List[Dict[str, Any]], retracement_from_peak_pct: float) -> ProvisionalExitPoint:
    """Primer muestreo donde el rendimiento retrocede `retracement_from_peak_pct`
    puntos respecto al máximo observado HASTA ESE MOMENTO (no el máximo del
    día completo -- el máximo se recalcula sobre la marcha, como lo vería
    el sistema en vivo). Umbral sin default, a propósito."""
    pico_hasta_ahora: Optional[float] = None
    for muestra in trajectory:
        valor = muestra.get("return_pct")
        if valor is None:
            continue
        if pico_hasta_ahora is None or valor > pico_hasta_ahora:
            pico_hasta_ahora = valor
            continue
        if pico_hasta_ahora - valor >= retracement_from_peak_pct:
            return ProvisionalExitPoint(
                timestamp=muestra["sampled_at"],
                regla_aplicada=f"primer retroceso >= {retracement_from_peak_pct} puntos desde el máximo observado hasta ese momento",
            )
    return ProvisionalExitPoint(timestamp=None, regla_aplicada=f"ningún retroceso >= {retracement_from_peak_pct} puntos")


def derive_impulse_end(
    trajectory: List[Dict[str, Any]],
    retracement_from_peak_pct: float,
    consecutive_quiet_samples: int,
) -> ProvisionalExitPoint:
    """`consecutive_quiet_samples` muestreos consecutivos sin nuevo máximo
    Y con retroceso ya confirmado (ver `derive_weakness_point`). Dos
    umbrales, ninguno con default."""
    pico_hasta_ahora: Optional[float] = None
    racha_quieta = 0
    for muestra in trajectory:
        valor = muestra.get("return_pct")
        if valor is None:
            continue
        if pico_hasta_ahora is None or valor > pico_hasta_ahora:
            pico_hasta_ahora = valor
            racha_quieta = 0
            continue
        if pico_hasta_ahora - valor >= retracement_from_peak_pct:
            racha_quieta += 1
            if racha_quieta >= consecutive_quiet_samples:
                return ProvisionalExitPoint(
                    timestamp=muestra["sampled_at"],
                    regla_aplicada=(
                        f"{consecutive_quiet_samples} muestreos consecutivos con retroceso >= "
                        f"{retracement_from_peak_pct} puntos desde el máximo, sin nuevo máximo"
                    ),
                )
        else:
            racha_quieta = 0
    return ProvisionalExitPoint(
        timestamp=None,
        regla_aplicada=f"nunca se acumularon {consecutive_quiet_samples} muestreos quietos consecutivos",
    )


def derive_movement_duration(
    trajectory: List[Dict[str, Any]],
    movement_threshold_pct: float,
    retracement_from_peak_pct: float,
    consecutive_quiet_samples: int,
) -> Optional[float]:
    """"Tiempo total del movimiento" = fin del impulso - inicio del
    movimiento, ambos derivados con los umbrales dados. `None` si
    cualquiera de los dos no se pudo determinar con esta trayectoria y
    estos umbrales -- nunca un número inventado."""
    inicio = derive_movement_start(trajectory, movement_threshold_pct)
    fin = derive_impulse_end(trajectory, retracement_from_peak_pct, consecutive_quiet_samples)
    if inicio.timestamp is None or fin.timestamp is None:
        return None
    t0 = datetime.fromisoformat(inicio.timestamp)
    t1 = datetime.fromisoformat(fin.timestamp)
    return (t1 - t0).total_seconds() / 60.0
