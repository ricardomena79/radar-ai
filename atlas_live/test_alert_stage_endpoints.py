"""Tests de los endpoints de ALERTA TEMPRANA (Fase 4, 2026-08-17):
GET /api/radar-alert-stages (público) y GET /api/admin/alert-effectiveness-report
(protegido) -- mismo patrón que los demás tests de endpoints: neutraliza el
arranque pesado, sin red."""

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

from atlas_live.radar import candidate_registry as radar_registry  # noqa: E402


def _client():
    return server.app.test_client()


def test_radar_alert_stages_es_publico_y_devuelve_conteos_reales():
    orig = radar_registry.current_alert_stages_for_date
    radar_registry.current_alert_stages_for_date = lambda market_date: [
        {"ticker": "AAPL", "stage": "ALERTA_FUERTE"},
        {"ticker": "TSLA", "stage": "ALERTA_FUERTE"},
        {"ticker": "MSFT", "stage": "PREPARACION"},
    ]
    try:
        r = _client().get("/api/radar-alert-stages")
        assert r.status_code == 200
        data = r.get_json()
        assert len(data["candidatas_con_alerta"]) == 3
        assert data["conteos_por_ventana"] == {"ALERTA_FUERTE": 2, "PREPARACION": 1}
    finally:
        radar_registry.current_alert_stages_for_date = orig


def test_alert_effectiveness_report_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().get("/api/admin/alert-effectiveness-report")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_alert_effectiveness_report_con_token_pasa_el_date_param():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    orig = radar_registry.alert_stage_effectiveness_report
    captured = {}
    radar_registry.alert_stage_effectiveness_report = lambda market_date=None: captured.update(
        {"market_date": market_date}
    ) or {"general": {}}
    try:
        r = _client().get("/api/admin/alert-effectiveness-report?token=secreto-real&date=2026-08-17")
        assert r.status_code == 200
        assert captured["market_date"] == "2026-08-17"
    finally:
        radar_registry.alert_stage_effectiveness_report = orig
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
