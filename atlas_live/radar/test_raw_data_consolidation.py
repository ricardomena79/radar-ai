"""Tests de Hito 2 -- consolidación de datos crudos (2026-09-02,
autorizado explícitamente): `raw_data_consolidation.py` (análisis),
`raw_data_consolidation_registry.py` (persistencia del manifiesto),
`raw_data_consolidation_pipeline.py` (orquestador). DBs temporales
aisladas, fixtures reales insertadas vía las funciones de escritura ya
existentes de `candidate_registry.py`/`shadow_detector_registry.py`.

Confirma: persistencia correcta, verificación posterior, idempotencia
(evitar doble contabilización), checksum determinista, metodología
versionada, que `status` nunca avanza automáticamente más allá de
`verified`, y que ninguna consulta usa `GROUP BY` global ni ordena más
de un bloque a la vez."""

import sqlite3
import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.radar import candidate_registry as reg
from atlas_live.radar import raw_data_consolidation as rdc
from atlas_live.radar import raw_data_consolidation_pipeline as pipeline
from atlas_live.radar import raw_data_consolidation_registry as registry
from atlas_live.radar import shadow_detector_registry as sreg

_ORIG_REG_DB = reg.DB_PATH
_ORIG_SHADOW_DB = sreg.DB_PATH
_ORIG_RDC_DB = registry.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_rdc_reg_{_uuid.uuid4().hex}.db"
    sreg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_rdc_shadow_{_uuid.uuid4().hex}.db"
    registry.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_rdc_manifest_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None


def _restore():
    reg.DB_PATH = _ORIG_REG_DB
    sreg.DB_PATH = _ORIG_SHADOW_DB
    registry.DB_PATH = _ORIG_RDC_DB


def _detect(ticker, market_date, detected_at, session="regular"):
    reg.record_detection(
        ticker, market_date, session, detected_at, "sweep-1",
        price_at_detection=10.0, change_pct_at_detection=6.0,
        volume_at_detection=500_000, average_volume_at_detection=100_000,
        relative_volume_at_detection=5.0, dollar_volume_at_detection=5_000_000.0,
        gates_fired=[{"gate": "cambio_de_precio"}],
    )


def _observe(ticker, market_date, observed_at, price=10.0, volume=100_000):
    reg.record_observation(
        ticker, market_date, observed_at, "sweep-1",
        price=price, change_pct=1.0, volume=volume, relative_volume=1.0, gates_fired_now=[],
    )


def _shadow(ticker, market_date, detected_at, price=10.0, volume=100_000):
    sreg.record_shadow_detection(
        ticker=ticker, market_date=market_date, session="regular",
        price=price, change_pct=6.0, volume=volume, average_volume=100_000,
        relative_volume=5.0, dollar_volume=price * volume,
        price_source="tradier", price_basis="tradier_last", price_is_stale=False,
        universe_source="piggyback_radar", gates_fired=[{"gate": "cambio_de_precio"}],
        snapshot={"price": price},
    )
    with sreg._connect() as conn:
        conn.execute(
            "UPDATE shadow_candidate_detection SET detected_at=? WHERE ticker=? AND market_date=? "
            "AND id = (SELECT MAX(id) FROM shadow_candidate_detection WHERE ticker=? AND market_date=?)",
            (detected_at, ticker, market_date, ticker, market_date),
        )
        conn.commit()


# ---------------------------------------------------------------------
# raw_data_consolidation.py -- análisis por bloque
# ---------------------------------------------------------------------

