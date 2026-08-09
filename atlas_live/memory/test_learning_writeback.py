"""Tests del write-back de aprendizaje (F3/F4, 2026-08-09). Offline,
determinista, contra un Memory Store temporal -- nunca la base real.
"""

import tempfile
from pathlib import Path

from atlas_live.memory import classifier, learning_writeback as lw
from atlas_live.memory import store

_ORIG = store.DB_PATH
_TMP = Path(tempfile.gettempdir()) / "atlas_test_writeback.db"


def _reset():
    for s in ("", "-wal", "-shm"):
        p = Path(str(_TMP) + s)
        if p.exists():
            p.unlink()
    store.DB_PATH = _TMP


def _restore():
    store.DB_PATH = _ORIG
    for s in ("", "-wal", "-shm"):
        p = Path(str(_TMP) + s)
        if p.exists():
            p.unlink()


_METRICS = {"price": 12.0, "gap_pct": 14.0, "change_pct": 18.0, "relative_volume": 4.0,
            "dollar_volume": 2e7, "volatility_score": 60.0, "market_cap": 3e8}


def _sealed(symbol="AAA", date="2026-08-09", score=80.0, metrics=None):
    return {"symbol": symbol, "date": date, "score": score,
            "metrics_snapshot": _METRICS if metrics is None else metrics}


def _summary(final_return_pct):
    return {"final_return_pct": final_return_pct}


def test_build_observation_none_without_result():
    # Sin final_return_pct -> no se puede clasificar -> None (no se inventa).
    assert lw.build_observation(_sealed(), _summary(None)) is None


def test_build_observation_uses_existing_classifier_definition():
    final = 20.0
    obs = lw.build_observation(_sealed(score=80.0), _summary(final))
    expected_cat = classifier.classify_observation(
        {"ground_truth_change_pct": final, "explosive": {"eligible": True}}
    )
    assert obs["category"] == expected_cat
    assert obs["source_version"] == "live"
    assert obs["checkpoint_minutes"] == lw.CLOSE_CHECKPOINT_MINUTES
    assert obs["metrics"] == _METRICS  # snapshot de detección real


def test_record_inserts_new_and_is_idempotent():
    _reset()
    try:
        assert lw.record_from_closed_trajectory(_sealed(), _summary(20.0)) is True
        assert store.count_observations(source_version="live") == 1
        # mismo cierre reprocesado (retry/reinicio) -> no duplica
        assert lw.record_from_closed_trajectory(_sealed(), _summary(20.0)) is False
        assert store.count_observations(source_version="live") == 1
    finally:
        _restore()


def test_acierto_vs_fallo_categoria():
    _reset()
    try:
        # resultado fuerte -> EXPLOSION (acierto)
        lw.record_from_closed_trajectory(_sealed("WIN", score=80.0), _summary(25.0))
        # resultado flojo, elegible -> FALSE_BREAKOUT (no acierto)
        lw.record_from_closed_trajectory(_sealed("LOSE", score=70.0), _summary(0.3))
        obs = store.get_observations()
        cats = {o["symbol"]: o["category"] for o in obs}
        assert cats["WIN"] == "EXPLOSION"
        assert cats["LOSE"] != "EXPLOSION"
    finally:
        _restore()


def test_historical_seed_not_counted_as_live():
    _reset()
    try:
        # una observación "histórica" (source v1) y una live
        store.record_observation("SEED", "2026-08-01", 10, "NORMAL", _METRICS, source_version="v1")
        lw.record_from_closed_trajectory(_sealed("LIVE"), _summary(20.0))
        assert store.count_observations() == 2
        assert store.count_observations(source_version="live") == 1  # solo la nueva
        assert store.count_observations(source_version="v1") == 1    # histórica aparte
    finally:
        _restore()


def test_missing_metrics_snapshot_still_records_with_none_fields():
    _reset()
    try:
        # fila vieja sin snapshot (pre-F2): se registra igual, métricas None,
        # sin inventar valores.
        sealed = _sealed(metrics=None)
        sealed["metrics_snapshot"] = None
        assert lw.record_from_closed_trajectory(sealed, _summary(20.0)) is True
        obs = store.get_observations()[0]
        assert obs["gap_pct"] is None and obs["relative_volume"] is None
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
