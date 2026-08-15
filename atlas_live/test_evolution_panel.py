"""Pruebas del Panel de Evolución de Atlas (2026-08-07, ver DECISION_LOG.md).
Con datos sintéticos, sin red real. Uso: `python -m atlas_live.test_evolution_panel`

Aislamiento de datos (mismo criterio que el resto del proyecto desde el
incidente real de la Investigación 3): las tres bases que toca el panel
(Exit Journal, Prediction Journal, Memory Store) se redirigen a un
directorio temporal propio ANTES de importar el módulo bajo prueba.
"""

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from atlas_live.memory import exit_journal as ej
from atlas_live.memory import prediction_journal as pj
from atlas_live.memory import store

_TMP = Path(tempfile.mkdtemp(prefix="atlas_test_evolution_"))
ej.DB_PATH = _TMP / "exit_journal.db"
pj.DB_PATH = _TMP / "prediction_journal.db"
store.DB_PATH = _TMP / "memory_store.db"

from atlas_live import evolution_panel as ep  # noqa: E402  (después de fijar las rutas)
from atlas_live.memory import market_hours  # noqa: E402


def _reset_dbs() -> None:
    for db_path in (ej.DB_PATH, pj.DB_PATH, store.DB_PATH):
        for suffix in ("", "-wal", "-shm"):
            p = str(db_path) + suffix
            if os.path.exists(p):
                os.remove(p)


def _insert_summary(symbol: str, date: str, final_return_pct: float) -> None:
    conn = sqlite3.connect(ej.DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(ej._SCHEMA)
    conn.execute(
        "INSERT INTO exit_summary (symbol, date, detected_at, entry_at, peak_at, peak_return_pct, "
        "final_return_pct, window_closed_at, total_window_minutes, sample_count) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (symbol, date, f"{date}T09:30:00+00:00", f"{date}T09:30:00+00:00", f"{date}T10:00:00+00:00",
         max(final_return_pct, 0), final_return_pct, f"{date}T16:00:00+00:00", 300, 10),
    )
    conn.commit()
    conn.close()


# --- Sección 1: precisión del modelo (aciertos por período) ---

def test_aciertos_por_periodo_y_precision_historica() -> None:
    _reset_dbs()
    now = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)
    hoy = market_hours.market_date(now)
    hace_mes = (datetime.strptime(hoy, "%Y-%m-%d").date() - timedelta(days=45)).strftime("%Y-%m-%d")

    _insert_summary("EXP1", hoy, 12.0)       # EXPLOSION (>=10%)
    _insert_summary("EXP2", hoy, 15.0)       # EXPLOSION
    _insert_summary("LEVE", hoy, 3.0)        # positivo pero NO explosión
    _insert_summary("VIEJA", hace_mes, 11.0) # EXPLOSION, mes anterior

    prec = ep.get_evolution(now)["precision_del_modelo"]
    assert prec["aciertos_hoy"] == 2, prec
    assert prec["aciertos_semana"] == 2, prec
    assert prec["aciertos_mes"] == 2, prec
    assert prec["aciertos_historico"] == 3, prec  # incluye la vieja
    assert prec["muestra_historica"] == 4
    assert prec["precision_historica_pct"] == 75.0  # 3 de 4 cerradas
    print("OK - aciertos por período contados con la definición del Clasificador (EXPLOSION)")


def test_precision_no_se_mezcla_con_win_rate_financiero() -> None:
    _reset_dbs()
    now = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)
    hoy = market_hours.market_date(now)
    _insert_summary("LEVE", hoy, 3.0)     # ganó plata, NO fue explosión
    _insert_summary("FUERTE", hoy, 12.0)  # ganó plata Y fue explosión

    evo = ep.get_evolution(now)
    assert evo["rendimiento_financiero"]["win_rate_financiero_pct"] == 100.0  # ambos ganaron
    assert evo["precision_del_modelo"]["aciertos_historico"] == 1             # solo uno fue explosión
    print("OK - precisión del modelo y rentabilidad quedan separadas, no se mezclan")


