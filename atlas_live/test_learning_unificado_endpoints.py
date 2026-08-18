"""Tests de los endpoints públicos del aprendizaje unificado (2026-08-18,
pedido explícito del usuario): GET /api/radar-explosion-bands (Marcador
Histórico Tradier) y GET /api/candidate-full-history (historia completa de
una candidata, caso real XOS). Mismo patrón que los demás endpoints
públicos: sin red, sin tocar la base real (las funciones de
`candidate_registry` se mockean)."""

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

from atlas_live.radar import candidate_registry as reg  # noqa: E402


def _client():
    return server.app.test_client()


def test_explosion_bands_endpoint_devuelve_lo_que_calcula_el_registro():
    orig = reg.explosion_bands_tradier
    reg.explosion_bands_tradier = lambda market_date=None: {
        "market_date": market_date,
        "n_total_evaluado": 2,
        "por_banda_acumulativa": {
            "10": {"n": 2, "mediana_max_pct": 60.0, "max_absoluto_pct": 136.8, "tickers": ["XOS", "CDTG"]},
            "100": {"n": 1, "mediana_max_pct": 136.8, "max_absoluto_pct": 136.8, "tickers": ["XOS"]},
            "200": {"n": 0, "estado": "No disponible"},
        },
    }
    try:
        r = _client().get("/api/radar-explosion-bands")
        assert r.status_code == 200
        body = r.get_json()
        assert body["n_total_evaluado"] == 2
        assert "XOS" in body["por_banda_acumulativa"]["100"]["tickers"]
        assert body["por_banda_acumulativa"]["200"]["estado"] == "No disponible"
    finally:
        reg.explosion_bands_tradier = orig


def test_explosion_bands_endpoint_pasa_el_parametro_date():
    orig = reg.explosion_bands_tradier
    llamadas = []

    def _fake(market_date=None):
        llamadas.append(market_date)
        return {"market_date": market_date, "n_total_evaluado": 0, "por_banda_acumulativa": {}}

    reg.explosion_bands_tradier = _fake
    try:
        r = _client().get("/api/radar-explosion-bands?date=2026-08-18")
        assert r.status_code == 200
        assert llamadas == ["2026-08-18"]
        assert r.get_json()["market_date"] == "2026-08-18"
    finally:
        reg.explosion_bands_tradier = orig


def test_candidate_full_history_endpoint_requiere_ticker():
    r = _client().get("/api/candidate-full-history")
    assert r.status_code == 400


def test_candidate_full_history_endpoint_404_sin_deteccion():
    orig = reg.candidate_full_history
    reg.candidate_full_history = lambda ticker, market_date: None
    try:
        r = _client().get("/api/candidate-full-history?ticker=NOPE&date=2026-08-18")
        assert r.status_code == 404
        body = r.get_json()
        assert body["ticker"] == "NOPE"
        assert body["market_date"] == "2026-08-18"
    finally:
        reg.candidate_full_history = orig


def test_candidate_full_history_endpoint_caso_xos():
    """Prueba obligatoria XOS (2026-08-18, pedido explícito del usuario) --
    el endpoint expone exactamente lo que devuelve `candidate_full_history`,
    incluida la separación entre estado inicial y evolución posterior."""
    orig = reg.candidate_full_history
    llamadas = []

    def _fake(ticker, market_date):
        llamadas.append((ticker, market_date))
        return {
            "ticker": "XOS", "market_date": "2026-08-18", "racional_available": True,
            "estado_inicial": {
                "detected_at": "2026-08-18T04:53:38Z", "price_at_detection": 2.09,
                "relative_volume_at_detection": 1.74, "dollar_volume_at_detection": 10_040_749,
                "direction_at_detection": "ALCISTA",
            },
            "evolucion": {"etapas": [{"stage": "NO_PERSEGUIR"}], "max_price_visto_en_vivo": 4.95,
                          "max_pct_visto_en_vivo": 136.8},
            "resultado_final": {
                "max_price_after_detection": 4.95, "max_return_after_detection_pct": 136.8,
                "minutes_to_max": 364.0, "reached_100": 1, "confiable_para_aprendizaje": 1,
            },
        }

    reg.candidate_full_history = _fake
    try:
        r = _client().get("/api/candidate-full-history?ticker=xos&date=2026-08-18")
        assert r.status_code == 200
        assert llamadas == [("XOS", "2026-08-18")]  # normaliza a mayúsculas
        body = r.get_json()
        assert body["estado_inicial"]["price_at_detection"] == 2.09
        assert body["evolucion"]["etapas"][0]["stage"] == "NO_PERSEGUIR"
        assert body["resultado_final"]["reached_100"] == 1
        assert body["resultado_final"]["max_return_after_detection_pct"] == 136.8
    finally:
        reg.candidate_full_history = orig
