"""Tests de `knowledge_eligibility_registry.py` (Hito 3, Fase 3.3,
2026-09-03, autorizado explícitamente en Plan Mode). DB temporal aislada
por test, mismo patrón que `test_decision_knowledge_registry.py`."""

import inspect
import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.core import knowledge_eligibility as ke
from atlas_live.core import knowledge_eligibility_registry as ker

_ORIG_DB = ker.DB_PATH


def _fresh():
    ker.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_ker_{_uuid.uuid4().hex}.db"


def _restore():
    ker.DB_PATH = _ORIG_DB


_LE_INSUFICIENTE = {
    "available": True,
    "validation_state": "MUESTRA_INSUFICIENTE",
    "sample_size": 40,
    "historical_success_pct_20": 45.0,
    "baseline_pct_20": 30.0,
    "lift_20": 15.0,
    "wilson_lower_bound_20_pct": 20.0,
    "wilson_upper_bound_20_pct": 70.0,
    "computed_as_of": "2026-08-20",
    "computed_at": "2026-08-21T00:00:00+00:00",
    "methodology_version": "v1_direction_timing_volatility_tercile",
}

_LE_ROBUSTA = dict(_LE_INSUFICIENTE, validation_state="VALIDACION_ROBUSTA", sample_size=600,
                    computed_at="2026-08-22T00:00:00+00:00")


def _record(direction="ALCISTA", timing="al_comienzo", evaluated_as_of="2026-08-24", le=None):
    resultado = ke.classify_eligibility(le if le is not None else _LE_INSUFICIENTE, evaluated_as_of)
    return ker.record_eligibility_snapshot(direction, timing, evaluated_as_of, resultado)


# --- persistencia básica -----------------------------------------------

def test_persiste_todos_los_campos():
    _fresh()
    try:
        assert _record(le=_LE_ROBUSTA) is True
        fila = ker.latest_eligibility_for("ALCISTA", "al_comienzo", "v1_direction_timing_volatility_tercile")
        assert fila["direction"] == "ALCISTA"
        assert fila["timing_deteccion"] == "al_comienzo"
        assert fila["methodology_version"] == "v1_direction_timing_volatility_tercile"
        assert fila["evaluated_as_of"] == "2026-08-24"
        assert fila["eligibility_state"] == "ELEGIBLE"
        assert "ELEGIBLE" in fila["reasons"]
        assert fila["validation_state"] == "VALIDACION_ROBUSTA"
        assert fila["sample_size"] == 600
        assert fila["wilson_lower_bound_20_pct"] == 20.0
        assert fila["wilson_upper_bound_20_pct"] == 70.0
        assert fila["baseline_pct_20"] == 30.0
        assert fila["lift_20"] == 15.0
        assert fila["computed_as_of"] == "2026-08-20"
        assert fila["computed_at"] == "2026-08-22T00:00:00+00:00"
    finally:
        _restore()


def test_conocimiento_no_disponible_persiste_con_methodology_version_none():
    _fresh()
    try:
        le = {"available": False, "reason": "SIN_CONOCIMIENTO_PARA_ESTA_CONDICION"}
        assert _record(direction="ALCISTA", timing="al_comienzo", le=le) is True
        fila = ker.latest_eligibility_for("ALCISTA", "al_comienzo", None)
        assert fila["eligibility_state"] == "NO_ELEGIBLE"
        assert fila["methodology_version"] is None
        assert "SIN_CONOCIMIENTO_PARA_ESTA_CONDICION" in fila["reasons"]
    finally:
        _restore()


# --- 7) repetición de la misma consulta -----------------------------------

def test_repeticion_de_la_misma_consulta_no_duplica():
    _fresh()
    try:
        assert _record(le=_LE_INSUFICIENTE) is True
        assert _record(le=_LE_INSUFICIENTE) is False
        assert _record(le=_LE_INSUFICIENTE) is False
        filas = ker.list_eligibility_log()
        assert len(filas) == 1
    finally:
        _restore()


def test_50_consultas_identicas_no_inflan_el_log():
    _fresh()
    try:
        for _ in range(50):
            _record(le=_LE_INSUFICIENTE)
        assert len(ker.list_eligibility_log()) == 1
    finally:
        _restore()


# --- 8) cambios de elegibilidad a través del tiempo -----------------------

def test_cambio_de_insuficiente_a_elegible_registra_ambas_filas():
    _fresh()
    try:
        assert _record(evaluated_as_of="2026-08-21", le=_LE_INSUFICIENTE) is True
        assert _record(evaluated_as_of="2026-08-24", le=_LE_ROBUSTA) is True
        filas = ker.list_eligibility_log()
        assert len(filas) == 2
        assert [f["eligibility_state"] for f in filas] == ["INSUFICIENTE", "ELEGIBLE"]
        assert filas[0]["id"] != filas[1]["id"]
    finally:
        _restore()