def test_analyze_block_agrega_correctamente_candidate_observation():
    _fresh()
    try:
        _observe("AAA", "2026-08-15", "2026-08-15T14:30:00+00:00", price=10.0, volume=100)
        _observe("AAA", "2026-08-15", "2026-08-15T14:31:00+00:00", price=10.5, volume=200)
        _observe("AAA", "2026-08-15", "2026-08-15T14:32:00+00:00", price=11.0, volume=300)
        _observe("BBB", "2026-08-15", "2026-08-15T14:30:00+00:00", price=5.0, volume=50)  # otro ticker, no debe mezclarse

        r = rdc.analyze_block("candidate_observation", "AAA", "2026-08-15")
        assert r["row_count_covered"] == 3
        assert r["summary"]["n_observaciones"] == 3
        assert r["summary"]["max_price_visto"] == 11.0
        assert r["summary"]["sum_volume"] == 600
        assert r["min_timestamp_covered"] == "2026-08-15T14:30:00+00:00"
        assert r["max_timestamp_covered"] == "2026-08-15T14:32:00+00:00"
        assert r["block_key"] == "AAA|2026-08-15"
        assert r["methodology_version"] == rdc.METHODOLOGY_VERSION
    finally:
        _restore()


def test_analyze_block_shadow_candidate_detection():
    _fresh()
    try:
        _shadow("XYZ", "2026-08-26", "2026-08-26T14:00:00+00:00", price=20.0, volume=1000)
        _shadow("XYZ", "2026-08-26", "2026-08-26T14:01:00+00:00", price=21.0, volume=1100)

        r = rdc.analyze_block("shadow_candidate_detection", "XYZ", "2026-08-26")
        assert r["row_count_covered"] == 2
        assert r["summary"]["max_price_visto"] == 21.0
    finally:
        _restore()


def test_analyze_block_sin_filas_devuelve_none():
    _fresh()
    try:
        # Fuerza la creación real del archivo (como en producción, donde
        # candidate_observation.db ya existe siempre) -- probar contra un
        # archivo que directamente no existe todavía no es el escenario
        # real que este test quiere cubrir.
        _observe("OTRO", "2026-08-15", "2026-08-15T14:30:00+00:00")
        assert rdc.analyze_block("candidate_observation", "NOEXISTE", "2026-08-15") is None
    finally:
        _restore()


def test_analyze_block_source_table_invalida_lanza():
    import pytest
    with pytest.raises(ValueError):
        rdc.analyze_block("candidate_detection", "AAA", "2026-08-15")


def test_checksum_es_determinista():
    _fresh()
    try:
        _observe("AAA", "2026-08-15", "2026-08-15T14:30:00+00:00", price=10.0, volume=100)
        r1 = rdc.analyze_block("candidate_observation", "AAA", "2026-08-15")
        r2 = rdc.analyze_block("candidate_observation", "AAA", "2026-08-15")
        assert r1["raw_data_checksum"] == r2["raw_data_checksum"]

        # Un dato crudo distinto -> checksum distinto.
        _observe("AAA", "2026-08-15", "2026-08-15T14:31:00+00:00", price=99.0, volume=1)
        r3 = rdc.analyze_block("candidate_observation", "AAA", "2026-08-15")
        assert r3["raw_data_checksum"] != r1["raw_data_checksum"]
    finally:
        _restore()


def test_ro_connect_de_raw_data_consolidation_bloquea_escritura():
    _fresh()
    try:
        _observe("AAA", "2026-08-15", "2026-08-15T14:30:00+00:00")
        conn = rdc._ro_connect(reg.DB_PATH)
        try:
            import pytest
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("INSERT INTO candidate_observation (ticker, market_date, observed_at, "
                              "created_at) VALUES ('X','2026-08-15','t','t')")
        finally:
            conn.close()
    finally:
        _restore()


# ---------------------------------------------------------------------
# raw_data_consolidation_registry.py -- persistencia del manifiesto
# ---------------------------------------------------------------------

def test_record_provisional_y_get_block():
    _fresh()
    try:
        inserto = registry.record_provisional(
            source_table="candidate_observation", block_key="AAA|2026-08-15",
            block_granularity="ticker_market_date", row_count_covered=3,
            min_timestamp_covered="2026-08-15T14:30:00+00:00", max_timestamp_covered="2026-08-15T14:32:00+00:00",
            summary={"n_observaciones": 3}, raw_data_checksum="abc123", methodology_version="v1",
        )
        assert inserto is True

        bloque = registry.get_block("candidate_observation", "AAA|2026-08-15", "v1")
        assert bloque is not None
        assert bloque["status"] == "provisional"
        assert bloque["verified_at"] is None
        assert bloque["summary"] == {"n_observaciones": 3}
    finally:
        _restore()


