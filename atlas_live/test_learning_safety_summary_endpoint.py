"""HITO 4 -- Fase 4.3 (2026-09-04, autorizado explícitamente en Plan Mode):
tests del endpoint PÚBLICO `/api/aprendizaje-seguridad-resumen` -- sin
token (a diferencia de los 5 endpoints admin de Hito 3), mismo molde
sin red/sin hilos de fondo que el resto de tests de endpoint."""

import atlas_live.backtest.seed_import as _si
import atlas_live.catalyst.catalyst_worker as _cw
import atlas_live.market_study.study_worker as _stw
import atlas_live.market_view as _mv
import atlas_live.radar.radar_worker as _rw
import atlas_live.scan_worker as _sw

_orig_seed = _si.import_all_seeds
_orig_refresh = _sw.start_background_refresh
_orig_radar = _rw.start_universe_radar
_orig_market_view = _mv.start_market_view
_orig_study = _stw.start_study_worker
_orig_catalyst = _cw.start_catalyst_worker
_si.import_all_seeds = lambda *a, **k: None
_sw.start_background_refresh = lambda *a, **k: None
_rw.start_universe_radar = lambda *a, **k: None
_mv.start_market_view = lambda *a, **k: None
_stw.start_study_worker = lambda *a, **k: None
_cw.start_catalyst_worker = lambda *a, **k: None
try:
    from atlas_live import server  # noqa: E402
finally:
    _si.import_all_seeds = _orig_seed
    _sw.start_background_refresh = _orig_refresh
    _rw.start_universe_radar = _orig_radar
    _mv.start_market_view = _orig_market_view
    _stw.start_study_worker = _orig_study
    _cw.start_catalyst_worker = _orig_catalyst

from atlas_live.core import learning_safety_summary as lss  # noqa: E402


def _client():
    return server.app.test_client()


def test_responde_200_sin_token():
    r = _client().get("/api/aprendizaje-seguridad-resumen")
    assert r.status_code == 200


def test_estructura_esperada_y_delega_en_build_safety_summary(monkeypatch):
    sintetico = {
        "generated_at": "2026-09-04T00:00:00+00:00",
        "activation_mechanism_state": "OFF",
        "eligibilidad": {"ok": True, "n_eventos": 0, "conteos_por_estado": {}},
        "shadow_observation": {"ok": True, "n_observaciones": 0, "universo_conocimiento_conteos": {}},
        "activacion": {"ok": True, "n_eventos": 0, "conteos_por_estado": {}, "n_revocaciones_registradas": 0},
        "evaluacion_continua": {"ok": True, "n_eventos": 0, "conteos_por_estado": {}, "n_revocaciones_disparadas": 0},
    }
    monkeypatch.setattr(lss, "build_safety_summary", lambda: sintetico)
    r = _client().get("/api/aprendizaje-seguridad-resumen")
    assert r.status_code == 200
    assert r.get_json() == sintetico


def test_contra_el_estado_real_no_filtra_eventos():
    r = _client().get("/api/aprendizaje-seguridad-resumen")
    assert r.status_code == 200
    body = r.get_json()

    def _sin_eventos(obj):
        if isinstance(obj, dict):
            assert "eventos" not in obj
            for v in obj.values():
                _sin_eventos(v)
        elif isinstance(obj, list):
            for item in obj:
                _sin_eventos(item)

    _sin_eventos(body)
    assert "activation_mechanism_state" in body
    assert body["activation_mechanism_state"] in ("OFF", "ON_CONTROLADO")
