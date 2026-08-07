"""Pruebas del Exit Journal, con trayectorias sintéticas (no reales de
mercado). Uso: `python -m atlas_live.memory.test_exit_journal`"""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from atlas_live.memory import exit_journal as ej

# Aislamiento de datos (2026-08-06, incidente real -- ver DECISIONES.md):
# antes, `_reset_db()` borraba `ej.DB_PATH` por defecto (el mismo archivo
# que usa la reconstrucción histórica real), y el `_reset_db()` final del
# bloque __main__ lo dejaba borrado sin volver a escribir nada. Ahora este
# módulo redirige `ej.DB_PATH` a un directorio temporal propio, generado
# una sola vez al importar -- ningún test de este archivo puede volver a
# tocar el archivo real, sin importar quién lo corra ni cuándo.
ej.DB_PATH = Path(tempfile.mkdtemp(prefix="atlas_test_exit_journal_")) / "exit_journal.db"


def _reset_db():
    if os.path.exists(ej.DB_PATH):
        os.remove(ej.DB_PATH)
    for ext in ("-wal", "-shm"):
        p = str(ej.DB_PATH) + ext
        if os.path.exists(p):
            os.remove(p)


# Trayectoria sintética que simula una explosión real: sube, hace pico,
# retrocede, se apaga -- 9 muestreos cada 5 minutos.
_BASE = datetime(2026, 8, 3, 9, 30, tzinfo=timezone.utc)
TIMES = [(_BASE + timedelta(minutes=5 * i)).isoformat() for i in range(9)]
RETURNS = [1.0, 3.5, 8.0, 15.0, 22.0, 18.0, 14.0, 13.5, 13.0]  # pico en el indice 4 (22.0)


def test_record_and_get_trajectory() -> None:
    _reset_db()
    for t, r in zip(TIMES, RETURNS):
        ej.record_trajectory_sample("SYM1", "2026-08-03", t, r, score=70.0, eligible=True)
    trayectoria = ej.get_trajectory("SYM1", "2026-08-03")
    assert len(trayectoria) == 9
    assert trayectoria[0]["sampled_at"] == TIMES[0]
    assert trayectoria[-1]["return_pct"] == 13.0
    print("OK - trayectoria cruda guardada y recuperada en orden cronológico")


def test_close_exit_summary_solo_metricas_objetivas() -> None:
    _reset_db()
    for t, r in zip(TIMES, RETURNS):
        ej.record_trajectory_sample("SYM1", "2026-08-03", t, r, score=70.0, eligible=True)

    resumen = ej.close_exit_summary(
        "SYM1", "2026-08-03", entry_at="2026-08-03T09:27:00+00:00", window_closed_at="2026-08-03T16:00:00+00:00"
    )
    assert resumen["detected_at"] == TIMES[0]
    assert resumen["entry_at"] == "2026-08-03T09:27:00+00:00"
    assert resumen["peak_at"] == TIMES[4]
    assert resumen["peak_return_pct"] == 22.0
    assert resumen["final_return_pct"] == 13.0
    assert resumen["sample_count"] == 9
    assert resumen["total_window_minutes"] > 0
    print("OK - resumen objetivo (deteccion/entrada/pico/final/duracion) sin ningun umbral")


def test_close_exit_summary_una_sola_vez() -> None:
    _reset_db()
    ej.record_trajectory_sample("SYM1", "2026-08-03", TIMES[0], 1.0, 50.0, True)
    ej.close_exit_summary("SYM1", "2026-08-03", None, "2026-08-03T16:00:00+00:00")
    try:
        ej.close_exit_summary("SYM1", "2026-08-03", None, "2026-08-03T16:05:00+00:00")
        print("FALLO - deberia haber rechazado un segundo cierre")
    except ej.AlreadyClosedError as e:
        print(f"OK - segundo cierre rechazado: {e}")


