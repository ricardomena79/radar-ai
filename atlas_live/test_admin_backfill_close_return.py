"""Tests del endpoint admin /api/admin/backfill-close-return (2026-08-23).
Mismo patrón que test_admin_build_historical_reference.py -- neutraliza el
arranque pesado antes de importar el servidor, sin red, sin disparar ningún
backfill real (start_background_backfill_close_return se reemplaza por un stub)."""

import os

import atlas_live.backtest.seed_import as _si
import atlas_live.market_view as _mv
import atlas_live.radar.radar_worker as _rw
import atlas_live.scan_worker as _sw

_orig_seed = _si.import_all_seeds
_orig_refresh = _sw.start_background_refresh
_orig_radar = _rw.start_universe_radar
_orig_market_view = _mv.start_market_view
_si.import_all_seeds = lambda *a, **k: None
_sw.start_background_refresh = lambda *a, **k: None
_rw.start_universe_radar = lambda *a, **k: None
_mv.start_market_view = lambda *a, **k: None
try:
    from atlas_live import server  # noqa: E402
finally:
    _si.import_all_seeds = _orig_seed
    _sw.start_background_refresh = _orig_refresh
    _rw.start_universe_radar = _orig_radar
    _mv.start_market_view = _orig_market_view

from atlas_live.radar import eod_report as eod  # noqa: E402


def _client():
    return server.app.test_client()


def test_sin_token_configurado_rechaza_siempre():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().post("/api/admin/backfill-close-return?date=2026-08-21&token=cualquiera")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_sin_date_devuelve_400():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    try:
        r = _client().post("/api/admin/backfill-close-return?token=secreto-real")
        assert r.status_code == 400
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_sin_tradier_token_devuelve_503():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    import atlas_live.data_fusion.universe_quotes as uq

    orig = uq.build_tradier_provider
    uq.build_tradier_provider = lambda: None
    try:
        r = _client().post("/api/admin/backfill-close-return?date=2026-08-21&token=secreto-real")
        assert r.status_code == 503
    finally:
        uq.build_tradier_provider = orig
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_token_correcto_dispara_y_devuelve_202():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    import atlas_live.data_fusion.universe_quotes as uq

    orig_provider = uq.build_tradier_provider
    orig_start = eod.start_background_backfill_close_return
    uq.build_tradier_provider = lambda: object()
    eod.start_background_backfill_close_return = lambda *a, **k: {"started": True, "market_date": "2026-08-21"}
    try:
        r = _client().post("/api/admin/backfill-close-return?date=2026-08-21&token=secreto-real")
        assert r.status_code == 202
        assert r.get_json()["started"] is True
    finally:
        uq.build_tradier_provider = orig_provider
        eod.start_background_backfill_close_return = orig_start
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_ya_en_curso_devuelve_409():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    import atlas_live.data_fusion.universe_quotes as uq

    orig_provider = uq.build_tradier_provider
    orig_start = eod.start_background_backfill_close_return
    uq.build_tradier_provider = lambda: object()
    eod.start_background_backfill_close_return = lambda *a, **k: {"started": False, "reason": "ya hay un backfill corriendo"}
    try:
        r = _client().post("/api/admin/backfill-close-return?date=2026-08-21&token=secreto-real")
        assert r.status_code == 409
    finally:
        uq.build_tradier_provider = orig_provider
        eod.start_background_backfill_close_return = orig_start
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_status_endpoint_exige_token():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().get("/api/admin/backfill-close-return/status")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_status_endpoint_con_token_devuelve_estado_real():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    orig = eod.get_backfill_close_return_status
    eod.get_backfill_close_return_status = lambda: {"running": False, "market_date": "2026-08-21", "result": {"n_actualizadas": 5}, "error": None}
    try:
        r = _client().get("/api/admin/backfill-close-return/status?token=secreto-real")
        assert r.status_code == 200
        assert r.get_json()["result"]["n_actualizadas"] == 5
    finally:
        eod.get_backfill_close_return_status = orig
        del os.environ["ATLAS_ADMIN_TOKEN"]


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
