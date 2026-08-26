"""Tests del orquestador de barrido (2026-08-14). DB temporal, Quotes falsas, sin red."""

import tempfile
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

from atlas.data.models.quote import Quote
from atlas_live.radar import candidate_gates as gates
from atlas_live.radar import candidate_registry as reg
from atlas_live.radar import candidate_tracker as tracker
from atlas_live.radar.sweep_history import SweepHistory

_ORIG = reg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_tracker_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None


def _restore():
    reg.DB_PATH = _ORIG


def _quote(symbol, price, change_pct, volume=500, avg_volume=500, rvol=1.0):
    return Quote(symbol=symbol, name=symbol, last_price=price, change_percent=change_pct,
                 volume=volume, open=price, high=price, low=price, previous_close=price,
                 average_volume=avg_volume, relative_volume=rvol)


def _now():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# PM-RVOL Fase 2 (2026-08-25) -- trazabilidad/aprendizaje, congelado en
# candidate_detection. Casos A-J pedidos explícitamente por el usuario.
# ---------------------------------------------------------------------------

def _padding_universe(n=120, volume=100):
    """Universo sintético amplio -- suficiente para que
    `premarket_volume_percentile` tenga MIN_UNIVERSE_SIZE_FOR_PM_PERCENTILE
    (100) símbolos válidos."""
    return {f"PAD{i}": _quote(f"PAD{i}", 10.0, 0.0, volume=volume, avg_volume=500, rvol=0.5) for i in range(n)}


def test_A_las_senales_se_congelan_en_el_primer_momento_de_deteccion():
    _fresh()
    try:
        h = SweepHistory()
        # 8 barridos previos de NSSC en premarket (2*K=8, suficiente para
        # aceleración) + universo amplio en cada barrido.
        for i in range(8):
            quotes = dict(_padding_universe())
            quotes["NSSC"] = _quote("NSSC", 38.09, 0.0, volume=100 + i * 200, avg_volume=372451, rvol=0.001)
            tracker.process_sweep(quotes, h, "2026-08-24", "premarket", _now())
        assert reg.count_candidates_for_date("2026-08-24") == 0  # todavía nada dispara (cambio chico)

        # Barrido de detección real: salto de precio real -> dispara gate_price_change.
        quotes = dict(_padding_universe())
        quotes["NSSC"] = _quote("NSSC", 40.5, 6.3, volume=100 + 8 * 200 + 5000, avg_volume=372451, rvol=0.005)
        result = tracker.process_sweep(quotes, h, "2026-08-24", "premarket", _now())
        assert "NSSC" in result.n_nuevas_detecciones

        det = reg.get_detection("NSSC", "2026-08-24")
        assert det["premarket_volume_percentile_state_at_detection"] == "VALID"
        assert det["premarket_volume_percentile_at_detection"] is not None
        assert det["premarket_volume_acceleration_state_at_detection"] == "VALID"
        assert det["premarket_volume_acceleration_at_detection"] is not None
        assert det["pm_universe_size_at_detection"] == len(_padding_universe()) + 1
        assert det["pm_volume_at_detection"] == 100 + 8 * 200 + 5000
        assert det["pm_dollar_volume_at_detection"] == 40.5 * (100 + 8 * 200 + 5000)
    finally:
        _restore()


def test_B_una_segunda_observacion_no_sobrescribe_los_valores_originales():
    _fresh()
    try:
        h = SweepHistory()
        for i in range(8):
            quotes = dict(_padding_universe())
            quotes["NSSC"] = _quote("NSSC", 38.09, 0.0, volume=100 + i * 200, avg_volume=372451, rvol=0.001)
            tracker.process_sweep(quotes, h, "2026-08-24", "premarket", _now())

        quotes = dict(_padding_universe())
        quotes["NSSC"] = _quote("NSSC", 40.5, 6.3, volume=100 + 8 * 200 + 5000, avg_volume=372451, rvol=0.005)
        tracker.process_sweep(quotes, h, "2026-08-24", "premarket", _now())
        det_original = reg.get_detection("NSSC", "2026-08-24")
        percentil_original = det_original["premarket_volume_percentile_at_detection"]
        aceleracion_original = det_original["premarket_volume_acceleration_at_detection"]

        # Barrido posterior con un universo MUY distinto (todo el padding con
        # volumen altísimo) -- si el percentil se recalculara, cambiaría
        # muchísimo. NSSC sigue disparando (sigue subiendo) -> pasa a señal,
        # pero record_detection() debe seguir siendo un no-op (INSERT OR IGNORE).
        quotes2 = _padding_universe(volume=1_000_000)
        quotes2["NSSC"] = _quote("NSSC", 45.0, 18.1, volume=100 + 8 * 200 + 50_000, avg_volume=372451, rvol=0.05)
        tracker.process_sweep(quotes2, h, "2026-08-24", "premarket", _now())

        det_despues = reg.get_detection("NSSC", "2026-08-24")
        assert det_despues["premarket_volume_percentile_at_detection"] == percentil_original
        assert det_despues["premarket_volume_acceleration_at_detection"] == aceleracion_original
    finally:
        _restore()