def test_summary_sin_muestras_no_inventa_nada() -> None:
    _reset_db()
    resumen = ej.close_exit_summary("VACIO", "2026-08-03", None, "2026-08-03T16:00:00+00:00")
    assert resumen["detected_at"] is None
    assert resumen["peak_return_pct"] is None
    assert resumen["sample_count"] == 0
    print("OK - sin muestras: todos los campos objetivos quedan None, no se inventa nada")


def test_derive_movement_start_requiere_umbral_explicito() -> None:
    trayectoria = [{"sampled_at": t, "return_pct": r} for t, r in zip(TIMES, RETURNS)]
    resultado = ej.derive_movement_start(trayectoria, movement_threshold_pct=2.0)
    assert resultado.timestamp == TIMES[1]  # primer valor >= 2.0 es el indice 1 (3.5)
    assert resultado.es_provisional is True
    assert "2.0" in resultado.regla_aplicada
    print(f"OK - derive_movement_start: {resultado.timestamp} (regla: {resultado.regla_aplicada})")

    # Un umbral distinto da un resultado distinto -- confirma que nada quedo fijo.
    resultado_alto = ej.derive_movement_start(trayectoria, movement_threshold_pct=10.0)
    assert resultado_alto.timestamp == TIMES[3]  # primer valor >= 10.0 es el indice 3 (15.0)
    print(f"OK - mismo dato, otro umbral (10.0) da otro resultado: {resultado_alto.timestamp}")


def test_derive_weakness_point() -> None:
    trayectoria = [{"sampled_at": t, "return_pct": r} for t, r in zip(TIMES, RETURNS)]
    # pico=22.0 en indice 4; retroceso de 4 puntos se cruza en indice 5 (18.0, retroceso=4.0)
    resultado = ej.derive_weakness_point(trayectoria, retracement_from_peak_pct=4.0)
    assert resultado.timestamp == TIMES[5]
    print(f"OK - derive_weakness_point (retroceso>=4pp): {resultado.timestamp}")


def test_derive_impulse_end() -> None:
    trayectoria = [{"sampled_at": t, "return_pct": r} for t, r in zip(TIMES, RETURNS)]
    # retroceso>=4pp desde indice 5 en adelante (18,14,13.5,13 -> retrocesos 4,8,8.5,9, todos >=4)
    # 2 muestreos consecutivos "quietos" se completan en el indice 6 (18.0 en 5, 14.0 en 6)
    resultado = ej.derive_impulse_end(trayectoria, retracement_from_peak_pct=4.0, consecutive_quiet_samples=2)
    assert resultado.timestamp == TIMES[6]
    print(f"OK - derive_impulse_end (retroceso>=4pp, 2 muestreos consecutivos): {resultado.timestamp}")


def test_derive_movement_duration_none_si_no_hay_datos_suficientes() -> None:
    trayectoria_plana = [{"sampled_at": t, "return_pct": 0.5} for t in TIMES]  # nunca se mueve
    duracion = ej.derive_movement_duration(
        trayectoria_plana, movement_threshold_pct=2.0, retracement_from_peak_pct=4.0, consecutive_quiet_samples=2
    )
    assert duracion is None
    print("OK - sin inicio de movimiento detectable, la duracion es None, no un numero inventado")


def test_derive_movement_duration_con_datos_reales() -> None:
    trayectoria = [{"sampled_at": t, "return_pct": r} for t, r in zip(TIMES, RETURNS)]
    duracion = ej.derive_movement_duration(
        trayectoria, movement_threshold_pct=2.0, retracement_from_peak_pct=4.0, consecutive_quiet_samples=2
    )
    assert duracion == 25.0  # de TIMES[1] (09:35) a TIMES[6] (10:00) = 25 minutos
    print(f"OK - tiempo total del movimiento (regla temporal aplicada): {duracion} minutos")


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
    print("OK -- todas las pruebas del Exit Journal pasaron.")
