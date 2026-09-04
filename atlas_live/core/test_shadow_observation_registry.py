"""Tests de `shadow_observation_registry.py` (Hito 3, Fase 3.4, 2026-09-03,
autorizado explícitamente en Plan Mode). DB temporal aislada por test,
mismo patrón que `test_knowledge_eligibility_registry.py`."""

import inspect
import tempfile
import uuid as _uuid
from pathlib import Path
from unittest import mock

from atlas_live.core import shadow_observation as so
from atlas_live.core import shadow_observation_registry as sor

_ORIG_DB = sor.DB_PATH


def _fresh():
    sor.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_sor_{_uuid.uuid4().hex}.db"


def _restore():
    sor.DB_PATH = _ORIG_DB


_LE = {
    "methodology_version": "v1_direction_timing_volatility_tercile",
    "validation_state": "VALIDACION_ROBUSTA",
    "sample_size": 600,
    "wilson_lower_bound_20_pct": 20.0,
    "wilson_upper_bound_20_pct": 28.0,
    "baseline_pct_20": 35.0,
    "lift_20": -20.0,
    "computed_as_of": "2026-08-20",
    "computed_at": "2026-08-21T00:00:00+00:00",
}


def _record(ticker="AAA", market_date="2026-08-24", decision="VIGILAR", decision_shadow="PREPARACION",
            shadow_differs=True, eligibility_state="ELEGIBLE", le=None):
    obs = so.classify_shadow_observation(
        decision=decision, decision_shadow=decision_shadow, shadow_differs=shadow_differs,
        eligibility_state=eligibility_state, computed_as_of=(le or _LE).get("computed_as_of"), market_date=market_date,
    )
    return sor.record_shadow_observation(
        ticker=ticker, market_date=market_date, decision_timestamp="2026-08-24T09:31:00+00:00",
        direction="ALCISTA", timing_deteccion="al_comienzo", core_methodology_version="v1_wraps_priority_classifier",
        observation=obs, learned_evidence=le or _LE,
    )


# --- persistencia básica -----------------------------------------------

def test_persiste_todos_los_campos():
    _fresh()
    try:
        assert _record() is True
        fila = sor.get_observations_for("AAA", "2026-08-24")[0]
        assert fila["ticker"] == "AAA"
        assert fila["market_date"] == "2026-08-24"
        assert fila["decision"] == "VIGILAR"
        assert fila["decision_shadow"] == "PREPARACION"
        assert fila["eligibility_state"] == "ELEGIBLE"
        assert fila["walk_forward_violation"] == 0
        assert fila["direction"] == "ALCISTA"
        assert fila["timing_deteccion"] == "al_comienzo"
        assert fila["methodology_version"] == "v1_direction_timing_volatility_tercile"
        assert fila["validation_state"] == "VALIDACION_ROBUSTA"
        assert fila["sample_size"] == 600
        assert fila["wilson_lower_bound_20_pct"] == 20.0
        assert fila["wilson_upper_bound_20_pct"] == 28.0
        assert fila["baseline_pct_20"] == 35.0
        assert fila["lift_20"] == -20.0
        assert fila["computed_as_of"] == "2026-08-20"
        assert fila["computed_at"] == "2026-08-21T00:00:00+00:00"
        assert fila["core_methodology_version"] == "v1_wraps_priority_classifier"
    finally:
        _restore()


def test_no_observado_no_escribe_nada():
    _fresh()
    try:
        assert _record(decision="VIGILAR", decision_shadow="VIGILAR", shadow_differs=False) is False
        assert sor.get_observations_for("AAA", "2026-08-24") == []
        assert sor._db_exists() is False
    finally:
        _restore()


# --- 9) repetición idéntica no duplica -----------------------------------

def test_repeticion_de_la_misma_observacion_no_duplica():
    _fresh()
    try:
        assert _record() is True
        assert _record() is False
        assert _record() is False
        assert len(sor.get_observations_for("AAA", "2026-08-24")) == 1
    finally:
        _restore()


def test_50_repeticiones_no_inflan_el_log():
    _fresh()
    try:
        for _ in range(50):
            _record()
        assert len(sor.get_observations_for("AAA", "2026-08-24")) == 1
    finally:
        _restore()


# --- 10) transición de conocimiento a través del tiempo -------------------

def test_cambio_de_decision_shadow_registra_ambas_filas():
    _fresh()
    try:
        assert _record(decision_shadow="PREPARACION") is True
        assert _record(decision_shadow="NO_TOCAR") is True
        filas = sor.get_observations_for("AAA", "2026-08-24")
        assert len(filas) == 2
        assert [f["decision_shadow"] for f in filas] == ["PREPARACION", "NO_TOCAR"]
        assert filas[0]["id"] != filas[1]["id"]
    finally:
        _restore()


