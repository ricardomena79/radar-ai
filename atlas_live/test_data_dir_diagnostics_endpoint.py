"""Tests de los endpoints admin de diagnostico de persistencia
(2026-08-17) -- mismo patron que test_admin_build_historical_reference.py:
neutraliza el arranque pesado antes de importar el servidor, sin red, sin
tocar ATLAS_DATA_DIR real (data_dir_diagnostics se mockea por completo)."""

import os

import atlas_live.backtest.seed_import as _si
import atlas_live.radar.radar_worker as _rw
import atlas_live.scan_worker as _sw

_orig_seed = _si.import_all_seeds
_orig_refresh = _sw.start_background_refresh
_orig_radar = _rw.start_universe_radar
_si.import_all_seeds = lambda *a, **k: None
_sw.start_background_refresh = lambda *a, **k: None
_rw.start_universe_radar = lambda *a, **k: None
try:
    from atlas_live import server  # noqa: E402
finally:
    _si.import_all_seeds = _orig_seed
    _sw.start_background_refresh = _orig_refresh
    _rw.start_universe_radar = _orig_radar

from atlas_live import data_dir_diagnostics as ddd  # noqa: E402


def _client():
    return server.app.test_client()


def test_diagnostics_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().get("/api/admin/data-dir-diagnostics")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_diagnostics_con_token_devuelve_reporte_real():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    orig = ddd.diagnostics
    ddd.diagnostics = lambda: {"atlas_data_dir_resolved": "/data", "atlas_data_dir_exists": True}
    try:
        r = _client().get("/api/admin/data-dir-diagnostics?token=secreto-real")
        assert r.status_code == 200
        assert r.get_json()["atlas_data_dir_resolved"] == "/data"
    finally:
        ddd.diagnostics = orig
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_marker_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().post("/api/admin/data-dir-diagnostics/marker")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_marker_con_token_dispara_write_marker_once():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    orig = ddd.write_marker_once
    ddd.write_marker_once = lambda: {"created": True, "already_existed": False, "marker": {"marker_id": "abc"}}
    try:
        r = _client().post("/api/admin/data-dir-diagnostics/marker?token=secreto-real")
        assert r.status_code == 200
        assert r.get_json()["marker"]["marker_id"] == "abc"
    finally:
        ddd.write_marker_once = orig
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
