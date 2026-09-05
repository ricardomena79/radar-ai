"""Hito 6, Fase 6.4-D1 (2026-09-05, autorizado explícitamente): tests de
`shadow_retention_dry_run.py`. DBs temporales aisladas -- nunca se toca
ninguna base real. Ningún test de este archivo ejecuta DELETE/VACUUM/
ALTER TABLE sobre ninguna base -- ni siquiera las temporales."""

import sqlite3
import tempfile
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from atlas_live.radar import raw_data_consolidation_registry as registry
from atlas_live.radar import shadow_detector_registry as sreg
from atlas_live.radar import shadow_retention_dry_run as srd

_ORIG_RDC_DB = registry.DB_PATH
_ORIG_SHADOW_DB = sreg.DB_PATH


def _fresh():
    registry.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_srd_manifest_{_uuid.uuid4().hex}.db"
    sreg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_srd_shadow_{_uuid.uuid4().hex}.db"


def _restore():
    registry.DB_PATH = _ORIG_RDC_DB
    sreg.DB_PATH = _ORIG_SHADOW_DB


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _insert_manifest_block(
    ticker: str, market_date: str, status: str, max_ts_days_ago: int,
    row_count: int = 100, source_table: str = "shadow_candidate_detection",
    checksum: str = "abc123",
) -> None:
    """Inserta un bloque de manifiesto directo por SQL -- necesario porque
    ningún código de producción escribe hoy los estados
    `compaction_authorized`/`compacted` (a propósito, ver diseño de H6.4);
    para poder probar la lógica de elegibilidad contra esos estados hace
    falta simularlos acá. Usa `registry.DB_PATH` (ya redirigido a una DB
    temporal por `_fresh()`) -- nunca toca la base real."""
    block_key = f"{ticker}|{market_date}"
    now = datetime.now(timezone.utc).isoformat()
    max_ts = _iso(max_ts_days_ago)
    min_ts = _iso(max_ts_days_ago + 1)
    with sqlite3.connect(registry.DB_PATH) as conn:
        conn.execute(
            """INSERT INTO raw_data_consolidation
               (source_table, block_key, block_granularity, row_count_covered,
                min_timestamp_covered, max_timestamp_covered, summary_json,
                raw_data_checksum, methodology_version, computed_at, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                source_table, block_key, "ticker_market_date", row_count,
                min_ts, max_ts, "{}", checksum, "v1_count_sum_minmax", now, status, now,
            ),
        )


# --- 1) los 4 estados de la máquina se clasifican correctamente ----------

def test_estado_provisional_nunca_elegible():
    _fresh()
    try:
        registry._connect().close()
        _insert_manifest_block("AAA", "2026-01-01", "provisional", max_ts_days_ago=300)
        reporte = srd.dry_run_retention_report()
        assert reporte["n_eligible_blocks"] == 0
        assert reporte["not_eligible_breakdown"]["estado_no_compacted"] == 1
    finally:
        _restore()


def test_estado_verified_NO_es_suficiente_para_ser_elegible():
    # Pedido explícito del usuario: "verified" NO alcanza, solo "compacted".
    _fresh()
    try:
        registry._connect().close()
        _insert_manifest_block("BBB", "2026-01-01", "verified", max_ts_days_ago=300)
        reporte = srd.dry_run_retention_report()
        assert reporte["n_eligible_blocks"] == 0
        assert reporte["not_eligible_breakdown"]["estado_no_compacted"] == 1
    finally:
        _restore()


def test_estado_compaction_authorized_todavia_no_es_elegible():
    # Paso intermedio manual, todavía no es "compacted" -- tampoco elegible.
    _fresh()
    try:
        registry._connect().close()
        _insert_manifest_block("CCC", "2026-01-01", "compaction_authorized", max_ts_days_ago=300)
        reporte = srd.dry_run_retention_report()
        assert reporte["n_eligible_blocks"] == 0
        assert reporte["not_eligible_breakdown"]["estado_no_compacted"] == 1
    finally:
        _restore()


def test_estado_compacted_y_mas_de_180_dias_es_elegible():
    _fresh()
    try:
        registry._connect().close()
        _insert_manifest_block("DDD", "2026-01-01", "compacted", max_ts_days_ago=200, row_count=555)
        reporte = srd.dry_run_retention_report()
        assert reporte["n_eligible_blocks"] == 1
        assert reporte["eligible_blocks"][0]["block_key"] == "DDD|2026-01-01"
        assert reporte["n_rows_eligible"] == 555
        assert reporte["estimated_bytes_recoverable"] == 555 * srd.ESTIMATED_BYTES_PER_ROW
    finally:
        _restore()


# --- 2) bloques recientes, aunque compacted, no son elegibles -------------

def test_bloque_compacted_pero_reciente_no_es_elegible():
    _fresh()
    try:
        registry._connect().close()
        _insert_manifest_block("EEE", "2026-09-01", "compacted", max_ts_days_ago=10)
        reporte = srd.dry_run_retention_report()
        assert reporte["n_eligible_blocks"] == 0
        assert reporte["not_eligible_breakdown"]["demasiado_reciente"] == 1
    finally:
        _restore()


def test_limite_exacto_de_retencion_no_es_elegible_un_dia_antes():
    # Un bloque justo por DEBAJO del piso de retención (179 días) no debe
    # colarse -- confirma que la comparación es estricta, no aproximada.
    _fresh()
    try:
        registry._connect().close()
        _insert_manifest_block("FFF", "2026-01-01", "compacted", max_ts_days_ago=179)
        reporte = srd.dry_run_retention_report(retention_days=180)
        assert reporte["n_eligible_blocks"] == 0
        assert reporte["not_eligible_breakdown"]["demasiado_reciente"] == 1
    finally:
        _restore()


def test_retention_days_menor_al_piso_se_rechaza():
    _fresh()
    try:
        registry._connect().close()
        try:
            srd.dry_run_retention_report(retention_days=30)
            assert False, "se esperaba ValueError -- 30 < RETENTION_MIN_DAYS"
        except ValueError:
            pass
    finally:
        _restore()


# --- 3) source_table distinto nunca es elegible ---------------------------

def test_source_table_distinto_no_afecta_ni_aparece():
    _fresh()
    try:
        registry._connect().close()
        _insert_manifest_block(
            "GGG", "2026-01-01", "compacted", max_ts_days_ago=300,
            source_table="candidate_observation",
        )
        reporte = srd.dry_run_retention_report()
        assert reporte["n_eligible_blocks"] == 0
        assert reporte["total_blocks_scanned"] == 0  # list_blocks() filtra por source_table
    finally:
        _restore()


# --- 4) garantía estructural: el dry-run NUNCA abre shadow_unified_detector.db --

def test_dry_run_nunca_abre_shadow_unified_detector_db(monkeypatch):
    _fresh()
    try:
        registry._connect().close()
        _insert_manifest_block("HHH", "2026-01-01", "compacted", max_ts_days_ago=300)

        original_connect = sqlite3.connect

        def _guarded_connect(*args, **kwargs):
            target = str(args[0]) if args else str(kwargs.get("database", ""))
            if "shadow_unified_detector" in target:
                raise AssertionError(
                    "dry_run_retention_report() intentó abrir shadow_unified_detector.db -- PROHIBIDO"
                )
            return original_connect(*args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", _guarded_connect)
        reporte = srd.dry_run_retention_report()
        assert reporte["n_eligible_blocks"] == 1
    finally:
        _restore()


def _fuente_sin_docstrings(modulo) -> str:
    """Quita los bloques `\"\"\"...\"\"\"` (docstrings de módulo/función) antes
    de buscar patrones -- el propio módulo MENCIONA en prosa
    "shadow_detector_registry"/"VACUUM" para explicar qué NO hace, y esas
    menciones no deben disparar un falso positivo en los chequeos
    estáticos de abajo. Solo importa el CÓDIGO real (imports/llamadas)."""
    import re
    fuente = open(modulo.__file__, encoding="utf-8").read()
    return re.sub(r'""".*?"""', "", fuente, flags=re.DOTALL)


def test_dry_run_no_importa_shadow_detector_registry():
    # Chequeo estático vía AST (no substring matching -- el propio módulo
    # menciona "shadow_detector_registry"/"importa" en prosa dentro de su
    # docstring para EXPLICAR la garantía, lo que rompería un chequeo de
    # texto ingenuo). Se inspeccionan los nodos Import/ImportFrom reales
    # del árbol sintáctico -- estructuralmente imposible que haya un
    # import de ese módulo sin que aparezca acá.
    import ast

    import atlas_live.radar.shadow_retention_dry_run as modulo

    assert not hasattr(modulo, "sreg")
    assert not hasattr(modulo, "shadow_detector_registry")

    arbol = ast.parse(open(modulo.__file__, encoding="utf-8").read())
    nombres_importados = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            nombres_importados.update(alias.name for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom):
            modulo_base = nodo.module or ""
            nombres_importados.add(modulo_base)
            nombres_importados.update(f"{modulo_base}.{alias.name}" for alias in nodo.names)

    for nombre in nombres_importados:
        assert "shadow_detector_registry" not in nombre, f"import real encontrado: {nombre}"


# --- 5) checksum inconsistente -- lógica preparada para la fase destructiva futura --

def test_verify_checksum_coincide_cuando_el_dato_no_cambio():
    _fresh()
    try:
        sreg._connect().close()
        sreg.record_shadow_detection(
            "III", "2026-01-01", "regular", 10.0, 5.0, 1000, 500, 2.0, 10_000.0,
            "tradier", "tradier_last", False, "broad_equity",
            gates_fired=[{"gate": "x"}], snapshot={"a": 1},
        )
        from atlas_live.radar import raw_data_consolidation as rdc
        analisis = rdc.analyze_block("shadow_candidate_detection", "III", "2026-01-01")
        bloque_manifiesto = {
            "source_table": "shadow_candidate_detection",
            "block_key": "III|2026-01-01",
            "raw_data_checksum": analisis["raw_data_checksum"],
            "row_count_covered": analisis["row_count_covered"],
        }
        resultado = srd.verify_block_checksum_still_matches(bloque_manifiesto)
        assert resultado["matches"] is True
        assert resultado["reason"] is None
    finally:
        _restore()


def test_verify_checksum_detecta_inconsistencia_cuando_el_dato_cambio():
    # Simula: el manifiesto quedó persistido con un checksum viejo, pero
    # el dato crudo cambió después (ej. se agregó una fila nueva al mismo
    # bloque) -- la reverificación debe detectarlo, nunca asumir que sigue
    # siendo seguro.
    _fresh()
    try:
        sreg._connect().close()
        sreg.record_shadow_detection(
            "JJJ", "2026-01-01", "regular", 10.0, 5.0, 1000, 500, 2.0, 10_000.0,
            "tradier", "tradier_last", False, "broad_equity",
            gates_fired=[{"gate": "x"}], snapshot={"a": 1},
        )
        bloque_manifiesto_desactualizado = {
            "source_table": "shadow_candidate_detection",
            "block_key": "JJJ|2026-01-01",
            "raw_data_checksum": "checksum_viejo_que_ya_no_coincide",
            "row_count_covered": 1,
        }
        resultado = srd.verify_block_checksum_still_matches(bloque_manifiesto_desactualizado)
        assert resultado["matches"] is False
        assert "cambió" in resultado["reason"]
    finally:
        _restore()


def test_verify_checksum_bloque_sin_filas_reales_no_coincide():
    _fresh()
    try:
        sreg._connect().close()
        bloque_manifiesto_huerfano = {
            "source_table": "shadow_candidate_detection",
            "block_key": "NOEXISTE|2026-01-01",
            "raw_data_checksum": "cualquiera",
            "row_count_covered": 5,
        }
        resultado = srd.verify_block_checksum_still_matches(bloque_manifiesto_huerfano)
        assert resultado["matches"] is False
    finally:
        _restore()


# --- 6) confirmación general: cero DELETE/VACUUM/ALTER en todo el módulo --

def test_modulo_nunca_ejecuta_sql_directo():
    # El módulo delega TODO acceso a datos en `registry.list_blocks()` y
    # `rdc.analyze_block()` (ambos ya existentes, ya de solo lectura, sin
    # tocar) -- confirma que `shadow_retention_dry_run.py` en sí mismo
    # NUNCA llama a `.execute(`/`sqlite3.connect(` directamente, así que
    # es estructuralmente imposible que ejecute un DELETE/VACUUM/ALTER
    # TABLE propio, sin importar qué digan sus strings de prosa (el campo
    # "note" del reporte SÍ menciona esas palabras para explicarle al
    # usuario la garantía -- eso es texto informativo, no código).
    import atlas_live.radar.shadow_retention_dry_run as modulo
    codigo = _fuente_sin_docstrings(modulo)
    assert ".execute(" not in codigo
    assert "sqlite3.connect" not in codigo