def test_no_elegible_a_elegible_a_no_elegible_registra_las_tres_transiciones():
    _fresh()
    try:
        le_no_disponible = {"available": False, "reason": "SIN_CONOCIMIENTO_PARA_ESTA_CONDICION"}
        r1 = _record(evaluated_as_of="2026-08-18", le=le_no_disponible)
        r2 = _record(evaluated_as_of="2026-08-21", le=_LE_INSUFICIENTE)
        r3 = _record(evaluated_as_of="2026-08-24", le=_LE_ROBUSTA)
        assert (r1, r2, r3) == (True, True, True)
        filas = ker.list_eligibility_log()
        assert len(filas) == 3
        assert [f["eligibility_state"] for f in filas] == ["NO_ELEGIBLE", "INSUFICIENTE", "ELEGIBLE"]
    finally:
        _restore()


# --- lectura / filtros -----------------------------------------------------

def test_list_eligibility_log_filtra_por_evaluated_as_of_y_state_y_respeta_limit():
    _fresh()
    try:
        _record(direction="ALCISTA", timing="al_comienzo", evaluated_as_of="2026-08-24", le=_LE_ROBUSTA)
        _record(direction="BAJISTA", timing="al_comienzo", evaluated_as_of="2026-08-24", le=_LE_INSUFICIENTE)
        _record(direction="ALCISTA", timing="al_comienzo", evaluated_as_of="2026-08-25", le=_LE_ROBUSTA)

        solo_24 = ker.list_eligibility_log(evaluated_as_of="2026-08-24")
        assert {f["direction"] for f in solo_24} == {"ALCISTA", "BAJISTA"}

        solo_elegibles = ker.list_eligibility_log(eligibility_state="ELEGIBLE")
        assert all(f["eligibility_state"] == "ELEGIBLE" for f in solo_elegibles)

        acotado = ker.list_eligibility_log(limit=1)
        assert len(acotado) == 1
    finally:
        _restore()


def test_condiciones_distintas_no_se_pisan_entre_si():
    _fresh()
    try:
        _record(direction="ALCISTA", timing="al_comienzo", le=_LE_INSUFICIENTE)
        _record(direction="BAJISTA", timing="al_comienzo", le=_LE_INSUFICIENTE)
        assert len(ker.list_eligibility_log()) == 2
        assert ker.latest_eligibility_for("ALCISTA", "al_comienzo", "v1_direction_timing_volatility_tercile") is not None
        assert ker.latest_eligibility_for("BAJISTA", "al_comienzo", "v1_direction_timing_volatility_tercile") is not None
    finally:
        _restore()


# --- DB inexistente --------------------------------------------------------

def test_db_inexistente_list_devuelve_vacio_sin_crear_archivo():
    _fresh()
    try:
        assert ker._db_exists() is False
        assert ker.list_eligibility_log() == []
        assert ker._db_exists() is False
    finally:
        _restore()


def test_db_inexistente_latest_devuelve_none_sin_crear_archivo():
    _fresh()
    try:
        assert ker.latest_eligibility_for("ALCISTA", "al_comienzo", "v1") is None
        assert ker._db_exists() is False
    finally:
        _restore()


# --- índices -----------------------------------------------------------

def test_indices_creados():
    _fresh()
    try:
        _record(le=_LE_INSUFICIENTE)
        with ker._connect() as conn:
            nombres = {r["name"] for r in conn.execute("PRAGMA index_list(knowledge_eligibility_log)")}
        assert "idx_kel_condition" in nombres
        assert "idx_kel_evaluated_as_of" in nombres
        assert "idx_kel_state" in nombres
    finally:
        _restore()


# --- inmutabilidad -- escaneo estático --------------------------------------

def test_modulo_nunca_escribe_UPDATE_ni_DELETE():
    fuente = inspect.getsource(ker)
    assert "UPDATE knowledge_eligibility_log" not in fuente
    assert "DELETE FROM knowledge_eligibility_log" not in fuente


def test_modulo_no_contiene_vacuum_ni_checkpoint():
    fuente = inspect.getsource(ker).upper()
    assert "VACUUM" not in fuente
    assert "CHECKPOINT" not in fuente


def test_modulo_nunca_menciona_apply_recalibration():
    fuente = inspect.getsource(ker)
    assert "apply_recalibration" not in fuente


def test_funciones_de_lectura_nunca_contienen_escritura_alguna_escaneo_estatico():
    for func in (ker.list_eligibility_log, ker.latest_eligibility_for):
        fuente = inspect.getsource(func)
        fuente_upper = fuente.upper()
        assert "JOURNAL_MODE=WAL" not in fuente_upper.replace(" ", "")
        assert "CREATE TABLE" not in fuente_upper
        assert "INSERT" not in fuente_upper
        assert "UPDATE" not in fuente_upper
        assert "DELETE" not in fuente_upper
        assert "VACUUM" not in fuente_upper
        assert "_ro_connect" in fuente or "_db_exists" in fuente


def test_ro_connect_usa_mode_ro_y_query_only():
    fuente = inspect.getsource(ker._ro_connect)
    assert "mode=ro" in fuente
    assert 'conn.execute("PRAGMA query_only=ON")' in fuente
    assert 'conn.execute("PRAGMA journal_mode' not in fuente
    assert "conn.executescript" not in fuente
