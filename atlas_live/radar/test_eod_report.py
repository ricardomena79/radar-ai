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

    def __init__(self, dfs_by_symbol, broken_symbols=None):
        self._dfs = dfs_by_symbol
        # Reproduce el bug real (2026-08-17): Tradier devuelve `{"series": null}`
        # para algunos símbolos y `get_intraday_timesales` explota con
        # AttributeError -- una excepción que `evaluate_candidate_outcome` NO
        # atrapa (solo atrapa QuoteNotFoundError/ProviderError).
        self._broken = set(broken_symbols or ())

    def get_intraday_timesales(self, symbol, interval="1min", session_filter="all", start=None, end=None):
        if symbol in self._broken:
            raise AttributeError("'NoneType' object has no attribute 'get'")
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

        # segunda corrida -- idempotente, no vuelve a evaluar ni duplica.
        # n_evaluadas ahora refleja el TOTAL de outcomes del día (para que
        # el resumen sea correcto aunque la corrida se reanude a medias),
        # así que sigue en 1 -- lo que importa es que NO se duplicó la fila.
        report2 = eod.run_eod_evaluation("2026-08-14", provider)
        assert report2.n_evaluadas == 1
        assert reg.has_outcome("XYZ", "2026-08-14")
        assert len(reg.list_outcomes_for_date("2026-08-14")) == 1  # no se duplicó
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

        # "Que Atlas aprenda" (2026-08-19): antes esto se perdía al terminar
        # la corrida -- ahora queda persistido, consultable después.
        guardados = reg.list_missed_movers("2026-08-14")
        assert [m["ticker"] for m in guardados] == ["MOONSHOT"]
        assert guardados[0]["change_pct_final"] == 45.0
    finally:
        _restore()


def test_direccion_correcta_y_resumen_diario():
    _fresh()
    try:
        reg.record_detection("XYZ", "2026-08-14", "regular", "2026-08-14T13:32:00Z", "s1",
                              10.5, 3.0, 1000, 500, 2.0, 10000, gates_fired=[{"name": "cambio_de_precio"}])
        reg.set_phase_tag("XYZ", "2026-08-14", "al_comienzo", direction_at_detection="ALCISTA")
        # velas posteriores suben con fuerza -- outcome también debería ser ALCISTA
        prices = [10, 10.2, 10.5, 11, 12, 15, 14, 13]
        provider = _FakeTradier({"XYZ": _df(prices)})
        report = eod.run_eod_evaluation("2026-08-14", provider, n_estudiadas=2575)

        assert report.n_estudiadas == 2575
        assert report.n_direccion_correcta == 1
        assert report.n_direccion_incorrecta == 0

        resumen = reg.get_daily_summary("2026-08-14")
        assert resumen is not None
        assert resumen["n_estudiadas"] == 2575
        assert resumen["n_candidatas"] == 1

        acumulado = reg.cumulative_precision()
        assert acumulado["n_dias"] == 1
        assert acumulado["precision_pct"] is not None
    finally:
        _restore()


def test_evaluate_outcome_desglose_por_tramo_dia():
    """Aprendizaje unificado (2026-08-18, pedido explícito del usuario):
    los 4 tramos (antes de detección ya existía; premarket-después-de-
    detección, post-apertura, total del día son nuevos) deben calcularse
    de las MISMAS velas ya pedidas, sin llamadas de red nuevas."""
    idx = pd.to_datetime([
        "2026-08-14T12:00:00Z",  # vela de detección (excluida, > estricto)
        "2026-08-14T12:01:00Z",
        "2026-08-14T12:02:00Z",  # máximo premarket después de detección
        "2026-08-14T13:29:00Z",
        "2026-08-14T13:30:00Z",  # apertura regular (09:30 ET)
        "2026-08-14T13:31:00Z",
        "2026-08-14T13:32:00Z",  # máximo de la sesión regular
        "2026-08-14T13:33:00Z",
    ])
    opens = [10.0, 10.4, 10.9, 10.7, 11.2, 11.9, 13.0, 12.8]
    highs = [10.0, 10.5, 11.0, 10.8, 11.5, 12.5, 13.5, 13.0]
    lows = [10.0, 10.3, 10.8, 10.6, 11.0, 11.8, 12.9, 12.5]
    closes = [10.0, 10.45, 10.95, 10.75, 11.4, 12.4, 13.2, 12.7]
    df = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes,
                        "Volume": [1000] * len(opens), "VWAP": closes}, index=idx)
    provider = _FakeTradier({"XYZ": df})

    outcome = eod.evaluate_candidate_outcome("XYZ", "2026-08-14T12:00:00Z", 10.0, 0.0, provider)

    assert outcome.max_price_premarket_after_detection == 11.0
    assert outcome.max_return_premarket_after_detection_pct == round(100 * (11.0 - 10.0) / 10.0, 3)
    assert outcome.price_at_market_open == 11.2
    assert outcome.max_price_regular_session == 13.5
    assert outcome.max_return_post_apertura_pct == round(100 * (13.5 - 11.2) / 11.2, 3)
    assert outcome.total_day_change_pct == round(100 * (12.7 - 10.0) / 10.0, 3)
    # Cierre real del día vs. precio de detección (2026-08-23, caso real
    # MRNX: tocó +44,3% intradía pero cerró en +17,8% -- "eso no es
    # acierto") -- MISMA base que max_return_after_detection_pct, pero
    # contra el último Close del día, no el máximo intradía.
    assert outcome.close_price_after_detection == 12.7
    assert outcome.close_return_after_detection_pct == round(100 * (12.7 - 10.0) / 10.0, 3)


