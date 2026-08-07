"""Pruebas del Panel de Desempeño de Atlas (2026-08-07, ver DECISION_LOG.md).
Con datos sintéticos, sin red real. Uso: `python -m atlas_live.test_performance_panel`
"""

import os
import tempfile
from pathlib import Path

from atlas_live.memory import exit_journal as ej

# Aislamiento de datos (mismo criterio que el resto del proyecto desde el
# incidente real de la Investigación 3): `ej.DB_PATH` se redirige a un
# directorio temporal propio al importar.
ej.DB_PATH = Path(tempfile.mkdtemp(prefix="atlas_test_performance_")) / "exit_journal.db"

from atlas_live import performance_panel as pp  # noqa: E402  (después de fijar DB_PATH)
from atlas_live.memory import prediction_journal as pj  # noqa: E402


def _reset_db() -> None:
    for db_path in (ej.DB_PATH, pj.DB_PATH):
        if os.path.exists(db_path):
            os.remove(db_path)
        for ext in ("-wal", "-shm"):
            p = str(db_path) + ext
            if os.path.exists(p):
                os.remove(p)


def _insert_summary(symbol: str, date: str, final_return_pct: float) -> None:
    import sqlite3
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


def test_financial_stats_ganadoras_y_perdedoras() -> None:
    _reset_db()
    for i, r in enumerate([10.0, -4.0, 6.0, -2.0]):
        _insert_summary(f"SYM{i}", "2026-08-01", r)

    resultado = pp.get_global_performance()
    fin = resultado["rendimiento_financiero"]
    assert fin["win_rate_financiero_pct"] == 50.0
    assert fin["ganancia_promedio_pct"] == 8.0
    assert fin["perdida_promedio_pct"] == -3.0
    assert fin["profit_factor"] == 16.0 / 6.0
    print("OK - win rate, ganancia/pérdida promedio y profit factor calculados sobre datos reales")


def test_tasa_acierto_distinta_de_win_rate_financiero() -> None:
    """El principio explícito del usuario: acierto y rentabilidad son dos
    cosas distintas. Un retorno positivo pero por debajo del umbral de
    EXPLOSION (10%) cuenta para el win rate financiero, no para el acierto."""
    _reset_db()
    _insert_summary("SYM_LEVE", "2026-08-01", 3.0)  # positivo pero no EXPLOSION
    _insert_summary("SYM_FUERTE", "2026-08-01", 12.0)  # positivo y EXPLOSION

    resultado = pp.get_global_performance()
    fin = resultado["rendimiento_financiero"]
    prec = resultado["precision_del_modelo"]

    assert fin["win_rate_financiero_pct"] == 100.0  # ambos ganaron plata
    assert prec["tasa_acierto_pct"] == 50.0  # solo uno fue una EXPLOSION real
    print("OK - acierto del modelo y rentabilidad quedan separados, no se mezclan")


def test_drawdown_hipotetico() -> None:
    _reset_db()
    # secuencia cronológica: +10, -20, +5 -> pico 110, valle 90, caida=20
    _insert_summary("A", "2026-08-01", 10.0)
    _insert_summary("B", "2026-08-02", -20.0)
    _insert_summary("C", "2026-08-03", 5.0)

    resultado = pp.get_global_performance()
    assert resultado["rendimiento_financiero"]["drawdown_hipotetico_pct"] == 20.0
    print("OK - drawdown hipotético calculado sobre la curva de capital simulada")


def test_atlas_score_usa_pesos_del_config() -> None:
    _reset_db()
    for i, r in enumerate([12.0, -3.0, 5.0, -2.0]):
        _insert_summary(f"SYM{i}", "2026-08-01", r)

    resultado = pp.get_global_performance()
    score = resultado["atlas_score"]
    assert score["score"] is not None
    assert 0 <= score["score"] <= 100
    assert score["pesos_usados"] == pp.load_config()["atlas_score_weights"]
    print("OK - Atlas Score usa los pesos de performance_config.json, no valores fijos en código")


def test_sin_datos_no_inventa_nada() -> None:
    _reset_db()
    resultado = pp.get_global_performance()
    fin = resultado["rendimiento_financiero"]
    assert fin["win_rate_financiero_pct"] is None
    assert fin["profit_factor"] is None
    assert resultado["atlas_score"]["score"] is None
    print("OK - sin operaciones cerradas, todo queda None -- nunca un número inventado")


def test_oportunidad_del_dia_sin_sellado() -> None:
    _reset_db()
    resultado = pp.get_daily_opportunity(date="2026-08-01")
    assert resultado["available"] is False
    print("OK - sin ranking sellado ese día, Nivel 1 responde 'no disponible', no un dato fabricado")


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
    else:
        print("OK -- todas las pruebas del Panel de Desempeño pasaron.")
