"""Tests del endpoint admin de solo lectura /api/admin/historical-scoring-report
(2026-08-17, Fase 3) -- mismo patrón que los demás tests de endpoints
admin: neutraliza el arranque pesado, sin red, sin tocar la base real
(generate_report se mockea por completo)."""

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

from atlas_live.learning import historical_scoring as hsc  # noqa: E402


def _client():
    return server.app.test_client()


def test_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().get("/api/admin/historical-scoring-report")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_con_token_devuelve_el_reporte_real():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    orig = hsc.generate_report
    hsc.generate_report = lambda **kwargs: {"n_filas": 123, "tabla_por_grupo": [], "falsos_positivos": []}
    try:
        r = _client().get("/api/admin/historical-scoring-report?token=secreto-real")
        assert r.status_code == 200
        assert r.get_json()["n_filas"] == 123
    finally:
        hsc.generate_report = orig
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