def test_C_valor_none_se_conserva_como_null_con_su_estado():
    _fresh()
    try:
        h = SweepHistory()
        # Universo chico (< 100) a propósito -- percentil queda INSUFFICIENT_UNIVERSE (None).
        quotes = {"NSSC": _quote("NSSC", 40.5, 6.3, volume=5000, avg_volume=372451, rvol=0.005)}
        result = tracker.process_sweep(quotes, h, "2026-08-24", "premarket", _now())
        assert "NSSC" in result.n_nuevas_detecciones

        det = reg.get_detection("NSSC", "2026-08-24")
        assert det["premarket_volume_percentile_at_detection"] is None
        assert det["premarket_volume_percentile_state_at_detection"] == "INSUFFICIENT_UNIVERSE"
        # sin historial tampoco -> aceleración también None con su propio estado
        assert det["premarket_volume_acceleration_at_detection"] is None
        assert det["premarket_volume_acceleration_state_at_detection"] == "INSUFFICIENT_HISTORY"
    finally:
        _restore()


def test_D_deteccion_fuera_de_premarket_queda_not_premarket():
    _fresh()
    try:
        h = SweepHistory()
        quotes = dict(_padding_universe())
        quotes["AAPL"] = _quote("AAPL", 305.0, 5.0, volume=5000, rvol=2.0)
        result = tracker.process_sweep(quotes, h, "2026-08-14", "regular", _now())
        assert "AAPL" in result.n_nuevas_detecciones

        det = reg.get_detection("AAPL", "2026-08-14")
        assert det["premarket_volume_percentile_at_detection"] is None
        assert det["premarket_volume_percentile_state_at_detection"] == "NOT_PREMARKET"
        assert det["premarket_volume_acceleration_at_detection"] is None
        assert det["premarket_volume_acceleration_state_at_detection"] == "NOT_PREMARKET"
    finally:
        _restore()


def test_E_universo_insuficiente_queda_insufficient_universe():
    _fresh()
    try:
        h = SweepHistory()
        quotes = {f"PAD{i}": _quote(f"PAD{i}", 10.0, 0.0, volume=100) for i in range(20)}  # 20 < 100
        quotes["NSSC"] = _quote("NSSC", 40.5, 6.3, volume=5000, avg_volume=372451, rvol=0.005)
        result = tracker.process_sweep(quotes, h, "2026-08-24", "premarket", _now())
        assert "NSSC" in result.n_nuevas_detecciones
        det = reg.get_detection("NSSC", "2026-08-24")
        assert det["premarket_volume_percentile_state_at_detection"] == "INSUFFICIENT_UNIVERSE"
        assert det["premarket_volume_percentile_at_detection"] is None
    finally:
        _restore()


def test_F_historial_insuficiente_queda_insufficient_history():
    _fresh()
    try:
        h = SweepHistory()
        # Único barrido -- sin historial previo -> aceleración INSUFFICIENT_HISTORY.
        quotes = dict(_padding_universe())
        quotes["NSSC"] = _quote("NSSC", 40.5, 6.3, volume=5000, avg_volume=372451, rvol=0.005)
        result = tracker.process_sweep(quotes, h, "2026-08-24", "premarket", _now())
        assert "NSSC" in result.n_nuevas_detecciones
        det = reg.get_detection("NSSC", "2026-08-24")
        assert det["premarket_volume_acceleration_state_at_detection"] == "INSUFFICIENT_HISTORY"
        assert det["premarket_volume_acceleration_at_detection"] is None
        # el universo SÍ alcanza -- percentil debe seguir siendo VALID (prueba
        # que ambos estados son independientes entre sí).
        assert det["premarket_volume_percentile_state_at_detection"] == "VALID"
    finally:
        _restore()


def test_G_campos_antiguos_de_candidate_detection_permanecen_intactos():
    """Los campos ya existentes (precio/cambio/volumen/gates_fired/
    price_basis_at_detection/etc.) no cambiaron de significado ni de
    valor por agregar las 7 columnas PM nuevas."""
    _fresh()
    try:
        h = SweepHistory()
        quotes = dict(_padding_universe())
        quotes["AAPL"] = _quote("AAPL", 305.0, 5.0, volume=2_000_000, avg_volume=500_000, rvol=2.0)
        tracker.process_sweep(quotes, h, "2026-08-14", "regular", _now())
        det = reg.get_detection("AAPL", "2026-08-14")
        assert det["price_at_detection"] == 305.0
        assert det["change_pct_at_detection"] == 5.0
        assert det["volume_at_detection"] == 2_000_000
        assert det["ticker"] == "AAPL"
        assert det["market_date"] == "2026-08-14"
        assert len(det["gates_fired"]) >= 1
        assert det["source"] == "tradier"
    finally:
        _restore()


