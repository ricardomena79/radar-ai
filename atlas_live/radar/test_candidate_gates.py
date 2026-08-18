"""Tests de las puertas de detección del radar (2026-08-14). Sin red -- lógica pura."""

from atlas_live.radar import candidate_gates as g
from atlas_live.radar.sweep_history import SweepSnapshot


def _snap(change_pct=0.0, rvol=1.0, price=10.0, volume=100_000, avg_volume=100_000, dollar_volume=1_000_000,
          sweep_id="s0", session="regular"):
    return SweepSnapshot(
        sweep_id=sweep_id, observed_at="2026-08-14T10:00:00Z", price=price, change_pct=change_pct,
        volume=volume, average_volume=avg_volume, relative_volume=rvol, dollar_volume=dollar_volume,
        session=session,
    )


def test_gate_price_change_dispara_sobre_el_piso():
    r = g.gate_price_change(_snap(change_pct=5.0), [], "regular")
    assert r.fired
    r2 = g.gate_price_change(_snap(change_pct=0.5), [], "regular")
    assert not r2.fired


def test_gate_price_change_capta_bajas_tambien_valor_absoluto():
    r = g.gate_price_change(_snap(change_pct=-6.0), [], "regular")
    assert r.fired


def test_gate_relative_volume():
    assert g.gate_relative_volume(_snap(rvol=2.5), [], "regular").fired
    assert not g.gate_relative_volume(_snap(rvol=1.0), [], "regular").fired


def test_gate_dollar_volume():
    assert g.gate_dollar_volume(_snap(dollar_volume=1_000_000), [], "regular").fired
    assert not g.gate_dollar_volume(_snap(dollar_volume=1_000), [], "regular").fired


def test_gate_acceleration_necesita_historial():
    r = g.gate_acceleration(_snap(change_pct=5.0), [], "regular")
    assert not r.fired  # sin historial, no dispara


def test_gate_acceleration_dispara_con_salto_real():
    history = [_snap(change_pct=1.0), _snap(change_pct=1.2), _snap(change_pct=1.3), _snap(change_pct=1.4)]
    current = _snap(change_pct=5.0)  # salto de +3.6pp vs 4 barridos atrás
    r = g.gate_acceleration(current, history, "regular")
    assert r.fired


def test_gate_wakeup_dispara_si_estaba_quieto_y_salta():
    history = [_snap(rvol=0.5), _snap(rvol=0.6), _snap(rvol=0.7), _snap(rvol=0.8)]
    current = _snap(rvol=3.0)
    r = g.gate_wakeup(current, history, "regular")
    assert r.fired


def test_gate_wakeup_no_dispara_si_ya_venia_activo():
    history = [_snap(rvol=2.0), _snap(rvol=2.1), _snap(rvol=2.2), _snap(rvol=2.3)]
    current = _snap(rvol=3.0)
    r = g.gate_wakeup(current, history, "regular")
    assert not r.fired  # no estaba "quieto" antes


def test_gate_recovery_dispara_tras_caida_y_rebote():
    history = [_snap(change_pct=8.0), _snap(change_pct=5.0), _snap(change_pct=2.0), _snap(change_pct=1.5)]
    current = _snap(change_pct=4.5)  # rebote desde el mínimo (1.5) de +3pp
    r = g.gate_recovery(current, history, "regular")
    assert r.fired


def test_gate_sustained_premarket_climb_solo_en_premarket():
    history = [_snap(change_pct=1.0), _snap(change_pct=2.0), _snap(change_pct=3.0), _snap(change_pct=4.0)]
    current = _snap(change_pct=5.0)
    r_pre = g.gate_sustained_premarket_climb(current, history, "premarket")
    r_reg = g.gate_sustained_premarket_climb(current, history, "regular")
    assert r_pre.fired
    assert not r_reg.fired


def test_gate_behavior_change_auto_relativo():
    history = [_snap(rvol=0.8), _snap(rvol=0.9), _snap(rvol=1.0), _snap(rvol=0.85)]
    current = _snap(rvol=3.0)  # varias veces la mediana propia (~0.9)
    r = g.gate_behavior_change(current, history, "regular")
    assert r.fired


def test_evaluate_all_gates_corre_las_7_activas_siempre():
    # dollar_volume NO está en ALL_GATES a propósito -- ver evidencia real
    # en el docstring de gate_dollar_volume (no discrimina en este universo).
    results = g.evaluate_all_gates(_snap(change_pct=10.0, rvol=5.0), [], "regular")
    assert len(results) == 7
    assert "dollar_volume" not in {r.name for r in results}
    assert g.any_gate_fired(results)


