"""HITO 2 (2026-09-14, PLAN Radar/Finnhub, autorizado explícitamente):
tests del endpoint PÚBLICO `/api/finnhub-radar-resumen` -- sin token,
mismo molde sin red/sin hilos de fondo que
`test_learning_safety_summary_endpoint.py`."""

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

from atlas_live.core import finnhub_radar_summary as frs  # noqa: E402


def _client():
    return server.app.test_client()


def test_responde_200_sin_token():
    r = _client().get("/api/finnhub-radar-resumen")
    assert r.status_code == 200


def test_estructura_esperada_y_delega_en_build_finnhub_radar_summary(monkeypatch):
    sintetico = {
        "generated_at": "2026-09-14T00:00:00+00:00",
        "finnhub_budget": {"ok": True, "por_consumidor": {}, "pisos_protegidos_por_minuto": {}, "limite_total_seguro_por_minuto": 55, "fail_safe_events": 0},
        "ultimo_sweep_radar": {
            "ok": True, "disponible": True, "ultimo_sweep_at": "2026-09-14T10:00:00Z",
            "ultimo_sweep_duracion_s": 12.5, "sweeps_total": 100, "sweeps_ok": 99, "sweeps_error": 1,
            "ultimo_tradier_error": None, "diagnostics": {"tradier_chunks_ok": 3, "tradier_chunks_error": 0, "tradier_chunk_errors": []},
        },
    }
    monkeypatch.setattr(frs, "build_finnhub_radar_summary", lambda: sintetico)
    r = _client().get("/api/finnhub-radar-resumen")
    assert r.status_code == 200
    assert r.get_json() == sintetico


def test_contra_el_estado_real_nunca_expone_tokens_o_keys():
    r = _client().get("/api/finnhub-radar-resumen")
    assert r.status_code == 200
    body = r.get_json()
    import json

    texto = json.dumps(body)
    assert "TRADIER_API_TOKEN" not in texto
    assert "FINNHUB_API_KEY" not in texto
    assert "finnhub_budget" in body
    assert "ultimo_sweep_radar" in body
