"""Tests de identidad/operabilidad Racional en reference_registry.py
(2026-08-17, universo de mercado completo). DB temporal aislada, nunca
toca historical_reference.db real -- mismo patrón que
scripts/test_build_historical_reference_background.py."""

import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.reference import reference_registry as reg

_ORIG_DB_PATH = reg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_refreg_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None


def _restore():
    reg.DB_PATH = _ORIG_DB_PATH
    reg._schema_ready_for = None


def test_mark_processed_guarda_identidad_y_racional_available():
    _fresh()
    try:
        reg.mark_processed("AAPL", "ok", 10, 8, exchange="NASDAQ", name="Apple Inc.", racional_available=True)
        with reg._connect() as conn:
            row = conn.execute("SELECT * FROM reference_checkpoint WHERE symbol='AAPL'").fetchone()
        assert row["exchange"] == "NASDAQ"
        assert row["name"] == "Apple Inc."
        assert row["racional_available"] == 1
    finally:
        _restore()


def test_mark_processed_racional_no_disponible():
    _fresh()
    try:
        reg.mark_processed("ZZZZ", "ok", 1, 1, exchange="NASDAQ", name="Zzzz Corp", racional_available=False)
        with reg._connect() as conn:
            row = conn.execute("SELECT racional_available FROM reference_checkpoint WHERE symbol='ZZZZ'").fetchone()
        assert row["racional_available"] == 0
    finally:
        _restore()


def test_mark_processed_sin_identidad_queda_null_no_falla():
    _fresh()
    try:
        reg.mark_processed("XYZ", "error", 0, 0, note="sin datos")
        with reg._connect() as conn:
            row = conn.execute("SELECT exchange, name, racional_available FROM reference_checkpoint WHERE symbol='XYZ'").fetchone()
        assert row["exchange"] is None
        assert row["name"] is None
        assert row["racional_available"] is None
    finally:
        _restore()


def test_universe_breakdown_cuenta_real():
    _fresh()
    try:
        reg.mark_processed("A", "ok", 1, 1, racional_available=True)
        reg.mark_processed("B", "ok", 1, 1, racional_available=True)
        reg.mark_processed("C", "ok", 1, 1, racional_available=False)
        reg.mark_processed("D", "error", 0, 0)  # sin identidad -> desconocido
        breakdown = reg.universe_breakdown()
        assert breakdown == {"racional_available": 2, "racional_no_disponible": 1, "racional_desconocido": 1}
    finally:
        _restore()


def test_universe_breakdown_vacio():
    _fresh()
    try:
        assert reg.universe_breakdown() == {"racional_available": 0, "racional_no_disponible": 0, "racional_desconocido": 0}
    finally:
        _restore()


def test_recent_daily_features_devuelve_mas_reciente_primero():
    _fresh()
    try:
        with reg._connect() as conn:
            for date, rv in [("2026-06-01", 1.0), ("2026-06-02", 2.0), ("2026-06-03", 3.0),
                              ("2026-06-04", 4.0), ("2026-06-05", 5.0), ("2026-06-06", 6.0)]:
                conn.execute(
                    "INSERT INTO daily_features (symbol, date, relative_volume, created_at) VALUES (?,?,?,?)",
                    ("AAPL", date, rv, reg._now()),
                )
            conn.commit()
        recent = reg.recent_daily_features("AAPL", n=5)
        assert [r["date"] for r in recent] == ["2026-06-06", "2026-06-05", "2026-06-04", "2026-06-03", "2026-06-02"]
        assert recent[0]["relative_volume"] == 6.0
    finally:
        _restore()


def test_recent_daily_features_simbolo_sin_historial():
    _fresh()
    try:
        assert reg.recent_daily_features("NOPE", n=5) == []
    finally:
        _restore()


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
            p += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
            traceback.print_exc()
            f += 1
    print(f"--- {p} passed, {f} failed ---")