# --- Sección 2: rendimiento financiero (mejor/peor global) ---

def test_mejor_y_peor_operacion_global() -> None:
    _reset_dbs()
    now = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)
    hoy = market_hours.market_date(now)
    viejo = (datetime.strptime(hoy, "%Y-%m-%d").date() - timedelta(days=10)).strftime("%Y-%m-%d")
    _insert_summary("A", hoy, 8.0)
    _insert_summary("B", viejo, -6.5)
    _insert_summary("C", viejo, 21.0)

    fin = ep.get_evolution(now)["rendimiento_financiero"]
    assert fin["mejor_operacion_global_pct"] == 21.0, fin
    assert fin["peor_operacion_global_pct"] == -6.5, fin
    print("OK - mejor/peor operación global sobre todo el historial")


# --- Sección 3: evolución del aprendizaje ---

def test_aprendizaje_cuenta_trayectorias_y_muestras() -> None:
    _reset_dbs()
    now = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)
    # 2 pares (symbol,date) distintos, 5 muestras en total
    ej.record_trajectory_sample("XX", "2026-08-01", "2026-08-01T09:30:00+00:00", 1.0, 50.0, True)
    ej.record_trajectory_sample("XX", "2026-08-01", "2026-08-01T09:35:00+00:00", 2.0, 51.0, True)
    ej.record_trajectory_sample("YY", "2026-08-02", "2026-08-02T09:30:00+00:00", 0.5, 40.0, False)
    ej.record_trajectory_sample("YY", "2026-08-02", "2026-08-02T09:35:00+00:00", 0.7, 41.0, False)
    ej.record_trajectory_sample("YY", "2026-08-02", "2026-08-02T09:40:00+00:00", 0.9, 42.0, False)

    apr = ep.get_evolution(now)["evolucion_aprendizaje"]
    assert apr["trayectorias_almacenadas"] == 2, apr
    assert apr["muestras_analizadas"] == 5, apr
    # Store vacío -> sin casos ni condiciones -> nivel "No disponible"
    assert apr["casos_similares_acumulados"] == 0
    assert apr["memory_engine_condition_coverage_pct"] is None
    assert apr["memory_engine_conditions_total"] > 0  # la grilla existe siempre
    assert "no es el nivel de aprendizaje" in apr["memory_engine_nota"].lower()
    print("OK - trayectorias y muestras contadas desde datos reales del Exit Journal")


def test_sin_datos_no_inventa_nada() -> None:
    _reset_dbs()
    now = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)
    evo = ep.get_evolution(now)

    prec = evo["precision_del_modelo"]
    fin = evo["rendimiento_financiero"]
    apr = evo["evolucion_aprendizaje"]

    assert prec["aciertos_historico"] == 0
    assert prec["precision_historica_pct"] is None
    assert fin["win_rate_financiero_pct"] is None
    assert fin["mejor_operacion_global_pct"] is None
    assert fin["peor_operacion_global_pct"] is None
    assert apr["trayectorias_almacenadas"] == 0
    assert apr["muestras_analizadas"] == 0
    assert apr["casos_similares_acumulados"] == 0
    assert apr["memory_engine_condition_coverage_pct"] is None  # "No disponible", nunca 0 fabricado
    print("OK - sin datos, todo queda None/'No disponible' -- ningún número inventado")


ALL_TESTS = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]

if __name__ == "__main__":
    fallos = []
    for test_fn in ALL_TESTS:
        try:
            test_fn()
        except AssertionError as exc:
            fallos.append((test_fn.__name__, str(exc)))
    _reset_dbs()
    print(f"\nPruebas corridas: {len(ALL_TESTS)}")
    if fallos:
        print(f"FALLÓ -- {len(fallos)}:")
        for nombre, motivo in fallos:
            print(f"  {nombre}: {motivo}")
    else:
        print("OK -- todas las pruebas del Panel de Evolución pasaron.")