def test_simbolo_tranquilo_no_dispara_ninguna():
    results = g.evaluate_all_gates(_snap(change_pct=0.1, rvol=1.0, dollar_volume=1_000_000_000), [], "regular")
    assert not g.any_gate_fired(results)


def test_gate_acceleration_ignora_historial_de_otra_sesion():
    """Prioridad 5 (Fase 6, 2026-08-18) -- hallazgo real de la sesión
    2026-08-17: 21 de 25 etiquetas INICIO se dispararon en los primeros 5
    minutos de la sesión regular, con volumen casi nulo -- SweepHistory
    mezclaba premarket con regular en el mismo lookback. El mismo salto de
    change_pct que SÍ dispara la puerta dentro de una sesión NO debe
    disparar cuando la referencia es de la sesión anterior."""
    historial_cruzado = [
        _snap(change_pct=1.0, session="premarket"), _snap(change_pct=1.2, session="premarket"),
        _snap(change_pct=1.3, session="premarket"), _snap(change_pct=1.4, session="premarket"),
    ]
    current = _snap(change_pct=5.0, session="regular")  # mismo salto que SÍ dispara en test_gate_acceleration_dispara_con_salto_real
    r = g.gate_acceleration(current, historial_cruzado, "regular")
    assert not r.fired  # sin historial de la MISMA sesión, no hay base de comparación


def test_gate_acceleration_dispara_con_salto_dentro_de_la_misma_sesion():
    historial_mismo_dia = [
        _snap(change_pct=1.0, session="regular"), _snap(change_pct=1.2, session="regular"),
        _snap(change_pct=1.3, session="regular"), _snap(change_pct=1.4, session="regular"),
    ]
    current = _snap(change_pct=5.0, session="regular")
    r = g.gate_acceleration(current, historial_mismo_dia, "regular")
    assert r.fired


def test_gate_wakeup_ignora_historial_de_otra_sesion():
    historial_cruzado = [
        _snap(rvol=0.5, session="premarket"), _snap(rvol=0.6, session="premarket"),
        _snap(rvol=0.7, session="premarket"), _snap(rvol=0.8, session="premarket"),
    ]
    current = _snap(rvol=3.0, session="regular")  # mismo salto que SÍ dispara en test_gate_wakeup_dispara_si_estaba_quieto_y_salta
    r = g.gate_wakeup(current, historial_cruzado, "regular")
    assert not r.fired


def test_gate_behavior_change_ignora_historial_de_otra_sesion():
    historial_cruzado = [
        _snap(rvol=0.8, session="premarket"), _snap(rvol=0.9, session="premarket"),
        _snap(rvol=1.0, session="premarket"), _snap(rvol=0.85, session="premarket"),
    ]
    current = _snap(rvol=3.0, session="regular")  # mismo salto que SÍ dispara en test_gate_behavior_change_auto_relativo
    r = g.gate_behavior_change(current, historial_cruzado, "regular")
    assert not r.fired


def test_gate_recovery_ignora_historial_de_otra_sesion():
    historial_cruzado = [
        _snap(change_pct=8.0, session="premarket"), _snap(change_pct=5.0, session="premarket"),
        _snap(change_pct=2.0, session="premarket"), _snap(change_pct=1.5, session="premarket"),
    ]
    current = _snap(change_pct=4.5, session="regular")  # mismo rebote que SÍ dispara en test_gate_recovery_dispara_tras_caida_y_rebote
    r = g.gate_recovery(current, historial_cruzado, "regular")
    assert not r.fired


def test_dollar_volume_alto_por_si_solo_ya_no_alcanza_para_ser_candidata():
    """Evidencia real (2026-08-14): dollar_volume alto por sí solo NO debe
    convertir a un símbolo en candidata -- AAPL con cambio casi nulo (+0.22%)
    y RVOL bajo (0.5x) tenía dollar_volume >$90M en la validación real y
    correctamente NO debería disparar ninguna puerta activa."""
    aapl_like = _snap(change_pct=0.22, rvol=0.50, dollar_volume=93_000_000)
    results = g.evaluate_all_gates(aapl_like, [], "regular")
    assert not g.any_gate_fired(results)


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
