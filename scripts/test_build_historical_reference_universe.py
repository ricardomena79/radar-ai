"""Tests del universo de mercado completo en run_batch() (2026-08-17):
solo EQUITY se procesa, ETF/WARRANT quedan excluidos y contados,
racional_available viaja como etiqueta sin filtrar nada. Sin red: se
mockea broad_universe y el provider. DB temporal, nunca toca
historical_reference.db real."""

import tempfile
import uuid as _uuid
from pathlib import Path

import pandas as pd

from atlas_live.reference import reference_registry as reg
from scripts import build_historical_reference as bhr

_ORIG_DB_PATH = reg.DB_PATH

_META = {
    "AAPL": {"exchange": "NASDAQ", "name": "Apple Inc.", "type": "EQUITY"},
    "ZZZZ": {"exchange": "NASDAQ", "name": "Zzzz Corp", "type": "EQUITY"},
    "QQQ": {"exchange": "NASDAQ", "name": "Invesco QQQ Trust", "type": "ETF"},
    "XYZW": {"exchange": "NASDAQ", "name": "XYZ Corp Warrants", "type": "WARRANT"},
}


def _synthetic_df(n=60):
    idx = pd.date_range(start="2026-05-01", periods=n, freq="B")
    closes = [10.0 * (1.002 ** i) for i in range(n)]
    return pd.DataFrame({
        "Open": closes, "High": [c * 1.02 for c in closes], "Low": [c * 0.98 for c in closes],
        "Close": closes, "Volume": [100_000] * n,
    }, index=idx)


class _FakeProvider:
    def get_history(self, symbol, period="3mo", interval="1d"):
        return _synthetic_df()


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_bhr_universe_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None


def _restore():
    reg.DB_PATH = _ORIG_DB_PATH
    reg._schema_ready_for = None


def test_run_batch_solo_procesa_equity(monkeypatch):
    _fresh()
    monkeypatch.setattr(bhr.broad_universe, "fetch_broad_universe_meta", lambda: _META)
    monkeypatch.setattr(bhr.broad_universe, "racional_symbols", lambda: {"AAPL"})
    monkeypatch.setattr(bhr, "build_tradier_provider", lambda: _FakeProvider())
    try:
        result = bhr.run_batch(limit=10, workers=1, delay_ms=0, period="3mo", batch_timeout_s=30)
        assert result["universo_total"] == 2  # solo AAPL y ZZZZ son EQUITY
        assert result["universo_total_bruto"] == 4
        assert result["clasificacion"] == {"EQUITY": 2, "ETF": 1, "WARRANT": 1}
        assert result["ok"] == 2
        procesados = reg.processed_symbols()
        assert procesados == {"AAPL", "ZZZZ"}  # QQQ/XYZW nunca se tocan
    finally:
        _restore()


def test_run_batch_marca_racional_available_correctamente(monkeypatch):
    _fresh()
    monkeypatch.setattr(bhr.broad_universe, "fetch_broad_universe_meta", lambda: _META)
    monkeypatch.setattr(bhr.broad_universe, "racional_symbols", lambda: {"AAPL"})
    monkeypatch.setattr(bhr, "build_tradier_provider", lambda: _FakeProvider())
    try:
        bhr.run_batch(limit=10, workers=1, delay_ms=0, period="3mo", batch_timeout_s=30)
        breakdown = reg.universe_breakdown()
        assert breakdown["racional_available"] == 1    # AAPL
        assert breakdown["racional_no_disponible"] == 1  # ZZZZ
        with reg._connect() as conn:
            row = conn.execute("SELECT exchange, name FROM reference_checkpoint WHERE symbol='AAPL'").fetchone()
        assert row["exchange"] == "NASDAQ"
        assert row["name"] == "Apple Inc."
    finally:
        _restore()


def test_run_batch_sin_token_no_toca_universo_ya_calculado(monkeypatch):
    """Si falta TRADIER_API_TOKEN, run_batch devuelve error temprano --
    pero el universo/clasificación ya quedaron persistidos en meta antes
    de fallar, para diagnóstico."""
    _fresh()
    monkeypatch.setattr(bhr.broad_universe, "fetch_broad_universe_meta", lambda: _META)
    monkeypatch.setattr(bhr.broad_universe, "racional_symbols", lambda: set())
    monkeypatch.setattr(bhr, "build_tradier_provider", lambda: None)
    try:
        result = bhr.run_batch(limit=10, workers=1, delay_ms=0, period="3mo", batch_timeout_s=30)
        assert result == {"error": "TRADIER_API_TOKEN no configurado"}
        assert reg.get_meta()["universe_total"] == 2
        assert reg.get_meta()["clasificacion"] == {"EQUITY": 2, "ETF": 1, "WARRANT": 1}
    finally:
        _restore()
