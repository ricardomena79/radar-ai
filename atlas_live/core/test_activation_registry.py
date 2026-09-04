"""Tests de `activation_registry.py` (Hito 3, Fase 3.5, 2026-09-03,
autorizado explícitamente en Plan Mode). DB temporal aislada por test,
mismo patrón que `test_shadow_observation_registry.py`."""

import inspect
import tempfile
import uuid as _uuid
from pathlib import Path

import pytest

from atlas_live.core import activation_gate as ag
from atlas_live.core import activation_registry as areg

_ORIG_DB = areg.DB_PATH


def _fresh():
    areg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_areg_{_uuid.uuid4().hex}.db"


def _restore():
    areg.DB_PATH = _ORIG_DB


_LE = {
    "methodology_version": "v1_direction_timing_volatility_tercile",
    "validation_state": "VALIDACION_ROBUSTA",
    "sample_size": 600,
    "computed_as_of": "2026-08-20",
    "computed_at": "2026-08-21T00:00:00+00:00",
}


def _record(ticker="AAA", market_date="2026-08-24", mechanism_state="ON_CONTROLADO",
            eligibility_state="ELEGIBLE", is_revoked=False, decision_controlada="VIGILAR", le=None):
    gate = ag.classify_activation(
        mechanism_state=mechanism_state, eligibility_state=eligibility_state,
        is_revoked=is_revoked, computed_as_of=(le or _LE).get("computed_as_of"), market_date=market_date,
    )
    return areg.record_activation_state(
        ticker=ticker, market_date=market_date, decision_timestamp="2026-08-24T09:31:00+00:00",
        direction="ALCISTA", timing_deteccion="al_comienzo", core_methodology_version="v1_wraps_priority_classifier",
        mechanism_state=mechanism_state, eligibility_state=eligibility_state,
        gate=gate, decision_controlada=decision_controlada if gate["activation_state"] == "ACTIVADO" else None,
        learned_evidence=le or _LE,
    )


# --- 1) mecanismo OFF por defecto -------------------------------------------

def test_mecanismo_off_por_defecto_db_inexistente():
    _fresh()
    try:
        assert areg._db_exists() is False
        assert areg.get_mechanism_state() == "OFF"
        assert areg._db_exists() is False  # get_mechanism_state nunca crea el archivo
    finally:
        _restore()


