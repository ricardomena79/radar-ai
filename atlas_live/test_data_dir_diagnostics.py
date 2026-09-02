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


def test_filesystem_write_test_pasa_en_directorio_normal():
    tmp, old = _with_temp_data_dir()
    try:
        result = ddd.filesystem_write_test()
        assert result == {"passed": True}
        # cleanup real -- no debe quedar ningún archivo temporal atrás
        leftovers = [p for p in Path(tmp).iterdir() if p.name.startswith("_fs_write_test_")]
        assert leftovers == []
    finally:
        _restore(old)


def test_filesystem_write_test_reporta_error_si_no_puede_escribir(monkeypatch):
    # _data_dir_path() apunta a un ARCHIVO (no un directorio) -- abrir
    # "archivo/algo.tmp" falla con NotADirectoryError, un OSError real,
    # sin necesitar simular permisos de filesystem.
    tmp = Path(tempfile.mkdtemp(prefix="atlas_test_notadir_")) / "esto_es_un_archivo"
    tmp.write_bytes(b"x")
    monkeypatch.setattr(ddd, "_data_dir_path", lambda: tmp)
    result = ddd.filesystem_write_test()
    assert result["passed"] is False
    assert "error_type" in result
    assert "error_message" in result


def test_file_stat_info_de_archivo_inexistente():
    tmp = Path(tempfile.mkdtemp(prefix="atlas_test_stat_")) / "no_existe.db"
    info = ddd._file_stat_info(tmp)
    assert info["exists"] is False
    assert "size_bytes" not in info
    assert info["wal"]["exists"] is False
    assert info["shm"]["exists"] is False


def test_file_stat_info_incluye_wal_y_shm_si_existen():
    base = Path(tempfile.mkdtemp(prefix="atlas_test_stat_"))
    db = base / "fake.db"
    db.write_bytes(b"fake-db-bytes")
    (base / "fake.db-wal").write_bytes(b"wal-bytes-123")
    (base / "fake.db-shm").write_bytes(b"shm")
    info = ddd._file_stat_info(db)
    assert info["exists"] is True
    assert info["size_bytes"] == len(b"fake-db-bytes")
    assert info["readable"] is True
    assert info["writable"] is True
    assert info["wal"]["exists"] is True
    assert info["wal"]["size_bytes"] == len(b"wal-bytes-123")
    assert info["shm"]["exists"] is True


def test_sqlite_read_only_test_archivo_inexistente_se_salta_sin_crearlo():
    tmp = Path(tempfile.mkdtemp(prefix="atlas_test_sqlro_")) / "no_existe.db"
    result = ddd._sqlite_read_only_test(tmp)
    assert result == {"connect": "SKIPPED", "read_test": "SKIPPED", "error": "archivo no existe"}
    assert not tmp.exists()  # nunca lo crea


def test_sqlite_read_only_test_db_real_pasa():
    import sqlite3

    tmp = Path(tempfile.mkdtemp(prefix="atlas_test_sqlro_")) / "real.db"
    conn = sqlite3.connect(str(tmp))
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.commit()
    conn.close()

    result = ddd._sqlite_read_only_test(tmp)
    assert result == {"connect": "OK", "read_test": "OK", "error": None}


def test_sqlite_read_only_test_archivo_corrupto_reporta_error_sin_lanzar():
    tmp = Path(tempfile.mkdtemp(prefix="atlas_test_sqlro_")) / "corrupto.db"
    tmp.write_bytes(b"esto no es un archivo sqlite valido")

    result = ddd._sqlite_read_only_test(tmp)
    assert result["read_test"] == "FAIL"
    assert result["error"] is not None


def test_disk_usage_info_reporta_total_used_free():
    tmp, old = _with_temp_data_dir()
    try:
        info = ddd.disk_usage_info(Path(tmp))
        assert info["total_bytes"] > 0
        assert info["used_bytes"] >= 0
        assert info["free_bytes"] >= 0
    finally:
        _restore(old)


