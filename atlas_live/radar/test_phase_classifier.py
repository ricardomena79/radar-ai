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