def test_H_las_7_gates_siguen_siendo_exactamente_las_mismas():
    assert len(gates.ALL_GATES) == 7
    nombres = {g.__name__ for g in gates.ALL_GATES}
    assert nombres == {
        "gate_price_change", "gate_relative_volume", "gate_acceleration", "gate_wakeup",
        "gate_recovery", "gate_sustained_premarket_climb", "gate_behavior_change",
    }


def test_I_scoring_y_ranking_no_cambian_con_o_sin_evidencia_pm():
    """Misma señal de precio real (gate_price_change), en dos universos --
    uno con PM-RVOL disponible (VALID) y otro sin evidencia suficiente
    (INSUFFICIENT_UNIVERSE) -- debe producir EXACTAMENTE la misma
    detección (mismas puertas disparadas, mismo resultado), probando que
    PM-RVOL nunca entra en la decisión de qué se detecta."""
    _fresh()
    try:
        h1 = SweepHistory()
        quotes_grande = dict(_padding_universe())
        quotes_grande["AAPL"] = _quote("AAPL", 305.0, 5.0, volume=2_000_000, avg_volume=500_000, rvol=2.0)
        r1 = tracker.process_sweep(quotes_grande, h1, "2026-08-14", "regular", _now())

        _restore()
        _fresh()
        h2 = SweepHistory()
        quotes_chico = {"AAPL": _quote("AAPL", 305.0, 5.0, volume=2_000_000, avg_volume=500_000, rvol=2.0)}
        r2 = tracker.process_sweep(quotes_chico, h2, "2026-08-14", "regular", _now())

        assert r1.n_nuevas_detecciones == r2.n_nuevas_detecciones == ["AAPL"]
        assert r1.gates_dispersion == r2.gates_dispersion
        det1 = reg.get_detection("AAPL", "2026-08-14")
        assert det1["price_at_detection"] == 305.0  # detección idéntica, con o sin universo PM amplio
    finally:
        _restore()


def test_J_candidata_ya_existente_no_vuelve_a_congelar_senales_nuevas():
    """Mismo caso que B, pero explícito con una candidata que YA estaba
    detectada ANTES de que llegue este barrido (no la primera vez que se
    procesa) -- debe seguir usando `es_senal`, nunca re-congelar."""
    _fresh()
    try:
        h = SweepHistory()
        quotes = dict(_padding_universe())
        quotes["AAPL"] = _quote("AAPL", 305.0, 5.0, volume=2_000_000, avg_volume=500_000, rvol=2.0)
        tracker.process_sweep(quotes, h, "2026-08-14", "regular", _now())
        det_original = reg.get_detection("AAPL", "2026-08-14")

        # Segundo barrido -- AAPL ya es candidata, este es un "señal" (2do
        # vistazo), con datos de PM completamente distintos.
        quotes2 = _padding_universe(volume=999)
        quotes2["AAPL"] = _quote("AAPL", 320.0, 10.0, volume=9_000_000, avg_volume=500_000, rvol=5.0)
        result2 = tracker.process_sweep(quotes2, h, "2026-08-14", "regular", _now())
        assert "AAPL" not in result2.n_nuevas_detecciones  # ya no es "nueva"

        det_despues = reg.get_detection("AAPL", "2026-08-14")
        assert det_despues["pm_volume_at_detection"] == det_original["pm_volume_at_detection"]
        assert det_despues["price_at_detection"] == det_original["price_at_detection"]  # 305.0, no 320.0
    finally:
        _restore()


def test_barrido_detecta_candidatas_reales():
    _fresh()
    try:
        h = SweepHistory()
        quotes = {
            "AAPL": _quote("AAPL", 305.0, 5.0, rvol=2.0),   # dispara varias puertas
            "MSFT": _quote("MSFT", 495.0, 0.1, rvol=1.0),   # tranquilo, no dispara
        }
        result = tracker.process_sweep(quotes, h, "2026-08-14", "regular", _now())
        assert result.n_evaluados == 2
        assert "AAPL" in result.n_nuevas_detecciones
        assert "MSFT" not in result.n_nuevas_detecciones
        assert reg.count_candidates_for_date("2026-08-14") == 1
    finally:
        _restore()


def test_candidata_sigue_en_seguimiento_aunque_el_siguiente_barrido_no_dispare_nada():
    _fresh()
    try:
        h = SweepHistory()
        # barrido 1: AAPL dispara
        tracker.process_sweep({"AAPL": _quote("AAPL", 305.0, 5.0, rvol=2.0)}, h, "2026-08-14", "regular", _now())
        assert reg.count_candidates_for_date("2026-08-14") == 1
        # barrido 2: AAPL ahora "tranquilo" (ninguna puerta dispara) -- pero YA es candidata
        tracker.process_sweep({"AAPL": _quote("AAPL", 305.5, 0.2, rvol=1.0)}, h, "2026-08-14", "regular", _now())
        obs = reg.get_observations("AAPL", "2026-08-14")
        assert len(obs) == 2  # se siguió registrando, no desapareció
        assert reg.count_candidates_for_date("2026-08-14") == 1  # sigue siendo UNA candidata, no una nueva
    finally:
        _restore()


