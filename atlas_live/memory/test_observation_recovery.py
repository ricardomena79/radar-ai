"""Tests de backup/recuperación de observaciones live (F5, 2026-08-09).
Round-trip export->import, idempotencia, y no-sobrescritura del histórico.
Offline, contra un Memory Store temporal.
"""

import tempfile
from pathlib import Path

from atlas_live.memory import observation_recovery as rec
from atlas_live.memory import store

_ORIG = store.DB_PATH
_TMP_DIR = Path(tempfile.mkdtemp(prefix="atlas_test_recovery_"))
_DB = _TMP_DIR / "memory_store.db"
_JSONL = _TMP_DIR / "live_observations.jsonl"

_METRICS = {"price": 12.0, "gap_pct": 14.0, "change_pct": 18.0, "relative_volume": 4.0,
            "dollar_volume": 2e7, "volatility_score": 60.0, "market_cap": 3e8}


def _fresh_db():
    for s in ("", "-wal", "-shm"):
        p = Path(str(_DB) + s)
        if p.exists():
            p.unlink()
    store.DB_PATH = _DB


def _restore():
    store.DB_PATH = _ORIG


def test_export_then_recover_after_volume_loss():
    _fresh_db()
    try:
        # estado inicial: 1 histórica + 2 live
        store.record_observation("SEED", "2026-08-01", 10, "NORMAL", _METRICS, source_version="v1")
        store.record_observation("LIVE1", "2026-08-09", -1, "EXPLOSION", _METRICS, source_version="live")
        store.record_observation("LIVE2", "2026-08-09", -1, "WEAK", _METRICS, source_version="live")
        ts_live1 = [o for o in store.get_observations() if o["symbol"] == "LIVE1"][0]["recorded_at"]

        rep = rec.export_live_to_jsonl(_JSONL)
        assert rep["exported"] == 2  # solo las live, no la histórica

        # "pérdida del Volume": base nueva vacía
        _fresh_db()
        assert store.count_observations() == 0

        # recuperación
        rec.import_all(_TMP_DIR)
        assert store.count_observations(source_version="live") == 2
        # recorded_at ORIGINAL preservado (no inventado en la recuperación)
        rec_live1 = [o for o in store.get_observations() if o["symbol"] == "LIVE1"][0]["recorded_at"]
        assert rec_live1 == ts_live1
    finally:
        _restore()


def test_reimport_is_idempotent():
    _fresh_db()
    try:
        store.record_observation("LIVE1", "2026-08-09", -1, "EXPLOSION", _METRICS, source_version="live")
        rec.export_live_to_jsonl(_JSONL)
        rec.import_all(_TMP_DIR)  # 1ra
        rec.import_all(_TMP_DIR)  # 2da -> no duplica
        assert store.count_observations(source_version="live") == 1
    finally:
        _restore()


def test_import_does_not_overwrite_existing_live():
    _fresh_db()
    try:
        store.record_observation("LIVE1", "2026-08-09", -1, "EXPLOSION", _METRICS, source_version="live")
        rec.export_live_to_jsonl(_JSONL)
        # una observación viva YA presente con misma clave: la importación no la pisa
        before = [o for o in store.get_observations() if o["symbol"] == "LIVE1"][0]
        rec.import_all(_TMP_DIR)
        after = [o for o in store.get_observations() if o["symbol"] == "LIVE1"][0]
        assert store.count_observations() == 1
        assert before["recorded_at"] == after["recorded_at"]  # intacta
    finally:
        _restore()


def test_import_all_missing_dir_is_noop():
    _fresh_db()
    try:
        reps = rec.import_all(Path(tempfile.gettempdir()) / "no_existe_atlas_xyz")
        assert reps == []
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
