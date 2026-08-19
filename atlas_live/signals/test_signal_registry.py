"""Tests del registro de señales (2026-08-09). DB SQLite temporal, sintética,
nunca la real. Offline y determinista.
"""

import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.signals import signal_registry as reg

_ORIG = reg.DB_PATH


def _fresh():
    # Archivo único por test: en Windows el -wal/-shm de SQLite puede quedar
    # bloqueado un instante tras cerrar la conexión; usar un path nuevo evita
    # tener que borrar un archivo "en uso" entre tests.
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_signal_registry_{_uuid.uuid4().hex}.db"


def _restore():
    reg.DB_PATH = _ORIG


_FEATURES = {"gap_pct": 12.0, "relative_volume": 3.5, "dollar_volume": 2e7,
             "volatility_score": 80.0, "market_cap": 3e8, "price": 4.82}


def _register(ticker="NUWE", date="2026-08-10", session="PREMARKET", at="2026-08-10T12:57:00+00:00"):
    return reg.register_signal(
        ticker=ticker, market_date=date, session=session, detected_at=at,
        price_at_detection=4.82, price_as_of=at, provider="yahoo_finance",
        features=_FEATURES, score=71.0, reasons=["gap alto", "RVOL alto"],
        conditions=["price", "liquidity", "rvol"],
        historical_group="similar a A/B (premarket fuerte)", similar_historical_cases=20,
        detector_version="0.1.0", feature_version="0.1.0", data_version="0.1.0",
    )


def test_crear_senal_y_campos():
    _fresh()
    try:
        s = _register()
        assert s["created"] is True
        assert s["ticker"] == "NUWE" and s["session"] == "PREMARKET"
        assert s["state"] == reg.DETECTADA
        assert s["features"]["gap_pct"] == 12.0  # features guardadas
        assert s["price_at_detection"] == 4.82
        assert s["provider"] == "yahoo_finance"  # provider guardado
        assert s["detected_at"] == "2026-08-10T12:57:00+00:00"  # timestamp real
        assert s["detector_version"] == "0.1.0"  # versionado
    finally:
        _restore()


def test_deduplicacion_y_preservar_deteccion():
    _fresh()
    try:
        s1 = _register()
        # segundo intento misma oportunidad (ticker+fecha), features distintas:
        # NO crea otra ni reescribe la detección original.
        s2 = reg.register_signal(
            ticker="NUWE", market_date="2026-08-10", session="REGULAR",
            detected_at="2026-08-10T13:35:00+00:00", price_at_detection=9.9,
            price_as_of="x", provider="finnhub", features={"gap_pct": 99.0},
            score=10.0, reasons=None, conditions=None, historical_group=None,
            similar_historical_cases=None, detector_version="0.1.0",
            feature_version="0.1.0", data_version="0.1.0",
        )
        assert s2["created"] is False
        assert s2["signal_uuid"] == s1["signal_uuid"]
        assert s2["price_at_detection"] == 4.82  # detección original intacta
        assert s2["session"] == "PREMARKET"      # no reescrita
        assert reg.count_signals() == 1
    finally:
        _restore()


def test_senal_no_tiene_columnas_de_resultado():
    # Anti-leakage estructural: la tabla signals no puede contener resultado.
    _fresh()
    try:
        _register()
        import sqlite3
        c = sqlite3.connect(reg.DB_PATH)
        cols = {r[1] for r in c.execute("PRAGMA table_info(signals)")}
        prohibidos = {"max_return_pct", "result", "future_price", "return_at_30pct", "acierto"}
        assert cols.isdisjoint(prohibidos), cols & prohibidos
    finally:
        _restore()


def test_observaciones_y_estado_observando():
    _fresh()
    try:
        s = _register()
        assert reg.record_observation(s["signal_uuid"], "2026-08-10T12:57:00+00:00", 5.0) is True
        # duplicado por (uuid, observed_at) -> no inserta
        assert reg.record_observation(s["signal_uuid"], "2026-08-10T12:57:00+00:00", 5.0) is False
        reg.record_observation(s["signal_uuid"], "2026-08-10T13:00:00+00:00", 12.0)
        assert reg.get_signal(s["signal_uuid"])["state"] == reg.OBSERVANDO
        assert len(reg.get_observations(s["signal_uuid"])) == 2
    finally:
        _restore()


def test_resultado_en_tabla_separada_y_write_once():
    _fresh()
    try:
        s = _register()
        reg.record_result(s["signal_uuid"], "2026-08-10T21:00:00+00:00", reg.RESUELTA_ACIERTO,
                          max_return_pct=45.0, result="ACIERTO", minutes_to_30pct=25.0)
        r = reg.get_result(s["signal_uuid"])
        assert r["max_return_pct"] == 45.0 and r["result"] == "ACIERTO"
        assert reg.get_signal(s["signal_uuid"])["state"] == reg.RESUELTA_ACIERTO
        # write-once: no se sobrescribe un desenlace
        try:
            reg.record_result(s["signal_uuid"], "x", reg.RESUELTA_FALLO, result="FALLO")
            assert False, "debió levantar AlreadyResolvedError"
        except reg.AlreadyResolvedError:
            pass
    finally:
        _restore()


def test_transicion_invalida():
    _fresh()
    try:
        s = _register()
        reg.set_state(s["signal_uuid"], reg.RESUELTA_ACIERTO)
        # RESUELTA_* es terminal: no puede volver a OBSERVANDO
        try:
            reg.set_state(s["signal_uuid"], reg.OBSERVANDO)
            assert False, "debió levantar InvalidTransitionError"
        except reg.InvalidTransitionError:
            pass
    finally:
        _restore()


def test_listados_active_y_results():
    _fresh()
    try:
        a = _register("AAA", "2026-08-10")
        b = _register("BBB", "2026-08-10")
        reg.record_result(b["signal_uuid"], "2026-08-10T21:00:00+00:00", reg.RESUELTA_FALLO,
                          max_return_pct=8.0, result="FALLO")
        activos = {x["ticker"] for x in reg.list_active()}
        resultados = {x["ticker"] for x in reg.list_results()}
        assert "AAA" in activos and "BBB" not in activos
        assert "BBB" in resultados
    finally:
        _restore()


def test_list_results_incluye_direction_desde_features():
    """2026-08-18, pedido explícito del usuario: `direction` (de
    `features.direction`, calculado en `explosive_engine.py`) debe quedar
    disponible en `list_results()` para poder ver si un FALLO ya venía
    cayendo desde la detección -- puramente informativo, no cambia el
    resultado ni ningún criterio existente."""
    _fresh()
    try:
        alcista = reg.register_signal(
            ticker="UP", market_date="2026-08-17", session="PREMARKET", detected_at="t1",
            price_at_detection=10.0, price_as_of="t1", provider="yahoo_finance",
            features={**_FEATURES, "direction": "ALCISTA"}, score=71.0, reasons=None,
            conditions=None, historical_group=None, similar_historical_cases=None,
            detector_version="0.1.0", feature_version="0.1.0", data_version="0.1.0",
        )
        bajista = reg.register_signal(
            ticker="DOWN", market_date="2026-08-17", session="PREMARKET", detected_at="t2",
            price_at_detection=10.0, price_as_of="t2", provider="yahoo_finance",
            features={**_FEATURES, "direction": "BAJISTA"}, score=71.0, reasons=None,
            conditions=None, historical_group=None, similar_historical_cases=None,
            detector_version="0.1.0", feature_version="0.1.0", data_version="0.1.0",
        )
        reg.record_result(alcista["signal_uuid"], "t1r", reg.RESUELTA_ACIERTO,
                          max_return_pct=45.0, result="ACIERTO")
        reg.record_result(bajista["signal_uuid"], "t2r", reg.RESUELTA_FALLO,
                          max_return_pct=-6.5, result="FALLO")

        resultados = {r["ticker"]: r for r in reg.list_results()}
        assert resultados["UP"]["direction"] == "ALCISTA"
        assert resultados["DOWN"]["direction"] == "BAJISTA"
        assert "features_json" not in resultados["UP"]  # no filtra el crudo, solo el campo limpio
    finally:
        _restore()


def test_persistencia_reconexion():
    _fresh()
    try:
        s = _register()
        # "reinicio": nueva conexión implícita en cada llamada -> sigue ahí
        assert reg.get_signal(s["signal_uuid"]) is not None
        assert reg.count_signals() == 1
    finally:
        _restore()


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__); p += 1
        except Exception as e:
            print("FAIL", fn.__name__, e); traceback.print_exc(); f += 1
    print(f"--- {p} passed, {f} failed ---")
