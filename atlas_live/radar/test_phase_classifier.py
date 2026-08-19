"""Tests del clasificador de fase (2026-08-15). Casos sintéticos controlados, sin red."""

from atlas_live.radar import phase_classifier as pc


def test_antes_del_movimiento_bajo_el_piso():
    tag = pc.from_live_detection(2.0, [], historical_percentile_90=15.0, session="regular")
    assert tag.timing_deteccion == "antes_del_movimiento"


def test_al_comienzo_moderado_y_acelerando():
    tag = pc.from_live_detection(6.0, ["aceleracion"], historical_percentile_90=15.0, session="regular")
    assert tag.timing_deteccion == "al_comienzo"


def test_expansion_temprana_moderado_sin_acelerar():
    tag = pc.from_live_detection(6.0, ["cambio_de_precio"], historical_percentile_90=15.0, session="regular")
    assert tag.timing_deteccion == "expansion_temprana"


def test_recorrido_significativo_ya_hecho_grande_y_acelerando():
    tag = pc.from_live_detection(20.0, ["aceleracion"], historical_percentile_90=15.0, session="regular")
    assert tag.timing_deteccion == "recorrido_significativo_ya_hecho"


def test_demasiado_tarde_grande_sin_acelerar():
    tag = pc.from_live_detection(20.0, ["cambio_de_precio"], historical_percentile_90=15.0, session="regular")
    assert tag.timing_deteccion == "demasiado_tarde"


def test_agotamiento_con_puerta_recuperacion():
    tag = pc.from_live_detection(10.0, ["recuperacion"], historical_percentile_90=15.0, session="regular")
    assert tag.timing_deteccion == "agotamiento"


def test_indeterminado_sin_percentil_historico_pero_bajo_el_piso():
    tag = pc.from_live_detection(2.0, [], historical_percentile_90=None, session="regular")
    assert tag.timing_deteccion == "antes_del_movimiento"  # sigue siendo determinable con el piso solo


def test_direccion_es_independiente_del_timing():
    tag_alcista = pc.from_live_detection(20.0, ["aceleracion"], historical_percentile_90=15.0, session="regular")
    tag_bajista = pc.from_live_detection(-20.0, ["aceleracion"], historical_percentile_90=15.0, session="regular")
    assert tag_alcista.direction == "ALCISTA"
    assert tag_bajista.direction == "BAJISTA"
    # mismo timing, direcciones opuestas -- son dimensiones independientes
    assert tag_alcista.timing_deteccion == tag_bajista.timing_deteccion == "recorrido_significativo_ya_hecho"


def test_comportamiento_post_apertura_continua():
    obs = [{"change_pct": 5.0}, {"change_pct": 6.0}, {"change_pct": 8.0}]
    assert pc.classify_post_open_behavior("premarket", obs) == "continua"


def test_comportamiento_post_apertura_colapsa():
    obs = [{"change_pct": 8.0}, {"change_pct": 4.0}, {"change_pct": 1.0}]
    assert pc.classify_post_open_behavior("premarket", obs) == "colapsa"


def test_comportamiento_post_apertura_no_aplica_fuera_de_premarket():
    obs = [{"change_pct": 8.0}, {"change_pct": 4.0}]
    assert pc.classify_post_open_behavior("regular", obs) == "no_aplica"


def test_historico_sin_granularidad_de_sesion_siempre_no_aplica():
    tag = pc.from_historical_day(10.0, 15.0, drop_from_peak_10d_pct=None, rebound_from_trough_pct=None)
    assert tag.comportamiento_post_apertura == "no_aplica"


def test_historico_detecta_agotamiento_solo_si_hubo_una_subida_real_antes():
    """REVISIÓN 2026-08-15: agotamiento requiere que el pico haya venido de
    una subida real (peak_gain_10d_pct >= piso) -- si no, es solo drift
    lateral, no un movimiento agotado."""
    tag = pc.from_historical_day(5.0, 15.0, drop_from_peak_10d_pct=-8.0, rebound_from_trough_pct=0.5,
                                  peak_gain_10d_pct=12.0)
    assert tag.timing_deteccion == "agotamiento"


def test_historico_no_confunde_drift_lateral_con_agotamiento():
    """Mismo patrón de pico/valle que el caso anterior, pero SIN que hubiera
    una subida real hacia ese pico (peak_gain_10d_pct bajo) -- ya NO debe
    clasificarse como agotamiento."""
    tag = pc.from_historical_day(5.0, 15.0, drop_from_peak_10d_pct=-8.0, rebound_from_trough_pct=0.5,
                                  peak_gain_10d_pct=1.0)
    assert tag.timing_deteccion != "agotamiento"


