"""Tests de `server._aplicar_histeresis_precio_a_estado_final()` (2026-09-23,
autorizado explícitamente -- fix del "parpadeo" del panel Oportunidades,
capa 2/3).

Mismo patrón conceptual que la histéresis ya aprobada y en producción de
`candidate_tracker._confirm_alert_stage_with_hysteresis()` (ver sus tests
en `atlas_live/radar/test_candidate_tracker.py`, sección "Hito 3"), pero
vive en `server.py` (el LLAMADOR real de `priority_classifier`/
`atlas_decision_core.decide()` para este endpoint, ver comentario extenso
en `server.py`) en vez de dentro de `priority_classifier.py` (módulo
protegido de esta sesión, se mantiene puro, sin memoria, sin cambios).

Tests puros de la función aislada -- sin red, sin DB, sin Flask test
client (eso lo cubre `test_radar_oportunidades_endpoint.py` de punta a
punta con el endpoint real)."""

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


def setup_function(_fn):
    server._reset_price_validation_hysteresis_state_for_tests()


def _aplicar(ticker, estado_crudo, motivo_crudo, es_problema_de_datos, sweep_marker, market_date="2026-09-23"):
    return server._aplicar_histeresis_precio_a_estado_final(
        ticker, market_date, estado_crudo, motivo_crudo, es_problema_de_datos, sweep_marker,
    )


def test_estado_bueno_se_confirma_de_inmediato():
    estado, motivo = _aplicar("XYZ", "OPORTUNIDAD_PRIORITARIA", "Etapa INICIO, dirección confirmada", False, "sweep-1")
    assert estado == "OPORTUNIDAD_PRIORITARIA"
    assert motivo == "Etapa INICIO, dirección confirmada"


def test_no_tocar_por_etapa_real_se_confirma_de_inmediato_sin_histeresis():
    """NO_TOCAR por NO_PERSEGUIR/FLUJO_VENDEDOR (no es un problema de
    datos) nunca se frena -- mismo comportamiento crudo de siempre."""
    estado, motivo = _aplicar("XYZ", "NO_TOCAR", "Etapa NO_PERSEGUIR", False, "sweep-1")
    assert estado == "NO_TOCAR"
    assert motivo == "Etapa NO_PERSEGUIR"


def test_no_tocar_por_datos_sin_bucket_accionable_previo_se_confirma_de_inmediato():
    """Primera vez que se ve esta candidata (o nunca estuvo en un bucket
    accionable) -- no hay "degradación" que frenar, por definición exige
    un estado bueno YA confirmado."""
    estado, motivo = _aplicar("XYZ", "NO_TOCAR", "Sin precio actual...", True, "sweep-1")
    assert estado == "NO_TOCAR"


def test_degradacion_se_frena_en_el_primer_sweep_con_problema_de_datos():
    _aplicar("XYZ", "OPORTUNIDAD_PRIORITARIA", "Etapa INICIO", False, "sweep-1")
    estado, motivo = _aplicar("XYZ", "NO_TOCAR", "Sin precio actual...", True, "sweep-2")
    assert estado == "OPORTUNIDAD_PRIORITARIA"  # se sostiene el último bucket bueno
    assert "REVALIDANDO" in motivo


def test_degradacion_se_confirma_tras_2_sweeps_consecutivos_de_problema_de_datos():
    _aplicar("XYZ", "VIGILAR", "Etapa ALERTA_TEMPRANA", False, "sweep-1")
    r1, _ = _aplicar("XYZ", "NO_TOCAR", "Precio vencido", True, "sweep-2")
    assert r1 == "VIGILAR"  # 1er sweep malo -- todavía frenado
    r2, motivo2 = _aplicar("XYZ", "NO_TOCAR", "Precio vencido", True, "sweep-3")
    assert r2 == "NO_TOCAR"  # 2do sweep consecutivo -- confirma
    assert motivo2 == "Precio vencido"


def test_mismo_sweep_marker_no_infla_el_contador_por_requests_http_repetidos():
    """Varios requests HTTP entre dos barridos reales (mismo
    `ultimo_sweep_at`) nunca deben, por sí solos, confirmar la
    degradación -- el contador solo avanza cuando el marcador CAMBIA."""
    _aplicar("XYZ", "OPORTUNIDAD_PRIORITARIA", "Etapa INICIO", False, "sweep-1")
    r1, _ = _aplicar("XYZ", "NO_TOCAR", "Sin precio actual...", True, "sweep-2")
    r2, _ = _aplicar("XYZ", "NO_TOCAR", "Sin precio actual...", True, "sweep-2")  # mismo marcador, otro request
    r3, _ = _aplicar("XYZ", "NO_TOCAR", "Sin precio actual...", True, "sweep-2")  # mismo marcador de nuevo
    assert [r1, r2, r3] == ["OPORTUNIDAD_PRIORITARIA"] * 3  # sigue frenado -- 1 solo sweep real visto
    r4, _ = _aplicar("XYZ", "NO_TOCAR", "Sin precio actual...", True, "sweep-3")  # ahora sí, 2do sweep real
    assert r4 == "NO_TOCAR"


