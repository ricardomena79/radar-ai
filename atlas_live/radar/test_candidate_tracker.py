"""Tests del orquestador de barrido (2026-08-14). DB temporal, Quotes falsas, sin red."""

import tempfile
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

from atlas.data.models.quote import Quote
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
