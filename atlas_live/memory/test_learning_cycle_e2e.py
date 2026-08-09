"""Prueba crítica END-TO-END del ciclo de aprendizaje (F3/F4, 2026-08-09).

Recorre el flujo REAL por `run_live_cycle`, con todas las bases redirigidas a
un directorio temporal (jamás toca las reales) y un `DataCollector` falso
para la calificación (jamás toca la red):

  candidato -> predicción sellada -> trayectoria -> cierre -> resultado real
  -> observación LIVE -> Memory Store -> recalibración -> cambio de métrica

Demuestra el cierre del circuito que faltaba, de forma determinista, sin
esperar a que el mercado esté abierto.
"""

import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from atlas_live.memory import exit_journal as ej
from atlas_live.memory import live_integration as li
from atlas_live.memory import prediction_journal as pj
from atlas_live.memory import store
from atlas_live.predictive_engine import prediction_log

# --- Aislamiento total de datos: todo a temp, nada real ---
_TMP = Path(tempfile.mkdtemp(prefix="atlas_test_e2e_"))
store.DB_PATH = _TMP / "memory_store.db"
ej.DB_PATH = _TMP / "exit_journal.db"
pj.DB_PATH = _TMP / "prediction_journal.db"
prediction_log.DB_PATH = _TMP / "predictive_engine.db"

ET = ZoneInfo("America/New_York")


def _et(h, m):
    return datetime(2026, 8, 3, h, m, tzinfo=ET)


def _row(symbol, eligible=False, score=None, **metrics):
    d = dict(price=10.0, gap_pct=0.0, change_pct=0.0, relative_volume=0.5,
             dollar_volume=3_000_000, volatility_score=50.0, market_cap=500_000_000)
    d.update(metrics)
    return {"symbol": symbol, "explosive": {"eligible": eligible, "score": score, "metrics": d}}


# `change_pct` en las métricas es lo que la trayectoria observa cada ciclo y,
# por lo tanto, el `final_return_pct` con el que el write-back clasifica la
# observación. FUERTE1 sostiene una explosión real (25%), FUERTE2 no (-3%).
RESULTS = [
    _row("FUERTE1", eligible=True, score=80.0, gap_pct=12.0, change_pct=25.0, relative_volume=15.0, volatility_score=95.0),
    _row("FUERTE2", eligible=True, score=70.0, gap_pct=11.0, change_pct=-3.0, relative_volume=12.0, volatility_score=92.0),
    _row("DEBIL1", eligible=False, gap_pct=0.5, change_pct=0.5, relative_volume=0.4),
]


class _FakeQuote:
    def __init__(self, change_percent):
        self.change_percent = change_percent


class _FakeCollector:
    RESULTS = {"FUERTE1": 25.0, "FUERTE2": -3.0}  # FUERTE1 explota (acierto), FUERTE2 no

    def get_quote(self, symbol):
        return _FakeQuote(self.RESULTS[symbol])


def _seed_historico(n=30):
    """Unas observaciones históricas (source v1) para que la evidencia tenga
    una base poblacional -- separadas de las live."""
    m = {"price": 10.0, "gap_pct": 5.0, "change_pct": 2.0, "relative_volume": 2.0,
         "dollar_volume": 5e6, "volatility_score": 50.0, "market_cap": 5e8}
    for i in range(n):
        cat = "EXPLOSION" if i % 5 == 0 else "NORMAL"
        store.record_observation(f"SEED{i}", "2026-07-01", 10, cat, m, source_version="v1")


def test_ciclo_completo_incorpora_observacion_y_recalibra():
    _seed_historico()
    li._evidence_cache.clear()

    live_antes = store.count_observations(source_version="live")
    total_antes = li.get_memory_engine_summary(now=_et(16, 35))["observation_count"]
    assert live_antes == 0, "no debe haber observaciones live antes del ciclo"

    # Flujo real
    li.run_live_cycle(RESULTS, now=_et(8, 0))    # premarket: snapshot dinámico
    li.run_live_cycle(RESULTS, now=_et(9, 27))   # ventana de sellado: sella (con metrics_snapshot)
    li.run_live_cycle(RESULTS, now=_et(10, 0))   # regular: punto de trayectoria
    li.run_live_cycle(RESULTS, now=_et(12, 0))   # regular: otro punto

    # Cierre con collector falso (sin red)
    orig = li.DataCollector
    li.DataCollector = lambda provider: _FakeCollector()
    try:
        resumen = li.run_live_cycle(RESULTS, now=_et(16, 30))  # afterhours: califica+cierra+write-back+recalibra
    finally:
        li.DataCollector = orig

    # 1. Se incorporaron observaciones LIVE nuevas (FUERTE1, FUERTE2 elegibles)
    live_despues = store.count_observations(source_version="live")
    assert live_despues >= 2, f"esperaba >=2 observaciones live, hubo {live_despues}"
    assert "observaciones_nuevas=" in resumen["accion"], resumen

    # 2. El acierto usa la definición existente: FUERTE1 (retorno 25%) = EXPLOSION
    fuerte1 = [o for o in store.get_observations() if o["symbol"] == "FUERTE1" and o["source_version"] == "live"][0]
    assert fuerte1["category"] == "EXPLOSION", fuerte1["category"]
    assert fuerte1["checkpoint_minutes"] == -1  # observación de cierre

    # 3. Histórico separado de nuevo
    assert store.count_observations(source_version="v1") == 30
    assert store.count_observations(source_version="live") >= 2

    # 4. Recalibración: la evidencia refleja las observaciones nuevas
    total_despues = li.get_memory_engine_summary(now=_et(16, 35))["observation_count"]
    assert total_despues == total_antes + live_despues, (total_antes, total_despues, live_despues)
    assert li.get_memory_engine_summary(now=_et(16, 35))["last_recalibrated_on"] is not None

    # 5. Idempotencia: reprocesar el cierre no duplica
    li.DataCollector = lambda provider: _FakeCollector()
    try:
        li.run_live_cycle(RESULTS, now=_et(16, 40))
    finally:
        li.DataCollector = orig
    assert store.count_observations(source_version="live") == live_despues, "un reproceso no debe duplicar"

    print(f"OK e2e: live 0 -> {live_despues}, total {total_antes} -> {total_despues}, FUERTE1=EXPLOSION, idempotente")


if __name__ == "__main__":
    import traceback
    try:
        test_ciclo_completo_incorpora_observacion_y_recalibra()
        print("--- 1 passed, 0 failed ---")
    except Exception as e:
        print("FAIL:", e); traceback.print_exc(); print("--- 0 passed, 1 failed ---")
