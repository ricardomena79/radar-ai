"""Registro de candidatas del radar de universo completo (2026-08-14).

Base persistente `radar_candidates.db` (config.db_path -> Volume Railway).
Separación anti-leakage (mismo criterio que `signal_registry`/
`study_registry`):

  - candidate_detection: condiciones EN el momento de la primera detección
    (precio, cambio %, volumen, RVOL, qué puertas dispararon). WRITE-ONCE
    por (ticker, market_date) -- una candidata se detecta una sola vez por
    día, nunca se re-detecta ni se pisa.
  - candidate_observation: seguimiento continuo (append-only) -- una
    candidata NO desaparece si deja de estar entre las primeras; cada
    barrido que la sigue viendo agrega una fila.
  - candidate_intraday_metrics: resultado del análisis de 1 minuto
    (velocidad, aceleración, VWAP, RVOL intradía, fase) -- tabla separada,
    se llena después y por separado de la detección.
  - candidate_outcome: RESULTADO real posterior (máximo alcanzado, bandas
    +20/+50/+100%, categoría) -- se calcula SOLO al cierre del mercado,
    nunca durante el día (evita fuga de información hacia la detección).

Idempotente: `record_detection`/`record_outcome` usan INSERT OR IGNORE por
(ticker, market_date). Nunca se pierde una candidata en silencio -- toda
fila de `candidate_detection` queda, se resuelva bien o mal.
"""

import json
import os as _os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from atlas.config.config import db_path

DB_PATH = db_path("radar_candidates.db", default=Path(__file__).parent)

# Piso de muestra para considerar confiable un % de Precisión de Magnitud
# (2026-08-23, pedido explícito del usuario: "no puede ser una simple suma
# de acierto... quiero ver la realidad") -- MISMO umbral ya usado en todo
# Atlas para esto (signal_tracker.py, explosion_history.py,
# experiments.MIN_PRIOR_ROWS_FOR_CUTS), nunca un número nuevo inventado acá.
MUESTRA_MINIMA_CONFIABLE_MAGNITUD = 30

# Rigor estadístico de Precisión de Magnitud (2026-08-24, pedido explícito
# del usuario: "evitar que una muestra pequeña produzca una falsa impresión
# de precisión"). Escala de 3 niveles DISTINTA del piso de arriba (30) --
# más estricta, pensada específicamente para el badge de validación
# visible en la Cabina, no para decidir si un % individual es "confiable"
# en otros reportes. Configurable acá, nunca un número hardcodeado suelto
# en el frontend.
VALIDACION_MUESTRA_INSUFICIENTE_MAX = 99   # n < 100 -> 🔴 MUESTRA INSUFICIENTE
VALIDACION_EN_VALIDACION_MAX = 499         # 100 <= n <= 499 -> 🟡 EN VALIDACIÓN
                                            # n >= 500 -> 🟢 VALIDACIÓN ROBUSTA

# Meta de confianza del 80% (2026-08-23/24, pedido explícito del usuario:
# "este porcentaje debe ser bien criterioso... quiero saber su progreso
# real") -- la meta NO se confirma solo por cruzar el %, también exige el
# mismo piso de muestra robusta de arriba (500) -- una precisión de 80%
# con n=150 todavía no alcanza para decir "meta cumplida".
META_CONFIANZA_PCT = 80.0
META_MUESTRA_MINIMA = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidate_detection (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    market_date TEXT NOT NULL,
    session TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    sweep_id TEXT,
    price_at_detection REAL,
    change_pct_at_detection REAL,
    volume_at_detection INTEGER,
    average_volume_at_detection INTEGER,
    relative_volume_at_detection REAL,
    dollar_volume_at_detection REAL,
    gates_fired TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'tradier',
    created_at TEXT NOT NULL,
    UNIQUE(ticker, market_date)
);
CREATE INDEX IF NOT EXISTS idx_det_date ON candidate_detection(market_date);

CREATE TABLE IF NOT EXISTS candidate_observation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    market_date TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    sweep_id TEXT,
    price REAL,
    change_pct REAL,
    volume INTEGER,
    relative_volume REAL,
    gates_fired_now TEXT,
    vwap REAL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_ticker_date ON candidate_observation(ticker, market_date);

CREATE TABLE IF NOT EXISTS candidate_intraday_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    market_date TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    vwap REAL,
    price_vs_vwap_pct REAL,
    velocity_pct_per_min REAL,
    acceleration REAL,
    rvol_intradia REAL,
    lifecycle_phase TEXT,
    n_velas_analizadas INTEGER,
    notes TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intraday_ticker_date ON candidate_intraday_metrics(ticker, market_date);

CREATE TABLE IF NOT EXISTS candidate_outcome (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    market_date TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    run_up_before_detection_pct REAL,
    max_price_after_detection REAL,
    max_return_after_detection_pct REAL,
    minutes_to_max REAL,
    reached_20 INTEGER NOT NULL DEFAULT 0,
    reached_50 INTEGER NOT NULL DEFAULT 0,
    reached_100 INTEGER NOT NULL DEFAULT 0,
    category TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(ticker, market_date)
);
CREATE INDEX IF NOT EXISTS idx_outcome_date ON candidate_outcome(market_date);

CREATE TABLE IF NOT EXISTS radar_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Capa observacional de ALERTA TEMPRANA (Fase 4, 2026-08-17) --
-- append-only, una fila por CAMBIO de ventana (no por sweep) --
-- ver atlas_live/radar/alert_stage.py. Nunca la lee candidate_gates.py,
-- el score en vivo ni decision_engine.py.
CREATE TABLE IF NOT EXISTS alert_stage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    market_date TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    stage TEXT NOT NULL,
    relative_volume_hoy REAL,
    volatility_14d_pct REAL,
    dias_volumen_elevado INTEGER,
    aceleracion_volumen REAL,
    timing_deteccion_hoy TEXT,
    racional_available INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_ticker_date ON alert_stage_log(ticker, market_date);
CREATE INDEX IF NOT EXISTS idx_alert_date_stage ON alert_stage_log(market_date, stage);

-- Reinicio del aprendizaje (2026-08-15): resumen auditable de cada día de
-- mercado -- estudiadas/candidatas/señales/aciertos/falsos positivos/
-- tardías/bandas, para que el informe de cierre y la precisión acumulada
-- se calculen de una sola fuente, nunca recalculados "a ojo".
CREATE TABLE IF NOT EXISTS daily_summary (
    market_date TEXT PRIMARY KEY,
    n_estudiadas INTEGER,
    n_candidatas INTEGER,
    n_senales INTEGER,
    n_evaluables INTEGER,
    n_aciertos INTEGER,
    n_falsos_positivos INTEGER,
    n_tardias INTEGER,
    n_reached_20 INTEGER,
    n_reached_50 INTEGER,
    n_reached_100 INTEGER,
    computed_at TEXT NOT NULL
);

-- "Que Atlas aprenda" (2026-08-19, pedido explícito del usuario, caso real
-- ETHU/MSTU/BNTX): el EOD ya calculaba `posibles_no_detectadas` -- símbolos
-- del último barrido con |change_pct| >= MISSED_OPPORTUNITY_MIN_CHANGE_PCT
-- que NINGÚN gate marcó como candidata -- pero nunca lo guardaba, se
-- perdía al terminar esa corrida. Esta tabla lo persiste, write-once por
-- (ticker, market_date), puramente informativo: nunca participa en
-- ningún gate ni cambia qué se detecta, solo evita perder la evidencia de
-- lo que el radar no vio.
CREATE TABLE IF NOT EXISTS missed_mover (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    market_date TEXT NOT NULL,
    change_pct_final REAL,
    created_at TEXT NOT NULL,
    UNIQUE(ticker, market_date)
);
CREATE INDEX IF NOT EXISTS idx_missed_mover_date ON missed_mover(market_date);

-- Predicción de magnitud (2026-08-20, aprobado por el usuario, ver mockup
-- "Predicción de Magnitud"): la PRIMERA vez que una candidata se vuelve
-- accionable (estado_final OPORTUNIDAD_PRIORITARIA/VIGILAR), se congela acá
-- la mediana histórica de `historical_scoring.score_candidate()` de ese
-- momento -- write-once por (ticker, market_date), nunca se recalcula
-- después, para poder calificarla contra el resultado real al cierre
-- (`candidate_outcome`) sin que la predicción "se mueva" con el tiempo.
-- Puramente informativo/de trazabilidad: no participa en ningún gate ni en
-- el `estado_final` en sí.
CREATE TABLE IF NOT EXISTS magnitud_prediction (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    market_date TEXT NOT NULL,
    frozen_at TEXT NOT NULL,
    estado_final_al_congelar TEXT,
    direction TEXT,
    timing_deteccion TEXT,
    bucket TEXT,
    muestra_n INTEGER,
    predicted_pct REAL NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(ticker, market_date)
);
CREATE INDEX IF NOT EXISTS idx_magnitud_pred_date ON magnitud_prediction(market_date);