def test_recuperacion_un_dato_bueno_en_el_medio_resetea_el_contador():
    """Mecanismo de recuperación explícito, mismo criterio que la
    histéresis de `stage`: si el dato vuelve a estar OK antes de
    confirmar la degradación, el contador se reinicia -- nunca acumula
    barridos NO consecutivos."""
    _aplicar("XYZ", "OPORTUNIDAD_PRIORITARIA", "Etapa INICIO", False, "sweep-1")
    r1, _ = _aplicar("XYZ", "NO_TOCAR", "Sin precio actual...", True, "sweep-2")
    assert r1 == "OPORTUNIDAD_PRIORITARIA"
    # el dato vuelve a estar bien -- resetea
    r2, _ = _aplicar("XYZ", "OPORTUNIDAD_PRIORITARIA", "Etapa INICIO", False, "sweep-3")
    assert r2 == "OPORTUNIDAD_PRIORITARIA"
    r3, _ = _aplicar("XYZ", "NO_TOCAR", "Sin precio actual...", True, "sweep-4")
    assert r3 == "OPORTUNIDAD_PRIORITARIA"  # de nuevo el 1er sweep malo -- todavía frenado
    r4, _ = _aplicar("XYZ", "NO_TOCAR", "Sin precio actual...", True, "sweep-5")
    assert r4 == "NO_TOCAR"  # recién el 2do sweep consecutivo real confirma


def test_promocion_a_bucket_mejor_nunca_se_frena():
    """Regla dura del pedido: la histéresis SOLO protege degradaciones --
    una promoción (ej. VIGILAR -> OPORTUNIDAD_PRIORITARIA) pasa siempre de
    inmediato, sin ningún frenado."""
    _aplicar("XYZ", "VIGILAR", "Etapa ALERTA_TEMPRANA", False, "sweep-1")
    estado, motivo = _aplicar("XYZ", "OPORTUNIDAD_PRIORITARIA", "Etapa INICIO, dirección confirmada", False, "sweep-2")
    assert estado == "OPORTUNIDAD_PRIORITARIA"
    assert motivo == "Etapa INICIO, dirección confirmada"


def test_preparacion_tambien_cuenta_como_bucket_accionable_a_proteger():
    _aplicar("XYZ", "PREPARACION", "Etapa PREPARACION, sin movimiento fuerte todavía", False, "sweep-1")
    r1, _ = _aplicar("XYZ", "NO_TOCAR", "Precio vencido", True, "sweep-2")
    assert r1 == "PREPARACION"


def test_configurable_via_constante(monkeypatch):
    """N configurable -- con N=3, 2 sweeps consecutivos malos siguen sin
    confirmar la degradación."""
    monkeypatch.setattr(server, "PRICE_VALIDATION_HYSTERESIS_SWEEPS", 3)
    _aplicar("XYZ", "OPORTUNIDAD_PRIORITARIA", "Etapa INICIO", False, "sweep-1")
    r1, _ = _aplicar("XYZ", "NO_TOCAR", "Sin precio actual...", True, "sweep-2")
    r2, _ = _aplicar("XYZ", "NO_TOCAR", "Sin precio actual...", True, "sweep-3")
    r3, _ = _aplicar("XYZ", "NO_TOCAR", "Sin precio actual...", True, "sweep-4")
    assert [r1, r2] == ["OPORTUNIDAD_PRIORITARIA"] * 2
    assert r3 == "NO_TOCAR"


def test_tickers_distintos_no_se_interfieren():
    _aplicar("AAA", "OPORTUNIDAD_PRIORITARIA", "Etapa INICIO", False, "sweep-1")
    r_bbb, _ = _aplicar("BBB", "NO_TOCAR", "Sin precio actual...", True, "sweep-1")
    assert r_bbb == "NO_TOCAR"  # BBB nunca tuvo un bucket accionable confirmado -- confirma de inmediato

    r_aaa, _ = _aplicar("AAA", "NO_TOCAR", "Sin precio actual...", True, "sweep-2")
    assert r_aaa == "OPORTUNIDAD_PRIORITARIA"  # AAA sí lo tenía -- se frena, independiente de BBB


def test_market_date_distinto_no_arrastra_estado_de_otro_dia():
    _aplicar("XYZ", "OPORTUNIDAD_PRIORITARIA", "Etapa INICIO", False, "sweep-1", market_date="2026-09-22")
    # mismo ticker, día de mercado distinto -- sin estado previo para HOY.
    estado, _ = _aplicar("XYZ", "NO_TOCAR", "Sin precio actual...", True, "sweep-1", market_date="2026-09-23")
    assert estado == "NO_TOCAR"