def test_cambio_de_eligibility_state_registra_nueva_fila():
    _fresh()
    try:
        assert _record(eligibility_state="INSUFICIENTE") is True
        assert _record(eligibility_state="ELEGIBLE") is True
        filas = sor.get_observations_for("AAA", "2026-08-24")
        assert len(filas) == 2
        assert [f["eligibility_state"] for f in filas] == ["INSUFICIENTE", "ELEGIBLE"]
    finally:
        _restore()


# --- lectura / filtros -----------------------------------------------------

def test_list_shadow_observations_filtra_por_market_date_y_estado_respeta_limit():
    _fresh()
    try:
        _record(ticker="AAA", market_date="2026-08-24", eligibility_state="ELEGIBLE")
        _record(ticker="BBB", market_date="2026-08-24", eligibility_state="INSUFICIENTE")
        _record(ticker="CCC", market_date="2026-08-25", eligibility_state="ELEGIBLE")

        solo_24 = sor.list_shadow_observations(market_date="2026-08-24")
        assert {f["ticker"] for f in solo_24} == {"AAA", "BBB"}

        solo_elegibles = sor.list_shadow_observations(eligibility_state="ELEGIBLE")
        assert all(f["eligibility_state"] == "ELEGIBLE" for f in solo_elegibles)

        acotado = sor.list_shadow_observations(limit=1)
        assert len(acotado) == 1
    finally:
        _restore()


def test_db_inexistente_list_y_get_devuelven_vacio_sin_crear_archivo():
    _fresh()
    try:
        assert sor._db_exists() is False
        assert sor.list_shadow_observations() == []
        assert sor.get_observations_for("AAA", "2026-08-24") == []
        assert sor._db_exists() is False
    finally:
        _restore()


def test_indices_creados():
    _fresh()
    try:
        _record()
        with sor._connect() as conn:
            nombres = {r["name"] for r in conn.execute("PRAGMA index_list(shadow_observation_log)")}
        assert "idx_sol_ticker_date" in nombres
        assert "idx_sol_market_date" in nombres
        assert "idx_sol_eligibility" in nombres
        assert "idx_sol_condition" in nombres
    finally:
        _restore()


# --- 8) trazabilidad completa / 11) outcome posterior relacionable -------

def test_full_shadow_observation_report_reconstruye_la_cadena_y_relaciona_outcome():
    _fresh()
    try:
        _record(ticker="AAA", market_date="2026-08-24", decision="VIGILAR", decision_shadow="NO_TOCAR",
                eligibility_state="ELEGIBLE")

        outcome_real = {"is_final": True, "confiable_para_aprendizaje": True, "category": "falsa_senal"}
        with mock.patch("atlas_live.radar.candidate_registry.get_outcome", return_value=outcome_real):
            reporte = sor.full_shadow_observation_report()

        assert reporte["ok"] is True
        assert reporte["n_observaciones"] == 1
        evento = reporte["eventos"][0]
        assert evento["ticker"] == "AAA"
        assert evento["eligibility_state"] == "ELEGIBLE"
        assert evento["decision_baseline"] == "VIGILAR"
        assert evento["decision_shadow"] == "NO_TOCAR"
        # baseline=VIGILAR (positiva) contra falsa_senal real -> ERROR
        assert evento["decision_baseline_veredicto"] == "ERROR"
        # shadow=NO_TOCAR (negativa) contra falsa_senal real -> ACIERTO
        assert evento["decision_shadow_veredicto"] == "ACIERTO"
        assert "ELEGIBLE" in reporte["agregado_por_elegibilidad"]
    finally:
        _restore()


def test_full_shadow_observation_report_sin_outcome_da_pendiente_nunca_inventado():
    _fresh()
    try:
        _record(ticker="AAA", market_date="2026-08-24")
        with mock.patch("atlas_live.radar.candidate_registry.get_outcome", return_value=None):
            reporte = sor.full_shadow_observation_report()
        assert reporte["eventos"][0]["outcome_evaluable"] is False
        assert reporte["eventos"][0]["decision_baseline_veredicto"] == "PENDIENTE"
        assert reporte["eventos"][0]["decision_shadow_veredicto"] == "PENDIENTE"
    finally:
        _restore()


# --- 2) denominador completo A/B/C (corrección 2026-09-03) ---------------

