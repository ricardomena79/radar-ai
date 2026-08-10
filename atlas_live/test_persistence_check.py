"""Tests del chequeo de persistencia (2026-08-10). Offline, DB/dirs temporales.

Simula dos ARRANQUES apuntando al mismo directorio (como haría un Volume que
sobrevive un redeploy) y verifica que el canario detecta la supervivencia.

NOTA: escritos sin poder ejecutarse localmente (sin Python en el PC); ejecución
en Railway/CI o por el usuario.
"""

import os
import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live import persistence_check
from atlas_live.signals import signal_registry


def _point_to(tmp: Path):
    signal_registry.DB_PATH = tmp / "signal_registry.db"


def test_pendiente_luego_probado_si_el_dir_sobrevive():
    tmp = Path(tempfile.gettempdir()) / f"atlas_persist_{_uuid.uuid4().hex}"
    tmp.mkdir(parents=True, exist_ok=True)
    orig_db = signal_registry.DB_PATH
    os.environ["ATLAS_DATA_DIR"] = str(tmp)  # "configurado"
    try:
        _point_to(tmp)
        # Arranque 1: configurado pero sin prueba todavía.
        s1 = persistence_check.status()
        assert s1["atlas_data_dir_set"] is True
        assert s1["critical"] is False
        assert s1["boots_recorded"] == 1
        assert s1["survived_at_least_one_restart"] is False
        assert s1["level"] == "PENDING_PROOF"

        # Arranque 2: el directorio (canario) sobrevivió -> persistencia PROBADA.
        s2 = persistence_check.status()
        assert s2["boots_recorded"] == 2
        assert s2["survived_at_least_one_restart"] is True
        assert s2["level"] == "OK"
    finally:
        signal_registry.DB_PATH = orig_db
        os.environ.pop("ATLAS_DATA_DIR", None)


def test_critico_sin_atlas_data_dir():
    tmp = Path(tempfile.gettempdir()) / f"atlas_persist_{_uuid.uuid4().hex}"
    tmp.mkdir(parents=True, exist_ok=True)
    orig_db = signal_registry.DB_PATH
    os.environ.pop("ATLAS_DATA_DIR", None)  # NO configurado
    try:
        _point_to(tmp)
        s = persistence_check.status()
        assert s["atlas_data_dir_set"] is False
        assert s["critical"] is True
        assert s["level"] == "CRITICAL"
    finally:
        signal_registry.DB_PATH = orig_db


def test_enforce_aborta_si_require_y_critico():
    tmp = Path(tempfile.gettempdir()) / f"atlas_persist_{_uuid.uuid4().hex}"
    tmp.mkdir(parents=True, exist_ok=True)
    orig_db = signal_registry.DB_PATH
    os.environ.pop("ATLAS_DATA_DIR", None)
    os.environ["ATLAS_REQUIRE_PERSISTENCE"] = "true"
    try:
        _point_to(tmp)
        raised = False
        try:
            persistence_check.enforce()
        except RuntimeError:
            raised = True
        assert raised is True  # NO continúa como si todo estuviera bien
    finally:
        signal_registry.DB_PATH = orig_db
        os.environ.pop("ATLAS_REQUIRE_PERSISTENCE", None)