def test_record_provisional_es_idempotente_evita_doble_contabilizacion():
    _fresh()
    try:
        r1 = registry.record_provisional(
            source_table="candidate_observation", block_key="AAA|2026-08-15",
            block_granularity="ticker_market_date", row_count_covered=3,
            min_timestamp_covered=None, max_timestamp_covered=None,
            summary={}, raw_data_checksum="abc123", methodology_version="v1",
        )
        r2 = registry.record_provisional(
            source_table="candidate_observation", block_key="AAA|2026-08-15",
            block_granularity="ticker_market_date", row_count_covered=999,  # distinto -- no debe pisar
            min_timestamp_covered=None, max_timestamp_covered=None,
            summary={}, raw_data_checksum="OTRO_CHECKSUM", methodology_version="v1",
        )
        assert r1 is True
        assert r2 is False  # la segunda corrida NUNCA se cuenta como nueva

        bloque = registry.get_block("candidate_observation", "AAA|2026-08-15", "v1")
        assert bloque["row_count_covered"] == 3  # el valor original, nunca sobreescrito
        assert bloque["raw_data_checksum"] == "abc123"
    finally:
        _restore()


def test_metodologia_distinta_permite_una_fila_nueva_sin_pisar_la_anterior():
    _fresh()
    try:
        registry.record_provisional(
            source_table="candidate_observation", block_key="AAA|2026-08-15",
            block_granularity="ticker_market_date", row_count_covered=3,
            min_timestamp_covered=None, max_timestamp_covered=None,
            summary={}, raw_data_checksum="v1_checksum", methodology_version="v1",
        )
        r = registry.record_provisional(
            source_table="candidate_observation", block_key="AAA|2026-08-15",
            block_granularity="ticker_market_date", row_count_covered=3,
            min_timestamp_covered=None, max_timestamp_covered=None,
            summary={}, raw_data_checksum="v2_checksum", methodology_version="v2",
        )
        assert r is True  # metodologia distinta = fila nueva, no un duplicado

        v1 = registry.get_block("candidate_observation", "AAA|2026-08-15", "v1")
        v2 = registry.get_block("candidate_observation", "AAA|2026-08-15", "v2")
        assert v1["raw_data_checksum"] == "v1_checksum"
        assert v2["raw_data_checksum"] == "v2_checksum"
    finally:
        _restore()


def test_mark_verified_avanza_el_estado():
    _fresh()
    try:
        registry.record_provisional(
            source_table="candidate_observation", block_key="AAA|2026-08-15",
            block_granularity="ticker_market_date", row_count_covered=3,
            min_timestamp_covered=None, max_timestamp_covered=None,
            summary={}, raw_data_checksum="abc", methodology_version="v1",
        )
        cambio = registry.mark_verified("candidate_observation", "AAA|2026-08-15", "v1")
        assert cambio is True

        bloque = registry.get_block("candidate_observation", "AAA|2026-08-15", "v1")
        assert bloque["status"] == "verified"
        assert bloque["verified_at"] is not None
    finally:
        _restore()


def test_mark_verified_no_hace_nada_si_ya_no_esta_provisional():
    _fresh()
    try:
        registry.record_provisional(
            source_table="candidate_observation", block_key="AAA|2026-08-15",
            block_granularity="ticker_market_date", row_count_covered=3,
            min_timestamp_covered=None, max_timestamp_covered=None,
            summary={}, raw_data_checksum="abc", methodology_version="v1",
        )
        registry.mark_verified("candidate_observation", "AAA|2026-08-15", "v1")
        segundo_intento = registry.mark_verified("candidate_observation", "AAA|2026-08-15", "v1")
        assert segundo_intento is False  # ya no estaba en 'provisional' -- no-op seguro
    finally:
        _restore()


