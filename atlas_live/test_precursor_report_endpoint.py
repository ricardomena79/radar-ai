"""Tests del endpoint admin de solo lectura /api/admin/precursor-report
(2026-08-17, Fase 3b) -- mismo patrón que los demás endpoints admin: sin
red, sin tocar la base real (generate_precursor_report se mockea)."""

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

from atlas_live.learning import precursor_analysis as pa  # noqa: E402


def _client():
    return server.app.test_client()


def test_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().get("/api/admin/precursor-report")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_con_token_devuelve_el_reporte_real():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    orig = pa.generate_precursor_report
    pa.generate_precursor_report = lambda **kwargs: {"n_filas_totales": 999, "por_umbral": {}}
    try:
        r = _client().get("/api/admin/precursor-report?token=secreto-real")
        assert r.status_code == 200
        assert r.get_json()["n_filas_totales"] == 999
    finally:
        pa.generate_precursor_report = orig
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_separation_report_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().get("/api/admin/separation-report")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_separation_report_con_token_devuelve_el_reporte_real():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    orig = pa.generate_separation_report
    pa.generate_separation_report = lambda **kwargs: {"n_onsets_por_categoria": {"A_20_49": 7}}
    try:
        r = _client().get("/api/admin/separation-report?token=secreto-real")
        assert r.status_code == 200
        assert r.get_json()["n_onsets_por_categoria"]["A_20_49"] == 7
    finally:
        pa.generate_separation_report = orig
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