def test_evaluate_outcome_sin_apertura_deja_tramos_post_apertura_en_none():
    # detección ya después de todas las velas disponibles del día -- no hay
    # ventana premarket ni regular que calcular, deben quedar en None, no 0.
    prices = [10, 10.1]
    provider = _FakeTradier({"XYZ": _df(prices, start="2026-08-14T13:00:00Z")})
    outcome = eod.evaluate_candidate_outcome("XYZ", "2026-08-14T20:00:00Z", 10.0, 3.0, provider)
    assert outcome.price_at_market_open is None
    assert outcome.max_price_regular_session is None
    assert outcome.max_return_post_apertura_pct is None
    assert outcome.max_price_premarket_after_detection is None


def test_eod_reemplaza_resultado_en_curso_no_final():
    """Fase 4 del aprendizaje unificado -- un resultado 'en curso'
    (is_final=False, como el que produce compute_interim_outcome durante
    el día) nunca debe bloquear al EOD: has_final_outcome (no has_outcome)
    es el chequeo correcto, y el EOD debe REEMPLAZARLO por el oficial."""
    _fresh()
    try:
        reg.record_detection("XYZ", "2026-08-14", "regular", "2026-08-14T13:32:00Z", "s1",
                              10.5, 3.0, 1000, 500, 2.0, 10000, gates_fired=[{"name": "cambio_de_precio"}])
        reg.record_outcome("XYZ", "2026-08-14", run_up_before_detection_pct=3.0,
                            max_price_after_detection=11.0, max_return_after_detection_pct=4.76,
                            minutes_to_max=None, reached_20=False, reached_50=False, reached_100=False,
                            category="EN_CURSO", is_final=False)
        assert reg.has_outcome("XYZ", "2026-08-14")
        assert not reg.has_final_outcome("XYZ", "2026-08-14")

        prices = [10, 10.2, 10.5, 11, 12, 15, 14, 13]
        provider = _FakeTradier({"XYZ": _df(prices)})
        report = eod.run_eod_evaluation("2026-08-14", provider)

        assert report.n_evaluadas == 1
        assert reg.has_final_outcome("XYZ", "2026-08-14")
        outcome = reg.get_outcome("XYZ", "2026-08-14")
        assert outcome["is_final"] == 1
        assert outcome["max_return_after_detection_pct"] == round(100 * (15 - 10.5) / 10.5, 3)
        assert len(reg.list_outcomes_for_date("2026-08-14")) == 1  # nunca duplicó la fila
    finally:
        _restore()


def test_eod_marca_confiable_para_aprendizaje_segun_dollar_volume():
    """Filtro de calidad (2026-08-18, pedido explícito del usuario, umbral
    evidencia-basado de $50.000): el EOD debe clasificar cada resultado con
    classify_learning_quality y guardarlo -- nunca se excluye la detección
    en sí, solo se marca como no confiable para las estadísticas."""
    _fresh()
    try:
        reg.record_detection("SUS", "2026-08-14", "regular", "2026-08-14T13:32:00Z", "s1",
                              10.5, 3.0, 1000, 500, 2.0, 100, gates_fired=[{"name": "despertar"}])
        reg.record_detection("REAL", "2026-08-14", "regular", "2026-08-14T13:32:00Z", "s1",
                              10.5, 3.0, 1000, 500, 2.0, 10_000_000, gates_fired=[{"name": "volumen_relativo"}])
        prices = [10, 10.2, 10.5, 11, 12, 15, 14, 13]
        provider = _FakeTradier({"SUS": _df(prices), "REAL": _df(prices)})
        eod.run_eod_evaluation("2026-08-14", provider)

        sus = reg.get_outcome("SUS", "2026-08-14")
        real = reg.get_outcome("REAL", "2026-08-14")
        assert sus["confiable_para_aprendizaje"] == 0
        assert "dinero_insuficiente" in sus["motivos_sospecha"]
        assert real["confiable_para_aprendizaje"] == 1
        assert real["motivos_sospecha"] == []

        # ambos siguen apareciendo en la lista cruda (nunca se ocultan) --
        # solo el filtro por defecto de las estadísticas los separa.
        assert len(reg.list_all_evaluated_candidates(solo_confiables=False)) == 2
        confiables = reg.list_all_evaluated_candidates(solo_confiables=True)
        assert [c["ticker"] for c in confiables] == ["REAL"]
    finally:
        _restore()


def test_eod_error_evaluacion_nunca_cuenta_como_confiable():
    _fresh()
    try:
        reg.record_detection("AETH", "2026-08-14", "regular", "2026-08-14T13:31:00Z", "s1",
                              29.77, 0.0, 1000, 500, 1.0, 10000, gates_fired=[{"name": "cambio_de_comportamiento"}])
        provider = _FakeTradier({}, broken_symbols={"AETH"})
        eod.run_eod_evaluation("2026-08-14", provider)

        outcome = reg.get_outcome("AETH", "2026-08-14")
        assert outcome["is_final"] == 1
        assert outcome["confiable_para_aprendizaje"] == 0
        assert outcome["motivos_sospecha"] == ["error_evaluacion_eod"]
    finally:
        _restore()


def test_ticker_roto_no_detiene_el_lote_y_queda_marcado_como_fallido():
    """Bug real de producción (2026-08-17, sesión del 17-Ago): AETH -> Tradier
    `{"series": null}` -> AttributeError sin capturar tumbaba TODA la
    evaluación de las 2.399 candidatas y quedaba reintentando el mismo
    ticker para siempre. Reproduce el caso mínimo: dos candidatas, una rota
    (AETH) entre dos sanas -- el lote debe evaluar las dos sanas igual,
    marcar AETH como fallida (no perdida en silencio, no reintentada) y
    nunca lanzar la excepción hacia el llamador."""
    _fresh()
    try:
        reg.record_detection("AAA", "2026-08-14", "regular", "2026-08-14T13:30:00Z", "s1",
                              10.0, 3.0, 1000, 500, 2.0, 10000, gates_fired=[{"name": "cambio_de_precio"}])
        reg.record_detection("AETH", "2026-08-14", "regular", "2026-08-14T13:31:00Z", "s1",
                              29.77, 0.0, 1000, 500, 1.0, 10000, gates_fired=[{"name": "cambio_de_comportamiento"}])
        reg.record_detection("ZZZ", "2026-08-14", "regular", "2026-08-14T13:32:00Z", "s1",
                              10.0, 3.0, 1000, 500, 2.0, 10000, gates_fired=[{"name": "cambio_de_precio"}])

        prices = [10, 10.2, 10.5, 11, 12, 15, 14, 13]
        provider = _FakeTradier(
            {"AAA": _df(prices, start="2026-08-14T13:30:00Z"),
             "ZZZ": _df(prices, start="2026-08-14T13:32:00Z")},
            broken_symbols={"AETH"},
        )

        report = eod.run_eod_evaluation("2026-08-14", provider)  # nunca debe lanzar

        assert report.n_candidatas == 3
        assert report.n_evaluadas == 3  # las 3 tienen fila en candidate_outcome, incluida la rota

        assert reg.has_outcome("AAA", "2026-08-14")
        assert reg.has_outcome("AETH", "2026-08-14")
        assert reg.has_outcome("ZZZ", "2026-08-14")

        outcomes = {o["ticker"]: o for o in reg.list_outcomes_for_date("2026-08-14")}
        assert outcomes["AAA"]["category"] != "error_evaluacion"
        assert outcomes["ZZZ"]["category"] != "error_evaluacion"
        assert outcomes["AETH"]["category"] == "error_evaluacion"
        assert "AttributeError" in outcomes["AETH"]["notes"]
        assert outcomes["AETH"]["max_return_after_detection_pct"] is None
        assert outcomes["AETH"]["reached_20"] == 0

        assert any(e.startswith("AETH: error inesperado") for e in report.errores)

        # segunda corrida -- AETH NO se reintenta (idempotente vía has_outcome),
        # nunca queda reprocesándose para siempre.
        report2 = eod.run_eod_evaluation("2026-08-14", provider)
        assert report2.n_evaluadas == 3
        assert len(reg.list_outcomes_for_date("2026-08-14")) == 3  # sigue sin duplicar
    finally:
        _restore()


# --- Backfill de close_return_after_detection_pct (2026-08-23) ---

def test_backfill_close_return_recalcula_solo_lo_que_falta():
    """Caso real MRNX/viernes: outcomes ya calculados ANTES de que
    existiera close_return_after_detection_pct (max_return_after_detection_pct
    ya correcto, close en None) -- el backfill lo completa sin tocar nada
    más."""
    _fresh()
    try:
        reg.record_detection("XYZ", "2026-08-21", "premarket", "2026-08-21T10:00:00Z", "s1",
                              10.0, 3.0, 1000, 500, 2.0, 10000, gates_fired=[{"name": "cambio_de_precio"}])
        # Outcome "viejo": is_final=True, con max_return_after_detection_pct
        # correcto pero SIN el campo nuevo -- exactamente el estado real de
        # las candidatas del viernes tras el deploy del criterio de cierre.
        reg.record_outcome(
            "XYZ", "2026-08-21", run_up_before_detection_pct=3.0,
            max_price_after_detection=15.0, max_return_after_detection_pct=50.0,
            minutes_to_max=20.0, reached_20=True, reached_50=True, reached_100=False,
            category="mejor_oportunidad", is_final=True, confiable_para_aprendizaje=True,
        )
        reg.record_magnitud_prediction("XYZ", "2026-08-21", "2026-08-21T10:05:00Z", 20.0)

        prices = [10, 10.2, 10.5, 11, 12, 15, 14, 13]  # cierra en 13 -> +30% desde detección
        provider = _FakeTradier({"XYZ": _df(prices, start="2026-08-21T10:00:00Z")})
        resultado = eod.backfill_close_return("2026-08-21", provider)

        assert resultado["n_predicciones"] == 1
        assert resultado["n_actualizadas"] == 1
        assert resultado["n_errores"] == 0

        outcome = reg.get_outcome("XYZ", "2026-08-21")
        assert outcome["close_price_after_detection"] == 13.0
        assert outcome["close_return_after_detection_pct"] == round(100 * (13.0 - 10.0) / 10.0, 3)
        # Nada más se tocó -- sigue siendo el mismo resultado ya correcto.
        assert outcome["max_return_after_detection_pct"] == 50.0
        assert outcome["category"] == "mejor_oportunidad"
        assert outcome["reached_20"] == 1
    finally:
        _restore()


def test_backfill_close_return_salta_los_que_ya_lo_tienen_sin_gastar_llamadas():
    """Idempotente: un ticker que ya tiene close_return_after_detection_pct
    no vuelve a pedir velas -- si lo hiciera, `_FakeTradier` con el símbolo
    marcado "broken" lanzaría una excepción y el test fallaría."""
    _fresh()
    try:
        reg.record_detection("YA_LISTO", "2026-08-21", "regular", "2026-08-21T13:32:00Z", "s1",
                              10.0, 3.0, 1000, 500, 2.0, 10000, gates_fired=[{"name": "cambio_de_precio"}])
        reg.record_outcome(
            "YA_LISTO", "2026-08-21", run_up_before_detection_pct=3.0,
            max_price_after_detection=15.0, max_return_after_detection_pct=50.0,
            minutes_to_max=20.0, reached_20=True, reached_50=True, reached_100=False,
            category="mejor_oportunidad", is_final=True, confiable_para_aprendizaje=True,
            close_price_after_detection=12.0, close_return_after_detection_pct=20.0,
        )
        reg.record_magnitud_prediction("YA_LISTO", "2026-08-21", "2026-08-21T10:05:00Z", 15.0)

        provider = _FakeTradier({}, broken_symbols=["YA_LISTO"])
        resultado = eod.backfill_close_return("2026-08-21", provider)

        assert resultado["n_actualizadas"] == 0
        assert resultado["n_saltadas_ya_tenian"] == 1
        assert resultado["n_errores"] == 0
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
