"""Tests de `candidate_observation_compaction.py` (2026-09-07, autorizado
explícitamente). DBs temporales aisladas para `candidate_registry.py`
(`radar_candidates.db`) y `raw_data_consolidation_registry.py`
(`raw_data_consolidation.db`) -- NUNCA toca ninguna base real. Mismo
patrón `_fresh()`/`_restore()` con tempfile ya usado en todo Hito 2/3."""

import inspect
import sqlite3
import tempfile
import uuid as _uuid
from datetime import date, timedelta
from pathlib import Path

from atlas_live.radar import candidate_observation_compaction as coc
from atlas_live.radar import candidate_registry as reg
from atlas_live.radar import raw_data_consolidation as rdc
from atlas_live.radar import raw_data_consolidation_registry as rdc_registry

_ORIG_REG_DB = reg.DB_PATH
_ORIG_RDC_DB = rdc_registry.DB_PATH

TODAY = "2026-09-07"


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_coc_reg_{_uuid.uuid4().hex}.db"
    rdc_registry.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_coc_rdc_{_uuid.uuid4().hex}.db"


def _restore():
    reg.DB_PATH = _ORIG_REG_DB
    rdc_registry.DB_PATH = _ORIG_RDC_DB


def _days_ago(n):
    return (date.fromisoformat(TODAY) - timedelta(days=n)).isoformat()


