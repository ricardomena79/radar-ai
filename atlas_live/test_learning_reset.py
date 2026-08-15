"""Tests del reinicio único del aprendizaje (2026-08-15). DBs temporales
para CADA módulo objetivo -- nunca toca las reales durante el test."""

import sqlite3
import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live import learning_reset as lr
from atlas_live.memory import exit_journal as ej
from atlas_live.memory import live_integration as li
from atlas_live.memory import prediction_journal as pj
from atlas_live.memory import store
from atlas_live.predictive_engine import prediction_log as pl
from atlas_live.signals import signal_registry as sr

_MODULES = [store, ej, pj, sr, pl]


def _fresh_all():
    saved = {}
    for mod in _MODULES:
        saved[mod.__name__] = mod.DB_PATH
        mod.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_reset_{mod.__name__.replace('.', '_')}_{_uuid.uuid4().hex}.db"
    return saved


def _restore_all(saved):
    for mod in _MODULES:
        mod.DB_PATH = saved[mod.__name__]


def _seed_real_data():
    store.record_observation(
        symbol="TEST", date="2026-08-01", checkpoint_minutes=10, category="EXPLOSION",
        metrics={"change_pct": 5.0, "gap_pct": 2.0, "relative_volume": 1.5,
                 "dollar_volume": 1_000_000, "volatility_score": 50, "market_cap": 1_000_000_000},
        sector="Technology", source_version="v1",
    )
    ej.record_trajectory_sample("TEST", "2026-08-01", "2026-08-01T10:00:00Z", 5.0, 50.0, True)


def test_reset_hace_backup_y_vacia():
    saved = _fresh_all()
    try:
        _seed_real_data()
        assert store.count_observations() == 1
        assert ej.count_trajectory_samples() == 1

        # marca en un directorio temporal propio para este test
        marker_dir = Path(tempfile.gettempdir()) / f"atlas_test_reset_marker_{_uuid.uuid4().hex}"
        orig_marker_fn = lr._marker_path
        lr._marker_path = lambda: marker_dir / "marker.txt"

        report = lr.reset_learning_once()
        assert report["applied"] is True

        assert store.count_observations() == 0
        assert ej.count_trajectory_samples() == 0

        # el backup del Memory Store existe y TODAVÍA tiene el dato viejo
        store_target = [t for t in report["targets"] if t["module"] == "atlas_live.memory.store"][0]
        assert store_target["backup"] is not None
        backup_path = Path(store_target["backup"])
        assert backup_path.exists()
        c = sqlite3.connect(backup_path)
        assert c.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1  # el backup preserva el dato

        lr._marker_path = orig_marker_fn
    finally:
        _restore_all(saved)


def test_reset_es_idempotente_no_se_repite():
    saved = _fresh_all()
    try:
        _seed_real_data()
        marker_dir = Path(tempfile.gettempdir()) / f"atlas_test_reset_marker_{_uuid.uuid4().hex}"
        orig_marker_fn = lr._marker_path
        lr._marker_path = lambda: marker_dir / "marker.txt"

        report1 = lr.reset_learning_once()
        assert report1["applied"] is True

        # se vuelve a sembrar dato -- si el reset se repitiera, lo borraría
        _seed_real_data()
        assert store.count_observations() == 1

        report2 = lr.reset_learning_once()
        assert report2["applied"] is False
        assert store.count_observations() == 1  # NO se tocó -- ya se había aplicado antes

        lr._marker_path = orig_marker_fn
    finally:
        _restore_all(saved)


def test_reset_invalida_la_cache_de_evidencia_en_memoria():
    """Reproduce el bug real reportado por el usuario (2026-08-15): la
    Cabina seguía mostrando 'Aprendizaje: 71.4%' / '10/14 condiciones'
    después del reset porque `live_integration._evidence_cache` (caché EN
    MEMORIA, recalculada solo una vez por día de mercado) no se invalidaba
    al vaciar las tablas en disco. Simula exactamente ese escenario: llena
    la caché ANTES del reset (como haría un proceso ya "caliente"), corre
    el reset, y confirma que la caché queda vacía -- no solo la base."""
    saved = _fresh_all()
    li._evidence_cache.clear()
    try:
        _seed_real_data()

        # el proceso ya calculó evidencia ANTES del reset (escenario real:
        # un ciclo de escaneo o una carga de la Cabina que corrió justo
        # antes de que el reset se aplicara)
        li._evidence_cache.update(
            baseline=0.5, proposals=["algo-viejo"], condition_value_cache={},
            computed_on="2026-08-14", observation_count=73123, days_backed=30,
        )
        assert li._evidence_cache.get("observation_count") == 73123

        marker_dir = Path(tempfile.gettempdir()) / f"atlas_test_reset_marker_{_uuid.uuid4().hex}"
        orig_marker_fn = lr._marker_path
        lr._marker_path = lambda: marker_dir / "marker.txt"

        report = lr.reset_learning_once()
        assert report["applied"] is True
        assert report["evidence_cache_invalidada"] is True

        # la caché vieja (73.123 observaciones, proposals viejas) ya no existe
        assert li._evidence_cache == {}

        lr._marker_path = orig_marker_fn
    finally:
        _restore_all(saved)
        li._evidence_cache.clear()


def test_reset_no_falla_si_una_base_no_existe():
    saved = _fresh_all()
    try:
        # ninguna base tiene datos sembrados -- ni siquiera existen los archivos
        marker_dir = Path(tempfile.gettempdir()) / f"atlas_test_reset_marker_{_uuid.uuid4().hex}"
        orig_marker_fn = lr._marker_path
        lr._marker_path = lambda: marker_dir / "marker.txt"

        report = lr.reset_learning_once()
        assert report["applied"] is True
        assert len(report["targets"]) == len(lr._TARGETS)

        lr._marker_path = orig_marker_fn
    finally:
        _restore_all(saved)


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
