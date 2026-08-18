"""Tests del endpoint público GET /api/radar-oportunidades (Fase 6,
2026-08-18) -- Prioridades 1/2/3/4: cada candidata de Tradier debe seguir
apareciendo acá, con el precio EN VIVO tomado del último barrido de
Tradier (radar_worker.get_last_quotes()), sin ninguna llamada a
Yahoo/Finnhub. Mismo patrón que los demás endpoints públicos: sin red,
sin tocar la base real (live_opportunities se mockea)."""

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

from atlas_live.radar import candidate_registry as reg  # noqa: E402


def _client():
    return server.app.test_client()


def test_no_llama_a_ningun_proveedor_yahoo_finnhub():
    """Prioridad 2: la función del endpoint nunca debe LLAMAR a
    Yahoo/Finnhub -- server.py sí importa `get_default_provider` para
    otros endpoints ya existentes, pero esta función en particular no
    debe invocarlo (se verifica el código real, no la prosa del
    docstring, que sí menciona Yahoo/Finnhub para explicar por qué no se
    usan)."""
    import inspect

    src = inspect.getsource(server.api_radar_oportunidades)
    assert "get_default_provider(" not in src
    assert "YahooFinance" not in src
    assert "FinnhubProvider" not in src


def test_devuelve_las_oportunidades_con_precio_en_vivo_mergeado():
    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes

    reg.live_opportunities = lambda market_date: [
        {"ticker": "ZIM", "price_at_detection": 28.14, "stage": "ALERTA_TEMPRANA"},
        {"ticker": "SIN_QUOTE", "price_at_detection": 1.0, "stage": reg.DETECCION_TEMPRANA},
    ]

    class _FakeQuote:
        last_price = 28.81
        change_percent = 2.38

    _rw.get_last_quotes = lambda: {"ZIM": _FakeQuote()}
    try:
        r = _client().get("/api/radar-oportunidades")
        assert r.status_code == 200
        body = r.get_json()
        by_ticker = {o["ticker"]: o for o in body["oportunidades"]}
        assert by_ticker["ZIM"]["price_actual"] == 28.81
        assert by_ticker["ZIM"]["price_actual_source"] == "tradier"
        # sin quote en el último barrido -- nunca se cae a otro proveedor, queda null
        assert by_ticker["SIN_QUOTE"]["price_actual"] is None
        assert by_ticker["SIN_QUOTE"]["price_actual_source"] is None
        assert body["conteos_por_etapa"]["ALERTA_TEMPRANA"] == 1
        assert body["conteos_por_etapa"][reg.DETECCION_TEMPRANA] == 1
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes
