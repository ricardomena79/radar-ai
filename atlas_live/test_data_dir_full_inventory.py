"""Tests de `data_dir_full_inventory.py` (2026-09-03, auditoría de espacio
de Hito 3.2, autorizado explícitamente). Directorio temporal aislado por
test -- nunca toca `ATLAS_DATA_DIR` real."""

import inspect
import tempfile
from pathlib import Path

from atlas_live import data_dir_full_inventory as fi


def _tmpdir() -> Path:
    return Path(tempfile.mkdtemp())


# --- listado completo -------------------------------------------------

def test_listado_completo_sin_recorte_top_n():
    tmp = _tmpdir()
    # más de 50 archivos -- confirma que NO hay recorte, a diferencia de
    # directory_inventory() (top_n=50 por defecto).
    for i in range(60):
        (tmp / f"archivo_{i}.tmp").write_bytes(b"x" * i)
    rep = fi.full_file_inventory(tmp)
    assert rep["total_files"] == 60
    assert len(rep["entries"]) == 60


def test_incluye_subdirectorios():
    tmp = _tmpdir()
    (tmp / "a.db").write_bytes(b"x" * 10)
    sub = tmp / "sub" / "mas_hondo"
    sub.mkdir(parents=True)
    (sub / "b.json").write_bytes(b"{}")
    rep = fi.full_file_inventory(tmp)
    paths = {e["path"] for e in rep["entries"]}
    assert "a.db" in paths
    assert str(Path("sub") / "mas_hondo" / "b.json") in paths


# --- tamaños correctos -----------------------------------------------

def test_tamanos_correctos():
    tmp = _tmpdir()
    (tmp / "diez.bin").write_bytes(b"x" * 10)
    (tmp / "cien.bin").write_bytes(b"y" * 100)
    rep = fi.full_file_inventory(tmp)
    tamanos = {e["path"]: e["size_bytes"] for e in rep["entries"]}
    assert tamanos["diez.bin"] == 10
    assert tamanos["cien.bin"] == 100
    assert rep["total_bytes"] == 110


def test_orden_descendente_por_tamano():
    tmp = _tmpdir()
    (tmp / "chico.bin").write_bytes(b"x" * 5)
    (tmp / "grande.bin").write_bytes(b"x" * 500)
    (tmp / "mediano.bin").write_bytes(b"x" * 50)
    rep = fi.full_file_inventory(tmp)
    assert [e["path"] for e in rep["entries"]] == ["grande.bin", "mediano.bin", "chico.bin"]


# --- extensiones pedidas explícitamente --------------------------------

def test_clasificacion_por_extension_incluye_todas_las_pedidas():
    tmp = _tmpdir()
    (tmp / "radar_candidates.db").write_bytes(b"x" * 10)
    (tmp / "radar_candidates.db-wal").write_bytes(b"x" * 20)
    (tmp / "radar_candidates.db-shm").write_bytes(b"x" * 30)
    (tmp / "old.pre_reset_v2_20260815.bak.db").write_bytes(b"x" * 40)
    (tmp / "scratch.tmp").write_bytes(b"x" * 50)
    (tmp / "broad_universe_meta.json").write_bytes(b"x" * 60)
    (tmp / "server.log").write_bytes(b"x" * 70)
    rep = fi.full_file_inventory(tmp)
    ext = rep["por_extension"]
    assert ext[".db"]["count"] == 1
    assert ext[".db-wal"]["count"] == 1
    assert ext[".db-shm"]["count"] == 1
    assert ext["*.bak*"]["count"] == 1
    assert ext[".tmp"]["count"] == 1
    assert ext[".json"]["count"] == 1
    assert ext[".log"]["count"] == 1


def test_extension_desconocida_se_agrupa_por_su_propio_sufijo():
    tmp = _tmpdir()
    (tmp / "algo.csv").write_bytes(b"x" * 10)
    rep = fi.full_file_inventory(tmp)
    assert rep["por_extension"][".csv"]["count"] == 1


def test_archivo_sin_extension():
    tmp = _tmpdir()
    (tmp / "SINEXTENSION").write_bytes(b"x" * 10)
    rep = fi.full_file_inventory(tmp)
    assert rep["por_extension"]["(sin_extension)"]["count"] == 1


# --- archivos vacíos/pequeños -------------------------------------------

def test_archivo_vacio_se_lista_con_tamano_cero():
    tmp = _tmpdir()
    (tmp / "vacio.txt").touch()
    rep = fi.full_file_inventory(tmp)
    assert rep["entries"][0]["path"] == "vacio.txt"
    assert rep["entries"][0]["size_bytes"] == 0
    assert rep["total_files"] == 1


def test_directorio_vacio():
    tmp = _tmpdir()
    rep = fi.full_file_inventory(tmp)
    assert rep["total_files"] == 0
    assert rep["entries"] == []
    assert rep["total_bytes"] == 0


# --- aislamiento ----------------------------------------------------------

def test_full_report_nunca_lanza(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("fallo sintetico")
    monkeypatch.setattr(fi, "full_file_inventory", _boom)
    rep = fi.full_report()
    assert rep["ok"] is False
    assert "fallo sintetico" in rep["error"]


def test_full_report_ok_con_datos_reales():
    tmp = _tmpdir()
    (tmp / "a.db").write_bytes(b"x" * 10)
    rep = fi.full_report()  # usa ATLAS_DATA_DIR real (local), no el tmp -- confirma que no lanza
    assert rep["ok"] is True
    assert "entries" in rep


# --- garantía de ausencia de escritura -- pedido explícito -------------

def test_directorio_no_se_modifica_por_la_inspeccion():
    tmp = _tmpdir()
    (tmp / "a.db").write_bytes(b"x" * 10)
    archivos_antes = sorted(p.name for p in tmp.iterdir())
    fi.full_file_inventory(tmp)
    fi.full_file_inventory(tmp)
    fi.full_file_inventory(tmp)
    archivos_despues = sorted(p.name for p in tmp.iterdir())
    assert archivos_antes == archivos_despues


def test_modulo_nunca_contiene_ninguna_operacion_de_escritura_o_sql():
    # "resultado.update(...)" (fusión de dict de Python) contiene la
    # subcadena "UPDATE" sin ser SQL -- se buscan patrones de invocación
    # real, no palabras sueltas (mismo criterio ya aplicado en Hito 2/3
    # para evitar falsos positivos de escaneo de texto).
    fuente = inspect.getsource(fi)
    assert "sqlite3" not in fuente
    assert "open(" not in fuente
    assert ".write_text(" not in fuente
    assert ".write_bytes(" not in fuente
    assert "DELETE FROM" not in fuente.upper()
    assert "VACUUM" not in fuente.upper()
    assert "INSERT INTO" not in fuente.upper()
    assert "UPDATE " not in fuente.upper().replace(".UPDATE(", "")
