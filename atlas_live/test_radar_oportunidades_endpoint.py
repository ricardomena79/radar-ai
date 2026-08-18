"""Tests del endpoint público GET /api/radar-oportunidades (Fase 6,
2026-08-18) -- Prioridades 1/2/3/4: cada candidata de Tradier debe seguir
apareciendo acá, con el precio EN VIVO tomado del último barrido de
Tradier (radar_worker.get_last_quotes()), sin ninguna llamada a
Yahoo/Finnhub. Mismo patrón que los demás endpoints públicos: sin red,
sin tocar la base real (live_opportunities se mockea).

Filtro de disponibilidad Racional (2026-08-18, caso real BATL): desde acá
la RESPUESTA del endpoint (no `live_opportunities()`, que sigue devolviendo
todo sin filtrar) excluye candidatas con `racional_available != True`. Los
mocks de abajo agregan `"racional_available": True` explícito donde no es
el foco del test, para no ensombrecer lo que cada test realmente prueba."""

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


def _fresh_quote(last_price, change_percent, previous_close=None, age_seconds=5):
    """Quote-like fake, COHERENTE y FRESCO por defecto (2026-08-18, cierre
    de confiabilidad) -- para tests que no están probando la cadena de
    validación en sí, solo necesitan un precio "de Tradier" normal."""
    from datetime import datetime, timedelta, timezone

    if previous_close is None:
        previous_close = last_price / (1 + change_percent / 100)

    class _Q:
        pass

    q = _Q()
    q.last_price = last_price
    q.change_percent = change_percent
    q.previous_close = previous_close
    q.timestamp = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return q


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
        {"ticker": "ZIM", "price_at_detection": 28.14, "stage": "ALERTA_TEMPRANA", "racional_available": True},
        {"ticker": "SIN_QUOTE", "price_at_detection": 1.0, "stage": reg.DETECCION_TEMPRANA, "racional_available": True},
    ]

    _rw.get_last_quotes = lambda: {"ZIM": _fresh_quote(28.81, 2.38)}
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
        assert body["total_detectadas_hoy"] == 2
        assert body["total_disponibles_racional"] == 2
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes


def test_cruza_sector_y_flujo_de_dinero_desde_el_snapshot_de_scan_worker():
    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes
    orig_snapshot = _sw.STATE.sector_flow_snapshot

    reg.live_opportunities = lambda market_date: [
        {"ticker": "XOM", "price_at_detection": 110.0, "stage": "INICIO", "direction": "ALCISTA", "racional_available": True},
        {"ticker": "AAPL", "price_at_detection": 200.0, "stage": "PREPARACION", "racional_available": True},
    ]

    _rw.get_last_quotes = lambda: {"XOM": _fresh_quote(111.0, 0.9), "AAPL": _fresh_quote(111.0, 0.9)}
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
        {"ticker": "ZIM", "price_at_detection": 28.14, "stage": "ALERTA_TEMPRANA",
         "detected_at": detected_at, "racional_available": True},
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
        {"ticker": "ZIM", "price_at_detection": 28.14, "stage": "ALERTA_TEMPRANA", "racional_available": True},
    ]
    _rw.get_last_quotes = lambda: {}
    try:
        r = _client().get("/api/radar-oportunidades")
        assert r.status_code == 200
        assert r.get_json()["oportunidades"][0]["minutos_desde_deteccion"] is None
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes


def test_ticker_no_disponible_en_racional_no_aparece_en_la_lista_operable():
    """Caso real BATL (2026-08-18): Tradier detecta con señal fuerte, pero
    el ticker no está en el snapshot de Racional -- no debe aparecer en la
    respuesta del endpoint, aunque `live_opportunities()` (la detección
    interna, para aprendizaje) sí lo devuelva."""
    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes

    reg.live_opportunities = lambda market_date: [
        {"ticker": "BATL", "price_at_detection": 5.0, "stage": "INICIO",
         "direction": "ALCISTA", "racional_available": False},
        {"ticker": "AAPL", "price_at_detection": 200.0, "stage": "PREPARACION", "racional_available": True},
    ]
    _rw.get_last_quotes = lambda: {}
    try:
        r = _client().get("/api/radar-oportunidades")
        assert r.status_code == 200
        body = r.get_json()
        tickers = [o["ticker"] for o in body["oportunidades"]]
        assert "BATL" not in tickers
        assert "AAPL" in tickers
        assert body["total_detectadas_hoy"] == 2
        assert body["total_disponibles_racional"] == 1
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes


def test_racional_available_none_tambien_queda_fuera():
    """`racional_available` puede ser `None` (is_available() falló o
    Racional no cargó) -- nunca se trata como "disponible por defecto"."""
    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes

    reg.live_opportunities = lambda market_date: [
        {"ticker": "XYZ", "price_at_detection": 1.0, "stage": "PREPARACION", "racional_available": None},
    ]
    _rw.get_last_quotes = lambda: {}
    try:
        r = _client().get("/api/radar-oportunidades")
        body = r.get_json()
        assert body["oportunidades"] == []
        assert body["total_detectadas_hoy"] == 1
        assert body["total_disponibles_racional"] == 0
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes


# --- Cierre de la cadena de confiabilidad (2026-08-18, caso real SBLK/BATL) ---
# Casos A/C/F del pedido del usuario, sobre el pipeline completo del
# endpoint (no solo priority_classifier -- acá se prueba también el
# cálculo de price_actual_as_of/price_age_seconds/estado_validacion).