-- SHADOW/VALIDACIÓN de LEK (2026-08-27, Fase 2 de la transición
-- SHADOW->VALIDACIÓN, autorizado explícitamente): `atlas_decision_core.decide()`
-- calcula `decision_shadow`/`shadow_differs` en cada request de
-- /api/radar-oportunidades, pero ese resultado se servía y se perdía --
-- sin ningún registro, era imposible medir después si LEK habría
-- acertado más que el Decision Core real. Esta tabla SOLO persiste el
-- resultado que LEK ya calculó (nunca recalcula nada, nunca es un
-- segundo algoritmo de decisión) -- write-once por (ticker, market_date),
-- mismo patrón que magnitud_prediction/missed_mover de arriba. Solo se
-- escribe cuando shadow_differs=True (ver record_shadow_decision(), que
-- rechaza cualquier otro caso) -- no hace falta una fila por candidata,
-- solo por evento real de divergencia. Puramente informativo/de
-- auditoría: nunca se lee desde atlas_decision_core.py,
-- current_top_opportunity.py, top_opportunity_stability.py, scan_worker.py
-- ni ningún punto que participe en una decisión real -- apply_recalibration
-- sigue en False, sin ninguna vía de configuración, sin cambios en esta fase.
CREATE TABLE IF NOT EXISTS shadow_decision_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    market_date TEXT NOT NULL,
    decision TEXT NOT NULL,
    decision_shadow TEXT NOT NULL,
    shadow_differs INTEGER NOT NULL,
    validation_state TEXT,
    sample_size INTEGER,
    wilson_upper_bound_20_pct REAL,
    baseline_pct_20 REAL,
    recorded_at TEXT NOT NULL,
    UNIQUE(ticker, market_date)
);
CREATE INDEX IF NOT EXISTS idx_shadow_decision_date ON shadow_decision_log(market_date);
"""

_schema_ready_for: Optional[str] = None
# Lock de migración (2026-09-01, fix de concurrencia -- autorizado
# explícitamente, cambio mínimo, solo sincronización, ver `_ensure_schema()`).
_schema_lock = threading.Lock()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Agrega una columna si todavía no existe -- migración aditiva y segura
    para bases ya creadas (`CREATE TABLE IF NOT EXISTS` no agrega columnas a
    una tabla existente). No toca ni una fila de datos. Necesario porque
    `radar_candidates.db` ya está desplegada en producción con el esquema
    de CAPA 2 (sin `es_senal`/`phase_tag`)."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Migra el esquema una sola vez por proceso -- protegido por
    `_schema_lock` (2026-09-01, fix de concurrencia, autorizado
    explícitamente): antes, dos hilos/requests podían llamar a `_connect()`
    casi al mismo tiempo justo después de un reinicio (con
    `_schema_ready_for` todavía en `None`), y ambos intentaban correr esta
    migración a la vez -- colisión real posible en `ALTER TABLE ADD COLUMN`
    (el segundo hilo la encuentra ya agregada por el primero). El
    double-check de abajo (`if _schema_ready_for == str(DB_PATH): return`,
    ya DENTRO del lock) evita que un hilo que esperó el lock repita la
    migración que otro ya terminó mientras esperaba. Ningún cambio de
    tabla/columna/query respecto a antes -- exclusivamente sincronización;
    el cuerpo de la migración es idéntico, solo se movió a esta función."""
    global _schema_ready_for
    with _schema_lock:
        if _schema_ready_for == str(DB_PATH):
            return  # otro hilo ya la corrió mientras este esperaba el lock
        conn.executescript(_SCHEMA)
        _ensure_column(conn, "candidate_detection", "es_senal", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "candidate_detection", "phase_tag", "TEXT")
        _ensure_column(conn, "candidate_detection", "direction_at_detection", "TEXT")
        _ensure_column(conn, "candidate_detection", "comportamiento_post_apertura", "TEXT")
        # Experimentos A/C (2026-08-16) -- SOLO diagnóstico: nunca se leen en
        # candidate_gates.py/phase_classifier.py, no afectan qué se detecta
        # ni el orden en que se muestra. Sirven para comparar, con datos en
        # vivo, si estas señales realmente mejoran algo frente al baseline.
        _ensure_column(conn, "candidate_detection", "volatility_14d_pct_at_detection", "REAL")
        _ensure_column(conn, "candidate_detection", "daily_range_pct_at_detection", "REAL")
        _ensure_column(conn, "candidate_outcome", "perdio_momentum_inmediato", "INTEGER")
        _ensure_column(conn, "candidate_outcome", "direccion_correcta", "INTEGER")
        # Fase 7 (2026-08-18) -- volumen/volatilidad detectan movimiento, no
        # dirección (caso real SEZL, ver alert_stage.py): `direction` es la
        # lectura EN VIVO (ALCISTA/BAJISTA/NEUTRAL/INDEFINIDA) de ese barrido;
        # `change_pct_confiable` distingue un 0.0% real de un dato no
        # disponible (Tradier en premarket muy ilíquido).
        _ensure_column(conn, "alert_stage_log", "direction", "TEXT")
        _ensure_column(conn, "alert_stage_log", "change_pct_confiable", "INTEGER")
        # Eje de retroceso desde máximo intradía (2026-08-18, caso real YYAI:
        # pico $1.57, luego $1.36 -- todavía +13% vs cierre de ayer, pero
        # cayendo fuerte -- ver alert_stage.py). Puramente informativo/de
        # trazabilidad acá; la decisión real vive en classify_alert_stage().
        _ensure_column(conn, "alert_stage_log", "retroceso_desde_maximo_pct", "REAL")
        # Aprendizaje unificado (2026-08-18, pedido explícito del usuario,
        # caso real XOS): trazabilidad del precio EXACTO en el momento de
        # la detección -- estado inicial (A), nunca se pisa después.
        _ensure_column(conn, "candidate_detection", "price_basis_at_detection", "TEXT")
        _ensure_column(conn, "candidate_detection", "bid_at_detection", "REAL")
        _ensure_column(conn, "candidate_detection", "ask_at_detection", "REAL")
        _ensure_column(conn, "candidate_detection", "spread_pct_at_detection", "REAL")
        # Filtro de calidad del aprendizaje (ver classify_learning_quality) --
        # puramente informativo, NUNCA excluye nada de la detección/
        # observación/registro, solo de las estadísticas agregadas por
        # defecto (list_all_evaluated_candidates(solo_confiables=True)).
        _ensure_column(conn, "candidate_outcome", "confiable_para_aprendizaje", "INTEGER")
        _ensure_column(conn, "candidate_outcome", "motivos_sospecha", "TEXT")
        # Resultado en curso vs final (2026-08-18): is_final=0 mientras el
        # mercado sigue abierto (actualizado en cada barrido, barato, desde
        # candidate_observation ya persistida) -- is_final=1 solo lo pone
        # el EOD (evaluate_candidate_outcome, con velas de 1 min de
        # Tradier, más preciso). record_outcome() ahora es upsert por
        # (ticker, market_date) para poder pasar de en-curso a final sin
        # duplicar filas.
        _ensure_column(conn, "candidate_outcome", "is_final", "INTEGER NOT NULL DEFAULT 1")
        # Desglose por tramo del día (2026-08-18, pedido explícito del
        # usuario): separa el recorrido en 4 tramos -- antes de la
        # detección (ya existía: run_up_before_detection_pct), en
        # premarket DESPUÉS de detectarla, post-apertura (09:30 ET en
        # adelante), y el total del día (solo informativo). Todos
        # calculados de las MISMAS velas de 1 min de Tradier que ya se
        # piden para max_return_after_detection_pct -- cero llamadas de
        # red nuevas.
        _ensure_column(conn, "candidate_outcome", "price_at_market_open", "REAL")
        _ensure_column(conn, "candidate_outcome", "max_price_premarket_after_detection", "REAL")
        _ensure_column(conn, "candidate_outcome", "max_return_premarket_after_detection_pct", "REAL")
        _ensure_column(conn, "candidate_outcome", "max_price_regular_session", "REAL")
        _ensure_column(conn, "candidate_outcome", "max_return_post_apertura_pct", "REAL")
        _ensure_column(conn, "candidate_outcome", "total_day_change_pct", "REAL")
        # Retorno al CIERRE desde la detección (2026-08-23, caso real MRNX:
        # tocó +44,3% intradía pero cerró en +17,8% -- "eso no es acierto",
        # pedido explícito del usuario de cambiar el criterio de Precisión
        # de Magnitud de "máximo intradía" a "cierre real del día", en TODA
        # esa función. `max_return_after_detection_pct` no se toca --
        # sigue siendo la fuente de reached_20/50/100/category en el resto
        # de Atlas, sin cambios.
        _ensure_column(conn, "candidate_outcome", "close_price_after_detection", "REAL")
        _ensure_column(conn, "candidate_outcome", "close_return_after_detection_pct", "REAL")
        # PM-RVOL Fase 2 (2026-08-25, pedido explícito del usuario) --
        # trazabilidad/aprendizaje, NUNCA detección: congela las 2 señales
        # de volumen premarket (`atlas_live/radar/candidate_gates.py::
        # premarket_volume_percentile()`/`premarket_volume_acceleration()`)
        # tal como estaban en el momento EXACTO de la primera detección --
        # mismo criterio "estado A, inmutable" que `price_basis_at_detection`
        # de arriba. Ninguna de estas 7 columnas la lee ningún gate, el
        # scoring, el ranking ni `decision_engine.py` -- son diagnóstico
        # puro, para poder correlacionar después contra `candidate_outcome`.
        # `_at_detection` REAL/TEXT quedan `NULL` cuando la señal no tuvo
        # evidencia suficiente (nunca se convierte en 0) -- el campo
        # `_state_at_detection` documenta siempre por qué.
        _ensure_column(conn, "candidate_detection", "premarket_volume_percentile_at_detection", "REAL")
        _ensure_column(conn, "candidate_detection", "premarket_volume_percentile_state_at_detection", "TEXT")
        _ensure_column(conn, "candidate_detection", "premarket_volume_acceleration_at_detection", "REAL")
        _ensure_column(conn, "candidate_detection", "premarket_volume_acceleration_state_at_detection", "TEXT")
        # Campos de reproducibilidad (pedido explícito): permiten reconstruir
        # después CON QUÉ universo/volumen se calculó el percentil/aceleración
        # de ese momento, sin tener que confiar solo en el número final.
        _ensure_column(conn, "candidate_detection", "pm_universe_size_at_detection", "INTEGER")
        _ensure_column(conn, "candidate_detection", "pm_volume_at_detection", "INTEGER")
        _ensure_column(conn, "candidate_detection", "pm_dollar_volume_at_detection", "REAL")
        _schema_ready_for = str(DB_PATH)


# Hito 5, Fase 5.3 (2026-09-04, autorizado explícitamente): conexión
# reutilizada ESTRICTAMENTE POR HILO -- optimización explícitamente
# diferida desde el hotfix de concurrencia `3f75dea` (2026-09-03), que
# resolvió la CONTENCIÓN entre requests concurrentes pero dejó intacto el
# costo base de abrir una conexión SQLite nueva (con su propio PRAGMA
# WAL/busy_timeout) en cada una de las ~46 llamadas a `_connect()` que
# puede hacer un solo request a `/api/radar-oportunidades` (~1.500
# candidatas). `threading.local()` -- nunca `check_same_thread=False` ni
# un pool compartido -- porque SQLite no garantiza seguridad de una
# conexión usada desde más de un hilo; cada hilo (los 8 de gunicorn, el
# hilo del radar, el watchdog de Fase 5.2, cualquier hilo de test) obtiene
# su propia conexión, nunca comparte la de otro.
_thread_local = threading.local()


def _connect() -> sqlite3.Connection:
    """Reutiliza la conexión de ESTE hilo si `DB_PATH` no cambió desde que
    se abrió -- si cambió (única forma real de que esto ocurra: los tests
    de este repo reasignan `DB_PATH` a un tempfile distinto entre corridas,
    dentro del mismo hilo de pytest), cierra la vieja y abre una nueva
    contra la ruta actual, para nunca leer/escribir sobre la DB de un test
    anterior. Mismo `journal_mode=WAL`/`busy_timeout=15000`/`row_factory`/
    `_ensure_schema()` de siempre -- comportamiento observable idéntico,
    solo cambia CUÁNTAS veces se abre una conexión nueva de verdad.

    Chequeo de vida (`SELECT 1`) antes de devolver la conexión cacheada
    (hallazgo real, 2026-09-04, `test_migracion_concurrente_no_colisiona`):
    bajo el contrato ANTERIOR (conexión nueva en cada llamada), cerrar la
    conexión devuelta por `_connect()` era seguro -- un patrón real ya
    usado a propósito en ese test para forzar releer un archivo recién
    creado. Con reutilización por-hilo, esa misma conexión cerrada
    quedaría cacheada y rompería TODAS las llamadas siguientes en ese
    hilo -- este chequeo (costo despreciable frente al de abrir una
    conexión nueva, confirmado en el benchmark) detecta ese caso y
    reabre, en vez de devolver una conexión muerta."""
    ruta_actual = str(DB_PATH)
    conn = getattr(_thread_local, "conn", None)
    if conn is not None and getattr(_thread_local, "conn_path", None) == ruta_actual:
        try:
            conn.execute("SELECT 1")
            return conn
        except sqlite3.ProgrammingError:
            conn = None  # cerrada por fuera de este mecanismo -- se reabre abajo

    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    if _schema_ready_for != ruta_actual:
        _ensure_schema(conn)
    _thread_local.conn = conn
    _thread_local.conn_path = ruta_actual
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    return dict(r)


# --------------------------- detección ---------------------------

def is_detected(ticker: str, market_date: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM candidate_detection WHERE ticker=? AND market_date=?", (ticker, market_date)
        ).fetchone()
        return row is not None


def record_detection(
    ticker: str, market_date: str, session: str, detected_at: str, sweep_id: str,
    price_at_detection: Optional[float], change_pct_at_detection: Optional[float],
    volume_at_detection: Optional[int], average_volume_at_detection: Optional[int],
    relative_volume_at_detection: Optional[float], dollar_volume_at_detection: Optional[float],
    gates_fired: List[Dict[str, Any]], source: str = "tradier",
    price_basis_at_detection: Optional[str] = None, bid_at_detection: Optional[float] = None,
    ask_at_detection: Optional[float] = None, spread_pct_at_detection: Optional[float] = None,
    pm_percentile_at_detection: Optional[float] = None, pm_percentile_state_at_detection: Optional[str] = None,
    pm_acceleration_at_detection: Optional[float] = None, pm_acceleration_state_at_detection: Optional[str] = None,
    pm_universe_size_at_detection: Optional[int] = None, pm_volume_at_detection: Optional[int] = None,
    pm_dollar_volume_at_detection: Optional[float] = None,
) -> bool:
    """Registra la primera detección. Devuelve True si fue nueva (INSERT
    real), False si ya existía (idempotente, nunca se pisa).

    `price_basis_at_detection`/`bid_at_detection`/`ask_at_detection`/
    `spread_pct_at_detection` (2026-08-18, pedido explícito del usuario):
    trazabilidad EXACTA del precio en el instante de la detección --
    "estado A" (lo que Atlas vio), inmutable, nunca se pisa por
    reclasificaciones posteriores (esas viven en `alert_stage_log`,
    "estado B", tabla completamente separada -- ver `candidate_full_history`).

    `pm_percentile_at_detection`/`pm_acceleration_at_detection` (+ sus
    `_state_at_detection`, 2026-08-25, PM-RVOL Fase 2): mismo criterio --
    las 2 señales de `candidate_gates.premarket_volume_*()` congeladas TAL
    CUAL estaban en este barrido, nunca recalculadas después. `None` real
    cuando la señal no tuvo evidencia (el `_state` explica por qué) --
    `INSERT` escribe `NULL` sin necesidad de lógica especial. Los 3
    `pm_*_at_detection` restantes son puramente de reproducibilidad -- con
    qué universo/volumen se calculó, para poder auditar el número después
    sin tener que confiar ciegamente en él."""
    with _connect() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO candidate_detection
               (ticker, market_date, session, detected_at, sweep_id, price_at_detection,
                change_pct_at_detection, volume_at_detection, average_volume_at_detection,
                relative_volume_at_detection, dollar_volume_at_detection, gates_fired, source, created_at,
                price_basis_at_detection, bid_at_detection, ask_at_detection, spread_pct_at_detection,
                premarket_volume_percentile_at_detection, premarket_volume_percentile_state_at_detection,
                premarket_volume_acceleration_at_detection, premarket_volume_acceleration_state_at_detection,
                pm_universe_size_at_detection, pm_volume_at_detection, pm_dollar_volume_at_detection)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ticker, market_date, session, detected_at, sweep_id, price_at_detection,
             change_pct_at_detection, volume_at_detection, average_volume_at_detection,
             relative_volume_at_detection, dollar_volume_at_detection,
             json.dumps(gates_fired, ensure_ascii=False), source, _now(),
             price_basis_at_detection, bid_at_detection, ask_at_detection, spread_pct_at_detection,
             pm_percentile_at_detection, pm_percentile_state_at_detection,
             pm_acceleration_at_detection, pm_acceleration_state_at_detection,
             pm_universe_size_at_detection, pm_volume_at_detection, pm_dollar_volume_at_detection),
        )
        conn.commit()
        return cur.rowcount > 0


def record_observation(
    ticker: str, market_date: str, observed_at: str, sweep_id: str,
    price: Optional[float], change_pct: Optional[float], volume: Optional[int],
    relative_volume: Optional[float], gates_fired_now: List[Dict[str, Any]],
    vwap: Optional[float] = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO candidate_observation
               (ticker, market_date, observed_at, sweep_id, price, change_pct, volume,
                relative_volume, gates_fired_now, vwap, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (ticker, market_date, observed_at, sweep_id, price, change_pct, volume,
             relative_volume, json.dumps(gates_fired_now, ensure_ascii=False), vwap, _now()),
        )
        conn.commit()


def get_observations(ticker: str, market_date: str) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM candidate_observation WHERE ticker=? AND market_date=? ORDER BY observed_at",
            (ticker, market_date),
        ).fetchall()
        return [_row(r) for r in rows]


def max_price_today(ticker: str, market_date: str) -> Optional[float]:
    """Precio máximo observado hoy para este ticker (2026-08-18, eje de
    retroceso desde máximo intradía -- ver alert_stage.py). Lee
    `candidate_observation`, que ya se puebla en CADA barrido desde antes
    de esta función -- nunca depende de memoria efímera (`SweepHistory`),
    así que un reinicio del contenedor NUNCA le hace "olvidar" el máximo
    real ya alcanzado hoy. `None` si todavía no hay ninguna observación
    con precio."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT MAX(price) AS maxp FROM candidate_observation WHERE ticker=? AND market_date=? AND price IS NOT NULL",
            (ticker, market_date),
        ).fetchone()
        return row["maxp"] if row and row["maxp"] is not None else None


def movers_since_detection(market_date: str, min_pct: float = 10.0) -> List[Dict[str, Any]]:
    """Investigación (2026-08-18, caso real XOS validado externamente con
    TradingView): para CADA candidata detectada hoy (todo el universo
    Tradier, nunca solo Racional), calcula el % real desde
    `price_at_detection` hasta el precio MÁXIMO efectivamente observado
    (`MAX(candidate_observation.price)`) -- ambos ya persistidos, ningún
    dato nuevo ni inventado. Devuelve solo las que llegaron a >= `min_pct`,
    ordenadas de mayor a menor. Puramente de solo lectura -- no escribe
    nada, no participa en ningún gate ni clasificación."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT d.ticker, d.detected_at, d.price_at_detection, d.session,
                      d.relative_volume_at_detection, MAX(o.price) AS max_price
               FROM candidate_detection d
               JOIN candidate_observation o ON o.ticker = d.ticker AND o.market_date = d.market_date
               WHERE d.market_date = ? AND d.price_at_detection IS NOT NULL AND d.price_at_detection > 0
               GROUP BY d.ticker""",
            (market_date,),
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        price_at_detection = r["price_at_detection"]
        max_price = r["max_price"]
        if max_price is None:
            continue
        max_pct_gain = round((max_price - price_at_detection) / price_at_detection * 100, 2)
        if max_pct_gain >= min_pct:
            out.append({
                "ticker": r["ticker"], "detected_at": r["detected_at"],
                "price_at_detection": price_at_detection, "max_price": max_price,
                "max_pct_gain": max_pct_gain, "session": r["session"],
                "relative_volume_at_detection": r["relative_volume_at_detection"],
            })
    out.sort(key=lambda m: -m["max_pct_gain"])
    return out


def get_detection(ticker: str, market_date: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM candidate_detection WHERE ticker=? AND market_date=?", (ticker, market_date)
        ).fetchone()
        if row is None:
            return None
        d = _row(row)
        d["gates_fired"] = json.loads(d["gates_fired"]) if d.get("gates_fired") else []
        return d


def list_candidates_for_date(market_date: str) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM candidate_detection WHERE market_date=? ORDER BY detected_at", (market_date,)
        ).fetchall()
        out = []
        for r in rows:
            d = _row(r)
            d["gates_fired"] = json.loads(d["gates_fired"]) if d.get("gates_fired") else []
            out.append(d)
        return out


def count_candidates_for_date(market_date: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM candidate_detection WHERE market_date=?", (market_date,)
        ).fetchone()
        return row["n"] if row else 0


def mark_as_signal(ticker: str, market_date: str) -> None:
    """Reinicio del aprendizaje (2026-08-15): una candidata pasa a "señal"
    cuando sigue activa en un segundo barrido (no fue un parpadeo de un solo
    tick) -- llamado desde `candidate_tracker` al procesar la segunda
    observación de una candidata ya existente."""
    with _connect() as conn:
        conn.execute(
            "UPDATE candidate_detection SET es_senal=1 WHERE ticker=? AND market_date=?", (ticker, market_date)
        )
        conn.commit()


def set_phase_tag(
    ticker: str, market_date: str, phase_tag: str,
    direction_at_detection: Optional[str] = None, comportamiento_post_apertura: Optional[str] = None,
) -> None:
    """Guarda las 3 dimensiones independientes del clasificador de fase
    (2026-08-15) -- cada una se actualiza por separado, ninguna se pisa con
    None si no se pasó."""
    sets = ["phase_tag=?"]
    params: list = [phase_tag]
    if direction_at_detection is not None:
        sets.append("direction_at_detection=?")
        params.append(direction_at_detection)
    if comportamiento_post_apertura is not None:
        sets.append("comportamiento_post_apertura=?")
        params.append(comportamiento_post_apertura)
    params += [ticker, market_date]
    with _connect() as conn:
        conn.execute(
            f"UPDATE candidate_detection SET {', '.join(sets)} WHERE ticker=? AND market_date=?", params
        )
        conn.commit()


def set_experimental_signals(
    ticker: str, market_date: str,
    volatility_14d_pct: Optional[float] = None, daily_range_pct: Optional[float] = None,
) -> None:
    """Experimentos A/C (2026-08-16) -- guarda las señales de diagnóstico en
    `candidate_detection`. Igual que `set_phase_tag`: cada campo se
    actualiza solo si se pasó, nunca se pisa con None. Estas columnas no las
    lee ningún gate ni el ranking -- son exclusivamente para comparar,
    después, contra el baseline."""
    sets = []
    params: list = []
    if volatility_14d_pct is not None:
        sets.append("volatility_14d_pct_at_detection=?")
        params.append(volatility_14d_pct)
    if daily_range_pct is not None:
        sets.append("daily_range_pct_at_detection=?")
        params.append(daily_range_pct)
    if not sets:
        return
    params += [ticker, market_date]
    with _connect() as conn:
        conn.execute(f"UPDATE candidate_detection SET {', '.join(sets)} WHERE ticker=? AND market_date=?", params)
        conn.commit()


def count_signals_for_date(market_date: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM candidate_detection WHERE market_date=? AND es_senal=1", (market_date,)
        ).fetchone()
        return row["n"] if row else 0


# --------------------------- alerta temprana (Fase 4, 2026-08-17) ---------------------------

def latest_alert_stage(ticker: str, market_date: str) -> Optional[str]:
    """Última ventana registrada para esta candidata hoy, o `None` si
    todavía no tiene ninguna -- usado por `candidate_tracker` para saber si
    hay que insertar una fila nueva (solo ante un CAMBIO real de ventana)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT stage FROM alert_stage_log WHERE ticker=? AND market_date=? ORDER BY observed_at DESC LIMIT 1",
            (ticker, market_date),
        ).fetchone()
        return row["stage"] if row else None


def record_alert_stage(
    ticker: str, market_date: str, observed_at: str, stage: str,
    relative_volume_hoy: Optional[float] = None, volatility_14d_pct: Optional[float] = None,
    dias_volumen_elevado: Optional[int] = None, aceleracion_volumen: Optional[float] = None,
    timing_deteccion_hoy: Optional[str] = None, racional_available: Optional[bool] = None,
    direction: Optional[str] = None, change_pct_confiable: Optional[bool] = None,
    retroceso_desde_maximo_pct: Optional[float] = None,
) -> bool:
    """Registra una nueva fila SOLO si `stage` es distinto del último
    registrado para esta candidata hoy (evita miles de filas duplicadas por
    sweep -- append-only de TRANSICIONES, no de cada observación). Devuelve
    True si se insertó.

    `retroceso_desde_maximo_pct` (2026-08-18): puramente informativo/de
    trazabilidad -- para cuando `stage=="NO_PERSEGUIR"` por haber caído
    fuerte desde su máximo de hoy, poder explicar exactamente cuánto (ver
    `alert_stage.classify_alert_stage`)."""
    if latest_alert_stage(ticker, market_date) == stage:
        return False
    with _connect() as conn:
        conn.execute(
            """INSERT INTO alert_stage_log
               (ticker, market_date, observed_at, stage, relative_volume_hoy, volatility_14d_pct,
                dias_volumen_elevado, aceleracion_volumen, timing_deteccion_hoy, racional_available,
                direction, change_pct_confiable, retroceso_desde_maximo_pct, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ticker, market_date, observed_at, stage, relative_volume_hoy, volatility_14d_pct,
             dias_volumen_elevado, aceleracion_volumen, timing_deteccion_hoy,
             None if racional_available is None else int(racional_available),
             direction, None if change_pct_confiable is None else int(change_pct_confiable),
             retroceso_desde_maximo_pct, _now()),
        )
        conn.commit()
        return True


def alert_stage_history_for_date(market_date: str) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM alert_stage_log WHERE market_date=? ORDER BY ticker, observed_at",
            (market_date,),
        ).fetchall()
        return [_row(r) for r in rows]


def alert_stage_history_for_ticker(ticker: str, market_date: str) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM alert_stage_log WHERE ticker=? AND market_date=? ORDER BY observed_at",
            (ticker, market_date),
        ).fetchall()
        return [_row(r) for r in rows]


def alert_stage_effectiveness_report(market_date: Optional[str] = None) -> Dict[str, Any]:
    """Mide con evidencia real qué tan efectiva fue cada ventana: cuántas
    ALERTA_TEMPRANA/ALERTA_FUERTE avanzan a INICIO/CONFIRMACION, cuántas
    terminan con `candidate_outcome.reached_20/50/100`, tiempo real (en
    minutos) desde la alerta hasta el inicio, cuántas ALERTA_FUERTE nunca
    llegan a +20% (falso positivo), y el mismo desglose separado por
    `racional_available` (capturado EN VIVO en cada fila, no una foto de la
    Base Histórica). `market_date=None` -- toda la historia registrada.
    Solo lectura, nunca escribe nada."""
    from atlas_live.radar.alert_stage import ALERT_STAGES

    with _connect() as conn:
        if market_date:
            rows = conn.execute(
                "SELECT * FROM alert_stage_log WHERE market_date=? ORDER BY ticker, market_date, observed_at",
                (market_date,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM alert_stage_log ORDER BY ticker, market_date, observed_at"
            ).fetchall()
    log = [_row(r) for r in rows]

    por_candidata: Dict[tuple, List[Dict[str, Any]]] = {}
    for r in log:
        por_candidata.setdefault((r["ticker"], r["market_date"]), []).append(r)

    outcomes: Dict[tuple, Dict[str, Any]] = {}
    with _connect() as conn:
        for ticker, md in por_candidata:
            row = conn.execute(
                "SELECT reached_20, reached_50, reached_100 FROM candidate_outcome WHERE ticker=? AND market_date=?",
                (ticker, md),
            ).fetchone()
            if row:
                outcomes[(ticker, md)] = _row(row)

    def _empty_bucket() -> Dict[str, Any]:
        return {
            "n_candidatas": 0, "n_avanza_a_inicio_o_confirmacion": 0,
            "n_con_outcome_cerrado": 0, "n_reached_20": 0, "n_reached_50": 0, "n_reached_100": 0,
            "n_falso_positivo": 0, "_tiempos_min": [],
        }

    general = {s: _empty_bucket() for s in ALERT_STAGES}
    racional = {
        "true": {s: _empty_bucket() for s in ALERT_STAGES},
        "false": {s: _empty_bucket() for s in ALERT_STAGES},
        "desconocido": {s: _empty_bucket() for s in ALERT_STAGES},
    }

    for (ticker, md), transiciones in por_candidata.items():
        vistos: set = set()
        outcome = outcomes.get((ticker, md))
        racional_val = transiciones[0].get("racional_available")
        racional_key = "true" if racional_val == 1 else ("false" if racional_val == 0 else "desconocido")

        for i, t in enumerate(transiciones):
            stage = t["stage"]
            if stage not in ALERT_STAGES or stage in vistos:
                continue  # una sola vez por candidata, la PRIMERA vez que llegó a esa ventana
            vistos.add(stage)

            for bucket in (general[stage], racional[racional_key][stage]):
                bucket["n_candidatas"] += 1
                avanza = any(t2["stage"] in ("INICIO", "CONFIRMACION") for t2 in transiciones[i + 1:])
                if stage in ("ALERTA_TEMPRANA", "ALERTA_FUERTE") and avanza:
                    bucket["n_avanza_a_inicio_o_confirmacion"] += 1
                    inicio = next((t2 for t2 in transiciones[i + 1:] if t2["stage"] in ("INICIO", "CONFIRMACION")), None)
                    if inicio is not None:
                        try:
                            dt1 = datetime.fromisoformat(t["observed_at"])
                            dt2 = datetime.fromisoformat(inicio["observed_at"])
                            bucket["_tiempos_min"].append(round((dt2 - dt1).total_seconds() / 60.0, 1))
                        except (ValueError, TypeError):
                            pass
                if outcome is not None:
                    bucket["n_con_outcome_cerrado"] += 1
                    if outcome.get("reached_20"):
                        bucket["n_reached_20"] += 1
                    if outcome.get("reached_50"):
                        bucket["n_reached_50"] += 1
                    if outcome.get("reached_100"):
                        bucket["n_reached_100"] += 1
                    if stage == "ALERTA_FUERTE" and not outcome.get("reached_20"):
                        bucket["n_falso_positivo"] += 1

    def _finalize(buckets: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        out = {}
        for stage, b in buckets.items():
            tiempos = b.pop("_tiempos_min")
            b["tiempo_promedio_hasta_inicio_min"] = round(sum(tiempos) / len(tiempos), 1) if tiempos else None
            b["tiempo_mediana_hasta_inicio_min"] = (
                round(sorted(tiempos)[len(tiempos) // 2], 1) if tiempos else None
            )
            out[stage] = b
        return out

    return {
        "market_date": market_date,
        "general": _finalize(general),
        "racional_available_true": _finalize(racional["true"]),
        "racional_available_false": _finalize(racional["false"]),
        "racional_available_desconocido": _finalize(racional["desconocido"]),
    }


def current_alert_stages_for_date(market_date: str) -> List[Dict[str, Any]]:
    """La última ventana de cada candidata que tuvo alguna alerta hoy --
    una fila por ticker, la más reciente -- para el panel en vivo de la
    Cabina (estado actual, no el historial completo de transiciones)."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT a.* FROM alert_stage_log a
               JOIN (
                   SELECT ticker, MAX(observed_at) AS max_observed_at
                   FROM alert_stage_log WHERE market_date=? GROUP BY ticker
               ) latest ON latest.ticker = a.ticker AND latest.max_observed_at = a.observed_at
               WHERE a.market_date=?
               ORDER BY a.observed_at DESC""",
            (market_date, market_date),
        ).fetchall()
        return [_row(r) for r in rows]


def candidate_timeline(ticker: str, market_date: str) -> Dict[str, Any]:
    """Reconstruye la evolución completa de UNA candidata en UN día (Fase 5,
    2026-08-17, diagnóstico pedido por el usuario tras el caso real de ZIM):
    une lo que ya está guardado en 3 tablas -- la detección inicial
    (`candidate_detection`), cada observación de seguimiento (un punto por
    barrido en que siguió viéndose, `candidate_observation`) y cada
    transición real de ventana de alerta (`alert_stage_log`) -- más el
    resultado de cierre si ya existe (`candidate_outcome`). Permite
    confirmar con evidencia minuto a minuto si una candidata pasó realmente
    por PREPARACION -> ALERTA_TEMPRANA -> ALERTA_FUERTE -> INICIO, o en qué
    paso se quedó, y comparar el precio/momento de cada punto.

    `racional_available` se calcula EN VIVO (mismo criterio ya usado por
    `candidate_tracker._tag_alert_stage`) -- es una etiqueta informativa,
    nunca decide qué se incluye en la respuesta ni limita ninguna de las 3
    tablas. Puramente de solo lectura: no calcula nada nuevo, no escribe
    nada, no toca `candidate_gates.py`, el score en vivo ni
    `decision_engine.py`."""
    ticker = ticker.upper()
    observaciones = get_observations(ticker, market_date)
    for o in observaciones:
        o["gates_fired_now"] = json.loads(o["gates_fired_now"]) if o.get("gates_fired_now") else []

    racional_available = None
    try:
        from atlas.data.universe import is_available

        racional_available = is_available(ticker)
    except Exception:
        racional_available = None

    return {
        "ticker": ticker,
        "market_date": market_date,
        "detection": get_detection(ticker, market_date),
        "observaciones": observaciones,
        "transiciones_alerta": alert_stage_history_for_ticker(ticker, market_date),
        "outcome": get_outcome(ticker, market_date),
        "racional_available": racional_available,
    }


DETECCION_TEMPRANA = "DETECCION_TEMPRANA"


def live_opportunities(market_date: str) -> List[Dict[str, Any]]:
    """Prioridades 1/2/3 (Fase 6, 2026-08-18): vista de solo lectura que
    une CADA candidata detectada por Tradier hoy (`candidate_detection`,
    nunca se borra) con su última etapa de Alerta Temprana conocida
    (`alert_stage_log`) -- para que una detección real nunca deje de
    aparecer acá por Memory Engine, Radar Explosivo, Yahoo/Finnhub o
    `market_cap_bucket`, que son capas completamente aparte (Recomendación)
    y no participan en esta función.

    Si el ticker todavía no tiene ninguna fila en `alert_stage_log` (nunca
    cruzó ni el piso de PREPARACION), la etapa se expone como
    `DETECCION_TEMPRANA` -- un valor de PRESENTACIÓN, no un estado nuevo
    del clasificador (`alert_stage.py` no se toca).

    `racional_available` se recalcula EN VIVO por ticker (mismo criterio
    que `_tag_alert_stage`), nunca se lee de una fila vieja de
    `alert_stage_log` -- y en ningún caso decide si el ticker aparece en
    la lista, solo es un campo más."""
    detecciones = list_candidates_for_date(market_date)
    stages_by_ticker = {a["ticker"]: a for a in current_alert_stages_for_date(market_date)}

    try:
        from atlas.data.universe import is_available
    except Exception:
        is_available = None

    out: List[Dict[str, Any]] = []
    for d in detecciones:
        stage_row = stages_by_ticker.get(d["ticker"])
        racional_available = None
        if is_available is not None:
            try:
                racional_available = is_available(d["ticker"])
            except Exception:
                racional_available = None
        out.append({
            "ticker": d["ticker"],
            "detected_at": d["detected_at"],
            "session": d["session"],
            "source": d["source"],
            "price_at_detection": d["price_at_detection"],
            "change_pct_at_detection": d["change_pct_at_detection"],
            "relative_volume_at_detection": d["relative_volume_at_detection"],
            "volume_at_detection": d["volume_at_detection"],
            "gates_fired": d["gates_fired"],
            "phase_tag": d.get("phase_tag"),
            "stage": stage_row["stage"] if stage_row else DETECCION_TEMPRANA,
            "stage_observed_at": stage_row["observed_at"] if stage_row else None,
            "relative_volume_hoy": stage_row.get("relative_volume_hoy") if stage_row else None,
            "volatility_14d_pct": stage_row.get("volatility_14d_pct") if stage_row else None,
            "dias_volumen_elevado": stage_row.get("dias_volumen_elevado") if stage_row else None,
            "timing_deteccion_hoy": stage_row.get("timing_deteccion_hoy") if stage_row else None,
            "direction": stage_row.get("direction") if stage_row else d.get("direction_at_detection"),
            "direction_at_detection": d.get("direction_at_detection"),
            "change_pct_confiable": stage_row.get("change_pct_confiable") if stage_row else None,
            "retroceso_desde_maximo_pct": stage_row.get("retroceso_desde_maximo_pct") if stage_row else None,
            "racional_available": racional_available,
            # Cierre de arquitectura (2026-08-18): ya se guardaban en
            # candidate_detection (tarea de detección temprana, 2026-08-16),
            # solo faltaba exponerlos acá -- evidencia para
            # historical_scoring.score_candidate() en el endpoint.
            "volatility_14d_pct_at_detection": d.get("volatility_14d_pct_at_detection"),
            "daily_range_pct_at_detection": d.get("daily_range_pct_at_detection"),
        })
    return out


# --------------------------- análisis 1 minuto ---------------------------

def record_intraday_metrics(
    ticker: str, market_date: str, vwap: Optional[float], price_vs_vwap_pct: Optional[float],
    velocity_pct_per_min: Optional[float], acceleration: Optional[float],
    rvol_intradia: Optional[float], lifecycle_phase: Optional[str], n_velas_analizadas: int,
    notes: Optional[str] = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO candidate_intraday_metrics
               (ticker, market_date, computed_at, vwap, price_vs_vwap_pct, velocity_pct_per_min,
                acceleration, rvol_intradia, lifecycle_phase, n_velas_analizadas, notes, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ticker, market_date, _now(), vwap, price_vs_vwap_pct, velocity_pct_per_min,
             acceleration, rvol_intradia, lifecycle_phase, n_velas_analizadas, notes, _now()),
        )
        conn.commit()


def get_latest_intraday_metrics(ticker: str, market_date: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM candidate_intraday_metrics WHERE ticker=? AND market_date=?
               ORDER BY computed_at DESC LIMIT 1""",
            (ticker, market_date),
        ).fetchone()
        return _row(row) if row else None


# --------------------------- filtro de calidad del aprendizaje ---------------------------
# 2026-08-18, pedido explícito del usuario -- evidencia real del cierre de
# hoy: 5 candidatas que llegaron a "+100%" tenían dollar_volume_at_detection
# de $125-$24.826 (RCON, CXAI, AIXC, CAST, UZX -- todas disparadas por
# `despertar` sobre un precio prácticamente sin operar), contra 5
# confirmadas con $1.157.769-$49.788.234 (CDTG, IPST, XOS, WETO, PFSA,
# todas por `volumen_relativo` con dinero real). Brecha limpia de ~47x
# entre el sospechoso más alto y el confirmado más bajo -- $50.000 queda
# en el medio, con margen amplio de los dos lados. Configurable, no
# hardcodeado, para poder ajustar con más evidencia sin tocar código.
LEARNING_MIN_DOLLAR_VOLUME = float(_os.environ.get("ATLAS_LEARNING_MIN_DOLLAR_VOLUME", 50_000))


def classify_learning_quality(detection_row: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Decide si una detección cuenta como evidencia CONFIABLE para las
    estadísticas de aprendizaje agregadas. NUNCA excluye nada de la
    detección, la observación ni el registro histórico -- Atlas sigue
    viendo y guardando TODO el mercado sin excepción; esto solo decide qué
    entra por defecto a `list_all_evaluated_candidates(solo_confiables=True)`
    y a `explosion_bands_tradier()`. Regla aprobada por el usuario:
    `dollar_volume_at_detection >= LEARNING_MIN_DOLLAR_VOLUME`."""
    motivos: List[str] = []
    dollar_volume = detection_row.get("dollar_volume_at_detection")
    rvol = detection_row.get("relative_volume_at_detection")

    if dollar_volume is None:
        motivos.append("dinero_operado_desconocido")
    elif dollar_volume < LEARNING_MIN_DOLLAR_VOLUME:
        motivos.append("dinero_insuficiente")

    if rvol is not None and rvol < 0.05:
        motivos.append("rvol_anomalo")  # informativo -- no cambia el booleano por sí solo

    confiable = dollar_volume is not None and dollar_volume >= LEARNING_MIN_DOLLAR_VOLUME
    return confiable, motivos


# --------------------------- resultado (EOD + en curso) ---------------------------

def has_outcome(ticker: str, market_date: str) -> bool:
    """True si existe CUALQUIER outcome (en curso o final). Ver
    `has_final_outcome()` para el chequeo que usa el EOD (no reintentar lo
    ya cerrado, pero sí actualizar lo que solo estaba "en curso")."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM candidate_outcome WHERE ticker=? AND market_date=?", (ticker, market_date)
        ).fetchone()
        return row is not None


def has_final_outcome(ticker: str, market_date: str) -> bool:
    """True solo si el outcome ya es FINAL (`is_final=1`, puesto por el
    EOD). Un outcome "en curso" (is_final=0, actualizado en vivo durante
    el día) no cuenta -- el EOD debe poder reemplazarlo por el definitivo."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM candidate_outcome WHERE ticker=? AND market_date=? AND is_final=1",
            (ticker, market_date),
        ).fetchone()
        return row is not None


def _deserialize_outcome(d: Dict[str, Any]) -> Dict[str, Any]:
    d["motivos_sospecha"] = json.loads(d["motivos_sospecha"]) if d.get("motivos_sospecha") else []
    return d


def get_outcome(ticker: str, market_date: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM candidate_outcome WHERE ticker=? AND market_date=?", (ticker, market_date)
        ).fetchone()
        return _deserialize_outcome(_row(row)) if row else None


def record_outcome(
    ticker: str, market_date: str, run_up_before_detection_pct: Optional[float],
    max_price_after_detection: Optional[float], max_return_after_detection_pct: Optional[float],
    minutes_to_max: Optional[float], reached_20: bool, reached_50: bool, reached_100: bool,
    category: str, notes: Optional[str] = None,
    perdio_momentum_inmediato: Optional[bool] = None, direccion_correcta: Optional[bool] = None,
    confiable_para_aprendizaje: Optional[bool] = None, motivos_sospecha: Optional[List[str]] = None,
    is_final: bool = True,
    price_at_market_open: Optional[float] = None,
    max_price_premarket_after_detection: Optional[float] = None,
    max_return_premarket_after_detection_pct: Optional[float] = None,
    max_price_regular_session: Optional[float] = None,
    max_return_post_apertura_pct: Optional[float] = None,
    total_day_change_pct: Optional[float] = None,
    close_price_after_detection: Optional[float] = None,
    close_return_after_detection_pct: Optional[float] = None,
) -> bool:
    """Upsert por (ticker, market_date) -- 2026-08-18, pedido explícito del
    usuario: un resultado "en curso" (`is_final=False`, calculado barato
    desde `candidate_observation` mientras el mercado sigue abierto, ver
    `compute_interim_outcome`) debe poder actualizarse en cada barrido sin
    duplicar filas, y el EOD (`is_final=True`, con velas de 1 min de
    Tradier, más preciso) debe poder reemplazarlo -- nunca los dos a la
    vez, siempre UNA fila por candidata por día, la más reciente/confiable
    disponible. `computed_at` se actualiza en cada upsert; `created_at` solo
    la primera vez (se preserva)."""
    with _connect() as conn:
        conn.execute(
            """INSERT INTO candidate_outcome
               (ticker, market_date, computed_at, run_up_before_detection_pct, max_price_after_detection,
                max_return_after_detection_pct, minutes_to_max, reached_20, reached_50, reached_100,
                category, notes, perdio_momentum_inmediato, direccion_correcta,
                confiable_para_aprendizaje, motivos_sospecha, is_final,
                price_at_market_open, max_price_premarket_after_detection,
                max_return_premarket_after_detection_pct, max_price_regular_session,
                max_return_post_apertura_pct, total_day_change_pct,
                close_price_after_detection, close_return_after_detection_pct, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(ticker, market_date) DO UPDATE SET
                 computed_at=excluded.computed_at,
                 run_up_before_detection_pct=excluded.run_up_before_detection_pct,
                 max_price_after_detection=excluded.max_price_after_detection,
                 max_return_after_detection_pct=excluded.max_return_after_detection_pct,
                 minutes_to_max=excluded.minutes_to_max,
                 reached_20=excluded.reached_20, reached_50=excluded.reached_50,
                 reached_100=excluded.reached_100, category=excluded.category, notes=excluded.notes,
                 perdio_momentum_inmediato=excluded.perdio_momentum_inmediato,
                 direccion_correcta=excluded.direccion_correcta,
                 confiable_para_aprendizaje=excluded.confiable_para_aprendizaje,
                 motivos_sospecha=excluded.motivos_sospecha, is_final=excluded.is_final,
                 price_at_market_open=excluded.price_at_market_open,
                 max_price_premarket_after_detection=excluded.max_price_premarket_after_detection,
                 max_return_premarket_after_detection_pct=excluded.max_return_premarket_after_detection_pct,
                 max_price_regular_session=excluded.max_price_regular_session,
                 max_return_post_apertura_pct=excluded.max_return_post_apertura_pct,
                 total_day_change_pct=excluded.total_day_change_pct,
                 close_price_after_detection=excluded.close_price_after_detection,
                 close_return_after_detection_pct=excluded.close_return_after_detection_pct""",
            (ticker, market_date, _now(), run_up_before_detection_pct, max_price_after_detection,
             max_return_after_detection_pct, minutes_to_max, int(reached_20), int(reached_50),
             int(reached_100), category, notes,
             None if perdio_momentum_inmediato is None else int(perdio_momentum_inmediato),
             None if direccion_correcta is None else int(direccion_correcta),
             None if confiable_para_aprendizaje is None else int(confiable_para_aprendizaje),
             json.dumps(motivos_sospecha or [], ensure_ascii=False), int(is_final),
             price_at_market_open, max_price_premarket_after_detection,
             max_return_premarket_after_detection_pct, max_price_regular_session,
             max_return_post_apertura_pct, total_day_change_pct,
             close_price_after_detection, close_return_after_detection_pct, _now()),
        )
        conn.commit()
        return True


def update_close_return_after_detection(
    ticker: str, market_date: str, close_price_after_detection: Optional[float],
    close_return_after_detection_pct: Optional[float],
) -> bool:
    """Actualización ANGOSTA -- SOLO estos 2 campos (2026-08-23, backfill
    del criterio de cierre de Precisión de Magnitud, ver
    `eod_report.backfill_close_return`). A propósito NO es un upsert de
    `record_outcome()`: un resultado ya `is_final=1` correctamente
    calculado (`max_return_after_detection_pct`, `category`,
    `reached_20/50/100`, `confiable_para_aprendizaje`) nunca debe volver a
    tocarse acá -- esos campos ya están bien, no dependen de este cambio.
    Devuelve `False` si el ticker/fecha no tiene ninguna fila (nunca crea
    una nueva)."""
    with _connect() as conn:
        cur = conn.execute(
            """UPDATE candidate_outcome SET
                 close_price_after_detection=?, close_return_after_detection_pct=?
               WHERE ticker=? AND market_date=? AND is_final=1""",
            (close_price_after_detection, close_return_after_detection_pct, ticker, market_date),
        )
        conn.commit()
        return cur.rowcount > 0


def list_outcomes_for_date(market_date: str) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM candidate_outcome WHERE market_date=? ORDER BY max_return_after_detection_pct DESC",
            (market_date,),
        ).fetchall()
        return [_deserialize_outcome(_row(r)) for r in rows]


def compute_interim_outcome(ticker: str, market_date: str) -> Optional[Dict[str, Any]]:
    """Resultado EN CURSO (2026-08-18, Fase 4 del aprendizaje unificado,
    pedido explícito del usuario) -- calculado barato desde datos YA
    persistidos (`candidate_detection` + `MAX(candidate_observation.price)`,
    sin ninguna llamada de red nueva a Tradier), para que Atlas pueda
    mostrar el resultado de una candidata MIENTRAS el mercado sigue
    abierto, sin esperar al EOD. `reached_20/50/100` se calculan sobre
    este máximo EN VIVO (puede subestimar el máximo real del día -- el EOD,
    con velas de 1 min de Tradier, es más preciso y siempre gana, ver
    guard de abajo). No calcula `run_up_before_detection_pct` ni el
    desglose por tramo (eso requiere las velas de Tradier, exclusivo del
    EOD) -- quedan en `None` explícitamente, nunca inventados.

    Si el ticker YA tiene un resultado FINAL (`has_final_outcome`), esta
    función NO lo toca -- devuelve el outcome existente tal cual, para que
    un sweep tardío nunca pise el resultado oficial del EOD con una
    versión "en curso" más pobre. `None` si todavía no hay detección ni
    ninguna observación con precio."""
    if has_final_outcome(ticker, market_date):
        return get_outcome(ticker, market_date)

    detection = get_detection(ticker, market_date)
    if detection is None:
        return None
    price_at_detection = detection.get("price_at_detection")
    if not price_at_detection:
        return None
    max_price = max_price_today(ticker, market_date)
    if max_price is None:
        return None

    max_return_pct = round((max_price - price_at_detection) / price_at_detection * 100, 2)
    confiable, motivos = classify_learning_quality(detection)

    record_outcome(
        ticker=ticker,
        market_date=market_date,
        run_up_before_detection_pct=None,
        max_price_after_detection=max_price,
        max_return_after_detection_pct=max_return_pct,
        minutes_to_max=None,
        reached_20=max_return_pct >= 20.0,
        reached_50=max_return_pct >= 50.0,
        reached_100=max_return_pct >= 100.0,
        category="EN_CURSO",
        notes="Resultado en curso, calculado en vivo desde candidate_observation -- no es el resultado oficial del EOD.",
        confiable_para_aprendizaje=confiable,
        motivos_sospecha=motivos,
        is_final=False,
    )
    return get_outcome(ticker, market_date)


def candidate_full_history(ticker: str, market_date: str) -> Optional[Dict[str, Any]]:
    """Historia completa de UNA candidata (2026-08-18, pedido explícito del
    usuario, caso real XOS) -- separa explícitamente en 3 bloques que NUNCA
    se pisan entre sí, cada uno de su propia tabla:

      A) `estado_inicial` -- de `candidate_detection`, WRITE-ONCE, exactamente
         lo que Atlas vio en el instante de la detección (precio, fuente del
         precio, bid/ask/spread, RVOL, dirección, dólares operados). Esto NO
         cambia nunca, sin importar qué pase después -- una detección
         correcta como XOS no puede "volverse mala" retroactivamente porque
         horas más tarde pasó a NO_PERSEGUIR.
      B) `evolucion` -- de `alert_stage_log` (todas las transiciones de
         etapa registradas) + el máximo visto hasta ahora en
         `candidate_observation` (barato, en vivo, puede no ser el máximo
         real del día -- ver C para el oficial).
      C) `resultado_final` -- de `candidate_outcome`, el resultado real
         (en curso mientras `is_final=False`, definitivo con las velas de
         1 min de Tradier una vez que el EOD corre).

    `None` si el ticker no tiene ninguna detección ese día."""
    detection = get_detection(ticker, market_date)
    if detection is None:
        return None

    etapas = alert_stage_history_for_ticker(ticker, market_date)
    outcome = get_outcome(ticker, market_date)
    max_visto_en_vivo = max_price_today(ticker, market_date)
    price_at_detection = detection.get("price_at_detection")
    max_pct_visto_en_vivo = None
    if max_visto_en_vivo is not None and price_at_detection:
        max_pct_visto_en_vivo = round((max_visto_en_vivo - price_at_detection) / price_at_detection * 100, 3)

    racional_available = None
    try:
        from atlas.data.universe import is_available
        racional_available = bool(is_available(ticker))
    except Exception:
        racional_available = None

    return {
        "ticker": ticker,
        "market_date": market_date,
        "racional_available": racional_available,
        "estado_inicial": {
            "detected_at": detection.get("detected_at"),
            "session": detection.get("session"),
            "source": detection.get("source"),
            "price_at_detection": price_at_detection,
            "price_basis_at_detection": detection.get("price_basis_at_detection"),
            "bid_at_detection": detection.get("bid_at_detection"),
            "ask_at_detection": detection.get("ask_at_detection"),
            "spread_pct_at_detection": detection.get("spread_pct_at_detection"),
            "change_pct_at_detection": detection.get("change_pct_at_detection"),
            "direction_at_detection": detection.get("direction_at_detection"),
            "relative_volume_at_detection": detection.get("relative_volume_at_detection"),
            "volume_at_detection": detection.get("volume_at_detection"),
            "dollar_volume_at_detection": detection.get("dollar_volume_at_detection"),
            "phase_tag": detection.get("phase_tag"),
            "gates_fired": detection.get("gates_fired"),
        },
        "evolucion": {
            "etapas": etapas,
            "max_price_visto_en_vivo": max_visto_en_vivo,
            "max_pct_visto_en_vivo": max_pct_visto_en_vivo,
        },
        "resultado_final": outcome,
    }


# --------------------------- resumen diario / precisión (Reinicio 2026-08-15) ---------------------------

def record_daily_summary(
    market_date: str, n_estudiadas: int, n_candidatas: int, n_senales: int, n_evaluables: int,
    n_aciertos: int, n_falsos_positivos: int, n_tardias: int,
    n_reached_20: int, n_reached_50: int, n_reached_100: int,
) -> None:
    """Idempotente por fecha (INSERT OR REPLACE) -- si el informe de cierre
    se recalcula el mismo día (ej. tras corregir un dato), el resumen se
    actualiza, nunca se duplica una fila por día."""
    with _connect() as conn:
        conn.execute(
            """INSERT INTO daily_summary
               (market_date, n_estudiadas, n_candidatas, n_senales, n_evaluables, n_aciertos,
                n_falsos_positivos, n_tardias, n_reached_20, n_reached_50, n_reached_100, computed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(market_date) DO UPDATE SET
                 n_estudiadas=excluded.n_estudiadas, n_candidatas=excluded.n_candidatas,
                 n_senales=excluded.n_senales, n_evaluables=excluded.n_evaluables,
                 n_aciertos=excluded.n_aciertos, n_falsos_positivos=excluded.n_falsos_positivos,
                 n_tardias=excluded.n_tardias, n_reached_20=excluded.n_reached_20,
                 n_reached_50=excluded.n_reached_50, n_reached_100=excluded.n_reached_100,
                 computed_at=excluded.computed_at""",
            (market_date, n_estudiadas, n_candidatas, n_senales, n_evaluables, n_aciertos,
             n_falsos_positivos, n_tardias, n_reached_20, n_reached_50, n_reached_100, _now()),
        )
        conn.commit()


def record_missed_mover(ticker: str, market_date: str, change_pct_final: Optional[float]) -> bool:
    """Registra un símbolo con movimiento grande el día que TERMINÓ sin que
    ningún gate de Atlas lo marcara como candidata (2026-08-19, ver
    `eod_report.MISSED_OPPORTUNITY_MIN_CHANGE_PCT`). Write-once por
    (ticker, market_date) -- devuelve False si ya estaba registrado, nunca
    lo pisa. Puramente informativo."""
    with _connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO missed_mover (ticker, market_date, change_pct_final, created_at) VALUES (?,?,?,?)",
            (ticker, market_date, change_pct_final, _now()),
        )
        conn.commit()
        return cur.rowcount > 0


def list_missed_movers(market_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """Símbolos con movimiento grande no detectado, más recientes/mayores
    primero. `market_date=None` trae toda la historia."""
    query = "SELECT * FROM missed_mover"
    params: tuple = ()
    if market_date:
        query += " WHERE market_date = ?"
        params = (market_date,)
    query += " ORDER BY market_date DESC, ABS(change_pct_final) DESC"
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
        return [_row(r) for r in rows]


def get_magnitud_prediction(ticker: str, market_date: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM magnitud_prediction WHERE ticker=? AND market_date=?",
            (ticker, market_date),
        ).fetchone()
        return _row(row) if row else None


def record_magnitud_prediction(
    ticker: str, market_date: str, frozen_at: str, predicted_pct: float,
    estado_final_al_congelar: Optional[str] = None, direction: Optional[str] = None,
    timing_deteccion: Optional[str] = None, bucket: Optional[str] = None,
    muestra_n: Optional[int] = None,
) -> bool:
    """Congela la predicción de magnitud UNA sola vez por (ticker,
    market_date) -- INSERT OR IGNORE, devuelve False si ya existía (nunca se
    pisa, para que calificarla después contra el resultado real tenga
    sentido: la predicción tiene que quedar fija en el momento en que se
    hizo)."""
    with _connect() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO magnitud_prediction
               (ticker, market_date, frozen_at, estado_final_al_congelar, direction,
                timing_deteccion, bucket, muestra_n, predicted_pct, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (ticker, market_date, frozen_at, estado_final_al_congelar, direction,
             timing_deteccion, bucket, muestra_n, predicted_pct, _now()),
        )
        conn.commit()
        return cur.rowcount > 0


def magnitud_predictions_for_date(market_date: str) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM magnitud_prediction WHERE market_date=? ORDER BY frozen_at",
            (market_date,),
        ).fetchall()
        return [_row(r) for r in rows]


def precision_validation_state(n_evaluables: int) -> str:
    """Una de `"MUESTRA_INSUFICIENTE"` / `"EN_VALIDACION"` /
    `"VALIDACION_ROBUSTA"` -- pura, sin DB. Umbrales en
    `VALIDACION_MUESTRA_INSUFICIENTE_MAX`/`VALIDACION_EN_VALIDACION_MAX`."""
    if n_evaluables <= VALIDACION_MUESTRA_INSUFICIENTE_MAX:
        return "MUESTRA_INSUFICIENTE"
    if n_evaluables <= VALIDACION_EN_VALIDACION_MAX:
        return "EN_VALIDACION"
    return "VALIDACION_ROBUSTA"


def wilson_confidence_interval(
    n_aciertos: int, n_evaluables: int, confidence: float = 0.95,
) -> Optional[Tuple[float, float]]:
    """Intervalo de confianza binomial de Wilson (2026-08-24, pedido
    explícito del usuario: "que una precisión basada en pocas
    observaciones no se presente como estadísticamente sólida" -- ej.
    4/5=80% debe mostrar un intervalo ancho, 1/146=0,68% uno angosto).
    Fórmula cerrada estándar, sin librerías nuevas. Devuelve
    `(limite_inferior_pct, limite_superior_pct)`, o `None` si
    `n_evaluables == 0` (no hay nada que estimar)."""
    if n_evaluables <= 0:
        return None
    # z=1.96 para 95% de confianza -- z=1.645 para 90%, z=2.576 para 99%.
    z_por_confianza = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}
    z = z_por_confianza.get(confidence, 1.96)
    p = n_aciertos / n_evaluables
    n = n_evaluables
    denominador = 1 + z * z / n
    centro = (p + z * z / (2 * n)) / denominador
    margen = (z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / denominador
    lower = max(0.0, centro - margen) * 100
    upper = min(1.0, centro + margen) * 100
    return (round(lower, 1), round(upper, 1))


def meta_confirmada(n_evaluables: int, precision_pct: Optional[float]) -> bool:
    """Punto 5 del pedido del usuario: la meta del 80% solo se confirma
    cuando la precisión LLEGA a la meta Y la muestra ya es robusta
    (`META_MUESTRA_MINIMA`) -- nunca solo por cruzar el %. Los otros dos
    requisitos que pidió ("predicciones realmente evaluadas", "sin
    duplicados de la misma señal") ya están garantizados estructuralmente
    por cómo se construye `n_evaluables`/`n_aciertos` en
    `magnitud_precision_report()` (ver auditoría del plan) -- no son un
    chequeo adicional acá."""
    return (
        precision_pct is not None
        and precision_pct >= META_CONFIANZA_PCT
        and n_evaluables >= META_MUESTRA_MINIMA
    )


def magnitud_precision_report(market_date: Optional[str] = None, solo_racional: bool = False) -> Dict[str, Any]:
    """Cruza cada predicción congelada con el resultado real ya cerrado
    (`candidate_outcome.is_final=1`, `confiable_para_aprendizaje=1`) --
    "acierto" = el CIERRE real del día (`close_return_after_detection_pct`)
    igualó o superó la predicción congelada (`predicted_pct`). Calculado en
    cada llamada (mismo patrón que `alert_stage_effectiveness_report`),
    nunca pre-agregado -- no toca `daily_summary`. `market_date=None` trae
    toda la historia registrada.

    `solo_racional` (2026-08-23, pedido explícito del usuario: "esa info
    la quiero en atlas" -- el desglose Racional que antes solo calculaba a
    mano): filtra a `atlas.data.universe.is_available(ticker)`, el MISMO
    universo estático real que ya usa `_racional_stats_for_dates` -- no
    depende de un barrido en vivo (por eso funciona incluso de noche o el
    fin de semana, a diferencia de filtrar contra `/api/radar-oportunidades`)."""
    is_available = None
    if solo_racional:
        try:
            from atlas.data.universe import is_available
        except Exception:
            is_available = None

    with _connect() as conn:
        if market_date:
            preds = conn.execute(
                "SELECT * FROM magnitud_prediction WHERE market_date=? ORDER BY frozen_at",
                (market_date,),
            ).fetchall()
        else:
            preds = conn.execute(
                "SELECT * FROM magnitud_prediction ORDER BY market_date, frozen_at"
            ).fetchall()
        preds = [_row(r) for r in preds]
        if solo_racional:
            preds = [p for p in preds if is_available is not None and is_available(p["ticker"])]

        candidatas: List[Dict[str, Any]] = []
        n_evaluables = 0
        n_aciertos = 0
        for p in preds:
            outcome = conn.execute(
                "SELECT * FROM candidate_outcome WHERE ticker=? AND market_date=? AND is_final=1",
                (p["ticker"], p["market_date"]),
            ).fetchone()
            if outcome is None:
                continue  # todavía no cerró -- no se evalúa como pendiente, no se inventa un resultado
            # Filtro de calidad (2026-08-23, caso real XCH: detectado con
            # $10 de volumen en dólares, "acierto" de +2064% que en
            # realidad fue un tick ilíquido -- Atlas mismo ya lo marca
            # `confiable_para_aprendizaje=0` con motivos_sospecha
            # explícitos, mismo criterio que usa TODO el resto de Atlas
            # (`list_all_evaluated_candidates(solo_confiables=True)`) --
            # acá no se estaba aplicando. "el listado tiene hartas fallas",
            # pedido explícito del usuario.
            if not outcome["confiable_para_aprendizaje"]:
                continue
            # Criterio de acierto = CIERRE real del día (2026-08-23, caso
            # real MRNX: tocó +44,3% intradía pero cerró en +17,8% -- "eso
            # no es acierto", pedido explícito del usuario). Nunca
            # `max_return_after_detection_pct` (el máximo intradía, que
            # sobrestima lo que un trader real podría haber capturado).
            # `close_return_after_detection_pct` es un campo NUEVO -- los
            # resultados calculados ANTES de este cambio no lo tienen
            # todavía (`None`), y se excluyen de evaluables en vez de
            # mostrar un acierto/fallo con la base vieja mezclada con la
            # nueva; se van a recuperar con un backfill aparte.
            resultado_real_pct = outcome["close_return_after_detection_pct"]
            if resultado_real_pct is None:
                continue
            n_evaluables += 1
            acierto = resultado_real_pct >= p["predicted_pct"]
            if acierto:
                n_aciertos += 1
            candidatas.append({
                "ticker": p["ticker"], "market_date": p["market_date"], "frozen_at": p["frozen_at"],
                "predicted_pct": p["predicted_pct"], "muestra_n": p["muestra_n"],
                "resultado_real_pct": resultado_real_pct, "acierto": acierto,
            })

    precision_pct = round(100 * n_aciertos / n_evaluables, 1) if n_evaluables else None
    return {
        "market_date": market_date,
        "n_predicciones": len(preds),
        "n_evaluables": n_evaluables,
        "n_aciertos": n_aciertos,
        "precision_pct": precision_pct,
        # Muestra confiable (2026-08-23, pedido explícito del usuario: "no
        # puede ser una simple suma de acierto... quiero ver la realidad")
        # -- MISMO piso ya usado en todo Atlas para decir "esto todavía no
        # alcanza para confiar" (signal_tracker.py, explosion_history.py,
        # experiments.MIN_PRIOR_ROWS_FOR_CUTS), no un número nuevo inventado
        # acá. Un 80% con n=3 no significa lo mismo que un 80% con n=300.
        "muestra_suficiente": n_evaluables >= MUESTRA_MINIMA_CONFIABLE_MAGNITUD,
        # Rigor estadístico (2026-08-24, pedido explícito del usuario) --
        # 3 campos nuevos, la fórmula/campos de arriba no cambiaron.
        "validation_state": precision_validation_state(n_evaluables),
        "wilson_ci": wilson_confidence_interval(n_aciertos, n_evaluables),
        "meta_confirmada": meta_confirmada(n_evaluables, precision_pct),
        "candidatas": candidatas,
    }


def magnitud_precision_report_racional(market_date: Optional[str] = None) -> Dict[str, Any]:
    """Versión Racional de `magnitud_precision_report()` -- mismo criterio
    de acierto, filtrado a `atlas.data.universe.is_available(ticker)`."""
    return magnitud_precision_report(market_date, solo_racional=True)


def magnitud_precision_rolling(n_ventana: int, solo_racional: bool = False) -> Dict[str, Any]:
    """Precisión sobre los últimos `n_ventana` casos YA CERRADOS (no
    predicciones totales -- "últimas 50" son 50 evaluables reales, nunca
    50 intentos con algunos todavía abiertos). Reutiliza EXACTAMENTE el
    mismo `candidatas` que ya arma `magnitud_precision_report()` (mismo
    filtro de calidad, mismo criterio de acierto, ordenado
    `market_date, frozen_at`) -- toma la cola de esa lista, no reimplementa
    ningún criterio nuevo. Si hay menos evaluables reales que `n_ventana`,
    `datos_suficientes=False` y `precision_pct=None` -- nunca se inventa
    un porcentaje sobre una ventana incompleta (pedido explícito del
    usuario, punto 8: "mostrar 'N datos disponibles' y NO inventar
    porcentaje")."""
    reporte = magnitud_precision_report(market_date=None, solo_racional=solo_racional)
    candidatas = reporte["candidatas"]
    ventana = candidatas[-n_ventana:] if n_ventana > 0 else []
    n = len(ventana)
    datos_suficientes = n >= n_ventana > 0
    if not datos_suficientes:
        return {
            "n_ventana": n_ventana, "n_evaluables": n, "datos_suficientes": False,
            "n_aciertos": None, "precision_pct": None, "wilson_ci": None,
        }
    n_aciertos = sum(1 for c in ventana if c["acierto"])
    precision_pct = round(100 * n_aciertos / n, 1)
    return {
        "n_ventana": n_ventana, "n_evaluables": n, "datos_suficientes": True,
        "n_aciertos": n_aciertos, "precision_pct": precision_pct,
        "wilson_ci": wilson_confidence_interval(n_aciertos, n),
    }


def magnitud_precision_by_day(solo_racional: bool = False) -> List[Dict[str, Any]]:
    """Evolución día por día de Precisión de Magnitud (2026-08-23, pedido
    explícito del usuario: "tiene q hacerlo todo los dias. para que ese %
    baje o suba" -- un acumulado total no responde si está mejorando o
    empeorando, hace falta el desglose por fecha). Por cada día con al
    menos una predicción congelada, devuelve `n_estudiadas` (universo
    escaneado ESE día, de `daily_summary.n_estudiadas`, ya registrado por
    el EOD -- la misma fuente que ya usa "Aprendizaje en Vivo"),
    `n_predicciones` (cuántas se congelaron), `n_evaluables` (cuántas ya
    cerraron) y `n_aciertos`/`precision_pct` sobre esas evaluables. Más
    reciente primero. `solo_racional` usa el mismo filtro estático que
    `magnitud_precision_report_racional`."""
    is_available = None
    if solo_racional:
        try:
            from atlas.data.universe import is_available
        except Exception:
            is_available = None

    with _connect() as conn:
        preds = [_row(r) for r in conn.execute(
            "SELECT * FROM magnitud_prediction ORDER BY market_date, frozen_at"
        ).fetchall()]
        if solo_racional:
            preds = [p for p in preds if is_available is not None and is_available(p["ticker"])]

        por_dia: Dict[str, Dict[str, int]] = {}
        for p in preds:
            d = por_dia.setdefault(p["market_date"], {"n_predicciones": 0, "n_evaluables": 0, "n_aciertos": 0})
            d["n_predicciones"] += 1
            outcome = conn.execute(
                "SELECT close_return_after_detection_pct, confiable_para_aprendizaje FROM candidate_outcome "
                "WHERE ticker=? AND market_date=? AND is_final=1",
                (p["ticker"], p["market_date"]),
            ).fetchone()
            if outcome is None or not outcome["confiable_para_aprendizaje"]:
                continue
            resultado = outcome["close_return_after_detection_pct"]
            if resultado is None:
                continue
            d["n_evaluables"] += 1
            if resultado >= p["predicted_pct"]:
                d["n_aciertos"] += 1

        resumenes_por_dia = {
            r["market_date"]: _row(r) for r in conn.execute("SELECT * FROM daily_summary").fetchall()
        }

    out = []
    for market_date in sorted(por_dia.keys(), reverse=True):
        stats = por_dia[market_date]
        resumen_dia = resumenes_por_dia.get(market_date)
        n_aciertos = stats["n_aciertos"]
        n_evaluables = stats["n_evaluables"]
        out.append({
            "market_date": market_date,
            "n_estudiadas": resumen_dia["n_estudiadas"] if resumen_dia else None,
            "n_predicciones": stats["n_predicciones"],
            "n_evaluables": n_evaluables,
            "n_aciertos": n_aciertos,
            "precision_pct": round(100 * n_aciertos / n_evaluables, 1) if n_evaluables else None,
            "muestra_suficiente": n_evaluables >= MUESTRA_MINIMA_CONFIABLE_MAGNITUD,
        })
    return out


def magnitud_precision_by_day_racional() -> List[Dict[str, Any]]:
    """Versión Racional de `magnitud_precision_by_day()`."""
    return magnitud_precision_by_day(solo_racional=True)


def get_daily_summary(market_date: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM daily_summary WHERE market_date=?", (market_date,)).fetchone()
        return _row(row) if row else None


def cumulative_precision() -> Dict[str, Any]:
    """Precisión acumulada desde el inicio de esta etapa (reinicio
    2026-08-15) -- suma real de todos los días con resumen registrado,
    nunca un promedio de porcentajes diarios (eso distorsiona con muestras
    chicas). Siempre devuelve numerador y denominador explícitos."""
    with _connect() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS n_dias, SUM(n_estudiadas) AS estudiadas, SUM(n_candidatas) AS candidatas,
                      SUM(n_senales) AS senales, SUM(n_evaluables) AS evaluables, SUM(n_aciertos) AS aciertos,
                      SUM(n_falsos_positivos) AS falsos_positivos, SUM(n_tardias) AS tardias,
                      SUM(n_reached_20) AS reached_20, SUM(n_reached_50) AS reached_50,
                      SUM(n_reached_100) AS reached_100
               FROM daily_summary"""
        ).fetchone()
    d = _row(row)
    evaluables = d.get("evaluables") or 0
    aciertos = d.get("aciertos") or 0
    d["precision_pct"] = round(100 * aciertos / evaluables, 1) if evaluables else None
    return d


def list_all_evaluated_candidates(solo_confiables: bool = True) -> List[Dict[str, Any]]:
    """Todas las candidatas con resultado ya evaluado (detección + outcome),
    de TODA la historia en vivo -- fuente única para
    `atlas_live.learning.maturity` (2026-08-15). Nunca incluye candidatas
    sin `candidate_outcome` (no cerradas todavía) ni datos de
    `atlas_live/reference/` (Base Histórica, siempre separada).

    Siempre exige `is_final=1` -- un resultado "en curso" (ver
    `compute_interim_outcome`) todavía puede cambiar, nunca debe contarse
    en las estadísticas acumuladas.

    `solo_confiables=True` (default, 2026-08-18, pedido explícito del
    usuario): además exige `confiable_para_aprendizaje=1` (ver
    `classify_learning_quality`) -- las estadísticas de aprendizaje usan
    por defecto SOLO evidencia confiable. `solo_confiables=False` devuelve
    TODO lo evaluado, sospechoso incluido (para auditoría, nunca se borra
    ni se oculta del todo)."""
    where_extra = " AND o.confiable_para_aprendizaje = 1" if solo_confiables else ""
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT d.ticker AS ticker, d.market_date AS market_date, d.session AS session,
                      d.change_pct_at_detection AS change_pct_at_detection,
                      d.direction_at_detection AS direction_at_detection,
                      d.phase_tag AS phase_tag, d.comportamiento_post_apertura AS comportamiento_post_apertura,
                      d.volatility_14d_pct_at_detection AS volatility_14d_pct_at_detection,
                      d.daily_range_pct_at_detection AS daily_range_pct_at_detection,
                      o.reached_20 AS reached_20, o.reached_50 AS reached_50, o.reached_100 AS reached_100,
                      o.category AS category, o.direccion_correcta AS direccion_correcta,
                      o.confiable_para_aprendizaje AS confiable_para_aprendizaje
               FROM candidate_detection d
               JOIN candidate_outcome o ON o.ticker = d.ticker AND o.market_date = d.market_date
               WHERE o.is_final = 1{where_extra}
               ORDER BY d.market_date"""
        ).fetchall()
        return [_row(r) for r in rows]


EXPLOSION_BANDS_TRADIER = [10, 20, 30, 50, 100, 150, 200]


def explosion_bands_tradier(market_date: Optional[str] = None) -> Dict[str, Any]:
    """Marcador Histórico Tradier (2026-08-18, pedido explícito del usuario)
    -- bandas ACUMULATIVAS (>= banda) de `max_return_after_detection_pct`
    sobre resultados FINALES (`is_final=1`) y CONFIABLES
    (`confiable_para_aprendizaje=1`, ver `classify_learning_quality`) del
    radar Tradier (CAPA1/2). Nunca reemplaza ni toca `explosion_history.py`
    (el Marcador Histórico legacy, alimentado por exit_journal/Yahoo) --
    sistema nuevo, en paralelo, sobre datos y fuente completamente
    distintos. `market_date=None` (default) agrega TODA la historia
    disponible; pasar una fecha para un solo día. Mismo shape de salida que
    `explosion_history.summarize_by_band()` (n, mediana/máximo del %) para
    poder compararlos lado a lado en la Cabina."""
    import statistics

    where = "o.is_final = 1 AND o.confiable_para_aprendizaje = 1"
    params: tuple = ()
    if market_date:
        where += " AND o.market_date = ?"
        params = (market_date,)

    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT o.ticker AS ticker, o.market_date AS market_date,
                      o.max_return_after_detection_pct AS max_return_after_detection_pct
               FROM candidate_outcome o
               WHERE {where} AND o.max_return_after_detection_pct IS NOT NULL""",
            params,
        ).fetchall()

    eventos = [_row(r) for r in rows]
    resumen: Dict[str, Any] = {}
    for band in EXPLOSION_BANDS_TRADIER:
        casos = [e for e in eventos if e["max_return_after_detection_pct"] >= band]
        if not casos:
            resumen[str(band)] = {"n": 0, "estado": "No disponible"}
            continue
        maxes = [c["max_return_after_detection_pct"] for c in casos]
        resumen[str(band)] = {
            "n": len(casos),
            "mediana_max_pct": round(statistics.median(maxes), 1),
            "max_absoluto_pct": round(max(maxes), 1),
            "tickers": sorted({c["ticker"] for c in casos}),
        }
    return {
        "market_date": market_date,
        "n_total_evaluado": len(eventos),
        "por_banda_acumulativa": resumen,
    }


def list_daily_summaries() -> List[Dict[str, Any]]:
    """Todos los resúmenes diarios, ordenados por fecha ascendente -- para
    ventanas de tiempo (consistencia/recencia/validación fuera de muestra
    en `atlas_live.learning.maturity`)."""
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM daily_summary ORDER BY market_date").fetchall()
        return [_row(r) for r in rows]


def recent_precision(window_days: int = 21) -> Dict[str, Any]:
    """Precisión de los últimos `window_days` días de mercado CON resumen
    (no días calendario) -- para mostrar siempre junto a la acumulada
    completa, nunca a solas (pedido explícito: la precisión puede bajar al
    crecer la muestra, y eso debe verse, no ocultarse)."""
    dias = list_daily_summaries()[-window_days:]
    evaluables = sum(d.get("n_evaluables") or 0 for d in dias)
    aciertos = sum(d.get("n_aciertos") or 0 for d in dias)
    return {
        "dias_incluidos": len(dias),
        "desde": dias[0]["market_date"] if dias else None,
        "hasta": dias[-1]["market_date"] if dias else None,
        "evaluables": evaluables,
        "aciertos": aciertos,
        "precision_pct": round(100 * aciertos / evaluables, 1) if evaluables else None,
    }


# --------------------------- marcador de acierto Racional (2026-08-18) ---------------------------
# Pedido explícito del usuario: Atlas estudia y aprende del universo
# COMPLETO (arriba, sin cambios -- get_daily_summary/cumulative_precision/
# recent_precision siguen exactamente iguales, ese es el marcador
# "universal"). Este bloque agrega, EN PARALELO, el mismo marcador pero
# recalculado solo sobre tickers disponibles en Racional -- nunca cachea ni
# agrega columnas nuevas: recalcula `is_available(ticker)` en cada llamada,
# mismo criterio ya usado en `live_opportunities()`, así que siempre refleja
# el universo Racional actual, no una foto vieja. No toca `daily_summary`,
# `run_eod_evaluation` ni ninguna otra función de arriba.

def _racional_stats_for_dates(fechas: List[str]) -> Dict[str, Any]:
    """Recalcula evaluables/aciertos/tardías/reached_20/50/100 SOLO sobre
    outcomes de tickers Racional-disponibles ahora, para las fechas dadas.
    Mismo criterio de "acierto" (`reached_20 AND category != deteccion_tardia`)
    que usa `run_eod_evaluation()` al poblar `daily_summary` -- se reproduce
    acá porque `daily_summary` es un agregado por día que ya perdió el
    detalle de qué ticker era Racional, así que no se puede filtrar
    retroactivamente sin volver a las filas crudas (`list_outcomes_for_date`,
    ya existente, sin red)."""
    try:
        from atlas.data.universe import is_available
    except Exception:
        is_available = None

    evaluables = aciertos = tardias = reached_20 = reached_50 = reached_100 = 0
    for fecha in fechas:
        for o in list_outcomes_for_date(fecha):
            if is_available is None:
                continue
            try:
                if not is_available(o["ticker"]):
                    continue
            except Exception:
                continue
            evaluables += 1
            if o.get("reached_20"):
                reached_20 += 1
            if o.get("reached_50"):
                reached_50 += 1
            if o.get("reached_100"):
                reached_100 += 1
            if o.get("category") == "deteccion_tardia":
                tardias += 1
            elif o.get("reached_20"):
                aciertos += 1
    return {
        "evaluables": evaluables, "aciertos": aciertos, "tardias": tardias,
        "reached_20": reached_20, "reached_50": reached_50, "reached_100": reached_100,
    }


def daily_precision_racional(market_date: str) -> Dict[str, Any]:
    """Versión Racional de la precisión del día -- mismo criterio que
    `get_daily_summary`, filtrado a Racional-disponible ahora."""
    stats = _racional_stats_for_dates([market_date])
    evaluables, aciertos = stats["evaluables"], stats["aciertos"]
    stats["precision_pct"] = round(100 * aciertos / evaluables, 1) if evaluables else None
    return stats


def cumulative_precision_racional() -> Dict[str, Any]:
    """Versión Racional de `cumulative_precision()` -- mismas fechas con
    resumen ya registrado, recalculado solo sobre Racional-disponible ahora."""
    fechas = [d["market_date"] for d in list_daily_summaries()]
    stats = _racional_stats_for_dates(fechas)
    stats["n_dias"] = len(fechas)
    evaluables, aciertos = stats["evaluables"], stats["aciertos"]
    stats["precision_pct"] = round(100 * aciertos / evaluables, 1) if evaluables else None
    return stats


def recent_precision_racional(window_days: int = 21) -> Dict[str, Any]:
    """Versión Racional de `recent_precision()`."""
    dias = list_daily_summaries()[-window_days:]
    fechas = [d["market_date"] for d in dias]
    stats = _racional_stats_for_dates(fechas)
    evaluables, aciertos = stats["evaluables"], stats["aciertos"]
    return {
        "dias_incluidos": len(fechas),
        "desde": fechas[0] if fechas else None,
        "hasta": fechas[-1] if fechas else None,
        "evaluables": evaluables, "aciertos": aciertos,
        "precision_pct": round(100 * aciertos / evaluables, 1) if evaluables else None,
    }


def phase_stats(phase_tag: Optional[str] = None) -> List[Dict[str, Any]]:
    """Estadística real por fase (Fase 4, clasificador A-G): conteo y % que
    alcanzó cada banda, con `n` SIEMPRE explícito -- una fase con n=2 se
    reporta con n=2, nunca se disfraza de porcentaje solo."""
    with _connect() as conn:
        query = """
            SELECT d.phase_tag AS phase_tag, COUNT(*) AS n,
                   SUM(o.reached_20) AS n_reached_20, SUM(o.reached_50) AS n_reached_50,
                   SUM(o.reached_100) AS n_reached_100
            FROM candidate_detection d
            JOIN candidate_outcome o ON o.ticker = d.ticker AND o.market_date = d.market_date
            WHERE d.phase_tag IS NOT NULL
        """
        params: tuple = ()
        if phase_tag:
            query += " AND d.phase_tag = ?"
            params = (phase_tag,)
        query += " GROUP BY d.phase_tag"
        rows = conn.execute(query, params).fetchall()
        out = []
        for r in rows:
            d = _row(r)
            n = d["n"] or 0
            d["pct_reached_20"] = round(100 * (d["n_reached_20"] or 0) / n, 1) if n else None
            d["pct_reached_50"] = round(100 * (d["n_reached_50"] or 0) / n, 1) if n else None
            d["pct_reached_100"] = round(100 * (d["n_reached_100"] or 0) / n, 1) if n else None
            out.append(d)
        return out


def early_vs_late_summary() -> Dict[str, Any]:
    """Hipótesis B del experimento (2026-08-16): agrupa TODAS las candidatas
    ya evaluadas en vivo en los 3 grupos de timing (early_genuino/late/
    antes_del_movimiento -- ver `atlas_live.learning.experiments`), separado
    por dirección. Nunca mezcla histórico (`atlas_live/reference/`) con esto
    -- exclusivamente `candidate_registry`, CAPA 2 en vivo. Con la base
    recién reiniciada, esto arranca vacío -- correctamente, no se fabrica
    nada mientras no haya evidencia real en vivo."""
    from atlas_live.learning import experiments as exp

    evaluados = list_all_evaluated_candidates()
    # `list_all_evaluated_candidates` trae reached_20/50/100 (bandas ya
    # calculadas por eod_report.py), no el % exacto -- alcanza para este
    # resumen, que reporta bandas, igual que el resto del proyecto.
    grupos: Dict[str, Dict[str, Dict[str, Any]]] = {
        d: {g: {"n": 0, "aciertos_20": 0, "aciertos_50": 0, "aciertos_100": 0}
            for g in ("early_genuino", "late", "antes_del_movimiento")}
        for d in exp.DIRECTIONS
    }
    for e in evaluados:
        direction = e.get("direction_at_detection")
        if direction not in exp.DIRECTIONS:
            continue
        grupo = exp.timing_group(e.get("phase_tag"))
        if grupo is None:
            continue
        bucket = grupos[direction][grupo]
        bucket["n"] += 1
        if e.get("reached_20"):
            bucket["aciertos_20"] += 1
        if e.get("reached_50"):
            bucket["aciertos_50"] += 1
        if e.get("reached_100"):
            bucket["aciertos_100"] += 1

    for d in grupos:
        for g, b in grupos[d].items():
            n = b["n"]
            b["pct_20"] = round(100 * b["aciertos_20"] / n, 1) if n else None
            b["pct_50"] = round(100 * b["aciertos_50"] / n, 1) if n else None
            b["pct_100"] = round(100 * b["aciertos_100"] / n, 1) if n else None
    return grupos


# --------------------------- meta / diagnóstico ---------------------------

def set_meta(**kwargs) -> None:
    with _connect() as conn:
        for k, v in kwargs.items():
            conn.execute(
                "INSERT INTO radar_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v),
            )
        conn.commit()


def get_meta() -> Dict[str, Any]:
    with _connect() as conn:
        rows = conn.execute("SELECT key, value FROM radar_meta").fetchall()
        out = {}
        for r in rows:
            v = r["value"]
            try:
                out[r["key"]] = json.loads(v)
            except (TypeError, ValueError):
                out[r["key"]] = v
        return out


def radar_status() -> Dict[str, Any]:
    meta = get_meta()
    today = meta.get("current_market_date")
    n_hoy = count_candidates_for_date(today) if today else 0
    return {
        "state": meta.get("state", "IDLE"),
        "session_actual": meta.get("session_actual"),
        "sweeps_total": meta.get("sweeps_total", 0),
        "sweeps_ok": meta.get("sweeps_ok", 0),
        "sweeps_error": meta.get("sweeps_error", 0),
        "ultimo_sweep_at": meta.get("ultimo_sweep_at"),
        "ultimo_sweep_duracion_s": meta.get("ultimo_sweep_duracion_s"),
        "ultimo_error": meta.get("ultimo_error"),
        "candidatas_hoy": n_hoy,
        "market_date_actual": today,
        "eod_ejecutado_para": meta.get("eod_ejecutado_para"),
    }


# ------------------- SHADOW/VALIDACIÓN de LEK (Fase 2) -------------------
# 2026-08-27, autorizado explícitamente. Ver docstring de `shadow_decision_log`
# en _SCHEMA arriba. Estas funciones SOLO persisten/leen -- nunca calculan
# `decision_shadow` ni `shadow_differs` (eso sigue siendo exclusivo de
# `atlas_live/core/atlas_decision_core.py`, que esta fase no toca), y nunca
# escriben ni leen `candidate_outcome`/ninguna decisión real.

_DOWNGRADE_RESULTADO_A_CAMPO = {
    "DOWNGRADE_CORRECTO": "correcto",
    "DOWNGRADE_INCORRECTO": "incorrecto",
    "AMBIGUO": "ambiguo",
    "PENDIENTE": "pendiente",
}


def record_shadow_decision(
    ticker: str, market_date: str, decision: str, decision_shadow: str,
    shadow_differs: bool, validation_state: Optional[str], sample_size: Optional[int],
    wilson_upper_bound_20_pct: Optional[float], baseline_pct_20: Optional[float],
) -> bool:
    """Persiste UN evento donde LEK (`atlas_decision_core.decide()`, Shadow
    Mode) propuso una decisión distinta a la real -- Fase 2 de la
    transición SHADOW->VALIDACIÓN. Nunca recalcula nada: solo guarda el
    resultado que el llamador ya calculó. Rechaza (no escribe nada) si
    `shadow_differs=False` -- solo interesan los eventos de divergencia
    real, no una fila por cada candidata evaluada. Write-once por
    (ticker, market_date) vía `INSERT OR IGNORE` (mismo patrón que
    `magnitud_prediction`/`missed_mover`) -- idempotente ante múltiples
    requests del mismo día a `/api/radar-oportunidades`. Devuelve `True`
    solo si se insertó una fila NUEVA."""
    if not shadow_differs:
        return False
    with _connect() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO shadow_decision_log
               (ticker, market_date, decision, decision_shadow, shadow_differs,
                validation_state, sample_size, wilson_upper_bound_20_pct,
                baseline_pct_20, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, market_date, decision, decision_shadow, 1,
             validation_state, sample_size, wilson_upper_bound_20_pct,
             baseline_pct_20, _now()),
        )
        conn.commit()
        return cur.rowcount > 0


def shadow_validation_report(market_date: Optional[str] = None) -> Dict[str, Any]:
    """Solo lectura -- cruza `shadow_decision_log` con `candidate_outcome`
    por `(ticker, market_date)`, la misma clave natural que usa TODO el
    proyecto para identificar una candidata en un día (ver `UNIQUE(ticker,
    market_date)` de ambas tablas). Nunca modifica ningún outcome ni
    ninguna decisión -- exclusivamente un reporte de auditoría.

    Definición de "downgrade correcto/incorrecto" -- declarada
    explícitamente acá, REUTILIZANDO la misma agrupación de categorías que
    ya usa `eod_report.py` (línea ~320, "mejores oportunidades del día" =
    mejor_oportunidad/buena_oportunidad) en vez de inventar un criterio
    nuevo para este reporte:
      - `category in ("mejor_oportunidad", "buena_oportunidad")` -> el
        downgrade de LEK habría sido INCORRECTO (la candidata sí era
        buena; LEK la habría rebajado sin motivo real).
      - `category == "falsa_senal"` -> el downgrade habría sido CORRECTO
        (la cautela extra de LEK estaba justificada por el resultado real).
      - cualquier otro valor (`oportunidad_moderada`, `deteccion_tardia`,
        `error_evaluacion`, o `category` ausente) -> AMBIGUO -- no cuenta
        ni a favor ni en contra de la tasa de acierto, se reporta aparte,
        nunca se fuerza a una de las dos categorías de arriba.
    Solo se considera "outcome cerrado" cuando `is_final=1` -- mismo
    criterio que `has_final_outcome()` usa en todo el proyecto; un outcome
    en curso (`is_final=0`) queda como pendiente, nunca se evalúa."""
    with _connect() as conn:
        if market_date:
            eventos = conn.execute(
                "SELECT * FROM shadow_decision_log WHERE market_date=? ORDER BY market_date, ticker",
                (market_date,),
            ).fetchall()
        else:
            eventos = conn.execute(
                "SELECT * FROM shadow_decision_log ORDER BY market_date, ticker"
            ).fetchall()
        eventos = [_row(r) for r in eventos]

        con_outcome = 0
        pendientes = 0
        downgrade_correcto = 0
        downgrade_incorrecto = 0
        ambiguos = 0
        por_decision_original: Dict[str, Dict[str, int]] = {}
        por_validation_state: Dict[str, Dict[str, int]] = {}
        detalle: List[Dict[str, Any]] = []

        for ev in eventos:
            outcome_row = conn.execute(
                "SELECT category, is_final FROM candidate_outcome WHERE ticker=? AND market_date=?",
                (ev["ticker"], ev["market_date"]),
            ).fetchone()
            outcome = _row(outcome_row) if outcome_row else None

            if outcome is None or not outcome.get("is_final"):
                pendientes += 1
                resultado = "PENDIENTE"
            else:
                con_outcome += 1
                categoria = outcome.get("category")
                if categoria in ("mejor_oportunidad", "buena_oportunidad"):
                    downgrade_incorrecto += 1
                    resultado = "DOWNGRADE_INCORRECTO"
                elif categoria == "falsa_senal":
                    downgrade_correcto += 1
                    resultado = "DOWNGRADE_CORRECTO"
                else:
                    ambiguos += 1
                    resultado = "AMBIGUO"

            campo = _DOWNGRADE_RESULTADO_A_CAMPO[resultado]
            od = por_decision_original.setdefault(
                ev["decision"], {"total": 0, "correcto": 0, "incorrecto": 0, "ambiguo": 0, "pendiente": 0},
            )
            od["total"] += 1
            od[campo] += 1

            vs = ev.get("validation_state") or "SIN_DATO"
            ov = por_validation_state.setdefault(
                vs, {"total": 0, "correcto": 0, "incorrecto": 0, "ambiguo": 0, "pendiente": 0},
            )
            ov["total"] += 1
            ov[campo] += 1

            detalle.append({
                "ticker": ev["ticker"], "market_date": ev["market_date"],
                "decision": ev["decision"], "decision_shadow": ev["decision_shadow"],
                "validation_state": ev["validation_state"], "sample_size": ev["sample_size"],
                "wilson_upper_bound_20_pct": ev["wilson_upper_bound_20_pct"],
                "baseline_pct_20": ev["baseline_pct_20"], "resultado": resultado,
                "outcome_category": outcome.get("category") if outcome else None,
            })

        # Mismo criterio que magnitud_precision_report(): ambiguos y
        # pendientes quedan FUERA del denominador de la tasa -- nunca se
        # cuenta un caso sin resultado claro como si fuera un acierto o un
        # fallo.
        n_evaluables_tasa = downgrade_correcto + downgrade_incorrecto
        tasa_acierto_pct = round(100 * downgrade_correcto / n_evaluables_tasa, 1) if n_evaluables_tasa else None
        wilson_ci = wilson_confidence_interval(downgrade_correcto, n_evaluables_tasa) if n_evaluables_tasa else None

        return {
            "market_date": market_date,
            "total_eventos_shadow_differs": len(eventos),
            "con_outcome_final": con_outcome,
            "pendientes": pendientes,
            "downgrade_correcto": downgrade_correcto,
            "downgrade_incorrecto": downgrade_incorrecto,
            "ambiguos": ambiguos,
            "n_evaluables_tasa": n_evaluables_tasa,
            "tasa_acierto_pct": tasa_acierto_pct,
            "wilson_ci": list(wilson_ci) if wilson_ci else None,
            "por_decision_original": por_decision_original,
            "por_validation_state": por_validation_state,
            "eventos": detalle,
            "nota_metodologica": (
                "DOWNGRADE_CORRECTO/DOWNGRADE_INCORRECTO reutilizan la "
                "agrupacion de categorias de candidate_outcome ya usada por "
                "eod_report.py (mejor_oportunidad/buena_oportunidad = "
                "candidata buena -> downgrade incorrecto; falsa_senal = "
                "candidata mala -> downgrade correcto). AMBIGUO "
                "(oportunidad_moderada/deteccion_tardia/error_evaluacion) no "
                "cuenta en la tasa de acierto. Con n_evaluables_tasa chico, "
                "el wilson_ci sera ancho -- no concluir nada sobre si LEK "
                "'funciona' hasta que la muestra sea suficiente."
            ),
        }