def test_aceleracion_dispara_en_barridos_sucesivos():
    _fresh()
    try:
        h = SweepHistory()
        for i, pct in enumerate([1.0, 1.2, 1.3, 1.4]):
            tracker.process_sweep({"NVDA": _quote("NVDA", 100 + i, pct, rvol=1.0)}, h, "2026-08-14", "regular", _now())
        assert reg.count_candidates_for_date("2026-08-14") == 0  # nada disparó todavía (cambios chicos)
        # ahora un salto real -- aceleración debería dispararse
        result = tracker.process_sweep({"NVDA": _quote("NVDA", 110, 6.0, rvol=1.0)}, h, "2026-08-14", "regular", _now())
        assert "NVDA" in result.n_nuevas_detecciones
        assert "aceleracion" in result.gates_dispersion or "cambio_de_precio" in result.gates_dispersion
    finally:
        _restore()


def test_candidata_pasa_a_senal_en_el_segundo_barrido():
    """Reinicio 2026-08-15: candidata = 1+ puerta en un barrido; señal =
    sigue activa en un barrido posterior (no un parpadeo de un solo tick)."""
    _fresh()
    try:
        h = SweepHistory()
        tracker.process_sweep({"AAPL": _quote("AAPL", 305.0, 5.0, rvol=2.0)}, h, "2026-08-14", "regular", _now())
        candidatas = reg.list_candidates_for_date("2026-08-14")
        assert candidatas[0]["es_senal"] == 0  # todavía no -- es la primera vez que se ve

        tracker.process_sweep({"AAPL": _quote("AAPL", 306.0, 5.3, rvol=2.1)}, h, "2026-08-14", "regular", _now())
        candidatas = reg.list_candidates_for_date("2026-08-14")
        assert candidatas[0]["es_senal"] == 1  # ya es la 2da vez -- pasa a señal
    finally:
        _restore()


def test_candidata_tranquila_en_2do_barrido_tambien_pasa_a_senal():
    """Mismo criterio aplica aunque el 2do barrido puntual no dispare
    ninguna puerta -- lo que importa es que sigue siendo vista, no que siga
    disparando."""
    _fresh()
    try:
        h = SweepHistory()
        tracker.process_sweep({"AAPL": _quote("AAPL", 305.0, 5.0, rvol=2.0)}, h, "2026-08-14", "regular", _now())
        tracker.process_sweep({"AAPL": _quote("AAPL", 305.1, 0.2, rvol=1.0)}, h, "2026-08-14", "regular", _now())
        candidatas = reg.list_candidates_for_date("2026-08-14")
        assert candidatas[0]["es_senal"] == 1
    finally:
        _restore()


def test_daily_range_pct_se_calcula_desde_high_low_del_quote():
    """Experimento C (2026-08-16) -- diagnóstico puro, calculado del propio
    Quote del barrido, sin red adicional."""
    _fresh()
    try:
        h = SweepHistory()
        q = Quote(symbol="AAPL", name="AAPL", last_price=100.0, change_percent=5.0,
                   volume=500, open=98.0, high=104.0, low=97.0, previous_close=95.0,
                   average_volume=500, relative_volume=2.0)
        tracker.process_sweep({"AAPL": q}, h, "2026-08-14", "regular", _now())
        candidatas = reg.list_candidates_for_date("2026-08-14")
        # (104-97)/100 * 100 = 7.0
        assert candidatas[0]["daily_range_pct_at_detection"] == 7.0
    finally:
        _restore()


def test_volatility_14d_pct_sale_de_la_base_historica_si_existe():
    """Experimento A (2026-08-16) -- usa la última lectura de
    `reference_registry`, nunca del día en curso. Sin historial de
    referencia para el símbolo, queda None (no se inventa nada)."""
    _fresh()
    try:
        from atlas_live.reference import reference_registry as ref_reg

        orig = ref_reg.latest_volatility_14d_pct
        ref_reg.latest_volatility_14d_pct = lambda symbol: 8.5 if symbol == "AAPL" else None
        try:
            h = SweepHistory()
            tracker.process_sweep({"AAPL": _quote("AAPL", 305.0, 5.0, rvol=2.0)}, h, "2026-08-14", "regular", _now())
            candidatas = reg.list_candidates_for_date("2026-08-14")
            assert candidatas[0]["volatility_14d_pct_at_detection"] == 8.5
        finally:
            ref_reg.latest_volatility_14d_pct = orig
    finally:
        _restore()


