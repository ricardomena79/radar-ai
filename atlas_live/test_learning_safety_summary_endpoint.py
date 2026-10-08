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


import pytest


@pytest.fixture(autouse=True)
def _sin_cache_de_resumen():
    server._SUMMARY_CACHE.clear()
    yield
    server._SUMMARY_CACHE.clear()


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
    # 2026-09-11 (misión "HACER OPERATIVO EL APRENDIZAJE REAL"): el veredicto
    # BASE vs INFORMADA debe estar presente y ser uno de los 4 valores conocidos.
    from atlas_live.core import base_vs_informed_verdict as bvi

    assert body["base_vs_informed_verdict"]["veredicto"] in bvi.VERDICTS
    # 2026-09-12 (misión "EXPERIMENTO SHADOW DE APRENDIZAJE BIDIRECCIONAL"):
    # el veredicto del experimento bidireccional debe estar presente y ser
    # uno de los 5 valores conocidos.
    assert body["bidirectional_shadow_verdict"]["veredicto"] in bvi.VERDICTS_BIDIRECCIONAL


def test_resumen_se_cachea_y_recalcula_una_sola_vez(monkeypatch):
    from atlas_live.core import learning_safety_summary as lss

    llamadas = []

    def fake():
        llamadas.append(1)
        return {"ok": True, "n": len(llamadas)}

    monkeypatch.setattr(lss, "build_safety_summary", fake)
    c = _client()
    r1 = c.get("/api/aprendizaje-seguridad-resumen").get_json()
    r2 = c.get("/api/aprendizaje-seguridad-resumen").get_json()
    assert r1 == r2 and len(llamadas) == 1


def test_learning_maturity_se_cachea_por_fecha(monkeypatch):
    from atlas_live.learning import live_summary

    llamadas = []
    monkeypatch.setattr(live_summary, "get_live_learning_summary",
                        lambda market_date=None: (llamadas.append(market_date) or {"d": market_date}))
    c = _client()
    c.get("/api/learning-maturity?date=2026-10-07")
    c.get("/api/learning-maturity?date=2026-10-07")
    c.get("/api/learning-maturity?date=2026-10-06")
    assert llamadas == ["2026-10-07", "2026-10-06"]
