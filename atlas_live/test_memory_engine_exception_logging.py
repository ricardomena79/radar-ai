"""Test de la instrumentación temporal de diagnóstico (2026-09-01,
autorizado explícitamente) -- confirma, contra el servidor REAL (Flask
test client, ruta real /api/memory-engine, mismo patrón que
test_admin_mercado_cycle_now.py: sin red, sin arrancar hilos de fondo
reales al importar server), que una excepción controlada:

  1) sigue devolviendo HTTP 500 al cliente (comportamiento SIN cambios);
  2) queda registrada en stderr con el marcador MEMORY_ENGINE_EXCEPTION
     y el traceback completo;
  3) el traceback NUNCA llega al cliente (el body sigue siendo la página
     genérica de Flask, nunca el texto de la excepción);
  4) no aparece nada sensible en el log (headers/body de la request)."""

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

from atlas_live.memory import live_integration  # noqa: E402


def _client():
    return server.app.test_client()


def test_excepcion_controlada_en_memory_engine_sigue_dando_500_y_queda_registrada(monkeypatch, capfd):
    def _boom(now=None):
        raise RuntimeError("fallo controlado de prueba -- nunca debe llegar al cliente")

    monkeypatch.setattr(live_integration, "get_memory_engine_summary", _boom)

    resp = _client().get("/api/memory-engine")

    # 1) comportamiento HTTP sin cambios -- sigue siendo 500 genérico
    assert resp.status_code == 500
    body = resp.get_data(as_text=True)
    assert "fallo controlado de prueba" not in body  # 3) el traceback NUNCA llega al cliente
    assert "RuntimeError" not in body
    assert "Traceback" not in body

    # 2) queda registrado en stderr con el marcador y el traceback completo
    captured = capfd.readouterr()
    assert "MEMORY_ENGINE_EXCEPTION" in captured.err
    assert "RuntimeError" in captured.err
    assert "fallo controlado de prueba" in captured.err
    assert "Traceback (most recent call last)" in captured.err
    assert "/api/memory-engine" in captured.err

    # 4) nada sensible -- nunca se registran headers/body de la request
    assert "Authorization" not in captured.err
    assert "Cookie" not in captured.err


def test_excepcion_en_otro_endpoint_usa_marcador_generico_no_memory_engine(monkeypatch, capfd):
    """Confirma que el marcador es específico -- una excepción en OTRO
    endpoint no se confunde con MEMORY_ENGINE_EXCEPTION."""
    from atlas_live.learning import live_summary

    def _boom(market_date=None):
        raise RuntimeError("fallo controlado en otro endpoint")

    monkeypatch.setattr(live_summary, "get_live_learning_summary", _boom)

    resp = _client().get("/api/learning-maturity")
    assert resp.status_code == 500

    captured = capfd.readouterr()
    assert "ATLAS_UNHANDLED_EXCEPTION" in captured.err
    assert "MEMORY_ENGINE_EXCEPTION" not in captured.err
    assert "/api/learning-maturity" in captured.err


def test_camino_normal_sin_excepcion_no_registra_nada(capfd):
    """Sin excepción, la señal nunca se dispara -- cero ruido nuevo en
    stderr para requests exitosos."""
    resp = _client().get("/api/memory-engine")
    assert resp.status_code == 200

    captured = capfd.readouterr()
    assert "MEMORY_ENGINE_EXCEPTION" not in captured.err
    assert "ATLAS_UNHANDLED_EXCEPTION" not in captured.err
