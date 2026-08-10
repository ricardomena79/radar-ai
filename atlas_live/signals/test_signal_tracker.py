"""Tests del seguimiento/resolución de señales (2026-08-09). DB temporal,
sintética. El estudio histórico (explosion_history) se lee de solo lectura.
"""

import tempfile
import uuid as _uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from atlas_live.signals import signal_registry as reg
from atlas_live.signals import signal_tracker as tr

ET = ZoneInfo("America/New_York")
_ORIG = reg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_signal_tracker_{_uuid.uuid4().hex}.db"
    tr._group_cache.clear()


def _restore():
    reg.DB_PATH = _ORIG


def _et(h, m, day=10):
    return datetime(2026, 8, day, h, m, tzinfo=ET)


def _row(symbol, eligible=True, score=70.0, change_pct=5.0, **metrics):
    m = dict(gap_pct=12.0, change_pct=change_pct, relative_volume=3.5,
             dollar_volume=2e7, volatility_score=80.0, market_cap=3e8, price=4.82,
             source="yahoo_finance", price_as_of="2026-08-10T12:00:00+00:00")
    m.update(metrics)
    return {"symbol": symbol, "price": m["price"],
            "explosive": {"eligible": eligible, "score": score, "metrics": m, "stage_trace": ["price", "rvol"]}}


def test_track_crea_senal_premarket_solo_elegibles():
    _fresh()
    try:
        results = [_row("NUWE", eligible=True), _row("XXXX", eligible=False)]
        out = tr.track_cycle(results, now=_et(8, 57))
        assert out["creadas"] == 1  # solo el elegible
        s = reg.get_signal_by_opportunity("NUWE", "2026-08-10")
        assert s is not None and s["session"] == "PREMARKET"
        assert s["historical_group"].startswith("similar a")  # comparación de setup
        assert isinstance(s["similar_historical_cases"], int)
    finally:
        _restore()


def test_track_no_duplica_polling():
    _fresh()
    try:
        results = [_row("NUWE", change_pct=5.0)]
        tr.track_cycle(results, now=_et(8, 57))
        tr.track_cycle([_row("NUWE", change_pct=8.0)], now=_et(9, 2))  # otro poll
        assert reg.count_signals() == 1  # una sola señal
        s = reg.get_signal_by_opportunity("NUWE", "2026-08-10")
        assert len(reg.get_observations(s["signal_uuid"])) == 2  # dos observaciones
    finally:
        _restore()


def test_resolucion_acierto_con_hitos_y_anticipacion():
    _fresh()
    try:
        # Trayectoria real seguida: 5% -> 12% -> 35% (cruza +30 a las 10:10)
        tr.track_cycle([_row("WIN", change_pct=5.0)], now=_et(9, 50))
        tr.track_cycle([_row("WIN", change_pct=12.0)], now=_et(10, 0))
        tr.track_cycle([_row("WIN", change_pct=35.0)], now=_et(10, 10))
        # Día siguiente: resolver
        out = tr.resolve_due(now=_et(9, 0, day=11))
        assert out["resueltas"] == 1
        s = reg.get_signal_by_opportunity("WIN", "2026-08-10")
        assert s["state"] == reg.RESUELTA_ACIERTO
        r = reg.get_result(s["signal_uuid"])
        assert r["result"] == "ACIERTO"
        assert r["max_return_pct"] == 35.0
        assert r["minutes_to_30pct"] is not None       # alcanzó +30%
        assert r["minutes_to_100pct"] is None          # no alcanzó +100% (honesto)
    finally:
        _restore()


def test_resolucion_sin_datos():
    _fresh()
    try:
        # señal sin ninguna observación con return_pct (change_pct None)
        tr.track_cycle([_row("NODATA", change_pct=None)], now=_et(9, 50))
        out = tr.resolve_due(now=_et(9, 0, day=11))
        s = reg.get_signal_by_opportunity("NODATA", "2026-08-10")
        assert s["state"] == reg.RESUELTA_SIN_DATOS
        assert reg.get_result(s["signal_uuid"])["result"] == "SIN_DATOS"  # nunca inventado
    finally:
        _restore()


def test_no_resuelve_el_mismo_dia():
    _fresh()
    try:
        tr.track_cycle([_row("HOY", change_pct=40.0)], now=_et(10, 0))
        out = tr.resolve_due(now=_et(15, 0, day=10))  # mismo día -> no cierra
        assert out["resueltas"] == 0
        assert reg.get_signal_by_opportunity("HOY", "2026-08-10")["state"] == reg.OBSERVANDO
    finally:
        _restore()


def test_stats_con_n_y_evidencia_insuficiente():
    _fresh()
    try:
        tr.track_cycle([_row("A1", change_pct=35.0)], now=_et(10, 0))
        tr.track_cycle([_row("A2", change_pct=10.0)], now=_et(10, 0))
        tr.resolve_due(now=_et(9, 0, day=11))
        st = tr.stats()
        assert st["total_senales"] == 2
        assert st["resueltas"] == 2
        assert st["aciertos"] == 1 and st["fallos"] == 1
        assert st["tasa_acierto_pct"] == 50.0
        assert st["muestra_suficiente"] is False               # n chico
        assert "Evidencia insuficiente" in st["aviso_muestra"]  # no se presenta como confiable
        assert st["pct_alcanzo"]["30pct"] == 50.0
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