def test_caso_real_zim_change_pct_cero_con_rvol_casi_nulo_no_es_confiable():
    """Fase 7 (2026-08-18) -- caso real de la sesión 2026-08-17: ZIM,
    detectada con `change_pct=0.0` y `relative_volume=0.0098` (casi sin
    operaciones). Se movió de verdad ese día (hasta +4.3%) -- el 0.0%
    nunca fue "neutral real", era falta de dato. Con `relative_volume`
    pasado explícitamente, debe marcarse no confiable, `direction="INDEFINIDA"`
    (no "NEUTRAL") y el timing "indeterminado" (no "antes_del_movimiento")."""
    tag = pc.from_live_detection(0.0, ["cambio_de_comportamiento"], historical_percentile_90=None,
                                  session="premarket", relative_volume=0.0098)
    assert tag.change_pct_confiable is False
    assert tag.direction == "INDEFINIDA"
    assert tag.timing_deteccion == "indeterminado"


def test_change_pct_cero_con_volumen_real_sigue_confiable():
    """Un 0.0% con volumen real de respaldo SÍ es un dato confiable --
    comportamiento sin cambios respecto a antes de la Fase 7."""
    tag = pc.from_live_detection(0.0, [], historical_percentile_90=None, session="regular",
                                  relative_volume=1.2)
    assert tag.change_pct_confiable is True
    assert tag.direction == "NEUTRAL"


def test_sin_pasar_relative_volume_preserva_el_comportamiento_de_siempre():
    """Compatibilidad hacia atrás explícita: llamadas que no pasan
    `relative_volume` (como antes de la Fase 7) no cambian de compotamiento."""
    tag = pc.from_live_detection(0.0, [], historical_percentile_90=None, session="regular")
    assert tag.change_pct_confiable is True
    assert tag.direction == "NEUTRAL"


def test_change_pct_none_nunca_es_confiable():
    tag = pc.from_live_detection(None, [], historical_percentile_90=None, session="regular", relative_volume=5.0)
    assert tag.change_pct_confiable is False
    assert tag.direction == "INDEFINIDA"


def test_caso_real_ken_precio_mid_bid_ask_sin_volumen_no_es_confiable():
    """2026-08-19, caso real de producción: KEN detectada con
    `change_pct_at_detection=1.61%` (NO 0.0, a diferencia del caso ZIM) pero
    con `price_basis="tradier_bid_ask_mid"` (sin operación real, Tradier
    usó el punto medio bid/ask) y `relative_volume=0.0` -- casi sin
    operaciones (TradingView mostraba -1.29% real, "No hay operaciones").
    El 1.61% no es un movimiento de mercado, es aritmética sobre el spread.
    Debe marcarse no confiable aunque el número no sea exactamente cero."""
    tag = pc.from_live_detection(1.6139092468423686, ["sostenido_premarket"], historical_percentile_90=None,
                                  session="premarket", relative_volume=0.0, price_basis="tradier_bid_ask_mid")
    assert tag.change_pct_confiable is False
    assert tag.direction == "INDEFINIDA"
    assert tag.timing_deteccion == "indeterminado"


def test_precio_mid_bid_ask_con_volumen_real_sigue_confiable():
    """El punto medio bid/ask por sí solo NO es motivo de desconfianza --
    solo cuando además casi no hay volumen real de respaldo."""
    tag = pc.from_live_detection(2.0, [], historical_percentile_90=None, session="premarket",
                                  relative_volume=0.5, price_basis="tradier_bid_ask_mid")
    assert tag.change_pct_confiable is True


def test_price_basis_tradier_last_nunca_activa_el_chequeo_nuevo():
    """Un precio de una operación real (`tradier_last`) no activa este
    chequeo nuevo, incluso con RVOL bajo -- ese caso sigue cubierto (o no)
    exclusivamente por el chequeo original de `change_pct == 0.0`."""
    tag = pc.from_live_detection(1.6, [], historical_percentile_90=None, session="premarket",
                                  relative_volume=0.01, price_basis="tradier_last")
    assert tag.change_pct_confiable is True


def test_sin_pasar_price_basis_preserva_el_comportamiento_de_siempre():
    """Compatibilidad hacia atrás: llamadas que no pasan `price_basis`
    (como antes de este fix) no cambian de comportamiento."""
    tag = pc.from_live_detection(1.6, [], historical_percentile_90=None, session="premarket",
                                  relative_volume=0.0)
    assert tag.change_pct_confiable is True


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
            p += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
            traceback.print_exc()
            f += 1
    print(f"--- {p} passed, {f} failed ---")
