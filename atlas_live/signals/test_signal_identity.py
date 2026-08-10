"""Tests de identidad + migración aditiva del Registro de Señales (FASE 11,
2026-08-10). DB SQLite temporal, nunca la real. Offline y determinista.

Lo crítico: la migración que agrega `exchange`/`name` a una base `signals` ya
poblada NO puede perder filas (la DB real tiene señales reales).

NOTA: escritos sin poder ejecutarse localmente (sin Python); ejecución en
Railway/CI o por el usuario.
"""

import sqlite3
import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.signals import signal_registry as reg

_ORIG = reg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_sig_id_{_uuid.uuid4().hex}.db"


def _restore():
    reg.DB_PATH = _ORIG


# Esquema ANTERIOR de `signals` (sin exchange/name), para simular una DB ya
# desplegada con datos reales antes de FASE 11.
_OLD_SIGNALS_DDL = """
CREATE TABLE signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_uuid TEXT NOT NULL UNIQUE,
    ticker TEXT NOT NULL,
    market_date TEXT NOT NULL,
    session TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(ticker, market_date)
);
"""


def test_migracion_aditiva_no_pierde_filas():
    _fresh()
    try:
        # DB "vieja" con una señal real ya guardada.
        conn = sqlite3.connect(reg.DB_PATH)
        conn.executescript(_OLD_SIGNALS_DDL)
        conn.execute(
            "INSERT INTO signals (signal_uuid, ticker, market_date, session, detected_at, state, created_at) "
            "VALUES ('u-1','AG','2026-08-10','PREMARKET','2026-08-10T12:05:35+00:00','OBSERVANDO','2026-08-10T12:05:35+00:00')"
        )
        conn.commit()
        conn.close()

        # Migración: _ensure_columns agrega columnas SIN borrar la fila.
        conn = sqlite3.connect(reg.DB_PATH)
        reg._ensure_columns(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
        assert "exchange" in cols and "name" in cols
        n = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        assert n == 1  # la señal real sobrevive
        row = conn.execute("SELECT ticker, exchange, name FROM signals WHERE signal_uuid='u-1'").fetchone()
        assert row[0] == "AG" and row[1] is None and row[2] is None  # identidad vieja = NULL honesto
        # Idempotente: correr de nuevo no rompe.
        reg._ensure_columns(conn)
        conn.close()
    finally:
        _restore()


def test_register_signal_con_identidad():
    _fresh()
    try:
        out = reg.register_signal(
            ticker="DNN", market_date="2026-08-10", session="PREMARKET",
            detected_at="2026-08-10T12:10:00+00:00", price_at_detection=2.1,
            price_as_of="2026-08-10T12:10:00+00:00", provider="yahoo_finance",
            features={"gap_pct": 8.0}, score=40.0, reasons=None, conditions=None,
            historical_group="similar a A/B (premarket fuerte)", similar_historical_cases=20,
            detector_version="0.1.0", feature_version="0.1.0", data_version="0.1.0",
            exchange="NYSE American", name="Denison Mines Corp",
        )
        assert out["created"] is True
        assert out["exchange"] == "NYSE American"
        assert out["name"] == "Denison Mines Corp"
        assert out["tradingview_symbol"] == "AMEX:DNN"  # identidad -> símbolo correcto

        got = reg.get_signal(out["signal_uuid"])
        assert got["exchange"] == "NYSE American" and got["name"] == "Denison Mines Corp"
    finally:
        _restore()


def test_identidad_es_opcional_compat():
    # Sin exchange/name (llamada vieja) sigue funcionando: identidad = None.
    _fresh()
    try:
        out = reg.register_signal(
            ticker="NUWE", market_date="2026-08-10", session="PREMARKET",
            detected_at="2026-08-10T12:11:00+00:00", price_at_detection=4.8,
            price_as_of="2026-08-10T12:11:00+00:00", provider="yahoo_finance",
            features=None, score=None, reasons=None, conditions=None,
            historical_group=None, similar_historical_cases=None,
            detector_version="0.1.0", feature_version="0.1.0", data_version="0.1.0",
        )
        assert out["created"] is True
        assert out["exchange"] is None and out["name"] is None
        assert out["tradingview_symbol"] == "NUWE"  # sin exchange -> sin prefijo
    finally:
        _restore()