def test_reset_de_dia_limpia_el_historial():
    _fresh()
    try:
        h = SweepHistory()
        tracker.process_sweep({"AMD": _quote("AMD", 150.0, 1.0)}, h, "2026-08-14", "regular", _now())
        assert h.current_market_date == "2026-08-14"
        assert h.symbols_tracked() == 1
        tracker.process_sweep({"AMD": _quote("AMD", 150.0, 1.0)}, h, "2026-08-15", "premarket", _now())
        assert h.current_market_date == "2026-08-15"
        assert h.symbols_tracked() == 1  # se reinició y volvió a poblarse con el barrido nuevo
    finally:
        _restore()


def test_tag_alert_stage_registra_preparacion_con_volatilidad_elevada():
    """Fase 4 (2026-08-17) -- capa observacional, nunca toca gates_fired ni
    candidate_detection. Con volatilidad de régimen alta y sin volumen
    elevado todavía, la ventana debe ser PREPARACION."""
    _fresh()
    try:
        from atlas_live.reference import reference_registry as ref_reg

        orig_vol = ref_reg.latest_volatility_14d_pct
        orig_recent = ref_reg.recent_daily_features
        orig_pct = ref_reg.percentile_change_pct
        ref_reg.latest_volatility_14d_pct = lambda symbol: 15.0
        ref_reg.recent_daily_features = lambda symbol, n=5: []  # sin historial de volumen
        ref_reg.percentile_change_pct = lambda symbol, p: None
        try:
            h = SweepHistory()
            # change_pct=5.0 dispara gate_price_change (>=3.0%) para que
            # process_sweep entre a la rama de deteccion y llame a
            # _tag_alert_stage -- rvol=1.0 (bajo el piso de 2.0) evita que
            # el volumen de HOY por si solo empuje a ALERTA_TEMPRANA.
            tracker.process_sweep({"AAPL": _quote("AAPL", 305.0, 5.0, rvol=1.0)}, h, "2026-08-14", "regular", _now())
            assert reg.latest_alert_stage("AAPL", "2026-08-14") == "PREPARACION"
        finally:
            ref_reg.latest_volatility_14d_pct = orig_vol
            ref_reg.recent_daily_features = orig_recent
            ref_reg.percentile_change_pct = orig_pct
    finally:
        _restore()


def test_tag_alert_stage_registra_alerta_fuerte_con_persistencia_y_aceleracion():
    _fresh()
    try:
        from atlas_live.reference import reference_registry as ref_reg

        orig_vol = ref_reg.latest_volatility_14d_pct
        orig_recent = ref_reg.recent_daily_features
        orig_pct = ref_reg.percentile_change_pct
        ref_reg.latest_volatility_14d_pct = lambda symbol: 15.0
        # recent_daily_features: mas reciente primero (T-1..T-5) -- 3 dias
        # elevados (>=2.0) y aceleracion positiva (T-1=6.0 menos T-5=2.0)
        ref_reg.recent_daily_features = lambda symbol, n=5: [
            {"relative_volume": 6.0}, {"relative_volume": 5.0}, {"relative_volume": 3.0},
            {"relative_volume": 1.0}, {"relative_volume": 2.0},
        ]
        ref_reg.percentile_change_pct = lambda symbol, p: None
        try:
            h = SweepHistory()
            tracker.process_sweep({"AAPL": _quote("AAPL", 305.0, 5.0, rvol=1.0)}, h, "2026-08-14", "regular", _now())
            assert reg.latest_alert_stage("AAPL", "2026-08-14") == "ALERTA_FUERTE"
        finally:
            ref_reg.latest_volatility_14d_pct = orig_vol
            ref_reg.recent_daily_features = orig_recent
            ref_reg.percentile_change_pct = orig_pct
    finally:
        _restore()


def test_caso_real_sezl_change_pct_cero_con_rvol_alto_da_alerta_temprana_no_flujo_vendedor():
    """Fase 7 (2026-08-18) -- caso real de la sesión 2026-08-17: SEZL,
    RVOL=8.5789 (real, no ruido) pero `change_pct=0.0` en el instante
    exacto de la detección. Como el volumen SÍ es real (por encima del
    piso de confiabilidad), `change_pct=0.0` se lee como NEUTRAL genuino,
    no como dato faltante -- y NEUTRAL nunca dispara FLUJO_VENDEDOR (solo
    BAJISTA confirmado lo hace). La etapa debe seguir siendo
    ALERTA_TEMPRANA, exactamente lo que Atlas mostró en producción --
    todavía no hay evidencia direccional para llamarla vendedora."""
    _fresh()
    try:
        from atlas_live.reference import reference_registry as ref_reg

        orig_vol = ref_reg.latest_volatility_14d_pct
        orig_recent = ref_reg.recent_daily_features
        orig_pct = ref_reg.percentile_change_pct
        ref_reg.latest_volatility_14d_pct = lambda symbol: 7.43
        ref_reg.recent_daily_features = lambda symbol, n=5: [{"relative_volume": 2.5}] + [{"relative_volume": 0.5}] * 4
        ref_reg.percentile_change_pct = lambda symbol, p: None
        try:
            h = SweepHistory()
            tracker.process_sweep({"SEZL": _quote("SEZL", 128.96, 0.0, rvol=8.5789)}, h, "2026-08-17", "premarket", _now())
            assert reg.latest_alert_stage("SEZL", "2026-08-17") == "ALERTA_TEMPRANA"
        finally:
            ref_reg.latest_volatility_14d_pct = orig_vol
            ref_reg.recent_daily_features = orig_recent
            ref_reg.percentile_change_pct = orig_pct
    finally:
        _restore()


def test_caida_real_con_volumen_da_flujo_vendedor_no_alerta_temprana():
    """La continuación realista del caso SEZL: una vez que el precio SÍ cae
    de verdad (change_pct confiable y negativo) con volumen elevado, la
    etapa debe pasar a FLUJO_VENDEDOR -- nunca quedar como una alerta de
    sabor alcista."""
    _fresh()
    try:
        from atlas_live.reference import reference_registry as ref_reg

        orig_vol = ref_reg.latest_volatility_14d_pct
        orig_recent = ref_reg.recent_daily_features
        orig_pct = ref_reg.percentile_change_pct
        ref_reg.latest_volatility_14d_pct = lambda symbol: 7.43
        ref_reg.recent_daily_features = lambda symbol, n=5: [{"relative_volume": 2.5}] + [{"relative_volume": 0.5}] * 4
        ref_reg.percentile_change_pct = lambda symbol, p: None
        try:
            h = SweepHistory()
            tracker.process_sweep({"SEZL": _quote("SEZL", 122.18, -5.26, rvol=3.0)}, h, "2026-08-17", "regular", _now())
            assert reg.latest_alert_stage("SEZL", "2026-08-17") == "FLUJO_VENDEDOR"
        finally:
            ref_reg.latest_volatility_14d_pct = orig_vol
            ref_reg.recent_daily_features = orig_recent
            ref_reg.percentile_change_pct = orig_pct
    finally:
        _restore()


def test_caso_real_ken_precio_mid_bid_ask_no_confunde_direction_con_alcista():
    """2026-08-19, caso real de producción: KEN detectada con precio del
    punto medio bid/ask (`price_basis="tradier_bid_ask_mid"`), casi sin
    volumen real (rvol=0.0), y un change_pct positivo que era aritmética
    del spread, no un movimiento de mercado real. Confirma que
    `process_sweep()` propaga `quote.price_basis` hasta
    `direction_at_detection` -- debe quedar "INDEFINIDA", NUNCA "ALCISTA"."""
    _fresh()
    try:
        h = SweepHistory()
        quote = Quote(symbol="KEN", name="KEN", last_price=65.09, change_percent=3.5,
                      volume=0, open=65.09, high=65.09, low=65.09, previous_close=62.9,
                      average_volume=1000, relative_volume=0.0, price_basis="tradier_bid_ask_mid")
        tracker.process_sweep({"KEN": quote}, h, "2026-08-19", "premarket", _now())

        deteccion = reg.get_detection("KEN", "2026-08-19")
        assert deteccion is not None
        assert deteccion["direction_at_detection"] == "INDEFINIDA"
    finally:
        _restore()


def test_tag_alert_stage_no_toca_gates_fired_ni_candidate_detection():
    """Confirma explícitamente que la capa observacional no altera nada de
    lo que ya escribía process_sweep antes de esta fase."""
    _fresh()
    try:
        h = SweepHistory()
        result = tracker.process_sweep({"AAPL": _quote("AAPL", 305.0, 5.0, rvol=2.0)}, h, "2026-08-14", "regular", _now())
        candidatas = reg.list_candidates_for_date("2026-08-14")
        assert "AAPL" in result.n_nuevas_detecciones
        assert candidatas[0]["gates_fired"]  # sigue disparando puertas normalmente
        assert "stage" not in candidatas[0]  # alert_stage vive en su propia tabla, no mezclada acá
    finally:
        _restore()


# --- Retroceso desde máximo intradía (2026-08-18, caso real YYAI) ---

