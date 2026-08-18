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
        # ZIM tiene precio actual y etapa ALERTA_TEMPRANA -> VIGILAR.
        assert by_ticker["ZIM"]["estado_final"] == "VIGILAR"
        # SIN_QUOTE no tiene precio actual -> NO_TOCAR sin importar la etapa.
        assert by_ticker["SIN_QUOTE"]["estado_final"] == "NO_TOCAR"
        assert "DATOS NO CONFIABLES" in by_ticker["SIN_QUOTE"]["motivo_estado_final"]
        assert body["conteos_por_estado_final"]["VIGILAR"] == 1
        assert body["conteos_por_estado_final"]["NO_TOCAR"] == 1
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes


def test_cruza_sector_y_flujo_de_dinero_desde_el_snapshot_de_scan_worker():
    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes
    orig_snapshot = _sw.STATE.sector_flow_snapshot

    reg.live_opportunities = lambda market_date: [
        {"ticker": "XOM", "price_at_detection": 110.0, "stage": "INICIO", "direction": "ALCISTA"},
        {"ticker": "AAPL", "price_at_detection": 200.0, "stage": "PREPARACION"},
    ]

    class _FakeQuote:
        last_price = 111.0
        change_percent = 0.9

    _rw.get_last_quotes = lambda: {"XOM": _FakeQuote(), "AAPL": _FakeQuote()}
    _sw.STATE.sector_flow_snapshot = {
        "generated_at": "2026-08-18T09:00:00+00:00",
        "cobertura": "Racional (watchlist escaneado por Yahoo en este ciclo)",
        "sectores": [
            {"sector": "Energy", "money_flow_score": 70.0, "stock_count": 5,
             "avg_change_percent": 2.0, "avg_relative_volume": 3.0, "top_stocks": ["XOM"]},
        ],
        "symbol_sector_map": {"XOM": "Energy"},
    }
    try:
        r = _client().get("/api/radar-oportunidades")
        assert r.status_code == 200
        body = r.get_json()
        by_ticker = {o["ticker"]: o for o in body["oportunidades"]}
        assert by_ticker["XOM"]["sector"] == "Energy"
        assert by_ticker["XOM"]["dinero_entra_sector"] is True
        assert by_ticker["XOM"]["estado_final"] == "OPORTUNIDAD_PRIORITARIA"
        assert "flujo de dinero activo" in by_ticker["XOM"]["motivo_estado_final"]
        # AAPL no está en symbol_sector_map -- sector desconocido, nunca inventado.
        assert by_ticker["AAPL"]["sector"] is None
        assert by_ticker["AAPL"]["dinero_entra_sector"] is False
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes
        _sw.STATE.sector_flow_snapshot = orig_snapshot


def test_minutos_desde_deteccion_se_calcula_desde_detected_at():
    from datetime import datetime, timedelta, timezone

    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes

    detected_at = (datetime.now(timezone.utc) - timedelta(minutes=7)).isoformat()
    reg.live_opportunities = lambda market_date: [
        {"ticker": "ZIM", "price_at_detection": 28.14, "stage": "ALERTA_TEMPRANA", "detected_at": detected_at},
    ]
    _rw.get_last_quotes = lambda: {}
    try:
        r = _client().get("/api/radar-oportunidades")
        body = r.get_json()
        minutos = body["oportunidades"][0]["minutos_desde_deteccion"]
        assert minutos is not None and 6.5 <= minutos <= 7.5
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes


def test_sin_detected_at_no_rompe_el_endpoint():
    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes

    reg.live_opportunities = lambda market_date: [
        {"ticker": "ZIM", "price_at_detection": 28.14, "stage": "ALERTA_TEMPRANA"},
    ]
    _rw.get_last_quotes = lambda: {}
    try:
        r = _client().get("/api/radar-oportunidades")
        assert r.status_code == 200
        assert r.get_json()["oportunidades"][0]["minutos_desde_deteccion"] is None
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes
