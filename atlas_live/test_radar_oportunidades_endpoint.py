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
    # Trazabilidad del precio de premarket (2026-08-18) -- ver Quote.price_basis.
    # Tests que no prueban esta cadena en sí solo necesitan que el atributo exista.
    q.price_basis = "tradier_last"
    q.bid = None
    q.ask = None
    q.bid_timestamp = None
    q.ask_timestamp = None
    return q


def _quote_con_basis(last_price, change_percent, price_basis, relative_volume=None, age_seconds=5):
    """Como `_fresh_quote`, pero permite fijar `price_basis`/`relative_volume`
    -- necesario para los tests del recálculo de dirección en vivo."""
    q = _fresh_quote(last_price, change_percent, age_seconds=age_seconds)
    q.price_basis = price_basis
    q.relative_volume = relative_volume
    return q


def test_direccion_se_recalcula_en_vivo_no_queda_congelada_en_la_etapa(monkeypatch):
    """2026-08-20, caso real COHR: la etapa (y con ella `direction`,
    guardada en `alert_stage_log`) no se vuelve a escribir mientras la
    candidata sigue en la misma etapa -- podía quedar "Comprado" congelado
    de hace 30 min mientras el precio ya se daba vuelta. `direction` debe
    reflejar SIEMPRE el `change_pct_actual` de este mismo request."""
    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes

    reg.live_opportunities = lambda market_date: [
        {"ticker": "COHR", "price_at_detection": 293.5, "stage": "ALERTA_TEMPRANA",
         "direction": "ALCISTA", "change_pct_confiable": True,  # valor viejo, de hace 30 min
         "racional_available": True},
    ]
    # ahora mismo el precio real ya está apenas negativo, con price_basis normal (tradier_last)
    _rw.get_last_quotes = lambda: {"COHR": _quote_con_basis(287.25, -0.08, "tradier_last", relative_volume=0.5)}
    try:
        r = _client().get("/api/radar-oportunidades")
        assert r.status_code == 200
        body = r.get_json()
        cohr = body["oportunidades"][0]
        assert cohr["direction"] != "ALCISTA"  # ya no puede seguir diciendo "Comprado"
        assert cohr["direction"] in ("BAJISTA", "NEUTRAL")
        assert cohr["change_pct_confiable"] is True  # RVOL 0.5 y precio real -- sigue siendo un dato confiable
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes


def test_direccion_indefinida_cuando_precio_sintetico_sin_volumen_real(monkeypatch):
    """Mismo caso KEN de anoche, ahora también recalculado en vivo en cada
    request (no solo al momento de la detección): precio del punto medio
    bid/ask + RVOL casi nulo -> INDEFINIDA, sin importar qué decía la
    etapa guardada."""
    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes

    reg.live_opportunities = lambda market_date: [
        {"ticker": "KEN", "price_at_detection": 65.0, "stage": "INICIO",
         "direction": "ALCISTA", "change_pct_confiable": True,
         "racional_available": True},
    ]
    _rw.get_last_quotes = lambda: {"KEN": _quote_con_basis(65.09, 1.6, "tradier_bid_ask_mid", relative_volume=0.0071)}
    try:
        r = _client().get("/api/radar-oportunidades")
        body = r.get_json()
        ken = body["oportunidades"][0]
        assert ken["direction"] == "INDEFINIDA"
        assert ken["change_pct_confiable"] is False
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes


def test_evidencia_historica_no_exige_daily_range_pct_para_calcularse():
    """Bug real encontrado en vivo (2026-08-20/21, caso COHR/CLSK/MRNA):
    la condición vieja exigía `daily_range_pct_at_detection` no-None ADEMÁS
    de `volatility_14d_pct_at_detection` -- pero ese campo solo se llena si
    Tradier ya tenía high/low en el instante EXACTO de la detección, algo
    que las candidatas más frescas (justo las accionables) sistemáticamente
    no tenían todavía. `historical_scoring.score_candidate()` ya degrada
    con gracia a bucket "poblacion_total" cuando falta una sola feature --
    alcanza con `direction`/`timing_deteccion_hoy` para calcular evidencia."""
    from atlas_live.learning import historical_scoring as hsc

    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes
    orig_table = hsc.get_cached_reference_table

    # Mismas 2 features que usa `hsc.REPORT_FEATURE_COLS` en producción --
    # para reproducir el caso real hay que exigir AMBAS en la tabla, así
    # `daily_range_pct=None` (el caso real de MRNA) cae en el chequeo
    # "falta una feature" de `_bucket_of_row` y degrada a poblacion_total.
    rows = [
        {"direction": "ALCISTA", "timing_deteccion": "al_comienzo",
         "volatility_14d_pct": float(v), "daily_range_pct": float(v), "max_advance_pct": 30.0}
        for v in range(1, 35)
    ]
    tabla = hsc.compute_reference_table(rows, list(hsc.REPORT_FEATURE_COLS), min_rows=30)

    reg.live_opportunities = lambda market_date: [
        {"ticker": "MRNA", "price_at_detection": 65.0, "stage": "INICIO",
         "direction": "ALCISTA", "change_pct_confiable": True, "racional_available": True,
         "timing_deteccion_hoy": "al_comienzo", "volatility_14d_pct_at_detection": 6.279,
         "daily_range_pct_at_detection": None},  # justo el caso real -- todavía sin high/low
    ]
    _rw.get_last_quotes = lambda: {"MRNA": _fresh_quote(68.0, 4.2)}
    hsc.get_cached_reference_table = lambda: tabla
    try:
        r = _client().get("/api/radar-oportunidades")
        body = r.get_json()
        mrna = body["oportunidades"][0]
        assert mrna["evidencia_historica"] is not None
        assert mrna["evidencia_historica"]["grupo_existe"] is True
        assert mrna["evidencia_historica"]["bucket"] == "poblacion_total"
        assert mrna["evidencia_historica"]["mediana_max_advance_pct"] == 30.0
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes
        hsc.get_cached_reference_table = orig_table


def test_direccion_no_se_toca_si_no_hay_quote_fresco():
    """Sin quote en el último barrido (ticker no visto en este ciclo), no
    hay nada más fresco que recalcular -- se preserva la última dirección
    conocida tal cual vino de `live_opportunities()`, igual que ya pasa
    con `price_actual`."""
    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes

    reg.live_opportunities = lambda market_date: [
        {"ticker": "SIN_QUOTE", "price_at_detection": 10.0, "stage": "ALERTA_TEMPRANA",
         "direction": "ALCISTA", "change_pct_confiable": True, "racional_available": True},
    ]
    _rw.get_last_quotes = lambda: {}
    try:
        r = _client().get("/api/radar-oportunidades")
        body = r.get_json()
        o = body["oportunidades"][0]
        assert o["direction"] == "ALCISTA"
        assert o["change_pct_confiable"] is True
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes


def test_no_llama_a_ningun_proveedor_yahoo_finnhub():
    """Prioridad 2: la función del endpoint nunca debe LLAMAR a
    Yahoo/Finnhub -- server.py sí importa `get_default_provider` para
    otros endpoints ya existentes, pero esta función en particular no
    debe invocarlo (se verifica el código real, no la prosa del
    docstring, que sí menciona Yahoo/Finnhub para explicar por qué no se
    usan)."""
    import inspect

    # 2026-09-03: la lógica real vive en `_api_radar_oportunidades_impl()`
    # desde el guard de reentrancia -- `api_radar_oportunidades` es ahora
    # solo el wrapper del lock, revisarlo acá no probaría nada.
    src = inspect.getsource(server._api_radar_oportunidades_impl)
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

    # change_percent=2.5 (2026-08-20): antes 0.9 -- alcanzaba porque
    # `direction` venía fijo en el mock de live_opportunities(). Ahora se
    # recalcula en vivo desde el quote (ver fix "dirección en vivo"), y
    # 0.9% cae bajo el piso neutral (NEUTRAL_BAND_PCT=1.0) -- se sube el
    # cambio real del quote para seguir probando un caso ALCISTA genuino.
    _rw.get_last_quotes = lambda: {"XOM": _fresh_quote(112.75, 2.5), "AAPL": _fresh_quote(112.75, 2.5)}
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


# --- PM-RVOL Fase 1 (2026-08-25) -- capa de observabilidad, integración ---

def test_pm_rvol_expone_las_4_senales_sin_tocar_estado_final(monkeypatch):
    """Las señales PM deben aparecer en la respuesta sin cambiar en nada
    `estado_final`/`price_actual`/`relative_volume` de siempre -- son
    puramente informativas."""
    import atlas_live.radar.candidate_gates as _gates

    from atlas_live.memory import market_hours as mh

    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes
    orig_sweep_hist = _rw.get_symbol_sweep_history
    orig_session_fn = mh.get_session

    reg.live_opportunities = lambda market_date: [
        {"ticker": "NSSC", "price_at_detection": 39.0, "stage": "INICIO",
         "direction": "ALCISTA", "racional_available": True},
    ]
    q = _quote_con_basis(39.0, 2.39, "tradier_bid_only", relative_volume=0.0009)
    q.volume = 458
    _rw.get_last_quotes = lambda: {"NSSC": q}
    _rw.get_symbol_sweep_history = lambda symbol: []  # sin historial -> INSUFFICIENT_HISTORY
    mh.get_session = lambda: "premarket"
    try:
        r = _client().get("/api/radar-oportunidades")
        assert r.status_code == 200
        o = r.get_json()["oportunidades"][0]
        assert o["premarket_volume_percentile_state"] == "INSUFFICIENT_UNIVERSE"  # universo de 1 símbolo
        assert o["premarket_volume_percentile"] is None
        assert o["premarket_volume_acceleration_state"] == "INSUFFICIENT_HISTORY"
        assert o["premarket_volume_acceleration"] is None
        # Nada de lo existente cambió por agregar estos campos.
        assert o["price_actual"] == 39.0
        assert o["price_basis"] == "tradier_bid_only"
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes
        _rw.get_symbol_sweep_history = orig_sweep_hist
        mh.get_session = orig_session_fn


def test_pm_rvol_universo_amplio_da_percentil_valido(monkeypatch):
    from atlas_live.memory import market_hours as mh

    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes
    orig_sweep_hist = _rw.get_symbol_sweep_history
    orig_session_fn = mh.get_session

    reg.live_opportunities = lambda market_date: [
        {"ticker": "NSSC", "price_at_detection": 39.0, "stage": "INICIO", "racional_available": True},
    ]
    q_nssc = _quote_con_basis(39.0, 2.39, "tradier_bid_only")
    q_nssc.volume = 458
    quotes = {"NSSC": q_nssc}
    for i in range(150):  # universo amplio -> >= MIN_UNIVERSE_SIZE_FOR_PM_PERCENTILE
        qx = _fresh_quote(10.0, 0.0)
        qx.volume = 100
        quotes[f"SYM{i}"] = qx
    _rw.get_last_quotes = lambda: quotes
    _rw.get_symbol_sweep_history = lambda symbol: []
    mh.get_session = lambda: "premarket"
    try:
        r = _client().get("/api/radar-oportunidades")
        o = r.get_json()["oportunidades"][0]
        assert o["premarket_volume_percentile_state"] == "VALID"
        assert o["premarket_volume_percentile"] is not None
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes
        _rw.get_symbol_sweep_history = orig_sweep_hist
        mh.get_session = orig_session_fn


def test_pm_rvol_fuera_de_premarket_da_not_premarket(monkeypatch):
    from atlas_live.memory import market_hours as mh

    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes
    orig_sweep_hist = _rw.get_symbol_sweep_history
    orig_session_fn = mh.get_session

    reg.live_opportunities = lambda market_date: [
        {"ticker": "ZIM", "price_at_detection": 28.14, "stage": "ALERTA_TEMPRANA", "racional_available": True},
    ]
    _rw.get_last_quotes = lambda: {"ZIM": _fresh_quote(28.81, 2.38)}
    _rw.get_symbol_sweep_history = lambda symbol: []
    mh.get_session = lambda: "regular"
    try:
        r = _client().get("/api/radar-oportunidades")
        o = r.get_json()["oportunidades"][0]
        assert o["premarket_volume_percentile_state"] == "NOT_PREMARKET"
        assert o["premarket_volume_acceleration_state"] == "NOT_PREMARKET"
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes
        _rw.get_symbol_sweep_history = orig_sweep_hist
        mh.get_session = orig_session_fn


def test_pm_rvol_no_altera_relative_volume_actual_ni_estado_final(monkeypatch):
    """Regresión directa del pedido: agregar PM-RVOL no debe cambiar
    `estado_final`/`direction`/nada de lo que el endpoint ya calculaba."""
    from atlas_live.memory import market_hours as mh

    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes
    orig_sweep_hist = _rw.get_symbol_sweep_history
    orig_session_fn = mh.get_session

    reg.live_opportunities = lambda market_date: [
        {"ticker": "SBLK", "price_at_detection": 29.0, "stage": "INICIO",
         "direction": "ALCISTA", "racional_available": True},
    ]
    _rw.get_last_quotes = lambda: {"SBLK": _fresh_quote(30.02, 3.3, age_seconds=10)}
    _rw.get_symbol_sweep_history = lambda symbol: []
    mh.get_session = lambda: "premarket"
    try:
        r = _client().get("/api/radar-oportunidades")
        o = r.get_json()["oportunidades"][0]
        # Mismo resultado que test_caso_a_quote_fresco_puede_seguir_siendo_oportunidad_prioritaria,
        # ahora con PM-RVOL también presente en la respuesta.
        assert o["estado_validacion"] == "OK"
        assert o["estado_final"] == "OPORTUNIDAD_PRIORITARIA"
        assert "premarket_volume_percentile" in o
        assert "premarket_volume_acceleration" in o
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes
        _rw.get_symbol_sweep_history = orig_sweep_hist
        mh.get_session = orig_session_fn


# --- Fase 4/5 (2026-08-25) -- learned_evidence, caso L del pedido ----------

def test_L_endpoint_agrega_learned_evidence_sin_alterar_lo_existente(monkeypatch):
    """El endpoint debe seguir devolviendo EXACTAMENTE lo mismo de siempre
    (estado_final/price_actual/etc.) más el bloque `learned_evidence`
    nuevo -- nunca reemplaza ni cambia un campo ya existente."""
    from atlas_live.learning import learned_evidence as le

    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes
    orig_get_learned_evidence = le.get_learned_evidence

    reg.live_opportunities = lambda market_date: [
        {"ticker": "SBLK", "price_at_detection": 29.0, "stage": "INICIO",
         "direction": "ALCISTA", "timing_deteccion_hoy": "al_comienzo",
         "racional_available": True},
    ]
    _rw.get_last_quotes = lambda: {"SBLK": _fresh_quote(30.02, 3.3, age_seconds=10)}
    le.get_learned_evidence = lambda direction, timing, market_date, **k: {
        "available": True, "validation_state": "EN_VALIDACION", "sample_size": 137,
        "historical_success_pct_20": 41.2, "baseline_pct_20": 23.1, "lift_20": 1.78,
        "wilson_lower_bound_20_pct": 33.3, "wilson_upper_bound_20_pct": 49.8,
        "computed_as_of": "2026-08-24", "computed_at": "2026-08-24T20:00:00+00:00",
        "methodology_version": "v1_direction_timing_volatility_tercile",
    }
    try:
        r = _client().get("/api/radar-oportunidades")
        assert r.status_code == 200
        o = r.get_json()["oportunidades"][0]
        # Todo lo existente sigue exactamente igual (mismo resultado que
        # test_caso_a_quote_fresco_puede_seguir_siendo_oportunidad_prioritaria).
        assert o["estado_validacion"] == "OK"
        assert o["estado_final"] == "OPORTUNIDAD_PRIORITARIA"
        assert o["price_actual"] == 30.02
        # Más el bloque nuevo, sin tocar nada de lo anterior.
        assert o["learned_evidence"]["available"] is True
        assert o["learned_evidence"]["validation_state"] == "EN_VALIDACION"
        assert o["learned_evidence"]["sample_size"] == 137
    finally:
        reg.live_opportunities = orig_live_opps
        _rw.get_last_quotes = orig_last_quotes
        le.get_learned_evidence = orig_get_learned_evidence


def test_L_learned_evidence_no_disponible_no_rompe_el_endpoint(monkeypatch):
    orig_live_opps = reg.live_opportunities
    orig_last_quotes = _rw.get_last_quotes

    reg.live_opportunities = lambda market_date: [
        {"ticker": "ZIM", "price_at_detection": 28.14, "stage": "ALERTA_TEMPRANA", "racional_available": True},
    ]
    _rw.get_last_quotes = lambda: {"ZIM": _fresh_quote(28.81, 2.38)}
    try:
        r = _client().get("/api/radar-oportunidades")
        assert r.status_code == 200
        o = r.get_json()["oportunidades"][0]
        # Sin direction/timing confiables -- CONDICION_NO_DISPONIBLE, nunca rompe.
        assert o["learned_evidence"]["available"] is False
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
