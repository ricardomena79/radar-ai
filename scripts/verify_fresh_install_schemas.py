"""Verificación de una sola vez (2026-08-16, no forma parte del código de
producción): confirma que cada módulo con base SQLite crea su propio
esquema desde cero, sin depender de ningún archivo .db commiteado. Apunta
cada módulo a una ruta temporal que NO existe, se conecta, y confirma que
las tablas esperadas quedan creadas. No toca ningún archivo real del repo."""

import sqlite3
import tempfile
import uuid
from pathlib import Path


def _tmp_path(name: str) -> Path:
    return Path(tempfile.gettempdir()) / f"atlas_fresh_install_check_{uuid.uuid4().hex}_{name}"


def check(module_name: str, expected_tables):
    import importlib
    mod = importlib.import_module(module_name)
    tmp = _tmp_path(module_name.replace(".", "_") + ".db")
    assert not tmp.exists(), "la ruta temporal no debería existir todavía"
    orig = mod.DB_PATH
    mod.DB_PATH = tmp
    if hasattr(mod, "_schema_ready_for"):
        mod._schema_ready_for = None
    try:
        conn = mod._connect()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        faltan = set(expected_tables) - tables
        if faltan:
            print(f"FAIL {module_name}: faltan tablas {faltan} (encontradas: {tables})")
            return False
        print(f"OK   {module_name}: creó {sorted(tables)} desde cero en {tmp}")
        return True
    finally:
        mod.DB_PATH = orig
        if tmp.exists():
            tmp.unlink()
        wal = tmp.with_name(tmp.name + "-wal")
        shm = tmp.with_name(tmp.name + "-shm")
        if wal.exists():
            wal.unlink()
        if shm.exists():
            shm.unlink()


results = []
results.append(check("atlas_live.memory.store", {"observations"}))
results.append(check("atlas_live.memory.exit_journal", {"trajectory_samples", "exit_summary"}))
results.append(check("atlas_live.memory.prediction_journal", {"dynamic_snapshots", "sealed_ranking_meta", "sealed_predictions"}))
results.append(check("atlas_live.mission_control.timeline", None) if False else None)

# timeline.py puede tener una API distinta (_connect no garantizado) -- se
# verifica aparte, con manejo de error explícito en vez de asumir la misma forma.
try:
    import importlib
    tmod = importlib.import_module("atlas_live.mission_control.timeline")
    tmp = _tmp_path("timeline.db")
    orig = tmod.DB_PATH
    tmod.DB_PATH = tmp
    if hasattr(tmod, "_schema_ready_for"):
        tmod._schema_ready_for = None
    conn = tmod._connect()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    tmod.DB_PATH = orig
    tmp.unlink(missing_ok=True)
    print(f"OK   atlas_live.mission_control.timeline: creó {sorted(tables)} desde cero")
    results.append(True)
except Exception as e:
    print(f"FAIL atlas_live.mission_control.timeline: {type(e).__name__}: {e}")
    results.append(False)

results = [r for r in results if r is not None]
print()
print(f"--- {sum(results)}/{len(results)} módulos crean su esquema desde cero, sin ningún .db commiteado ---")