def _snapshot(ticker="AAA", market_date="2026-08-24", decision="VIGILAR", decision_shadow=None,
              shadow_differs=False, knowledge_available=True, validation_state="VALIDACION_ROBUSTA",
              computed_as_of="2026-08-20"):
    return {
        "ticker": ticker, "market_date": market_date, "decision": decision,
        "decision_shadow": decision_shadow, "shadow_differs": int(shadow_differs),
        "knowledge_available": int(knowledge_available), "knowledge_reason": None,
        "methodology_version": "v1_direction_timing_volatility_tercile",
        "computed_as_of": computed_as_of, "computed_at": "2026-08-21T00:00:00+00:00",
        "validation_state": validation_state, "sample_size": 600 if validation_state == "VALIDACION_ROBUSTA" else 40,
        "wilson_lower_bound_20_pct": 20.0, "wilson_upper_bound_20_pct": 28.0,
        "baseline_pct_20": 35.0, "lift_20": -20.0,
    }


def test_universo_abc_sin_conocimiento_elegible_va_a_grupo_a():
    _fresh()
    try:
        snap = _snapshot(validation_state="MUESTRA_INSUFICIENTE")  # INSUFICIENTE -> no ELEGIBLE
        with mock.patch("atlas_live.core.decision_knowledge_registry.list_snapshots", return_value=[snap]), \
             mock.patch("atlas_live.radar.candidate_registry.get_outcome", return_value=None):
            reporte = sor.full_shadow_observation_report()
        universo = reporte["universo_conocimiento"]
        assert universo["A_sin_elegible"]["n_eventos"] == 1
        assert universo["B_elegible_sin_divergencia"]["n_eventos"] == 0
        assert universo["C_elegible_con_divergencia"]["n_eventos"] == 0
    finally:
        _restore()


def test_universo_abc_elegible_sin_divergencia_va_a_grupo_b():
    _fresh()
    try:
        snap = _snapshot(decision="VIGILAR", decision_shadow="VIGILAR", shadow_differs=False,
                          validation_state="VALIDACION_ROBUSTA")
        with mock.patch("atlas_live.core.decision_knowledge_registry.list_snapshots", return_value=[snap]), \
             mock.patch("atlas_live.radar.candidate_registry.get_outcome", return_value=None):
            reporte = sor.full_shadow_observation_report()
        universo = reporte["universo_conocimiento"]
        assert universo["A_sin_elegible"]["n_eventos"] == 0
        assert universo["B_elegible_sin_divergencia"]["n_eventos"] == 1
        assert universo["C_elegible_con_divergencia"]["n_eventos"] == 0
        assert universo["B_elegible_sin_divergencia"]["eventos"][0]["eligibility_state"] == "ELEGIBLE"
    finally:
        _restore()


def test_universo_abc_elegible_con_divergencia_va_a_grupo_c():
    _fresh()
    try:
        snap = _snapshot(decision="VIGILAR", decision_shadow="NO_TOCAR", shadow_differs=True,
                          validation_state="VALIDACION_ROBUSTA")
        with mock.patch("atlas_live.core.decision_knowledge_registry.list_snapshots", return_value=[snap]), \
             mock.patch("atlas_live.radar.candidate_registry.get_outcome", return_value=None):
            reporte = sor.full_shadow_observation_report()
        universo = reporte["universo_conocimiento"]
        assert universo["A_sin_elegible"]["n_eventos"] == 0
        assert universo["B_elegible_sin_divergencia"]["n_eventos"] == 0
        assert universo["C_elegible_con_divergencia"]["n_eventos"] == 1
    finally:
        _restore()


def test_universo_abc_conocimiento_no_disponible_va_a_grupo_a():
    _fresh()
    try:
        snap = _snapshot(knowledge_available=False, validation_state=None, computed_as_of=None)
        with mock.patch("atlas_live.core.decision_knowledge_registry.list_snapshots", return_value=[snap]), \
             mock.patch("atlas_live.radar.candidate_registry.get_outcome", return_value=None):
            reporte = sor.full_shadow_observation_report()
        assert reporte["universo_conocimiento"]["A_sin_elegible"]["n_eventos"] == 1
    finally:
        _restore()


def test_universo_abc_outcome_real_es_el_mismo_para_baseline_y_shadow():
    _fresh()
    try:
        snap = _snapshot(decision="VIGILAR", decision_shadow="NO_TOCAR", shadow_differs=True,
                          validation_state="VALIDACION_ROBUSTA")
        outcome_real = {"is_final": True, "confiable_para_aprendizaje": True, "category": "falsa_senal"}
        with mock.patch("atlas_live.core.decision_knowledge_registry.list_snapshots", return_value=[snap]), \
             mock.patch("atlas_live.radar.candidate_registry.get_outcome", return_value=outcome_real) as mocked:
            reporte = sor.full_shadow_observation_report()
        evento = reporte["universo_conocimiento"]["C_elegible_con_divergencia"]["eventos"][0]
        # mismo outcome real (una sola llamada a get_outcome por evento) evalua ambos veredictos
        assert mocked.call_count == 1
        assert evento["decision_baseline_veredicto"] == "ERROR"   # VIGILAR (positiva) vs falsa_senal
        assert evento["decision_shadow_veredicto"] == "ACIERTO"   # NO_TOCAR (negativa) vs falsa_senal
    finally:
        _restore()


def test_universo_abc_nunca_fabrica_outcome_sin_evaluable():
    _fresh()
    try:
        snap = _snapshot(decision="VIGILAR", decision_shadow="NO_TOCAR", shadow_differs=True)
        with mock.patch("atlas_live.core.decision_knowledge_registry.list_snapshots", return_value=[snap]), \
             mock.patch("atlas_live.radar.candidate_registry.get_outcome", return_value=None):
            reporte = sor.full_shadow_observation_report()
        evento = reporte["universo_conocimiento"]["C_elegible_con_divergencia"]["eventos"][0]
        assert evento["outcome_evaluable"] is False
        assert evento["decision_baseline_veredicto"] == "PENDIENTE"
        assert evento["decision_shadow_veredicto"] == "PENDIENTE"
    finally:
        _restore()


def test_universo_abc_no_escribe_nada_solo_lee(monkeypatch):
    # Confirma que reconstruir el universo A/B/C es puramente de lectura --
    # ninguna llamada nueva a record_shadow_observation ni a ninguna
    # funcion de escritura de decision_knowledge_registry.
    _fresh()
    try:
        snap = _snapshot()

        def _fail_si_se_llama(*a, **k):
            raise AssertionError("full_shadow_observation_report no debe escribir nada")

        monkeypatch.setattr(sor, "record_shadow_observation", _fail_si_se_llama)
        with mock.patch("atlas_live.core.decision_knowledge_registry.list_snapshots", return_value=[snap]), \
             mock.patch("atlas_live.radar.candidate_registry.get_outcome", return_value=None):
            reporte = sor.full_shadow_observation_report()
        assert reporte["ok"] is True
    finally:
        _restore()


def test_universo_abc_no_agrega_filas_a_shadow_observation_log():
    # El denominador A/B/C se deriva 100% por lectura de
    # decision_knowledge_snapshot -- reconstruirlo no debe insertar nada en
    # shadow_observation_log (que sigue acotada solo a los casos con
    # shadow_differs=True + walk-forward seguro).
    _fresh()
    try:
        snap = _snapshot(decision="VIGILAR", decision_shadow="NO_TOCAR", shadow_differs=True)
        with mock.patch("atlas_live.core.decision_knowledge_registry.list_snapshots", return_value=[snap] * 20), \
             mock.patch("atlas_live.radar.candidate_registry.get_outcome", return_value=None):
            sor.full_shadow_observation_report()
        assert sor._db_exists() is False  # nunca se creo el archivo -- cero escrituras
    finally:
        _restore()


def test_full_shadow_observation_report_nunca_lanza_ante_error():
    _fresh()
    try:
        _record()
        with mock.patch("atlas_live.radar.candidate_registry.get_outcome", side_effect=RuntimeError("boom")):
            reporte = sor.full_shadow_observation_report()
        assert reporte["ok"] is False
        assert "boom" in reporte["error"]
    finally:
        _restore()


# --- inmutabilidad -- escaneo estático --------------------------------------

def test_modulo_nunca_escribe_UPDATE_ni_DELETE():
    fuente = inspect.getsource(sor)
    assert "UPDATE shadow_observation_log" not in fuente
    assert "DELETE FROM shadow_observation_log" not in fuente


def test_modulo_no_contiene_vacuum_ni_checkpoint():
    fuente = inspect.getsource(sor).upper()
    assert "VACUUM" not in fuente
    assert "CHECKPOINT" not in fuente


def test_modulo_nunca_pasa_apply_recalibration_true():
    fuente = inspect.getsource(sor)
    assert "apply_recalibration=True" not in fuente
    assert "apply_recalibration = True" not in fuente


def test_funciones_de_lectura_nunca_contienen_escritura_alguna_escaneo_estatico():
    for func in (sor.list_shadow_observations, sor.get_observations_for):
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
    fuente = inspect.getsource(sor._ro_connect)
    assert "mode=ro" in fuente
    assert 'conn.execute("PRAGMA query_only=ON")' in fuente
    assert 'conn.execute("PRAGMA journal_mode' not in fuente
    assert "conn.executescript" not in fuente
