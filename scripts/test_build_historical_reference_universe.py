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

# Universo con un ETF apalancado real (2026-09-11, corrección PM-RVOL --
# Base Histórica coherente con lo que el Radar en vivo ya detecta desde
# 2026-08-17, ver `universe.is_leveraged_etf_name()`) -- QQQ (pasivo) sigue
# excluido, SOXL (apalancado 3x) debe entrar al universo del batch.
_META_CON_LEVERAGED = {
    "AAPL": {"exchange": "NASDAQ", "name": "Apple Inc.", "type": "EQUITY"},
    "QQQ": {"exchange": "NASDAQ", "name": "Invesco QQQ Trust", "type": "ETF"},
    "SOXL": {"exchange": "NASDAQ", "name": "Direxion Daily Semiconductor Bull 3X Shares", "type": "ETF"},
    "UVXY": {"exchange": "NYSE ARCA", "name": "ProShares Ultra VIX Short-Term Futures ETF", "type": "ETF"},
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


# ---------------------------------------------------------------------------
# Cobertura de ETFs apalancados/inversos (2026-09-11, autorizado
# explícitamente -- Parte 2, casos 6/7/8 pedidos).
# ---------------------------------------------------------------------------

def test_run_batch_etf_pasivo_no_entra_al_universo(monkeypatch):
    """Caso 6: un ETF pasivo (QQQ, sin 2X/3X/ULTRA/DAILY TARGET/LEVERAGED
    en el nombre) sigue excluido del batch, exactamente como antes."""
    _fresh()
    monkeypatch.setattr(bhr.broad_universe, "fetch_broad_universe_meta", lambda: _META_CON_LEVERAGED)
    monkeypatch.setattr(bhr.broad_universe, "racional_symbols", lambda: {"AAPL"})
    monkeypatch.setattr(bhr, "build_tradier_provider", lambda: _FakeProvider())
    try:
        bhr.run_batch(limit=10, workers=1, delay_ms=0, period="3mo", batch_timeout_s=30)
        assert "QQQ" not in reg.processed_symbols()
    finally:
        _restore()


def test_run_batch_etf_apalancado_si_entra_al_universo(monkeypatch):
    """Caso 7: un ETF apalancado/inverso que el Radar en vivo ya acepta
    (SOXL 3x, UVXY "Ultra") SÍ entra y se procesa -- vía
    `is_leveraged_etf_name()`, mecanismo ya existente, sin tocar
    `classify_instrument_type()`."""
    _fresh()
    monkeypatch.setattr(bhr.broad_universe, "fetch_broad_universe_meta", lambda: _META_CON_LEVERAGED)
    monkeypatch.setattr(bhr.broad_universe, "racional_symbols", lambda: {"AAPL"})
    monkeypatch.setattr(bhr, "build_tradier_provider", lambda: _FakeProvider())
    try:
        result = bhr.run_batch(limit=10, workers=1, delay_ms=0, period="3mo", batch_timeout_s=30)
        assert result["ok"] == 3  # AAPL + SOXL + UVXY
        procesados = reg.processed_symbols()
        assert "SOXL" in procesados
        assert "UVXY" in procesados
        with reg._connect() as conn:
            row = conn.execute("SELECT status FROM reference_checkpoint WHERE symbol='SOXL'").fetchone()
        assert row["status"] == "ok"
    finally:
        _restore()


def test_run_batch_universo_es_exactamente_equity_mas_leveraged(monkeypatch):
    """Caso 8: el universo resultante es exactamente EQUITY (AAPL) +
    ETFs apalancados/inversos (SOXL, UVXY) -- ni QQQ (pasivo) ni XYZW
    (warrant) entran."""
    _fresh()
    monkeypatch.setattr(bhr.broad_universe, "fetch_broad_universe_meta", lambda: _META_CON_LEVERAGED)
    monkeypatch.setattr(bhr.broad_universe, "racional_symbols", lambda: {"AAPL"})
    monkeypatch.setattr(bhr, "build_tradier_provider", lambda: _FakeProvider())
    try:
        result = bhr.run_batch(limit=10, workers=1, delay_ms=0, period="3mo", batch_timeout_s=30)
        assert result["universo_total"] == 3  # AAPL, SOXL, UVXY
        assert reg.processed_symbols() == {"AAPL", "SOXL", "UVXY"}
        # clasificacion sigue contando TODOS los tipos reales de la fuente,
        # sin cambios -- el filtro de universo es aparte de este conteo.
        assert result["clasificacion"] == {"EQUITY": 1, "ETF": 3, "WARRANT": 1}  # QQQ+SOXL+UVXY
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
