"""Tests del estudio amplio de mercado (2026-08-10). Detección pura + registro
con DB temporal. Offline, determinista, sin red, sin tocar bases reales.
"""

import sqlite3
import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.market_study import explosion_scan as es
from atlas_live.market_study import study_registry as reg

_ORIG = reg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_market_study_{_uuid.uuid4().hex}.db"


def _restore():
    reg.DB_PATH = _ORIG


def _bar(date, o, h, l, c, v):
    return {"date": date, "open": o, "high": h, "low": l, "close": c, "volume": v}


# --------------------------- detección (función pura) ---------------------------

def test_detecta_dia_de_explosion_y_features_leakage_safe():
    bars = [
        _bar("2026-07-01", 10, 10.5, 9.8, 10.0, 1000),
        _bar("2026-07-02", 10, 10.5, 9.8, 10.0, 1200),
        # día de explosión: prev_close=10, high=14 -> +40%
        _bar("2026-07-03", 10.2, 14.0, 10.0, 13.0, 9000),
    ]
    ex = es.detect_explosions_from_daily(bars)
    assert len(ex) == 1
    e = ex[0]
    assert e["date"] == "2026-07-03"
    assert e["max_intraday_pct"] == 40.0                 # RESULTADO
    assert round(e["gap_open_pct"], 1) == 2.0            # feature (open 10.2 vs prev 10)
    # prior_avg_volume usa SOLO días previos (1000,1200) -> 1100, NO el 9000
    assert e["prior_avg_volume"] == 1100.0


def test_no_detecta_movimiento_chico():
    bars = [_bar("2026-07-01", 10, 10.5, 9.8, 10.0, 1000),
            _bar("2026-07-02", 10, 11.5, 9.8, 11.0, 1000)]  # +15% intradía, no llega a +30
    assert es.detect_explosions_from_daily(bars) == []


def test_bandas_por_magnitud():
    bars = [_bar("2026-07-01", 10, 10, 10, 10.0, 1000),
            _bar("2026-07-02", 10, 31.0, 10, 25.0, 5000)]  # +210% intradía
    ex = es.detect_explosions_from_daily(bars)
    assert ex[0]["max_intraday_pct"] == 210.0


# --------------------------- registro (DB temporal) ---------------------------

def test_registro_separa_features_de_outcome():
    _fresh()
    try:
        reg.record_explosion("BOOM", "2026-07-03", prev_close=10, open_price=10.2,
                             gap_open_pct=2.0, prior_avg_volume=1100, market_cap=3e8,
                             available_in_racional=True, max_intraday_pct=140.0,
                             close_change_pct=30.0, day_volume=9000)
        # anti-leakage estructural: features NO puede tener el resultado
        c = sqlite3.connect(reg.DB_PATH)
        feat_cols = {r[1] for r in c.execute("PRAGMA table_info(explosion_features)")}
        assert "max_intraday_pct" not in feat_cols and "reached_100" not in feat_cols
        out_cols = {r[1] for r in c.execute("PRAGMA table_info(explosion_outcome)")}
        assert "max_intraday_pct" in out_cols
        assert reg.count_explosions() == 1
    finally:
        _restore()


def test_idempotencia_no_duplica():
    _fresh()
    try:
        for _ in range(2):
            reg.record_explosion("BOOM", "2026-07-03", 10, 10.2, 2.0, 1100, 3e8, True,
                                 140.0, 30.0, 9000)
        assert reg.count_explosions() == 1  # (ticker,date) único
    finally:
        _restore()


def test_available_in_racional_flag():
    _fresh()
    try:
        reg.record_explosion("INRAC", "2026-07-03", 10, 10, 0, 1000, 1e8, True, 50.0, 10.0, 5000)
        reg.record_explosion("OUTRAC", "2026-07-03", 10, 10, 0, 1000, 1e8, False, 60.0, 12.0, 5000)
        assert reg.count_explosions(available_in_racional=True) == 1
        assert reg.count_explosions(available_in_racional=False) == 1
        # el conocimiento fuera de Racional NO se descarta
        s = reg.summary()
        assert s["explosiones_en_racional"] == 1 and s["explosiones_fuera_de_racional"] == 1
    finally:
        _restore()


def test_checkpoint_reanudacion():
    _fresh()
    try:
        assert not reg.is_processed("AAA")
        reg.mark_processed("AAA", "ok", explosions_found=2)
        reg.mark_processed("BBB", "sin_datos", explosions_found=0)
        assert reg.is_processed("AAA")
        assert reg.processed_symbols() == {"AAA", "BBB"}
        # re-marcar no rompe (idempotente)
        reg.mark_processed("AAA", "ok", explosions_found=3)
        assert reg.summary()["simbolos_procesados"] == 2
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
