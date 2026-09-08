"""Tests de `capacity_registry.py` (2026-09-07, autorizado explícitamente
-- Capacity Monitor). DB temporal aislada por test, mismo patrón que
`test_knowledge_eligibility_registry.py`."""

import inspect
import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live import capacity_registry as creg

_ORIG_DB = creg.DB_PATH


def _fresh():
    creg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_capacity_{_uuid.uuid4().hex}.db"


def _restore():
    creg.DB_PATH = _ORIG_DB


def test_latest_snapshot_sin_db_devuelve_none():
    _fresh()
    try:
        assert creg.latest_snapshot() is None
        assert creg.list_snapshots() == []
    finally:
        _restore()


def test_record_y_latest_snapshot():
    _fresh()
    try:
        creg.record_snapshot(total_bytes=10_000_000_000, used_bytes=6_000_000_000, free_bytes=4_000_000_000,
                              observed_at="2026-09-07T10:00:00+00:00")
        latest = creg.latest_snapshot()
        assert latest["total_bytes"] == 10_000_000_000
        assert latest["used_bytes"] == 6_000_000_000
        assert latest["free_bytes"] == 4_000_000_000
        assert latest["observed_at"] == "2026-09-07T10:00:00+00:00"
    finally:
        _restore()


def test_latest_snapshot_devuelve_el_mas_reciente():
    _fresh()
    try:
        creg.record_snapshot(10, 1, 9, observed_at="2026-09-07T10:00:00+00:00")
        creg.record_snapshot(10, 2, 8, observed_at="2026-09-07T11:00:00+00:00")
        creg.record_snapshot(10, 3, 7, observed_at="2026-09-07T09:00:00+00:00")  # insertado fuera de orden
        latest = creg.latest_snapshot()
        assert latest["observed_at"] == "2026-09-07T11:00:00+00:00"
        assert latest["used_bytes"] == 2
    finally:
        _restore()


def test_list_snapshots_orden_ascendente_y_since():
    _fresh()
    try:
        creg.record_snapshot(10, 1, 9, observed_at="2026-09-07T09:00:00+00:00")
        creg.record_snapshot(10, 2, 8, observed_at="2026-09-07T10:00:00+00:00")
        creg.record_snapshot(10, 3, 7, observed_at="2026-09-07T11:00:00+00:00")
        todos = creg.list_snapshots()
        assert [s["used_bytes"] for s in todos] == [1, 2, 3]  # mas viejo primero

        desde = creg.list_snapshots(since="2026-09-07T10:00:00+00:00")
        assert [s["used_bytes"] for s in desde] == [2, 3]
    finally:
        _restore()


def test_record_snapshot_es_append_only_nunca_pisa():
    _fresh()
    try:
        creg.record_snapshot(10, 1, 9)
        creg.record_snapshot(10, 1, 9)  # mismos valores -- igual se agrega, no hay unique
        assert len(creg.list_snapshots()) == 2
    finally:
        _restore()


def test_modulo_nunca_escribe_update_ni_delete():
    """Escaneo estático -- mismo patrón que `knowledge_eligibility_registry.py`:
    ninguna sentencia de escritura destructiva en todo el archivo."""
    src = inspect.getsource(creg)
    assert "UPDATE " not in src.upper().replace("PRAGMA JOURNAL_MODE=WAL", "")
    assert "DELETE FROM" not in src.upper()
    assert "DROP TABLE" not in src.upper()
    assert "VACUUM" not in src.upper()


def test_ro_connect_usa_mode_ro_y_query_only():
    src = inspect.getsource(creg._ro_connect)
    assert "mode=ro" in src
    assert "query_only=ON" in src
    assert "journal_mode" not in src.lower()
