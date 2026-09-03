"""Tests de `candidate_observation_diagnostics.py` (2026-09-03, auditoría
de espacio, autorizado explícitamente). DB temporal aislada por test."""

import inspect
import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.radar import candidate_observation_diagnostics as cod
from atlas_live.radar import candidate_registry as reg

_ORIG_DB = reg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_cod_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None


def _restore():
    reg.DB_PATH = _ORIG_DB


def _observe(ticker, market_date, n, price=10.0):
    for i in range(n):
        reg.record_observation(
            ticker, market_date, f"{market_date}T09:{i % 59:02d}:00+00:00", f"sweep-{i}",
            price=price, change_pct=1.0, volume=100, relative_volume=1.0, gates_fired_now=[],
        )


# --- pragma / file_sizes -----------------------------------------------

def test_pragma_diagnostics_sobre_db_inexistente():
    _fresh()
    try:
        rep = cod.pragma_diagnostics(reg.DB_PATH)
        assert "error" in rep
    finally:
        _restore()


def test_pragma_diagnostics_sobre_db_real():
    _fresh()
    try:
        _observe("AAA", "2026-08-24", 3)
        rep = cod.pragma_diagnostics(reg.DB_PATH)
        assert rep["journal_mode"] == "wal"
        assert rep["auto_vacuum"] == 0
        assert rep["page_size"] > 0
        assert rep["page_count"] >= 1
        assert rep["freelist_count"] is not None
        assert rep["estimated_used_bytes"] is not None
    finally:
        _restore()


def test_file_sizes_reporta_db_y_wal_shm():
    _fresh()
    try:
        _observe("AAA", "2026-08-24", 3)
        rep = cod.file_sizes(reg.DB_PATH)
        assert rep["db_bytes"] > 0
    finally:
        _restore()


def test_file_sizes_sobre_db_inexistente_no_lanza():
    _fresh()
    try:
        rep = cod.file_sizes(reg.DB_PATH)
        assert rep["db_bytes"] is None
    finally:
        _restore()


# --- conteos --------------------------------------------------------------

def test_total_rows_correcto():
    _fresh()
    try:
        _observe("AAA", "2026-08-24", 5)
        _observe("BBB", "2026-08-24", 3)
        assert cod.candidate_observation_total_rows(reg.DB_PATH) == 8
    finally:
        _restore()


def test_total_rows_sobre_db_inexistente_devuelve_none():
    _fresh()
    try:
        assert cod.candidate_observation_total_rows(reg.DB_PATH) is None
    finally:
        _restore()


# --- distribución por bloque --------------------------------------------

def test_block_distribution_usa_el_indice_existente():
    _fresh()
    try:
        _observe("AAA", "2026-08-24", 5)
        _observe("BBB", "2026-08-25", 10)
        rep = cod.block_distribution(reg.DB_PATH)
        assert rep["query_plan_usa_indice_existente"] is True
        assert any("idx_obs_ticker_date" in str(p.get("detail", "")) for p in rep["query_plan"])
    finally:
        _restore()


def test_block_distribution_estadisticas_correctas():
    _fresh()
    try:
        _observe("AAA", "2026-08-24", 5)
        _observe("BBB", "2026-08-24", 15)
        _observe("CCC", "2026-08-25", 1)
        rep = cod.block_distribution(reg.DB_PATH)
        assert rep["n_bloques"] == 3
        assert rep["min"] == 1
        assert rep["max"] == 15
        assert rep["mediana"] == 5
        nombres = {(b["ticker"], b["n_observaciones"]) for b in rep["top_20_bloques_mas_grandes"]}
        assert ("BBB", 15) in nombres
        chicos = {(b["ticker"], b["n_observaciones"]) for b in rep["bottom_20_bloques_mas_chicos"]}
        assert ("CCC", 1) in chicos
    finally:
        _restore()


def test_block_distribution_sobre_db_inexistente():
    _fresh()
    try:
        rep = cod.block_distribution(reg.DB_PATH)
        assert "error" in rep
    finally:
        _restore()


def test_block_distribution_top20_y_bottom20_acotados_a_20():
    _fresh()
    try:
        for i in range(30):
            _observe(f"T{i}", "2026-08-24", i + 1)
        rep = cod.block_distribution(reg.DB_PATH)
        assert len(rep["top_20_bloques_mas_grandes"]) == 20
        assert len(rep["bottom_20_bloques_mas_chicos"]) == 20
        assert rep["top_20_bloques_mas_grandes"][0]["n_observaciones"] == 30  # el más grande primero
        assert rep["bottom_20_bloques_mas_chicos"][0]["n_observaciones"] == 1  # el más chico primero
    finally:
        _restore()


def test_percentiles_monotonos():
    _fresh()
    try:
        for i in range(1, 51):
            _observe(f"T{i}", "2026-08-24", i)
        rep = cod.block_distribution(reg.DB_PATH)
        p = rep["percentiles"]
        assert p["p10"] <= p["p25"] <= p["p50"] <= p["p75"] <= p["p90"] <= p["p95"] <= p["p99"]
    finally:
        _restore()


def test_tope_defensivo_de_bloques(monkeypatch):
    _fresh()
    try:
        _observe("AAA", "2026-08-24", 2)
        monkeypatch.setattr(cod, "MAX_BLOCK_DISTRIBUTION_ROWS", 0)
        rep = cod.block_distribution(reg.DB_PATH)
        assert "error" in rep
        assert rep["n_bloques"] == 1
    finally:
        _restore()


# --- full_report / aislamiento -------------------------------------------

def test_full_report_ok_con_datos_reales():
    _fresh()
    try:
        _observe("AAA", "2026-08-24", 5)
        rep = cod.full_report()
        assert rep["ok"] is True
        assert rep["error"] is None
        assert rep["candidate_observation_total_rows"] == 5
    finally:
        _restore()


def test_full_report_nunca_lanza_ante_fallo_interno(monkeypatch):
    _fresh()
    try:
        def _boom(*a, **k):
            raise RuntimeError("fallo sintetico")
        monkeypatch.setattr(cod, "pragma_diagnostics", _boom)
        rep = cod.full_report()
        assert rep["ok"] is False
        assert "fallo sintetico" in rep["error"]
    finally:
        _restore()


# --- garantías de solo lectura -- pedidas explícitamente -------------------

def test_ro_connect_usa_mode_ro_y_query_only():
    fuente = inspect.getsource(cod._ro_connect)
    assert "mode=ro" in fuente
    assert 'conn.execute("PRAGMA query_only=ON")' in fuente


def test_modulo_nunca_contiene_ninguna_sentencia_de_escritura():
    # Busca la INVOCACIÓN real (conn.execute("...")), no menciones en
    # docstrings que explican por qué NO se usan -- mismo criterio ya
    # aplicado en Hito 2/3 para evitar falsos positivos de escaneo de texto.
    fuente = inspect.getsource(cod)
    invocaciones = [
        linea.strip() for linea in fuente.splitlines()
        if "conn.execute(" in linea or "conn.executescript(" in linea or "conn.executemany(" in linea
    ]
    texto_invocaciones = " ".join(invocaciones).upper()
    assert "CREATE TABLE" not in texto_invocaciones
    assert "CREATE INDEX" not in texto_invocaciones
    assert "INSERT INTO" not in texto_invocaciones
    assert "UPDATE " not in texto_invocaciones
    assert "DELETE FROM" not in texto_invocaciones
    # "PRAGMA auto_vacuum" es una LECTURA legítima del ajuste (contiene la
    # subcadena "VACUUM" sin ser el comando) -- se excluye explícitamente
    # antes de buscar el comando VACUUM real.
    assert "VACUUM" not in texto_invocaciones.replace("AUTO_VACUUM", "")
    assert "CHECKPOINT" not in texto_invocaciones
    assert "JOURNAL_MODE=WAL" not in texto_invocaciones.replace(" ", "")
    assert "conn.executescript" not in fuente
    assert "conn.executemany" not in fuente


def test_ninguna_funcion_publica_abre_connect_de_escritura():
    """Ninguna función pública del módulo usa `sqlite3.connect(` directo
    (sin `mode=ro` en la URI) -- todas pasan por `_ro_connect()`."""
    for nombre in ("pragma_diagnostics", "block_distribution", "candidate_observation_total_rows"):
        fuente = inspect.getsource(getattr(cod, nombre))
        assert "sqlite3.connect(" not in fuente  # solo _ro_connect() abre conexiones
        assert "_ro_connect" in fuente or "Path(path).exists()" in fuente
