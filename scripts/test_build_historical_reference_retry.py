"""Tests del reintento de fallos transitorios en run_batch() (2026-09-11,
autorizado explícitamente -- Parte 1): un símbolo con `status="error"` o
`"sin_datos"` de una corrida anterior debe volver a quedar pendiente en la
siguiente corrida; `status="ok"` sigue siendo la única exclusión
definitiva. Sin red: se mockea broad_universe y el provider. DB temporal,
nunca toca historical_reference.db real."""

import subprocess
import tempfile
import uuid as _uuid
from pathlib import Path

import pandas as pd

from atlas_live.reference import reference_registry as reg
from scripts import build_historical_reference as bhr

_ORIG_DB_PATH = reg.DB_PATH

_META = {
    "AAPL": {"exchange": "NASDAQ", "name": "Apple Inc.", "type": "EQUITY"},
    "MSFT": {"exchange": "NASDAQ", "name": "Microsoft Corporation", "type": "EQUITY"},
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
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_bhr_retry_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None


def _restore():
    reg.DB_PATH = _ORIG_DB_PATH
    reg._schema_ready_for = None


def _n_daily_features(symbol):
    with reg._connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM daily_features WHERE symbol=?", (symbol,)).fetchone()[0]


def test_1_status_ok_no_se_vuelve_a_procesar(monkeypatch):
    _fresh()
    monkeypatch.setattr(bhr.broad_universe, "fetch_broad_universe_meta", lambda: _META)
    monkeypatch.setattr(bhr.broad_universe, "racional_symbols", lambda: set())
    monkeypatch.setattr(bhr, "build_tradier_provider", lambda: _FakeProvider())
    try:
        reg.mark_processed("AAPL", "ok", 40, 30, exchange="NASDAQ", name="Apple Inc.")
        result = bhr.run_batch(limit=10, workers=1, delay_ms=0, period="3mo", batch_timeout_s=30)
        # Solo MSFT queda pendiente -- AAPL (status="ok") no se reprocesa.
        assert result["procesados_esta_corrida"] == 1
        with reg._connect() as conn:
            row = conn.execute("SELECT n_features FROM reference_checkpoint WHERE symbol='AAPL'").fetchone()
        assert row["n_features"] == 40  # checkpoint original intacto, nunca sobreescrito
    finally:
        _restore()


def test_2_status_error_vuelve_a_quedar_pendiente(monkeypatch):
    _fresh()
    monkeypatch.setattr(bhr.broad_universe, "fetch_broad_universe_meta", lambda: _META)
    monkeypatch.setattr(bhr.broad_universe, "racional_symbols", lambda: set())
    monkeypatch.setattr(bhr, "build_tradier_provider", lambda: _FakeProvider())
    try:
        reg.mark_processed("AAPL", "error", 0, 0, note="Timeout: fallo transitorio")
        result = bhr.run_batch(limit=10, workers=1, delay_ms=0, period="3mo", batch_timeout_s=30)
        assert result["procesados_esta_corrida"] == 2  # AAPL Y MSFT, ambos pendientes
        assert result["ok"] == 2
        with reg._connect() as conn:
            row = conn.execute("SELECT status FROM reference_checkpoint WHERE symbol='AAPL'").fetchone()
        assert row["status"] == "ok"  # se reprocesó con éxito, checkpoint actualizado
    finally:
        _restore()


def test_3_status_sin_datos_vuelve_a_quedar_pendiente(monkeypatch):
    _fresh()
    monkeypatch.setattr(bhr.broad_universe, "fetch_broad_universe_meta", lambda: _META)
    monkeypatch.setattr(bhr.broad_universe, "racional_symbols", lambda: set())
    monkeypatch.setattr(bhr, "build_tradier_provider", lambda: _FakeProvider())
    try:
        reg.mark_processed("AAPL", "sin_datos", 0, 0, note="solo 5 velas")
        result = bhr.run_batch(limit=10, workers=1, delay_ms=0, period="3mo", batch_timeout_s=30)
        assert result["procesados_esta_corrida"] == 2
        assert result["ok"] == 2
        with reg._connect() as conn:
            row = conn.execute("SELECT status FROM reference_checkpoint WHERE symbol='AAPL'").fetchone()
        assert row["status"] == "ok"
    finally:
        _restore()


def test_4_simbolo_sin_checkpoint_queda_pendiente(monkeypatch):
    """Comportamiento ya existente, reconfirmado sin cambios: un símbolo
    que nunca tuvo ningún checkpoint queda pendiente, igual que antes."""
    _fresh()
    monkeypatch.setattr(bhr.broad_universe, "fetch_broad_universe_meta", lambda: _META)
    monkeypatch.setattr(bhr.broad_universe, "racional_symbols", lambda: set())
    monkeypatch.setattr(bhr, "build_tradier_provider", lambda: _FakeProvider())
    try:
        result = bhr.run_batch(limit=10, workers=1, delay_ms=0, period="3mo", batch_timeout_s=30)
        assert result["procesados_esta_corrida"] == 2
        assert reg.processed_symbols() == {"AAPL", "MSFT"}
    finally:
        _restore()


def test_5_retry_no_duplica_datos_validos(monkeypatch):
    """Un símbolo con status="error" que YA tenía algunas filas reales en
    daily_features (caso límite: una excepción no capturada a mitad del
    loop de _process_one, ver run_batch) no debe duplicarlas al
    reprocesarse -- record_features() ya usa INSERT OR IGNORE con
    UNIQUE(symbol, date), reutilizado tal cual, sin cambios."""
    _fresh()
    monkeypatch.setattr(bhr.broad_universe, "fetch_broad_universe_meta", lambda: _META)
    monkeypatch.setattr(bhr.broad_universe, "racional_symbols", lambda: set())
    monkeypatch.setattr(bhr, "build_tradier_provider", lambda: _FakeProvider())
    try:
        # Simula el caso límite: AAPL quedó con status="error" pero ya
        # tenía filas reales persistidas de un intento anterior.
        df = _synthetic_df()
        from atlas_live.reference import daily_reference as dr
        feats = dr.compute_features(df, 25)
        reg.record_features("AAPL", feats)
        assert _n_daily_features("AAPL") == 1
        reg.mark_processed("AAPL", "error", 1, 0, note="fallo a mitad de camino")

        bhr.run_batch(limit=10, workers=1, delay_ms=0, period="3mo", batch_timeout_s=30)

        # El reprocesamiento vuelve a calcular las mismas fechas -- INSERT
        # OR IGNORE evita duplicar la fila ya existente; el conteo final
        # debe ser el mismo que produce un procesamiento limpio de AAPL
        # (nunca el doble).
        n_final = _n_daily_features("AAPL")
        assert n_final > 1  # se completaron las filas faltantes
        with reg._connect() as conn:
            duplicados = conn.execute(
                "SELECT symbol, date, COUNT(*) c FROM daily_features WHERE symbol='AAPL' "
                "GROUP BY symbol, date HAVING c > 1"
            ).fetchall()
        assert duplicados == []  # ninguna fecha duplicada
    finally:
        _restore()


def test_9_archivos_protegidos_sin_diff():
    """Confirma que esta corrección no tocó ninguna regla de detección ni
    componente fuera de alcance -- `git diff --stat` sobre la lista
    protegida debe quedar vacío. `universe.py`/`server.py`/
    `racional_universe.json` quedan fuera de esta lista a propósito: ya
    tenían diff propio, ajeno a esta corrección, desde antes de esta
    sesión (no relacionado con reglas de detección)."""
    protegidos = [
        "atlas_live/radar/candidate_gates.py",
        "atlas_live/radar/alert_stage.py",
        "atlas_live/radar/priority_classifier.py",
        "atlas_live/radar/candidate_tracker.py",
        "atlas_live/scan_worker.py",
        "atlas_live/static/cabina",
        "atlas/data/universe/universe.py",
        "atlas_live/core",
    ]
    resultado = subprocess.run(
        ["git", "diff", "--stat", "--"] + protegidos, capture_output=True, text=True, cwd=".",
    )
    assert resultado.stdout.strip() == "", f"archivos protegidos con diff pendiente: {resultado.stdout}"
