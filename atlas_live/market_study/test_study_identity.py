"""Tests de identidad + separación anti-leakage del estudio (FASE 11,
2026-08-10). DB temporal, offline, determinista.

NOTA: escritos sin poder ejecutarse localmente (sin Python); ejecución en
Railway/CI o por el usuario.
"""

import sqlite3
import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.market_study import study_registry as reg


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_study_id_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None  # forzar recreación de esquema en la DB nueva


def test_record_explosion_guarda_identidad_y_separa_leakage():
    _fresh()
    inserted = reg.record_explosion(
        ticker="DNN", date="2026-07-06", prev_close=1.0, open_price=1.1,
        gap_open_pct=10.0, prior_avg_volume=1e6, market_cap=9e8,
        available_in_racional=True, max_intraday_pct=120.0, close_change_pct=80.0,
        day_volume=5e6, exchange="NYSE American", name="Denison Mines Corp",
    )
    assert inserted is True

    filas = reg.list_explosions(limit=10)
    assert len(filas) == 1
    f = filas[0]
    assert f["ticker"] == "DNN"
    assert f["exchange"] == "NYSE American" and f["name"] == "Denison Mines Corp"
    assert f["tradingview_symbol"] == "AMEX:DNN"
    # La banda/máximo (RESULTADO) viene del JOIN con la tabla de outcome.
    assert f["max_intraday_pct"] == 120.0 and f["band"] == "100"

    # Anti-leakage a nivel esquema: la tabla de features NO tiene columnas de resultado.
    with sqlite3.connect(reg.DB_PATH) as conn:
        feat_cols = {r[1] for r in conn.execute("PRAGMA table_info(explosion_features)")}
    assert "max_intraday_pct" not in feat_cols  # el máximo jamás es feature
    assert "exchange" in feat_cols and "name" in feat_cols


def test_record_explosion_idempotente():
    _fresh()
    args = dict(
        ticker="ABCD", date="2026-06-01", prev_close=2.0, open_price=2.1,
        gap_open_pct=5.0, prior_avg_volume=1e5, market_cap=None,
        available_in_racional=False, max_intraday_pct=55.0, close_change_pct=30.0,
        day_volume=1e6, exchange="NASDAQ", name="Abcd Inc",
    )
    assert reg.record_explosion(**args) is True
    assert reg.record_explosion(**args) is False  # (ticker,date) único -> no duplica
    assert len(reg.list_explosions(limit=10)) == 1


def test_ensure_columns_sobre_db_vieja():
    _fresh()
    # DB "vieja" de explosion_features sin exchange/name.
    conn = sqlite3.connect(reg.DB_PATH)
    conn.executescript(
        "CREATE TABLE explosion_features (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ticker TEXT NOT NULL, date TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(ticker,date));"
    )
    conn.execute("INSERT INTO explosion_features (ticker,date,created_at) VALUES ('X','2026-01-01','t')")
    conn.commit()
    reg._ensure_columns(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(explosion_features)")}
    assert "exchange" in cols and "name" in cols
    assert conn.execute("SELECT COUNT(*) FROM explosion_features").fetchone()[0] == 1  # no pierde filas
    conn.close()
