"""Tests de la evaluación de cierre (2026-08-14). Fake de Tradier (duck-typed), DB temporal, sin red real."""

import tempfile
import uuid as _uuid
from pathlib import Path

import pandas as pd

from atlas.data.models.quote import Quote
from atlas_live.radar import candidate_registry as reg
from atlas_live.radar import eod_report as eod

_ORIG = reg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_eod_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None


def _restore():
    reg.DB_PATH = _ORIG


def _df(prices, start="2026-08-14T13:30:00Z"):
    idx = pd.date_range(start=start, periods=len(prices), freq="1min", tz="UTC")
    return pd.DataFrame({"Open": prices, "High": prices, "Low": prices, "Close": prices,
                          "Volume": [1000] * len(prices), "VWAP": prices}, index=idx)


class _FakeTradier:
    """Duck-typed: solo implementa get_intraday_timesales, con datos fijados por símbolo."""

    def __init__(self, dfs_by_symbol):
        self._dfs = dfs_by_symbol

    def get_intraday_timesales(self, symbol, interval="1min", session_filter="all", start=None, end=None):
        return self._dfs.get(symbol, pd.DataFrame())


def test_categorize_falsa_senal():
    assert eod._categorize(5.0, 1.0) == "falsa_senal"


def test_categorize_deteccion_tardia():
    # ya venía +80% antes de detectarla, y después solo le quedó +10% -- detección tardía
    assert eod._categorize(80.0, 10.0) == "deteccion_tardia"


def test_categorize_mejor_oportunidad():
    assert eod._categorize(2.0, 60.0) == "mejor_oportunidad"


def test_evaluate_outcome_calcula_maximo_posterior_correctamente():
    # detectada a las 13:32 (precio 10.5); el pico real (15) llega DESPUÉS,
    # a las 13:35 -- separado de la vela de detección para que sea inequívoco
    # cuál parte es "posterior" (la implementación excluye la vela exacta de
    # detección con `>` estricto, a propósito -- ver docstring del módulo).
    prices = [10, 10.2, 10.5, 11, 12, 15, 14, 13]  # velas 13:30..13:37
    provider = _FakeTradier({"XYZ": _df(prices)})
    outcome = eod.evaluate_candidate_outcome("XYZ", "2026-08-14T13:32:00Z", 10.5, 3.0, provider)
    assert outcome.max_return_after_detection_pct == round(100 * (15 - 10.5) / 10.5, 3)
    assert outcome.reached_20 is True
    assert outcome.reached_50 is False
    assert outcome.category in ("buena_oportunidad", "mejor_oportunidad")


def test_evaluate_outcome_sin_velas_posteriores():
    prices = [10, 10.1]
    provider = _FakeTradier({"XYZ": _df(prices, start="2026-08-14T13:00:00Z")})
    # detectada DESPUÉS de todas las velas disponibles
    outcome = eod.evaluate_candidate_outcome("XYZ", "2026-08-14T20:00:00Z", 10.0, 3.0, provider)
    assert outcome.category == "falsa_senal"
    assert outcome.max_return_after_detection_pct == 0.0


def test_run_eod_evaluation_completo_e_idempotente():
    _fresh()
    try:
        reg.record_detection("XYZ", "2026-08-14", "regular", "2026-08-14T13:32:00Z", "s1",
                              10.5, 3.0, 1000, 500, 2.0, 10000, gates_fired=[{"name": "cambio_de_precio"}])
        prices = [10, 10.2, 10.5, 11, 12, 15, 14, 13]
        provider = _FakeTradier({"XYZ": _df(prices)})
        report1 = eod.run_eod_evaluation("2026-08-14", provider)
        assert report1.n_candidatas == 1
        assert report1.n_evaluadas == 1
        assert report1.n_reached_20 == 1
        assert len(report1.mejores_oportunidades) == 1

        # segunda corrida -- idempotente, no vuelve a evaluar ni duplica
        report2 = eod.run_eod_evaluation("2026-08-14", provider)
        assert report2.n_evaluadas == 0  # ya tenía outcome
        assert reg.has_outcome("XYZ", "2026-08-14")
    finally:
        _restore()


def test_run_eod_evaluation_detecta_posibles_no_detectadas():
    _fresh()
    try:
        reg.record_detection("XYZ", "2026-08-14", "regular", "2026-08-14T13:35:00Z", "s1",
                              10.0, 3.0, 1000, 500, 2.0, 10000, gates_fired=[])
        provider = _FakeTradier({"XYZ": _df([10, 10.1])})
        ultimo_barrido = {
            "XYZ": Quote(symbol="XYZ", name="XYZ", last_price=10.1, change_percent=3.0, volume=1000,
                         open=10, high=10.5, low=9.9, previous_close=9.7),
            "MOONSHOT": Quote(symbol="MOONSHOT", name="MOONSHOT", last_price=50.0, change_percent=45.0,
                               volume=5000, open=35, high=51, low=34, previous_close=34.5),
        }
        report = eod.run_eod_evaluation("2026-08-14", provider, last_sweep_quotes=ultimo_barrido)
        assert any(m["ticker"] == "MOONSHOT" for m in report.posibles_no_detectadas)
        assert not any(m["ticker"] == "XYZ" for m in report.posibles_no_detectadas)  # XYZ SÍ fue detectada
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
