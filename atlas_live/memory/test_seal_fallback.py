"""Tests del fallback de sellado del Prediction Journal (2026-08-28,
autorizado explícitamente -- bloqueo real confirmado en producción:
`sealed_today=null` varios días seguidos porque ningún ciclo de
`scan_worker` caía dentro de la ventana ideal de 5 minutos, 09:25-09:30
ET). Mismo patrón de aislamiento de DB que `test_live_integration.py`
-- nunca toca `pj.DB_PATH`/`ej.DB_PATH`/`store.DB_PATH` reales."""

import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from atlas_live.memory import exit_journal as ej
from atlas_live.memory import live_integration as li
from atlas_live.memory import market_hours
from atlas_live.memory import prediction_journal as pj
from atlas_live.memory import store

_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="atlas_test_seal_fallback_"))
ej.DB_PATH = _TEST_DATA_DIR / "exit_journal.db"
pj.DB_PATH = _TEST_DATA_DIR / "prediction_journal.db"

_TEST_STORE = _TEST_DATA_DIR / "memory_store.db"
if store.DB_PATH.exists():
    shutil.copy(store.DB_PATH, _TEST_STORE)
store.DB_PATH = _TEST_STORE

ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def _row(symbol, eligible=False, score=None, **metrics):
    defaults = dict(price=10.0, gap_pct=0.0, change_pct=0.0, relative_volume=0.5,
                     dollar_volume=3_000_000, volatility_score=50.0, market_cap=500_000_000)
    defaults.update(metrics)
    return {"symbol": symbol, "explosive": {"eligible": eligible, "score": score, "metrics": defaults}}


_RESULTS = [
    _row("FUERTE1", eligible=True, score=80.0, gap_pct=12.0, relative_volume=15.0, volatility_score=95.0),
    _row("FUERTE2", eligible=True, score=70.0, gap_pct=11.0, relative_volume=12.0, volatility_score=92.0),
    _row("DEBIL1", eligible=False, gap_pct=0.5, relative_volume=0.4),
]


def _reset_db(fecha_prefix=None):
    """Igual que `test_live_integration._reset_db()`, sin importar ese
    módulo (evita arrastrar sus propios efectos de import)."""
    import os
    for db_path in (pj.DB_PATH, ej.DB_PATH):
        if os.path.exists(db_path):
            os.remove(db_path)
        for ext in ("-wal", "-shm"):
            p = str(db_path) + ext
            if os.path.exists(p):
                os.remove(p)


# --- A: sellado normal dentro de 09:25-09:30 (comportamiento SIN cambios) --

def test_A_sellado_normal_dentro_de_la_ventana():
    _reset_db()
    date = "2026-08-03"  # lunes
    li.run_live_cycle(_RESULTS, now=_et(2026, 8, 3, 9, 27))
    assert pj.is_sealed(date) is True
    sellado = pj.get_sealed_predictions(date)
    assert {r["symbol"] for r in sellado} == {"FUERTE1", "FUERTE2"}
    resumen = li.run_live_cycle(_RESULTS, now=_et(2026, 8, 3, 9, 28))
    # dentro de la ventana, ya sellado -> comportamiento normal, SIN fallback.
    assert resumen["accion"] == "snapshot_dinamico"


# --- B: ventana perdida -- el primer ciclo de sesión regular sella --------

def test_B_ventana_perdida_el_primer_ciclo_regular_sella():
    _reset_db()
    date = "2026-08-04"  # martes
    # Ningún ciclo cayó en 09:25-09:30 -- va directo de premarket (08:00) a
    # regular (10:00), simulando exactamente el patrón real de producción.
    li.run_live_cycle(_RESULTS, now=_et(2026, 8, 4, 8, 0))
    assert pj.is_sealed(date) is False  # todavía no, nadie selló

    resumen = li.run_live_cycle(_RESULTS, now=_et(2026, 8, 4, 10, 0))
    assert resumen["session"] == "regular"
    assert resumen["error"] is None, resumen
    assert pj.is_sealed(date) is True
    assert resumen["accion"].startswith("sellado_fallback+")
    sellado = pj.get_sealed_predictions(date)
    assert {r["symbol"] for r in sellado} == {"FUERTE1", "FUERTE2"}
    # Y además, en el MISMO ciclo, ya se registró un punto de trayectoria
    # (no se pierde el primer punto por sellar tarde).
    assert len(ej.get_trajectory("FUERTE1", date)) == 1


# --- C: día ya sellado -- el fallback nunca vuelve a sellar ---------------