def _seed_observations(market_date, tickers, n_per_ticker=3):
    """Inserta filas sintéticas de `candidate_observation` directo por SQL
    -- mismo schema real de `candidate_registry.py`, sin pasar por todo el
    pipeline de `candidate_tracker.py` (innecesario para estos tests)."""
    with reg._connect() as conn:
        for t in tickers:
            for i in range(n_per_ticker):
                conn.execute(
                    """INSERT INTO candidate_observation
                       (ticker, market_date, observed_at, sweep_id, price, change_pct, volume,
                        relative_volume, gates_fired_now, vwap, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (t, market_date, f"{market_date}T{10+i:02d}:00:00+00:00", f"sweep{i}",
                     10.0 + i, 5.0, 1000 * (i + 1), 2.0, "[]", None, f"{market_date}T{10+i:02d}:00:00+00:00"),
                )
        conn.commit()


def _seed_detection_and_outcome(market_date, ticker):
    reg.record_detection(
        ticker, market_date, "regular", f"{market_date}T09:31:00+00:00", "sweep0",
        10.0, 5.0, 1000, 500, 2.0, 10000.0, [{"name": "gate_x", "reason": "r", "value": 1.0}],
    )
    reg.record_outcome(
        ticker=ticker, market_date=market_date, run_up_before_detection_pct=None,
        max_price_after_detection=12.0, max_return_after_detection_pct=20.0, minutes_to_max=30.0,
        reached_20=True, reached_50=False, reached_100=False, category="FINAL", is_final=True,
    )


def _count_observations(market_date=None):
    with reg._connect() as conn:
        if market_date is None:
            return conn.execute("SELECT COUNT(*) FROM candidate_observation").fetchone()[0]
        return conn.execute(
            "SELECT COUNT(*) FROM candidate_observation WHERE market_date=?", (market_date,)
        ).fetchone()[0]


def _full_pipeline_to_authorized(market_date):
    coc.run_provisional_for_date(market_date, today=TODAY)
    coc.run_verification_for_date(market_date)
    coc.authorize_compaction_for_date(market_date)


# --- 1) bloque diario correcto ----------------------------------------------

def test_bloque_diario_correcto():
    _fresh()
    try:
        old_date = _days_ago(100)
        _seed_observations(old_date, ["AAA", "BBB"], n_per_ticker=3)
        block = rdc.analyze_daily_block("candidate_observation", old_date)
        assert block["row_count_covered"] == 6
        assert block["summary"]["n_tickers_distintos"] == 2
        assert block["block_key"] == old_date
        assert block["block_granularity"] == "market_date"
    finally:
        _restore()


# --- 2) provisional -> verified ----------------------------------------------

def test_provisional_a_verified():
    _fresh()
    try:
        old_date = _days_ago(100)
        _seed_observations(old_date, ["AAA"], n_per_ticker=2)
        r1 = coc.run_provisional_for_date(old_date, today=TODAY)
        assert r1["ok"] is True
        assert r1["inserted_new"] is True

        block = rdc_registry.get_block("candidate_observation", old_date, rdc.METHODOLOGY_VERSION)
        assert block["status"] == "provisional"

        r2 = coc.run_verification_for_date(old_date)
        assert r2["ok"] is True
        block = rdc_registry.get_block("candidate_observation", old_date, rdc.METHODOLOGY_VERSION)
        assert block["status"] == "verified"
    finally:
        _restore()


# --- 3) verified -> compaction_authorized ------------------------------------

def test_verified_a_compaction_authorized():
    _fresh()
    try:
        old_date = _days_ago(100)
        _seed_observations(old_date, ["AAA"], n_per_ticker=2)
        coc.run_provisional_for_date(old_date, today=TODAY)
        coc.run_verification_for_date(old_date)
        r = coc.authorize_compaction_for_date(old_date)
        assert r["ok"] is True
        block = rdc_registry.get_block("candidate_observation", old_date, rdc.METHODOLOGY_VERSION)
        assert block["status"] == "compaction_authorized"
    finally:
        _restore()


# --- 4) compaction autorizada elimina SOLO el bloque correcto ----------------

def test_compaction_autorizada_elimina_solo_el_bloque_correcto():
    _fresh()
    try:
        old_date = _days_ago(100)
        other_date = _days_ago(95)
        _seed_observations(old_date, ["AAA", "BBB"], n_per_ticker=3)
        _seed_observations(other_date, ["CCC"], n_per_ticker=2)
        _full_pipeline_to_authorized(old_date)

        r = coc.compact_block(old_date, today=TODAY)
        assert r["ok"] is True
        assert r["deleted_row_count"] == 6
        assert _count_observations(old_date) == 0
        assert _count_observations(other_date) == 2  # intacto
    finally:
        _restore()


# --- 5) checksum modificado bloquea DELETE -----------------------------------

def test_checksum_modificado_bloquea_delete():
    _fresh()
    try:
        old_date = _days_ago(100)
        _seed_observations(old_date, ["AAA"], n_per_ticker=2)
        _full_pipeline_to_authorized(old_date)

        # el dato crudo cambia DESPUES de autorizar (ej. una fila se agrega
        # por error, o un proceso tardio la escribe) -- el checksum ya no
        # coincide con el que se autorizo.
        _seed_observations(old_date, ["AAA"], n_per_ticker=1)

        r = coc.compact_block(old_date, today=TODAY)
        assert r["ok"] is False
        assert r["reason"] == "checksum_cambio_justo_antes_del_delete_abortado"
        assert r["deleted_row_count"] == 0
        assert _count_observations(old_date) == 3  # nada se borro
        block = rdc_registry.get_block("candidate_observation", old_date, rdc.METHODOLOGY_VERSION)
        assert block["status"] == "compaction_authorized"  # sin avanzar a compacted
    finally:
        _restore()


# --- 6) día actual bloquea DELETE --------------------------------------------

def test_dia_actual_bloquea_compaction():
    _fresh()
    try:
        _seed_observations(TODAY, ["AAA"], n_per_ticker=2)
        r = coc.run_provisional_for_date(TODAY, today=TODAY)
        assert r["ok"] is False
        assert r["reason"] == "no_se_compacta_dia_actual_ni_futuro"

        # ni siquiera con un manifiesto fabricado a mano llega a borrar --
        # el guard de compact_block es independiente del guard de provisional.
        r2 = coc.compact_block(TODAY, today=TODAY)
        assert r2["ok"] is False
        assert r2["deleted_row_count"] == 0
    finally:
        _restore()


# --- 7) <14 días bloquea DELETE ----------------------------------------------

def test_menos_de_14_dias_bloquea_compaction():
    _fresh()
    try:
        reciente = _days_ago(5)
        _seed_observations(reciente, ["AAA"], n_per_ticker=2)
        r = coc.run_provisional_for_date(reciente, today=TODAY)
        assert r["ok"] is False
        assert "dentro_de_ventana_de_retencion_14d" in r["reason"]

        # limite exacto: 13 dias -> bloqueado; 14 dias -> permitido
        r13 = coc.run_provisional_for_date(_days_ago(13), today=TODAY)
        assert r13["ok"] is False
        _seed_observations(_days_ago(14), ["AAA"], n_per_ticker=1)
        r14 = coc.run_provisional_for_date(_days_ago(14), today=TODAY)
        assert r14["ok"] is True
    finally:
        _restore()


# --- 8/9) candidate_detection / candidate_outcome intactas -------------------

def test_candidate_detection_y_outcome_quedan_intactas():
    _fresh()
    try:
        old_date = _days_ago(100)
        _seed_observations(old_date, ["AAA"], n_per_ticker=3)
        _seed_detection_and_outcome(old_date, "AAA")
        _full_pipeline_to_authorized(old_date)

        with reg._connect() as conn:
            det_antes = conn.execute("SELECT COUNT(*) FROM candidate_detection WHERE market_date=?", (old_date,)).fetchone()[0]
            out_antes = conn.execute("SELECT COUNT(*) FROM candidate_outcome WHERE market_date=?", (old_date,)).fetchone()[0]
        assert det_antes == 1 and out_antes == 1

        r = coc.compact_block(old_date, today=TODAY)
        assert r["ok"] is True

        with reg._connect() as conn:
            det_despues = conn.execute("SELECT COUNT(*) FROM candidate_detection WHERE market_date=?", (old_date,)).fetchone()[0]
            out_despues = conn.execute("SELECT COUNT(*) FROM candidate_outcome WHERE market_date=?", (old_date,)).fetchone()[0]
        assert det_despues == 1  # intacta
        assert out_despues == 1  # intacta
        assert _count_observations(old_date) == 0  # esta si se borro
    finally:
        _restore()


# --- 10) idempotencia ---------------------------------------------------------

def test_idempotencia_compact_block_dos_veces():
    _fresh()
    try:
        old_date = _days_ago(100)
        _seed_observations(old_date, ["AAA"], n_per_ticker=3)
        _full_pipeline_to_authorized(old_date)

        r1 = coc.compact_block(old_date, today=TODAY)
        assert r1["ok"] is True and r1["deleted_row_count"] == 3

        r2 = coc.compact_block(old_date, today=TODAY)  # segunda vez
        assert r2["ok"] is False
        assert r2["deleted_row_count"] == 0
        assert "compacted" in r2["reason"]

        # el manifiesto sigue siendo UNA sola fila, nunca duplicada
        rows = rdc_registry.list_blocks("candidate_observation")
        assert len([b for b in rows if b["block_key"] == old_date]) == 1
    finally:
        _restore()


def test_idempotencia_provisional_y_verificacion_repetidas():
    _fresh()
    try:
        old_date = _days_ago(100)
        _seed_observations(old_date, ["AAA"], n_per_ticker=2)
        r1 = coc.run_provisional_for_date(old_date, today=TODAY)
        r2 = coc.run_provisional_for_date(old_date, today=TODAY)  # repetido
        assert r1["inserted_new"] is True
        assert r2["inserted_new"] is False  # no duplica

        v1 = coc.run_verification_for_date(old_date)
        v2 = coc.run_verification_for_date(old_date)  # ya no esta en provisional
        assert v1["ok"] is True
        assert v2["ok"] is False
    finally:
        _restore()


# --- 11) otra tabla bloqueada --------------------------------------------------

def test_otra_tabla_bloqueada_en_registro():
    _fresh()
    try:
        try:
            rdc_registry.record_provisional(
                source_table="candidate_detection", block_key="x", block_granularity="market_date",
                row_count_covered=1, min_timestamp_covered=None, max_timestamp_covered=None,
                summary={}, raw_data_checksum="x", methodology_version="v1",
            )
            assert False, "debia lanzar ValueError"
        except ValueError:
            pass
    finally:
        _restore()


def test_delete_block_rows_hardcodeado_a_candidate_observation():
    """Escaneo estatico -- confirma que el UNICO DELETE EJECUTABLE (dentro
    de un `conn.execute(...)`, no una mencion en un docstring) de todo el
    modulo esta hardcodeado a `candidate_observation`."""
    src = inspect.getsource(coc._delete_block_rows)
    assert 'conn.execute("DELETE FROM candidate_observation' in src
    # ninguna OTRA funcion del modulo ejecuta un DELETE real
    import ast
    tree = ast.parse(inspect.getsource(coc))
    funciones_con_delete = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            cuerpo_src = ast.unparse(node)
            if "DELETE FROM" in cuerpo_src and node.name != "_delete_block_rows":
                funciones_con_delete.append(node.name)
    assert funciones_con_delete == []


# --- 12) ausencia de VACUUM -----------------------------------------------------

def test_ausencia_de_vacuum_en_todo_el_pipeline():
    """Busca `VACUUM` dentro de una llamada real a `execute(...)`/
    `executescript(...)` -- nunca en prosa de docstring (que sí lo
    menciona a propósito, ej. 'nunca ejecuta VACUUM')."""
    import re
    patron = re.compile(r"execute(script)?\s*\([^)]*vacuum", re.IGNORECASE | re.DOTALL)
    for modulo in (coc, rdc, rdc_registry):
        src = inspect.getsource(modulo)
        assert not patron.search(src), f"VACUUM dentro de un execute() real en {modulo.__name__}"


# --- 13) manifiesto persistente -------------------------------------------------

def test_manifiesto_persiste_con_evidencia_tras_compactar():
    _fresh()
    try:
        old_date = _days_ago(100)
        _seed_observations(old_date, ["AAA", "BBB"], n_per_ticker=4)
        _full_pipeline_to_authorized(old_date)
        r = coc.compact_block(old_date, today=TODAY)
        assert r["ok"] is True

        block = rdc_registry.get_block("candidate_observation", old_date, rdc.METHODOLOGY_VERSION)
        assert block["status"] == "compacted"
        assert block["deleted_row_count"] == 8
        assert block["compacted_at"] is not None
        assert block["row_count_covered"] == 8  # evidencia de cuanto cubria el bloque original
        assert block["summary"]["n_tickers_distintos"] == 2
    finally:
        _restore()


# --- 14) recuperación/reinicio en estados intermedios ---------------------------

def test_recuperacion_tras_reinicio_tras_provisional():
    """Simula que el proceso murio justo despues de provisional -- sin
    ningun estado en memoria, un 'reinicio' es simplemente volver a llamar
    a la siguiente funcion. Nada depende de que sea la MISMA ejecucion."""
    _fresh()
    try:
        old_date = _days_ago(100)
        _seed_observations(old_date, ["AAA"], n_per_ticker=2)
        coc.run_provisional_for_date(old_date, today=TODAY)
        # "reinicio" -- no hay nada que restaurar, el estado vive en disco
        r = coc.run_verification_for_date(old_date)
        assert r["ok"] is True
    finally:
        _restore()


def test_recuperacion_tras_reinicio_tras_authorized_antes_de_compactar():
    _fresh()
    try:
        old_date = _days_ago(100)
        _seed_observations(old_date, ["AAA"], n_per_ticker=2)
        _full_pipeline_to_authorized(old_date)
        # "reinicio" aca -- compact_block se llama en una invocacion
        # completamente separada, sin ningun estado previo en memoria.
        r = coc.compact_block(old_date, today=TODAY)
        assert r["ok"] is True
        assert r["deleted_row_count"] == 2
    finally:
        _restore()


def test_recuperacion_no_reintenta_delete_si_ya_esta_compacted():
    """El caso mas peligroso: si el proceso muriera JUSTO DESPUES del
    DELETE pero ANTES de marcar compacted, un reintento debe detectar que
    ya no hay filas y no romper nada. Simulado forzando ese estado
    intermedio a mano."""
    _fresh()
    try:
        old_date = _days_ago(100)
        _seed_observations(old_date, ["AAA"], n_per_ticker=2)
        _full_pipeline_to_authorized(old_date)
        # simula el DELETE ya ejecutado pero el manifiesto sin actualizar todavia
        coc._delete_block_rows(old_date)
        assert _count_observations(old_date) == 0
        # "reinicio" -- se reintenta compact_block con la tabla ya vacia
        r = coc.compact_block(old_date, today=TODAY)
        assert r["ok"] is True
        assert r["deleted_row_count"] == 0  # no hay nada mas que borrar, pero no rompe
        block = rdc_registry.get_block("candidate_observation", old_date, rdc.METHODOLOGY_VERSION)
        assert block["status"] == "compacted"
    finally:
        _restore()


# --- prueba de integración: varios días sintéticos ------------------------------

def test_integracion_multiples_dias_sinteticos():
    """Escenario representativo de varios dias reales: uno de hoy (nunca se
    toca), uno reciente (<14d, bloqueado), uno justo en el limite (14d,
    elegible), y dos bien viejos (30d/100d, elegibles) -- corre el
    pipeline completo sobre los 5 y verifica el resultado final de cada
    uno, sin mezclar datos entre dias."""
    _fresh()
    try:
        dias = {
            "hoy": TODAY,
            "reciente_5d": _days_ago(5),
            "limite_14d": _days_ago(14),
            "viejo_30d": _days_ago(30),
            "viejo_100d": _days_ago(100),
        }
        for etiqueta, fecha in dias.items():
            _seed_observations(fecha, [f"TCK_{etiqueta.upper()}"], n_per_ticker=5)

        resultados = {}
        for etiqueta, fecha in dias.items():
            resultados[etiqueta] = coc.run_daily_pipeline(fecha, today=TODAY, auto_authorize=True)

        # hoy: bloqueado en la fase provisional
        assert resultados["hoy"]["provisional"]["ok"] is False
        assert _count_observations(dias["hoy"]) == 5  # intacto

        # reciente: bloqueado por retencion
        assert resultados["reciente_5d"]["provisional"]["ok"] is False
        assert _count_observations(dias["reciente_5d"]) == 5  # intacto

        # limite y viejos: compactados de punta a punta
        for etiqueta in ("limite_14d", "viejo_30d", "viejo_100d"):
            r = resultados[etiqueta]
            assert r["provisional"]["ok"] is True
            assert r["verified"]["ok"] is True
            assert r["authorized"]["ok"] is True
            assert r["compacted"]["ok"] is True
            assert r["compacted"]["deleted_row_count"] == 5
            assert _count_observations(dias[etiqueta]) == 0

        # confirmar que ningun dia se mezclo con otro -- total final coherente
        assert _count_observations() == 5 + 5  # solo hoy + reciente sobreviven
    finally:
        _restore()
