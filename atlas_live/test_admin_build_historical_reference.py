"""Tests del endpoint admin /api/admin/build-historical-reference
(2026-08-16). Neutraliza el arranque pesado antes de importar el servidor
(mismo patrón que test_config_endpoint.py) -- sin red, sin disparar ningún
batch real (start_background_build se reemplaza por un stub)."""

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

from scripts import build_historical_reference as bhr  # noqa: E402


def _client():
    return server.app.test_client()


def test_sin_token_configurado_rechaza_siempre():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().post("/api/admin/build-historical-reference?token=cualquiera")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_token_incorrecto_rechaza():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    try:
        r = _client().post("/api/admin/build-historical-reference?token=incorrecto")
        assert r.status_code == 403
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_token_correcto_dispara_y_devuelve_202():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    orig = bhr.start_background_build
    bhr.start_background_build = lambda **kwargs: {"started": True, **kwargs}
    try:
        r = _client().post("/api/admin/build-historical-reference?token=secreto-real")
        assert r.status_code == 202
        assert r.get_json()["started"] is True
    finally:
        bhr.start_background_build = orig
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_ya_en_curso_devuelve_409():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    orig = bhr.start_background_build
    bhr.start_background_build = lambda **kwargs: {"started": False, "reason": "ya hay una construcción en curso"}
    try:
        r = _client().post("/api/admin/build-historical-reference?token=secreto-real")
        assert r.status_code == 409
    finally:
        bhr.start_background_build = orig
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_header_x_admin_token_tambien_funciona():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    orig = bhr.start_background_build
    bhr.start_background_build = lambda **kwargs: {"started": True}
    try:
        r = _client().post("/api/admin/build-historical-reference", headers={"X-Admin-Token": "secreto-real"})
        assert r.status_code == 202
    finally:
        bhr.start_background_build = orig
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_status_endpoint_tambien_exige_token():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().get("/api/admin/build-historical-reference/status")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_status_endpoint_con_token_devuelve_estado_real():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    orig = bhr.build_status
    bhr.build_status = lambda: {"build_state": "NUNCA_INICIADO", "corriendo_en_este_proceso": False}
    try:
        r = _client().get("/api/admin/build-historical-reference/status?token=secreto-real")
        assert r.status_code == 200
        assert r.get_json()["build_state"] == "NUNCA_INICIADO"
    finally:
        bhr.build_status = orig
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