def test_caso_real_yyai_pico_y_caida_fuerza_no_perseguir():
    """Reproduce el patrón real de YYAI (2026-08-18): sube a un pico
    ($1,57), después retrocede fuerte ($1,36) -- todavía positiva contra el
    precio base, con timing/dirección que SIN el eje de retroceso darían
    INICIO (se simula fijando from_live_detection, ya testeado aparte en
    test_phase_classifier.py -- acá se prueba el WIRING nuevo end-to-end:
    candidate_tracker calcula el retroceso desde `candidate_observation`
    persistida y se lo pasa a classify_alert_stage, que debe ganarle a
    INICIO)."""
    _fresh()
    try:
        from atlas_live.radar import phase_classifier as pc_module
        from atlas_live.reference import reference_registry as ref_reg

        orig_from_live = tracker.pc.from_live_detection
        orig_vol = ref_reg.latest_volatility_14d_pct
        orig_recent = ref_reg.recent_daily_features
        orig_pct = ref_reg.percentile_change_pct
        # Fija timing="al_comienzo"/direction="ALCISTA" -- SIN el eje de
        # retroceso, esto daría INICIO (ver test_al_comienzo_da_inicio en
        # test_alert_stage.py). El objetivo es probar que el retroceso real
        # calculado del historial persistido le gana a esto.
        tracker.pc.from_live_detection = lambda *a, **k: pc_module.PhaseTag(
            timing_deteccion="al_comienzo", direction="ALCISTA",
            comportamiento_post_apertura="desconocido", reason="test", change_pct_confiable=True,
        )
        ref_reg.latest_volatility_14d_pct = lambda symbol: None
        ref_reg.recent_daily_features = lambda symbol, n=5: []
        ref_reg.percentile_change_pct = lambda symbol, p: None
        try:
            h = SweepHistory()
            # sweep 1: precio base, dispara detección (change_pct >= 3.0%)
            tracker.process_sweep({"YYAI": _quote("YYAI", 1.22, 11.5, rvol=10.0)}, h, "2026-08-18", "regular", _now())
            assert reg.latest_alert_stage("YYAI", "2026-08-18") == "INICIO"  # sin retroceso todavía -- INICIO normal

            # sweep 2: sube a su pico real de hoy
            tracker.process_sweep({"YYAI": _quote("YYAI", 1.57, 28.7, rvol=15.0)}, h, "2026-08-18", "regular", _now())
            assert reg.max_price_today("YYAI", "2026-08-18") == 1.57

            # sweep 3: retrocede fuerte desde el pico (~13.4%) -- sigue
            # positiva contra el precio base, timing/dirección seguirían
            # dando INICIO si no fuera por el retroceso nuevo.
            tracker.process_sweep({"YYAI": _quote("YYAI", 1.36, 11.5, rvol=11.7)}, h, "2026-08-18", "regular", _now())
            assert reg.latest_alert_stage("YYAI", "2026-08-18") == "NO_PERSEGUIR"

            ops = reg.live_opportunities("2026-08-18")
            yyai = next(o for o in ops if o["ticker"] == "YYAI")
            assert yyai["retroceso_desde_maximo_pct"] is not None
            assert yyai["retroceso_desde_maximo_pct"] > 8.0  # por encima del umbral real
        finally:
            tracker.pc.from_live_detection = orig_from_live
            ref_reg.latest_volatility_14d_pct = orig_vol
            ref_reg.recent_daily_features = orig_recent
            ref_reg.percentile_change_pct = orig_pct
    finally:
        _restore()


def test_retroceso_no_se_dispara_si_el_precio_actual_es_el_nuevo_maximo():
    """Un nuevo máximo (o precio estable) nunca puede dar un retroceso
    positivo -- confirma que no se inventa una caída donde no la hay."""
    _fresh()
    try:
        from atlas_live.reference import reference_registry as ref_reg

        orig_vol = ref_reg.latest_volatility_14d_pct
        orig_recent = ref_reg.recent_daily_features
        orig_pct = ref_reg.percentile_change_pct
        ref_reg.latest_volatility_14d_pct = lambda symbol: None
        ref_reg.recent_daily_features = lambda symbol, n=5: []
        ref_reg.percentile_change_pct = lambda symbol, p: None
        try:
            h = SweepHistory()
            tracker.process_sweep({"AAPL": _quote("AAPL", 100.0, 5.0, rvol=3.0)}, h, "2026-08-18", "regular", _now())
            tracker.process_sweep({"AAPL": _quote("AAPL", 105.0, 5.0, rvol=3.0)}, h, "2026-08-18", "regular", _now())
            ops = reg.live_opportunities("2026-08-18")
            aapl = next(o for o in ops if o["ticker"] == "AAPL")
            assert aapl["retroceso_desde_maximo_pct"] is None
        finally:
            ref_reg.latest_volatility_14d_pct = orig_vol
            ref_reg.recent_daily_features = orig_recent
            ref_reg.percentile_change_pct = orig_pct
    finally:
        _restore()


# --- Predicción de magnitud (2026-08-20, aprobado por el usuario) ---