def test_C_dia_ya_sellado_no_vuelve_a_sellar():
    _reset_db()
    date = "2026-08-05"  # miércoles
    li.run_live_cycle(_RESULTS, now=_et(2026, 8, 5, 9, 27))  # sella en la ventana ideal
    assert pj.is_sealed(date) is True
    sellado_antes = pj.get_sealed_predictions(date)

    resumen = li.run_live_cycle(_RESULTS, now=_et(2026, 8, 5, 11, 0))
    assert resumen["error"] is None, resumen
    # Comportamiento normal (sin prefijo de fallback) -- ya estaba sellado.
    assert not resumen["accion"].startswith("sellado_fallback+")
    assert resumen["accion"].startswith("trayectoria_muestreada=")
    sellado_despues = pj.get_sealed_predictions(date)
    assert sellado_antes == sellado_despues  # sin duplicar ni recrear


# --- D: día incorrecto -- cada fecha se sella con SU propia clave ----------

def test_D_no_mezcla_ni_sella_un_dia_incorrecto():
    _reset_db()
    dia1, dia2 = "2026-08-06", "2026-08-07"  # jueves, viernes
    li.run_live_cycle(_RESULTS, now=_et(2026, 8, 6, 10, 0))  # fallback día 1
    assert pj.is_sealed(dia1) is True
    assert pj.is_sealed(dia2) is False  # el día siguiente sigue sin sellar

    li.run_live_cycle(_RESULTS, now=_et(2026, 8, 7, 10, 0))  # fallback día 2
    assert pj.is_sealed(dia2) is True
    # Cada día tiene su propio sellado, ninguno se pisa ni se mezcla.
    assert len(pj.get_sealed_predictions(dia1)) == 2
    assert len(pj.get_sealed_predictions(dia2)) == 2


# --- E: fin de semana -- nunca sella (sesión nunca es "regular") ----------

def test_E_fin_de_semana_no_sella():
    _reset_db()
    sabado = _et(2026, 8, 8, 10, 0)  # sábado
    assert market_hours.get_session(sabado) not in ("premarket", "regular")
    resumen = li.run_live_cycle(_RESULTS, now=sabado)
    assert resumen["error"] is None, resumen
    assert pj.is_sealed("2026-08-08") is False


# --- F: el cambio no altera ningún dato/decisión del circuito existente ---

def test_F_camino_normal_identico_al_comportamiento_previo():
    """Mismo escenario que `test_run_live_cycle_sella_en_la_ventana_una_sola_vez`
    (test_live_integration.py, sin tocar): sellar dentro de la ventana
    ideal produce EXACTAMENTE el mismo resultado que antes de este
    cambio -- mismos símbolos, mismo `accion`, sin ningún prefijo nuevo."""
    _reset_db()
    date = "2026-08-10"  # lunes
    li.run_live_cycle(_RESULTS, now=_et(2026, 8, 10, 8, 0))
    li.run_live_cycle(_RESULTS, now=_et(2026, 8, 10, 9, 0))
    resumen_sellado = li.run_live_cycle(_RESULTS, now=_et(2026, 8, 10, 9, 27))
    assert resumen_sellado["accion"] == "snapshot_dinamico+sellado"
    assert len(pj.get_sealed_predictions(date)) == 2

    resumen_regular = li.run_live_cycle(_RESULTS, now=_et(2026, 8, 10, 10, 0))
    assert resumen_regular["accion"] == "trayectoria_muestreada=2/2"  # SIN prefijo "sellado_fallback+"

    # Verificación estructural: el fallback vive exclusivamente en
    # live_integration.py -- ningún archivo protegido tiene diff.
    import subprocess
    protegidos = [
        "atlas_live/core/atlas_decision_core.py",
        "atlas_live/core/current_top_opportunity.py",
        "atlas_live/core/top_opportunity_stability.py",
        "atlas_live/core/current_top_opportunity_registry.py",
        "atlas_live/scan_worker.py",
        "atlas_live/radar/radar_worker.py",
        "atlas_live/radar/candidate_gates.py",
        "atlas_live/radar/priority_classifier.py",
        "atlas/engine/decision_engine.py",
        "atlas_live/memory/exit_journal.py",
        "atlas_live/memory/prediction_journal.py",
        "atlas_live/memory/market_hours.py",
    ]
    resultado = subprocess.run(
        ["git", "diff", "--stat", "--"] + protegidos,
        capture_output=True, text=True, cwd=".",
    )
    assert resultado.stdout.strip() == "", f"archivos protegidos con diff pendiente: {resultado.stdout}"


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