def test_caso_a_quote_fresco_puede_seguir_siendo_oportunidad_prioritaria():
    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes

    reg.live_opportunities = lambda market_date: [
        {"ticker": "SBLK", "price_at_detection": 29.0, "stage": "INICIO",
         "direction": "ALCISTA", "racional_available": True},
    ]
    _rw.get_last_quotes = lambda: {"SBLK": _fresh_quote(30.02, 3.3, age_seconds=10)}
    try:
        r = _client().get("/api/radar-oportunidades")
        body = r.get_json()
        o = body["oportunidades"][0]
        assert o["estado_validacion"] == "OK"
        assert o["estado_final"] == "OPORTUNIDAD_PRIORITARIA"
        assert o["price_age_seconds"] is not None and o["price_age_seconds"] < 60
        assert o["price_actual_as_of"] is not None
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes


def test_caso_b_y_c_quote_viejo_o_last_quotes_congelado_fuerza_no_recomendar():
    """B + C: mismo mecanismo cubre ambos -- un quote de Tradier con
    timestamp real pero viejo (porque _last_quotes quedó congelado tras
    barridos fallidos, radar_worker.py::run_sweep_once() no lo limpia en
    su except) se detecta por antigüedad al SERVIR el request, sin
    importar que el diccionario en memoria siga "teniendo" el símbolo."""
    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes

    reg.live_opportunities = lambda market_date: [
        {"ticker": "SBLK", "price_at_detection": 29.0, "stage": "INICIO",
         "direction": "ALCISTA", "racional_available": True},
    ]
    # 48 minutos de antigüedad -- el mismo caso real SBLK.
    _rw.get_last_quotes = lambda: {"SBLK": _fresh_quote(30.02, 3.3, age_seconds=48 * 60)}
    try:
        r = _client().get("/api/radar-oportunidades")
        body = r.get_json()
        o = body["oportunidades"][0]
        assert o["estado_validacion"] == "VENCIDO"
        assert o["estado_final"] == "NO_TOCAR"
        assert "DATOS NO CONFIABLES" in o["motivo_estado_final"]
        assert o["price_age_seconds"] > 2000
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes


def test_caso_d_quote_sin_timestamp_fuerza_no_recomendar():
    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes

    class _SinTimestamp:
        last_price = 30.02
        change_percent = 3.3
        previous_close = 29.06
        timestamp = None

    reg.live_opportunities = lambda market_date: [
        {"ticker": "SBLK", "price_at_detection": 29.0, "stage": "ALERTA_TEMPRANA", "racional_available": True},
    ]
    _rw.get_last_quotes = lambda: {"SBLK": _SinTimestamp()}
    try:
        r = _client().get("/api/radar-oportunidades")
        body = r.get_json()
        o = body["oportunidades"][0]
        assert o["estado_validacion"] == "SIN_TIMESTAMP"
        assert o["estado_final"] == "NO_TOCAR"
        assert o["price_actual_as_of"] is None
        assert o["price_age_seconds"] is None
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes


def test_caso_e_cambio_pct_incoherente_fuerza_no_recomendar():
    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes

    reg.live_opportunities = lambda market_date: [
        {"ticker": "SBLK", "price_at_detection": 29.0, "stage": "PREPARACION", "racional_available": True},
    ]
    # last_price/previous_close implican ~-8.7%, pero el quote trae +3.3%.
    _rw.get_last_quotes = lambda: {"SBLK": _fresh_quote(30.02, 3.3, previous_close=32.9, age_seconds=5)}
    try:
        r = _client().get("/api/radar-oportunidades")
        body = r.get_json()
        o = body["oportunidades"][0]
        assert o["estado_validacion"] == "CAMBIO_PCT_INCOHERENTE"
        assert o["estado_final"] == "NO_TOCAR"
        assert "incoherente" in o["motivo_estado_final"]
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes


def test_caso_f_dato_de_sesion_anterior_fuera_de_la_ventana_de_frescura():
    """F: no existe un campo de "sesión" separado en el Quote de Tradier
    (a diferencia de Yahoo) -- el mecanismo real que cubre "dato de la
    sesión anterior" es el mismo chequeo de antigüedad: un precio de la
    sesión regular de ayer, visto hoy en premarket, ya tiene horas de
    antigüedad y queda VENCIDO igual que el caso B/C."""
    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes

    reg.live_opportunities = lambda market_date: [
        {"ticker": "SBLK", "price_at_detection": 29.0, "stage": "ALERTA_FUERTE", "racional_available": True},
    ]
    # Precio "de ayer" -- 18 horas de antigüedad.
    _rw.get_last_quotes = lambda: {"SBLK": _fresh_quote(29.5, 1.0, age_seconds=18 * 3600)}
    try:
        r = _client().get("/api/radar-oportunidades")
        body = r.get_json()
        o = body["oportunidades"][0]
        assert o["estado_validacion"] == "VENCIDO"
        assert o["estado_final"] == "NO_TOCAR"
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes


def test_caso_g_oportunidad_prioritaria_nunca_tiene_estado_validacion_vencido():
    """G: verificación end-to-end -- una señal que normalmente daría
    OPORTUNIDAD_PRIORITARIA (INICIO + ALCISTA) nunca llega a serlo si el
    precio está vencido."""
    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes

    reg.live_opportunities = lambda market_date: [
        {"ticker": "SBLK", "price_at_detection": 29.0, "stage": "INICIO",
         "direction": "ALCISTA", "racional_available": True},
    ]
    _rw.get_last_quotes = lambda: {"SBLK": _fresh_quote(30.02, 3.3, age_seconds=48 * 60)}
    try:
        r = _client().get("/api/radar-oportunidades")
        body = r.get_json()
        o = body["oportunidades"][0]
        assert o["estado_final"] != "OPORTUNIDAD_PRIORITARIA"
        assert o["estado_final"] == "NO_TOCAR"
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes
