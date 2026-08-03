"""Pruebas de la integración en tiempo real. Usa la evidencia REAL del
Memory Store (ya validada en los Entregables 4-6) pero un `results`
sintético con la misma forma que produce `scan_worker.py`, y un
`DataCollector` falso para la calificación al cierre -- nunca golpea la
red real. Uso: `python -m atlas_live.memory.test_live_integration`
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from atlas_live.memory import exit_journal as ej
from atlas_live.memory import live_integration as li
from atlas_live.memory import prediction_journal as pj

ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def _row(symbol, eligible=False, score=None, **metrics):
    defaults = dict(price=10.0, gap_pct=0.0, change_pct=0.0, relative_volume=0.5,
                     dollar_volume=3_000_000, volatility_score=50.0, market_cap=500_000_000)
    defaults.update(metrics)
    return {"symbol": symbol, "explosive": {"eligible": eligible, "score": score, "metrics": defaults}}


SYNTHETIC_RESULTS = [
    _row("FUERTE1", eligible=True, score=80.0, gap_pct=12.0, relative_volume=15.0, volatility_score=95.0),
    _row("FUERTE2", eligible=True, score=70.0, gap_pct=11.0, relative_volume=12.0, volatility_score=92.0),
    _row("DEBIL1", eligible=False, gap_pct=0.5, relative_volume=0.4),
    _row("DEBIL2", eligible=False, gap_pct=-1.0, relative_volume=0.3),
]


class _FakeQuote:
    def __init__(self, change_percent):
        self.change_percent = change_percent


class _FakeCollector:
    """Reemplaza a DataCollector(YahooFinanceProvider()) en la calificación
    -- nunca llama a la red real."""

    RESULTS = {"FUERTE1": 45.0, "FUERTE2": -3.0}

    def get_quote(self, symbol):
        if symbol not in self.RESULTS:
            raise KeyError(symbol)
        return _FakeQuote(self.RESULTS[symbol])


def _reset_db():
    for db_path in (pj.DB_PATH, ej.DB_PATH):
        if os.path.exists(db_path):
            os.remove(db_path)
        for ext in ("-wal", "-shm"):
            p = str(db_path) + ext
            if os.path.exists(p):
                os.remove(p)


def test_build_live_ranking_ordena_por_evidencia() -> None:
    ranking = li.build_live_ranking(SYNTHETIC_RESULTS)
    simbolos = [c.symbol for c in ranking]
    assert simbolos.index("FUERTE1") < simbolos.index("DEBIL1")
    assert simbolos.index("FUERTE2") < simbolos.index("DEBIL2")
    print("OK - build_live_ranking prioriza candidatos con señal fuerte sobre los débiles")


def test_run_live_cycle_premarket_guarda_snapshot_dinamico() -> None:
    _reset_db()
    now = _et(2026, 8, 3, 8, 0)  # premarket, lejos de la ventana de sellado
    resumen = li.run_live_cycle(SYNTHETIC_RESULTS, now=now)
    assert resumen["error"] is None, resumen
    assert resumen["session"] == "premarket"
    assert resumen["accion"] == "snapshot_dinamico"
    assert pj.is_sealed("2026-08-03") is False

    snaps = pj.get_dynamic_snapshots("2026-08-03")
    # Regla de consenso (2026-08-03): DEBIL1/DEBIL2 (eligible=False) nunca
    # entran al Prediction Journal, ni siquiera al snapshot dinámico
    # informativo -- solo FUERTE1/FUERTE2 (eligible=True).
    assert len(snaps) == 2
    assert {s["symbol"] for s in snaps} == {"FUERTE1", "FUERTE2"}
    print("OK - ciclo en premarket (fuera de la ventana de sellado) guarda solo snapshot dinámico, sin inelegibles")


def test_run_live_cycle_sella_en_la_ventana_una_sola_vez() -> None:
    _reset_db()
    date = "2026-08-03"
    # Un par de snapshots dinámicos antes de la ventana de sellado.
    li.run_live_cycle(SYNTHETIC_RESULTS, now=_et(2026, 8, 3, 8, 0))
    li.run_live_cycle(SYNTHETIC_RESULTS, now=_et(2026, 8, 3, 9, 0))

    resumen_sellado = li.run_live_cycle(SYNTHETIC_RESULTS, now=_et(2026, 8, 3, 9, 27))
    assert resumen_sellado["accion"] == "snapshot_dinamico+sellado"
    assert pj.is_sealed(date) is True
    sellado = pj.get_sealed_predictions(date)
    # Regla de consenso (2026-08-03): solo los 2 elegibles quedan sellados,
    # nunca DEBIL1/DEBIL2 -- el Prediction Journal completo, no solo el
    # candidato #1, respeta el veto de Radar Explosivo.
    assert len(sellado) == 2
    assert {r["symbol"] for r in sellado} == {"FUERTE1", "FUERTE2"}

    # Un segundo ciclo dentro de la misma ventana NO debe intentar re-sellar
    # (y por lo tanto no debe fallar con AlreadySealedError).
    resumen_segundo = li.run_live_cycle(SYNTHETIC_RESULTS, now=_et(2026, 8, 3, 9, 28))
    assert resumen_segundo["error"] is None, resumen_segundo
    assert resumen_segundo["accion"] == "snapshot_dinamico"  # ya sellado, no se repite
    assert len(pj.get_sealed_predictions(date)) == 2  # sin duplicar
    print("OK - el sellado ocurre una sola vez, ciclos siguientes en la ventana no re-sellan ni fallan, sin inelegibles")


def test_run_live_cycle_regular_registra_trayectoria_si_hay_sellado() -> None:
    _reset_db()
    date = "2026-08-03"
    li.run_live_cycle(SYNTHETIC_RESULTS, now=_et(2026, 8, 3, 8, 0))
    li.run_live_cycle(SYNTHETIC_RESULTS, now=_et(2026, 8, 3, 9, 27))  # sella
    assert pj.is_sealed(date)

    resumen = li.run_live_cycle(SYNTHETIC_RESULTS, now=_et(2026, 8, 3, 10, 0))
    assert resumen["session"] == "regular"
    assert resumen["error"] is None, resumen
    # Regla de consenso (2026-08-03): solo 2 símbolos quedaron sellados
    # (FUERTE1/FUERTE2), no 4 -- la trayectoria se muestrea sobre el
    # ranking sellado, así que ahora son 2/2, no 4/4.
    assert "trayectoria_muestreada=2/2" in resumen["accion"], resumen

    trayectoria = ej.get_trajectory("FUERTE1", date)
    assert len(trayectoria) == 1
    assert trayectoria[0]["return_pct"] == 0.0  # change_pct por defecto de FUERTE1 en _row(), no se sobreescribió
    print("OK - sesion regular con ranking sellado registra un punto de trayectoria por simbolo")

    # Un segundo ciclo agrega OTRO punto -- append-only, no reemplaza al anterior.
    li.run_live_cycle(SYNTHETIC_RESULTS, now=_et(2026, 8, 3, 10, 5))
    assert len(ej.get_trajectory("FUERTE1", date)) == 2
    print("OK - ciclos sucesivos van acumulando la trayectoria, sin sobrescribir")


def test_run_live_cycle_regular_sin_sellado_no_registra_nada() -> None:
    _reset_db()
    resumen = li.run_live_cycle(SYNTHETIC_RESULTS, now=_et(2026, 8, 3, 12, 0))
    assert resumen["session"] == "regular"
    assert resumen["accion"] == "sin_sellado_hoy"
    assert resumen["error"] is None
    assert ej.get_trajectory("FUERTE1", "2026-08-03") == []
    print("OK - sin ranking sellado, la sesión regular no registra ninguna trayectoria")


def test_run_live_cycle_califica_al_cierre_con_collector_falso() -> None:
    _reset_db()
    date = "2026-08-03"
    li.run_live_cycle(SYNTHETIC_RESULTS, now=_et(2026, 8, 3, 8, 0))
    li.run_live_cycle(SYNTHETIC_RESULTS, now=_et(2026, 8, 3, 9, 27))  # sella
    li.run_live_cycle(SYNTHETIC_RESULTS, now=_et(2026, 8, 3, 10, 0))   # un punto de trayectoria
    li.run_live_cycle(SYNTHETIC_RESULTS, now=_et(2026, 8, 3, 12, 0))   # otro punto
    assert pj.is_sealed(date)

    original_collector = li.DataCollector
    li.DataCollector = lambda provider: _FakeCollector()  # monkeypatch -- sin red real
    try:
        resumen = li.run_live_cycle(SYNTHETIC_RESULTS, now=_et(2026, 8, 3, 16, 30))
    finally:
        li.DataCollector = original_collector

    assert resumen["session"] == "afterhours"
    assert resumen["error"] is None, resumen
    assert "calificados=" in resumen["accion"], resumen

    sellado = {r["symbol"]: r for r in pj.get_sealed_predictions(date)}
    fuerte1 = sellado["FUERTE1"]
    assert fuerte1["result_change_pct"] == 45.0
    assert fuerte1["result_category"] == "EXPLOSION"
    assert fuerte1["graded_at"] is not None
    assert fuerte1["anticipation_minutes"] is not None and fuerte1["anticipation_minutes"] > 0
    print(f"OK - FUERTE1 calificado: categoria={fuerte1['result_category']} "
          f"anticipacion={fuerte1['anticipation_minutes']:.0f} min")

    fuerte2 = sellado["FUERTE2"]
    # FUERTE2 era eligible=True al momento de sellar y cerró en -3.0% (< 5%
    # de techo) -- por la regla de prioridad ya validada del Clasificador
    # (Entregable 2), esto es FALSE_BREAKOUT, no EXPLOSION: parecía
    # explosiva y no lo sostuvo. Confirma que la calificación en vivo usa
    # la MISMA regla que ya se validó sobre los 30 días históricos.
    assert fuerte2["result_category"] == "FALSE_BREAKOUT"
    print(f"OK - FUERTE2 calificado: categoria={fuerte2['result_category']} (cambio real -3.0%, era elegible)")

    # Regla de consenso (2026-08-03): DEBIL1/DEBIL2 (eligible=False) nunca
    # llegaron a sellarse -- ni siquiera están en `sellado` para calificar.
    # Antes de esta regla, se sellaban igual y quedaban sin calificar por
    # falta de cotización; ahora Radar Explosivo los descarta desde el
    # sellado mismo, un paso antes.
    assert "DEBIL1" not in sellado
    assert "DEBIL2" not in sellado
    assert len(sellado) == 2
    print("OK - DEBIL1/DEBIL2 (inelegibles) nunca llegaron a sellarse -- ni siquiera compiten por calificación")

    # Exit Journal: el resumen objetivo de FUERTE1 debe quedar cerrado en
    # el mismo ciclo de calificación, usando la trayectoria de 2 puntos ya
    # registrada durante la sesión regular -- sin ningún umbral.
    resumen_ej = ej.get_exit_summary("FUERTE1", date)
    assert resumen_ej is not None
    assert resumen_ej["sample_count"] == 2
    assert resumen_ej["entry_at"] is not None  # = hora de sellado
    assert resumen_ej["peak_return_pct"] == 0.0  # unica variacion registrada en la trayectoria sintetica
    print(f"OK - Exit Journal cerrado para FUERTE1 junto con la calificación: {resumen_ej['sample_count']} muestras")

    # DEBIL1 nunca se calificó -> tampoco debería tener resumen de salida cerrado.
    assert ej.get_exit_summary("DEBIL1", date) is None
    print("OK - DEBIL1 (sin calificar) tampoco tiene Exit Journal cerrado")


ALL_TESTS = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]

if __name__ == "__main__":
    fallos = []
    for test_fn in ALL_TESTS:
        try:
            test_fn()
        except AssertionError as exc:
            fallos.append((test_fn.__name__, str(exc)))
    _reset_db()
    print(f"\nPruebas corridas: {len(ALL_TESTS)}")
    if fallos:
        print(f"FALLÓ -- {len(fallos)}:")
        for nombre, motivo in fallos:
            print(f"  {nombre}: {motivo}")
        raise SystemExit(1)
    print("OK -- todas las pruebas de live_integration pasaron.")