def test_mecanismo_off_por_defecto_tras_error_simulado(monkeypatch):
    _fresh()
    try:

        def _falla(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(areg, "_db_exists", _falla)
        assert areg.get_mechanism_state() == "OFF"
    finally:
        _restore()


def test_set_mechanism_state_y_lectura_posterior():
    _fresh()
    try:
        assert areg.set_mechanism_state("ON_CONTROLADO", reason="prueba controlada 2026-09-03") is True
        assert areg.get_mechanism_state() == "ON_CONTROLADO"
    finally:
        _restore()


def test_set_mechanism_state_rechaza_valor_ambiguo():
    _fresh()
    try:
        with pytest.raises(ValueError):
            areg.set_mechanism_state("ALGO_RARO", reason="x")
        assert areg.get_mechanism_state() == "OFF"  # no se aplico ningun cambio
    finally:
        _restore()


def test_set_mechanism_state_rechaza_reason_vacio():
    _fresh()
    try:
        with pytest.raises(ValueError):
            areg.set_mechanism_state("ON_CONTROLADO", reason="")
        assert areg.get_mechanism_state() == "OFF"
    finally:
        _restore()


def test_mechanism_history_append_only_conserva_todos_los_cambios():
    _fresh()
    try:
        areg.set_mechanism_state("ON_CONTROLADO", reason="prueba 1")
        areg.set_mechanism_state("OFF", reason="fin de prueba 1")
        areg.set_mechanism_state("ON_CONTROLADO", reason="prueba 2")
        historial = areg.get_mechanism_history()
        assert len(historial) == 3
        assert areg.get_mechanism_state() == "ON_CONTROLADO"  # el mas reciente
    finally:
        _restore()


# --- revocación --------------------------------------------------------------

def test_revoke_global_bloquea_cualquier_condicion():
    _fresh()
    try:
        assert areg.is_revoked("ALCISTA", "al_comienzo", "v1") is False
        areg.revoke(scope="GLOBAL", reason="incidente real detectado")
        assert areg.is_revoked("ALCISTA", "al_comienzo", "v1") is True
        assert areg.is_revoked("BAJISTA", "demasiado_tarde", "v2") is True
    finally:
        _restore()


def test_revoke_condicion_solo_bloquea_esa_condicion():
    _fresh()
    try:
        areg.revoke(scope="CONDICION", reason="condicion especifica mala",
                    direction="ALCISTA", timing_deteccion="al_comienzo", methodology_version="v1")
        assert areg.is_revoked("ALCISTA", "al_comienzo", "v1") is True
        assert areg.is_revoked("BAJISTA", "al_comienzo", "v1") is False
    finally:
        _restore()


def test_revoke_condicion_requiere_los_3_campos():
    _fresh()
    try:
        with pytest.raises(ValueError):
            areg.revoke(scope="CONDICION", reason="x", direction="ALCISTA")
    finally:
        _restore()


def test_no_existe_mecanismo_de_des_revocar():
    fuente = inspect.getsource(areg)
    assert "def unrevoke" not in fuente
    assert "def des_revocar" not in fuente
    assert "DELETE FROM activation_revocation_log" not in fuente


def test_is_revoked_false_sin_db():
    _fresh()
    try:
        assert areg.is_revoked("ALCISTA", "al_comienzo", "v1") is False
        assert areg._db_exists() is False
    finally:
        _restore()


# --- persistencia básica -----------------------------------------------

def test_persiste_todos_los_campos_activado():
    _fresh()
    try:
        assert _record() is True
        fila = areg.get_activation_states_for("AAA", "2026-08-24")[0]
        assert fila["activation_state"] == "ACTIVADO"
        assert fila["reason"] == "CONOCIMIENTO_ELEGIBLE_Y_VIGENTE"
        assert fila["eligibility_state"] == "ELEGIBLE"
        assert fila["mechanism_state"] == "ON_CONTROLADO"
        assert fila["decision_controlada"] == "VIGILAR"
        assert fila["direction"] == "ALCISTA"
        assert fila["timing_deteccion"] == "al_comienzo"
        assert fila["methodology_version"] == "v1_direction_timing_volatility_tercile"
        assert fila["validation_state"] == "VALIDACION_ROBUSTA"
        assert fila["sample_size"] == 600
        assert fila["core_methodology_version"] == "v1_wraps_priority_classifier"
    finally:
        _restore()


def test_bloqueado_persiste_decision_controlada_none():
    _fresh()
    try:
        assert _record(eligibility_state="INSUFICIENTE") is True
        fila = areg.get_activation_states_for("AAA", "2026-08-24")[0]
        assert fila["activation_state"] == "BLOQUEADO"
        assert fila["decision_controlada"] is None
    finally:
        _restore()


# --- 10) repetición idéntica no duplica -------------------------------------

def test_repeticion_identica_no_duplica():
    _fresh()
    try:
        assert _record() is True
        assert _record() is False
        assert _record() is False
        assert len(areg.get_activation_states_for("AAA", "2026-08-24")) == 1
    finally:
        _restore()


def test_50_repeticiones_no_inflan_el_log():
    _fresh()
    try:
        for _ in range(50):
            _record()
        assert len(areg.get_activation_states_for("AAA", "2026-08-24")) == 1
    finally:
        _restore()


# --- transición a través del tiempo -----------------------------------------

def test_cambio_de_activation_state_registra_nueva_fila():
    _fresh()
    try:
        assert _record(eligibility_state="INSUFICIENTE") is True
        assert _record(eligibility_state="ELEGIBLE") is True
        filas = areg.get_activation_states_for("AAA", "2026-08-24")
        assert len(filas) == 2
        assert [f["activation_state"] for f in filas] == ["BLOQUEADO", "ACTIVADO"]
    finally:
        _restore()


# --- 11) trazabilidad completa -----------------------------------------------

def test_trazabilidad_completa_reconstruible_desde_una_fila():
    _fresh()
    try:
        _record(ticker="AAA", market_date="2026-08-24", eligibility_state="ELEGIBLE", decision_controlada="NO_TOCAR")
        fila = areg.get_activation_states_for("AAA", "2026-08-24")[0]
        # conocimiento -> eligibility_state -> activation_state -> decision_controlada, todo en una fila
        assert fila["eligibility_state"] == "ELEGIBLE"
        assert fila["activation_state"] == "ACTIVADO"
        assert fila["decision_controlada"] == "NO_TOCAR"
        assert fila["computed_as_of"] == "2026-08-20"
        assert fila["mechanism_state"] == "ON_CONTROLADO"
    finally:
        _restore()


# --- lectura / filtros -----------------------------------------------------

def test_list_activation_states_filtra_por_market_date_y_estado():
    _fresh()
    try:
        _record(ticker="AAA", market_date="2026-08-24", eligibility_state="ELEGIBLE")
        _record(ticker="BBB", market_date="2026-08-24", eligibility_state="NO_ELEGIBLE")
        _record(ticker="CCC", market_date="2026-08-25", eligibility_state="ELEGIBLE")

        solo_24 = areg.list_activation_states(market_date="2026-08-24")
        assert {f["ticker"] for f in solo_24} == {"AAA", "BBB"}

        solo_activados = areg.list_activation_states(activation_state="ACTIVADO")
        assert all(f["activation_state"] == "ACTIVADO" for f in solo_activados)
    finally:
        _restore()


def test_db_inexistente_list_y_get_devuelven_vacio_sin_crear_archivo():
    _fresh()
    try:
        assert areg.list_activation_states() == []
        assert areg.get_activation_states_for("AAA", "2026-08-24") == []
        assert areg._db_exists() is False
    finally:
        _restore()


def test_indices_creados():
    _fresh()
    try:
        _record()
        with areg._connect() as conn:
            nombres = {r["name"] for r in conn.execute("PRAGMA index_list(activation_state_log)")}
        assert "idx_asl_ticker_date" in nombres
        assert "idx_asl_market_date" in nombres
        assert "idx_asl_state" in nombres
    finally:
        _restore()


# --- reporte -----------------------------------------------------------------

def test_full_activation_report_agrega_por_estado():
    _fresh()
    try:
        areg.set_mechanism_state("ON_CONTROLADO", reason="prueba")
        _record(ticker="AAA", eligibility_state="ELEGIBLE")
        _record(ticker="BBB", eligibility_state="NO_ELEGIBLE")
        reporte = areg.full_activation_report()
        assert reporte["ok"] is True
        assert reporte["n_eventos"] == 2
        assert reporte["conteos_por_estado"]["ACTIVADO"] == 1
        assert reporte["conteos_por_estado"]["BLOQUEADO"] == 1
        assert reporte["mechanism_state_actual"] == "ON_CONTROLADO"
    finally:
        _restore()


def test_full_activation_report_nunca_lanza():
    _fresh()
    try:
        reporte = areg.full_activation_report()
        assert reporte["ok"] is True
        assert reporte["n_eventos"] == 0
    finally:
        _restore()


# --- inmutabilidad -- escaneo estático --------------------------------------

def test_modulo_nunca_escribe_UPDATE_ni_DELETE_en_las_3_tablas():
    fuente = inspect.getsource(areg)
    for tabla in ("activation_mechanism_state", "activation_revocation_log", "activation_state_log"):
        assert f"UPDATE {tabla}" not in fuente
        assert f"DELETE FROM {tabla}" not in fuente


def test_modulo_no_contiene_vacuum_ni_checkpoint():
    fuente = inspect.getsource(areg).upper()
    assert "VACUUM" not in fuente
    assert "CHECKPOINT" not in fuente


def test_funciones_de_lectura_nunca_contienen_escritura_alguna_escaneo_estatico():
    for func in (areg.list_activation_states, areg.get_activation_states_for, areg.is_revoked, areg.list_revocations, areg.get_mechanism_history):
        fuente = inspect.getsource(func)
        fuente_upper = fuente.upper()
        assert "JOURNAL_MODE=WAL" not in fuente_upper.replace(" ", "")
        assert "CREATE TABLE" not in fuente_upper
        assert "INSERT" not in fuente_upper
        assert "UPDATE" not in fuente_upper
        assert "DELETE" not in fuente_upper
        assert "VACUUM" not in fuente_upper


def test_ro_connect_usa_mode_ro_y_query_only():
    fuente = inspect.getsource(areg._ro_connect)
    assert "mode=ro" in fuente
    assert 'conn.execute("PRAGMA query_only=ON")' in fuente
    assert 'conn.execute("PRAGMA journal_mode' not in fuente
    assert "conn.executescript" not in fuente


def test_sin_vocabulario_de_ejecucion_financiera():
    # Solo código ejecutable -- el docstring del módulo SÍ menciona
    # "broker" en prosa para explicar el alcance (mismo criterio de falsos
    # positivos ya resuelto varias veces en este Hito).
    for func in (
        areg.get_mechanism_state, areg.set_mechanism_state, areg.revoke, areg.is_revoked,
        areg.record_activation_state, areg.full_activation_report,
    ):
        fuente = inspect.getsource(func).lower()
        for palabra in ("broker", "place_order", "execute_trade"):
            assert palabra not in fuente