def _mock_evidencia_real(direction="ALCISTA", timing="al_comienzo", mediana=30.0, n=34):
    """Tabla de referencia sintética controlada, mismo estilo que
    test_historical_scoring.py -- todas las filas con el mismo
    max_advance_pct para que la mediana sea exactamente ese valor."""
    rows = [
        {"direction": direction, "timing_deteccion": timing,
         "volatility_14d_pct": float(v), "max_advance_pct": mediana}
        for v in range(1, n + 1)
    ]
    return tracker.hsc.compute_reference_table(rows, ["volatility_14d_pct"], min_rows=30)


def test_candidata_llega_a_inicio_congela_prediccion_de_magnitud_real():
    _fresh()
    try:
        from atlas_live.radar import phase_classifier as pc_module
        from atlas_live.reference import reference_registry as ref_reg

        orig_from_live = tracker.pc.from_live_detection
        orig_vol = ref_reg.latest_volatility_14d_pct
        orig_recent = ref_reg.recent_daily_features
        orig_pct = ref_reg.percentile_change_pct
        orig_table = tracker.hsc.get_cached_reference_table

        tracker.pc.from_live_detection = lambda *a, **k: pc_module.PhaseTag(
            timing_deteccion="al_comienzo", direction="ALCISTA",
            comportamiento_post_apertura="desconocido", reason="test", change_pct_confiable=True,
        )
        ref_reg.latest_volatility_14d_pct = lambda symbol: None
        ref_reg.recent_daily_features = lambda symbol, n=5: []
        ref_reg.percentile_change_pct = lambda symbol, p: None
        tabla = _mock_evidencia_real(mediana=30.0)
        tracker.hsc.get_cached_reference_table = lambda: tabla
        try:
            h = SweepHistory()
            tracker.process_sweep({"MRNA": _quote("MRNA", 65.0, 5.0, rvol=8.0)}, h, "2026-08-19", "regular", _now())
            assert reg.latest_alert_stage("MRNA", "2026-08-19") == "INICIO"

            pred = reg.get_magnitud_prediction("MRNA", "2026-08-19")
            assert pred is not None
            assert pred["predicted_pct"] == 30.0
            assert pred["estado_final_al_congelar"] == "OPORTUNIDAD_PRIORITARIA"
            assert pred["direction"] == "ALCISTA"

            # segundo barrido, mismo día -- aunque la etapa cambie, la
            # predicción congelada NUNCA se pisa (write-once).
            tracker.process_sweep({"MRNA": _quote("MRNA", 90.0, 45.0, rvol=20.0)}, h, "2026-08-19", "regular", _now())
            pred2 = reg.get_magnitud_prediction("MRNA", "2026-08-19")
            assert pred2["predicted_pct"] == 30.0
        finally:
            tracker.pc.from_live_detection = orig_from_live
            ref_reg.latest_volatility_14d_pct = orig_vol
            ref_reg.recent_daily_features = orig_recent
            ref_reg.percentile_change_pct = orig_pct
            tracker.hsc.get_cached_reference_table = orig_table
    finally:
        _restore()


def test_candidata_en_preparacion_no_congela_prediccion_sin_evidencia_accionable():
    _fresh()
    try:
        from atlas_live.radar import phase_classifier as pc_module
        from atlas_live.reference import reference_registry as ref_reg

        orig_from_live = tracker.pc.from_live_detection
        orig_vol = ref_reg.latest_volatility_14d_pct
        orig_recent = ref_reg.recent_daily_features
        orig_pct = ref_reg.percentile_change_pct
        orig_table = tracker.hsc.get_cached_reference_table

        # "antes_del_movimiento" + volatilidad elevada -> PREPARACION (no
        # accionable) -- ver alert_stage.py. Nunca debería congelar nada.
        tracker.pc.from_live_detection = lambda *a, **k: pc_module.PhaseTag(
            timing_deteccion="antes_del_movimiento", direction="NEUTRAL",
            comportamiento_post_apertura="desconocido", reason="test", change_pct_confiable=True,
        )
        ref_reg.latest_volatility_14d_pct = lambda symbol: 15.0
        ref_reg.recent_daily_features = lambda symbol, n=5: []
        ref_reg.percentile_change_pct = lambda symbol, p: None
        tracker.hsc.get_cached_reference_table = lambda: _mock_evidencia_real(direction="NEUTRAL", timing="antes_del_movimiento")
        try:
            h = SweepHistory()
            tracker.process_sweep({"ZZZZ": _quote("ZZZZ", 10.0, 4.0, rvol=1.0)}, h, "2026-08-19", "regular", _now())
            assert reg.latest_alert_stage("ZZZZ", "2026-08-19") == "PREPARACION"
            assert reg.get_magnitud_prediction("ZZZZ", "2026-08-19") is None
        finally:
            tracker.pc.from_live_detection = orig_from_live
            ref_reg.latest_volatility_14d_pct = orig_vol
            ref_reg.recent_daily_features = orig_recent
            ref_reg.percentile_change_pct = orig_pct
            tracker.hsc.get_cached_reference_table = orig_table
    finally:
        _restore()


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