def test_directory_inventory_lista_archivos_por_tamano_descendente():
    tmp = Path(tempfile.mkdtemp(prefix="atlas_test_inventory_"))
    (tmp / "chico.txt").write_bytes(b"x" * 10)
    (tmp / "grande.txt").write_bytes(b"y" * 1000)
    sub = tmp / "subdir"
    sub.mkdir()
    (sub / "mediano.txt").write_bytes(b"z" * 100)

    result = ddd.directory_inventory(tmp)
    assert result["total_files_found"] == 3
    assert result["total_accounted_bytes"] == 1110
    sizes = [e["size_bytes"] for e in result["entries"]]
    assert sizes == sorted(sizes, reverse=True)
    assert result["entries"][0]["path"] == "grande.txt"
    assert any(e["path"] == os.path.join("subdir", "mediano.txt") for e in result["entries"])


def test_directory_inventory_directorio_vacio():
    tmp = Path(tempfile.mkdtemp(prefix="atlas_test_inventory_empty_"))
    result = ddd.directory_inventory(tmp)
    assert result["entries"] == []
    assert result["total_files_found"] == 0
    assert result["total_accounted_bytes"] == 0


def test_radar_candidates_table_counts_archivo_inexistente():
    tmp = Path(tempfile.mkdtemp(prefix="atlas_test_counts_")) / "no_existe.db"
    result = ddd.radar_candidates_table_counts(tmp)
    assert result == {"skipped": "archivo no existe"}
    assert not tmp.exists()


def test_radar_candidates_table_counts_db_real_con_schema():
    import sqlite3

    from atlas_live.radar.candidate_registry import _SCHEMA

    tmp = Path(tempfile.mkdtemp(prefix="atlas_test_counts_")) / "real.db"
    conn = sqlite3.connect(str(tmp))
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO candidate_detection (ticker, market_date, session, detected_at, sweep_id, "
        "price_at_detection, change_pct_at_detection, volume_at_detection, average_volume_at_detection, "
        "relative_volume_at_detection, dollar_volume_at_detection, gates_fired, created_at) "
        "VALUES ('AAA','2026-09-02','regular','2026-09-02T10:00:00Z','s1',10.0,5.0,1000,200,5.0,10000.0,'[]','2026-09-02T10:00:00Z')"
    )
    conn.commit()
    conn.close()

    result = ddd.radar_candidates_table_counts(tmp)
    assert result["candidate_detection"] == 1
    assert result["candidate_observation"] == 0
    assert result["candidate_intraday_metrics"] == 0
    for table in ddd._RADAR_CANDIDATES_TABLE_NAMES:
        assert table in result


def test_diagnostics_incluye_disk_usage_e_inventario_y_conteos(monkeypatch):
    tmp, old = _with_temp_data_dir()
    try:
        fake_dbs = {name: Path(tmp) / name for name in ddd._DATABASES_UNDER_DIAGNOSIS}
        monkeypatch.setattr(ddd, "_DATABASES_UNDER_DIAGNOSIS", fake_dbs)
        monkeypatch.setattr(ddd, "RADAR_CANDIDATES_DB_PATH", fake_dbs["radar_candidates.db"])

        info = ddd.diagnostics()

        assert "total_bytes" in info["disk_usage"]
        assert "entries" in info["directory_inventory"]
        assert info["radar_candidates_table_counts"] == {"skipped": "archivo no existe"}
    finally:
        _restore(old)


def test_diagnostics_incluye_filesystem_write_test_y_las_5_bases(monkeypatch):
    tmp, old = _with_temp_data_dir()
    try:
        fake_dbs = {name: Path(tmp) / name for name in ddd._DATABASES_UNDER_DIAGNOSIS}
        monkeypatch.setattr(ddd, "_DATABASES_UNDER_DIAGNOSIS", fake_dbs)

        info = ddd.diagnostics()

        assert "passed" in info["filesystem_write_test"]
        assert set(info["databases"].keys()) == set(fake_dbs.keys())
        for name, entry in info["databases"].items():
            assert entry["exists"] is False  # ninguna de las 5 existe en el tmp dir
            assert entry["connect"] == "SKIPPED"
            assert entry["read_test"] == "SKIPPED"
    finally:
        _restore(old)


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