def test_status_nunca_avanza_a_compaction_authorized_ni_compacted_automaticamente():
    """Ningún código de este módulo (ni de raw_data_consolidation_pipeline.py)
    ESCRIBE 'compaction_authorized' ni 'compacted' -- busca el patrón real
    de asignación/SQL (`status='...'`), no cualquier mención en prosa
    (ambos nombres aparecen legítimamente en los docstrings, explicando
    qué NO se escribe en esta fase)."""
    import inspect

    src_registry = inspect.getsource(registry)
    src_pipeline = inspect.getsource(pipeline)
    patrones_prohibidos = (
        "status='compaction_authorized'", 'status="compaction_authorized"',
        "status='compacted'", 'status="compacted"',
        "SET status='compaction_authorized'", "SET status='compacted'",
    )
    for modulo_src, nombre in ((src_registry, "registry"), (src_pipeline, "pipeline")):
        for patron in patrones_prohibidos:
            assert patron not in modulo_src, f"{nombre}: encontrado patrón de escritura prohibido {patron!r}"

    # Comportamiento real, no solo texto: no existe ninguna función pública
    # que pueda escribir esos 2 estados -- solo `record_provisional`
    # ('provisional') y `mark_verified` ('verified') escriben `status` en
    # todo el módulo.
    funciones_publicas = [n for n in dir(registry) if not n.startswith("_") and callable(getattr(registry, n))]
    for nombre_prohibido in ("mark_compaction_authorized", "mark_compacted", "authorize_compaction", "compact"):
        assert nombre_prohibido not in funciones_publicas


def test_get_block_inexistente_devuelve_none():
    _fresh()
    try:
        assert registry.get_block("candidate_observation", "NOEXISTE|2026-08-15", "v1") is None
    finally:
        _restore()


def test_list_blocks_filtra_por_source_table():
    _fresh()
    try:
        registry.record_provisional(
            source_table="candidate_observation", block_key="AAA|2026-08-15",
            block_granularity="ticker_market_date", row_count_covered=1,
            min_timestamp_covered=None, max_timestamp_covered=None,
            summary={}, raw_data_checksum="c1", methodology_version="v1",
        )
        registry.record_provisional(
            source_table="shadow_candidate_detection", block_key="XYZ|2026-08-26",
            block_granularity="ticker_market_date", row_count_covered=1,
            min_timestamp_covered=None, max_timestamp_covered=None,
            summary={}, raw_data_checksum="c2", methodology_version="v1",
        )
        solo_obs = registry.list_blocks("candidate_observation")
        assert len(solo_obs) == 1
        assert solo_obs[0]["source_table"] == "candidate_observation"

        todos = registry.list_blocks()
        assert len(todos) == 2
    finally:
        _restore()


def test_source_table_invalida_lanza_en_todas_las_funciones():
    import pytest
    with pytest.raises(ValueError):
        registry.record_provisional(
            source_table="candidate_detection", block_key="x", block_granularity="x",
            row_count_covered=0, min_timestamp_covered=None, max_timestamp_covered=None,
            summary={}, raw_data_checksum="x", methodology_version="v1",
        )
    with pytest.raises(ValueError):
        registry.get_block("candidate_detection", "x", "v1")
    with pytest.raises(ValueError):
        registry.mark_verified("candidate_detection", "x", "v1")


# ---------------------------------------------------------------------
# raw_data_consolidation_pipeline.py -- orquestador end-to-end
# ---------------------------------------------------------------------

def test_consolidate_block_end_to_end_real():
    _fresh()
    try:
        _observe("AAA", "2026-08-15", "2026-08-15T14:30:00+00:00", price=10.0, volume=100)
        _observe("AAA", "2026-08-15", "2026-08-15T14:31:00+00:00", price=12.0, volume=200)

        r = pipeline.consolidate_block("candidate_observation", "AAA", "2026-08-15")

        assert r["ok"] is True
        assert r["already_consolidated"] is False
        assert r["status"] == "verified"
        assert r["row_count_covered"] == 2
        assert r["error"] is None
    finally:
        _restore()


def test_consolidate_block_segunda_corrida_es_idempotente():
    _fresh()
    try:
        _observe("AAA", "2026-08-15", "2026-08-15T14:30:00+00:00")

        r1 = pipeline.consolidate_block("candidate_observation", "AAA", "2026-08-15")
        r2 = pipeline.consolidate_block("candidate_observation", "AAA", "2026-08-15")

        assert r1["already_consolidated"] is False
        assert r2["already_consolidated"] is True
        assert r2["status"] == "verified"  # sigue verified, no retrocede ni duplica

        bloques = registry.list_blocks("candidate_observation")
        assert len(bloques) == 1  # nunca se duplicó la fila del manifiesto
    finally:
        _restore()


def test_consolidate_block_sin_filas_no_falla_y_no_persiste_nada():
    _fresh()
    try:
        _observe("OTRO", "2026-08-15", "2026-08-15T14:30:00+00:00")  # fuerza que el archivo exista
        r = pipeline.consolidate_block("candidate_observation", "NOEXISTE", "2026-08-15")
        assert r["ok"] is True
        assert "nada que consolidar" in r["error"]
        assert registry.list_blocks("candidate_observation") == []
    finally:
        _restore()


def test_consolidate_block_nunca_toca_candidate_observation_ni_candidate_detection():
    _fresh()
    try:
        _detect("AAA", "2026-08-15", "2026-08-15T14:30:00+00:00")
        _observe("AAA", "2026-08-15", "2026-08-15T14:30:00+00:00")
        _observe("AAA", "2026-08-15", "2026-08-15T14:31:00+00:00")

        conteo_obs_antes = reg.count_candidates_for_date("2026-08-15")  # candidate_detection
        with reg._connect() as conn:
            n_obs_antes = conn.execute("SELECT COUNT(*) AS n FROM candidate_observation").fetchone()["n"]

        pipeline.consolidate_block("candidate_observation", "AAA", "2026-08-15")
        pipeline.consolidate_block("candidate_observation", "AAA", "2026-08-15")  # dos veces

        conteo_obs_despues = reg.count_candidates_for_date("2026-08-15")
        with reg._connect() as conn:
            n_obs_despues = conn.execute("SELECT COUNT(*) AS n FROM candidate_observation").fetchone()["n"]

        assert conteo_obs_antes == conteo_obs_despues
        assert n_obs_antes == n_obs_despues  # ni una fila cruda cambio
    finally:
        _restore()


def test_consolidate_block_shadow_no_toca_shadow_candidate_detection():
    _fresh()
    try:
        _shadow("XYZ", "2026-08-26", "2026-08-26T14:00:00+00:00")
        antes = sreg.count_shadow_detections("2026-08-26")

        pipeline.consolidate_block("shadow_candidate_detection", "XYZ", "2026-08-26")

        despues = sreg.count_shadow_detections("2026-08-26")
        assert antes == despues == 1
    finally:
        _restore()


def test_consolidate_block_fuente_invalida_no_lanza_se_maneja_en_el_dict():
    resultado = pipeline.consolidate_block("candidate_detection", "AAA", "2026-08-15")
    assert resultado["ok"] is False
    assert "ValueError" in resultado["error"]


def test_no_group_by_global_en_el_codigo_de_analisis():
    """Verificación estática: ninguna consulta de `raw_data_consolidation.py`
    usa `GROUP BY` (agrupación global sobre toda la tabla) -- todo el
    análisis está acotado a `WHERE ticker=? AND market_date=?`, un bloque
    a la vez."""
    import inspect

    src = inspect.getsource(rdc)
    assert "GROUP BY" not in src.upper()
    assert "ORDER BY" not in src.upper()


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
