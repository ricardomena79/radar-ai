"""Tests de atlas_live/data_dir_diagnostics.py (2026-08-17) -- prueba
minima de persistencia (marcador no se sobrescribe, metadatos reales del
archivo). Aisla con ATLAS_DATA_DIR apuntando a un directorio temporal,
nunca toca el /data real ni historical_reference.db real."""

import os
import tempfile
from pathlib import Path

from atlas_live import data_dir_diagnostics as ddd


def _with_temp_data_dir():
    tmp = tempfile.mkdtemp(prefix="atlas_test_datadir_")
    old = os.environ.get("ATLAS_DATA_DIR")
    os.environ["ATLAS_DATA_DIR"] = tmp
    return tmp, old


def _restore(old):
    if old is None:
        os.environ.pop("ATLAS_DATA_DIR", None)
    else:
        os.environ["ATLAS_DATA_DIR"] = old


def test_write_marker_once_crea_marcador_nuevo():
    tmp, old = _with_temp_data_dir()
    try:
        result = ddd.write_marker_once()
        assert result["created"] is True
        assert result["already_existed"] is False
        assert "marker_id" in result["marker"]
        assert (Path(tmp) / ddd.MARKER_FILENAME).exists()
    finally:
        _restore(old)


def test_write_marker_once_nunca_sobrescribe_uno_existente():
    tmp, old = _with_temp_data_dir()
    try:
        first = ddd.write_marker_once()
        second = ddd.write_marker_once()
        assert second["created"] is False
        assert second["already_existed"] is True
        assert second["marker"]["marker_id"] == first["marker"]["marker_id"]
    finally:
        _restore(old)


def test_diagnostics_reporta_marcador_ausente_si_nunca_se_escribio():
    tmp, old = _with_temp_data_dir()
    try:
        info = ddd.diagnostics()
        assert info["persistence_marker"]["exists"] is False
        assert info["atlas_data_dir_resolved"] == str(Path(tmp).resolve())
        assert info["atlas_data_dir_exists"] is True
        assert info["atlas_data_dir_writable"] is True
    finally:
        _restore(old)


def test_diagnostics_reporta_marcador_presente_con_mismo_id():
    tmp, old = _with_temp_data_dir()
    try:
        written = ddd.write_marker_once()
        info = ddd.diagnostics()
        assert info["persistence_marker"]["exists"] is True
        assert info["persistence_marker"]["content"]["marker_id"] == written["marker"]["marker_id"]
    finally:
        _restore(old)


def test_diagnostics_reporta_db_ausente_cuando_nunca_se_creo():
    tmp, old = _with_temp_data_dir()
    try:
        info = ddd.diagnostics()
        # historical_reference.db se resuelve con el DB_PATH ya calculado
        # al importar reference_registry (proceso real) -- acá solo
        # confirmamos la forma del reporte cuando el archivo no existe ahí.
        db_info = info["historical_reference_db"]
        assert "path" in db_info
        assert "exists" in db_info
        if not db_info["exists"]:
            assert "size_bytes" not in db_info
    finally:
        _restore(old)


def test_diagnostics_reporta_tamano_y_fecha_si_la_db_existe():
    # Sustituye HISTORICAL_REFERENCE_DB_PATH por un archivo de prueba en un
    # directorio temporal -- nunca toca reference_registry.DB_PATH real ni
    # el archivo real (regla explícita: no tocar datos existentes).
    tmp = Path(tempfile.mkdtemp(prefix="atlas_test_dbfile_")) / "historical_reference.db"
    tmp.write_bytes(b"fake-sqlite-bytes-for-test")
    original = ddd.HISTORICAL_REFERENCE_DB_PATH
    ddd.HISTORICAL_REFERENCE_DB_PATH = tmp
    try:
        info = ddd.diagnostics()
        db_info = info["historical_reference_db"]
        assert db_info["exists"] is True
        assert db_info["size_bytes"] > 0
        assert "modified_at" in db_info
    finally:
        ddd.HISTORICAL_REFERENCE_DB_PATH = original


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
