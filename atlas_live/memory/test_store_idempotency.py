"""Tests de idempotencia y persistencia del Memory Store (F1, 2026-08-09).

Todo corre contra una base SQLite temporal (nunca la real): se apunta
`store.DB_PATH` a un archivo de test y se restaura al terminar. Offline,
determinista, sin red.
"""

import tempfile
from pathlib import Path

from atlas_live.memory import store

_ORIG_DB_PATH = store.DB_PATH
_TMP = Path(tempfile.gettempdir()) / "atlas_test_store_idempotency.db"


def _reset_tmp_db():
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(_TMP) + suffix)
        if p.exists():
            p.unlink()
    store.DB_PATH = _TMP


def _restore():
    store.DB_PATH = _ORIG_DB_PATH
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(_TMP) + suffix)
        if p.exists():
            p.unlink()


_METRICS = {"price": 10.0, "gap_pct": 12.0, "change_pct": 15.0, "relative_volume": 3.2,
            "dollar_volume": 1e7, "volatility_score": 40.0, "market_cap": 2e8}


def test_insert_returns_true_first_time():
    _reset_tmp_db()
    try:
        inserted = store.record_observation("AAA", "2026-08-09", 10, "EXPLOSION", _METRICS, source_version="live")
        assert inserted is True
        assert store.count_observations() == 1
    finally:
        _restore()


def test_duplicate_is_ignored_and_returns_false():
    _reset_tmp_db()
    try:
        assert store.record_observation("AAA", "2026-08-09", 10, "EXPLOSION", _METRICS, source_version="live") is True
        # misma tripleta (symbol,date,checkpoint) -> ignorada, no duplica
        assert store.record_observation("AAA", "2026-08-09", 10, "NORMAL", _METRICS, source_version="live") is False
        assert store.count_observations() == 1  # sigue habiendo una sola
    finally:
        _restore()


def test_different_checkpoint_is_a_new_observation():
    _reset_tmp_db()
    try:
        store.record_observation("AAA", "2026-08-09", 10, "EXPLOSION", _METRICS, source_version="live")
        assert store.record_observation("AAA", "2026-08-09", -1, "EXPLOSION", _METRICS, source_version="live") is True
        assert store.count_observations() == 2
    finally:
        _restore()


def test_observation_exists():
    _reset_tmp_db()
    try:
        assert store.observation_exists("AAA", "2026-08-09", 10) is False
        store.record_observation("AAA", "2026-08-09", 10, "EXPLOSION", _METRICS, source_version="live")
        assert store.observation_exists("AAA", "2026-08-09", 10) is True
    finally:
        _restore()


def test_count_by_source_and_date_separates_historical_from_live():
    _reset_tmp_db()
    try:
        store.record_observation("SEEDSYM", "2026-08-01", 10, "NORMAL", _METRICS, source_version="v1")
        store.record_observation("LIVESYM", "2026-08-09", -1, "EXPLOSION", _METRICS, source_version="live")
        assert store.count_observations() == 2
        assert store.count_observations(source_version="v1") == 1
        assert store.count_observations(source_version="live") == 1
        assert store.count_observations(source_version="live", date="2026-08-09") == 1
        assert store.count_observations(source_version="live", date="2026-08-01") == 0
    finally:
        _restore()


def test_last_recorded_at_none_then_value():
    _reset_tmp_db()
    try:
        assert store.last_recorded_at(source_version="live") is None  # sin datos -> None, no inventado
        store.record_observation("AAA", "2026-08-09", -1, "EXPLOSION", _METRICS, source_version="live")
        ts = store.last_recorded_at(source_version="live")
        assert ts is not None and "T" in ts  # ISO real
    finally:
        _restore()


def test_reinicio_no_duplica():
    # Simula un reinicio: se vuelve a "abrir" la misma DB y se reintenta el
    # mismo cierre -> INSERT OR IGNORE lo salta, no duplica.
    _reset_tmp_db()
    try:
        store.record_observation("AAA", "2026-08-09", -1, "EXPLOSION", _METRICS, source_version="live")
        # "reinicio": nueva conexión (implícita en cada llamada) + reintento
        assert store.record_observation("AAA", "2026-08-09", -1, "EXPLOSION", _METRICS, source_version="live") is False
        assert store.count_observations() == 1
    finally:
        _restore()


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__); p += 1
        except Exception as e:
            print("FAIL", fn.__name__, e); traceback.print_exc(); f += 1
    print(f"--- {p} passed, {f} failed ---")
