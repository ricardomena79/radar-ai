"""Tests de `bidirectional_shadow_registry.py` (2026-09-12, misión
"EXPERIMENTO SHADOW DE APRENDIZAJE BIDIRECCIONAL"). DB temporal aislada
por test, mismo patrón que `test_shadow_observation_registry.py`."""

import tempfile
import uuid as _uuid
from pathlib import Path
from unittest import mock

from atlas_live.core import bidirectional_shadow as bidi
from atlas_live.core import bidirectional_shadow_registry as bsr

_ORIG_DB = bsr.DB_PATH


def _fresh():
    bsr.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_bsr_{_uuid.uuid4().hex}.db"


def _restore():
    bsr.DB_PATH = _ORIG_DB


_LE = {
    "methodology_version": "v1_direction_timing_volatility_tercile",
    "validation_state": "VALIDACION_ROBUSTA",
    "sample_size": 600,
    "historical_success_pct_20": 5.0,
    "wilson_lower_bound_20_pct": 40.0,
    "wilson_upper_bound_20_pct": 60.0,
    "baseline_pct_20": 10.0,
    "lift_20": 4.0,
    "computed_as_of": "2026-08-20",
    "computed_at": "2026-08-21T00:00:00+00:00",
}


def _record(ticker="AAA", market_date="2026-08-24", decision_base="NO_TOCAR", decision_shadow_downgrade="NO_TOCAR",
            eligibility_state="ELEGIBLE", le=None):
    le = le or _LE
    resultado = bidi.compute_bidirectional_decision(
        decision_base=decision_base, decision_shadow_downgrade=decision_shadow_downgrade,
        eligibility_state=eligibility_state, learned_evidence=le,
    )
    return bsr.record_bidirectional_observation(
        ticker=ticker, market_date=market_date, decision_timestamp="2026-08-24T09:31:00+00:00",
        direction="ALCISTA", timing_deteccion="al_comienzo", core_methodology_version="v1_wraps_priority_classifier",
        decision_base=decision_base, resultado=resultado, learned_evidence=le, eligibility_state=eligibility_state,
    )


# --- persistencia básica ----------------------------------------------------

def test_persiste_todos_los_campos_de_un_upgrade_real():
    _fresh()
    try:
        assert _record() is True  # NO_TOCAR + ELEGIBLE + evidencia favorable -> upgrade
        fila = bsr.get_observations_for("AAA", "2026-08-24")[0]
        assert fila["decision_base"] == "NO_TOCAR"
        assert fila["decision_informada"] == "VIGILAR"
        assert fila["upgrade_aplicado"] == 1
        assert fila["eligibility_state"] == "ELEGIBLE"
        assert fila["validation_state"] == "VALIDACION_ROBUSTA"
        assert fila["sample_size"] == 600
        assert fila["historical_success_pct_20"] == 5.0
        assert fila["baseline_pct_20"] == 10.0
        assert fila["wilson_lower_bound_20_pct"] == 40.0
        assert fila["computed_as_of"] == "2026-08-20"
        assert fila["core_methodology_version"] == "v1_wraps_priority_classifier"
    finally:
        _restore()


def test_sin_divergencia_no_escribe_nada():
    _fresh()
    try:
        # eligibility_state INSUFICIENTE -> compute_bidirectional_decision no
        # produce upgrade -> decision_informada == decision_base -> no-op.
        assert _record(eligibility_state="INSUFICIENTE") is False
        assert bsr.get_observations_for("AAA", "2026-08-24") == []
        assert bsr._db_exists() is False
    finally:
        _restore()


# --- transition-only ---------------------------------------------------------

def test_repeticion_identica_no_duplica():
    _fresh()
    try:
        assert _record() is True
        assert _record() is False
        assert _record() is False
        assert len(bsr.get_observations_for("AAA", "2026-08-24")) == 1
    finally:
        _restore()


def test_cambio_de_eligibilidad_a_traves_del_tiempo_registra_ambas_filas():
    _fresh()
    try:
        # Primero sin evidencia suficiente (sin upgrade, no-op)...
        assert _record(eligibility_state="INSUFICIENTE") is False
        # ...luego con evidencia suficiente (upgrade real, primera fila real).
        assert _record(eligibility_state="ELEGIBLE") is True
        filas = bsr.get_observations_for("AAA", "2026-08-24")
        assert len(filas) == 1
        assert filas[0]["decision_informada"] == "VIGILAR"
    finally:
        _restore()


def test_db_inexistente_list_y_get_devuelven_vacio_sin_crear_archivo():
    _fresh()
    try:
        assert bsr._db_exists() is False
        assert bsr.list_bidirectional_observations() == []
        assert bsr.get_observations_for("AAA", "2026-08-24") == []
        assert bsr._db_exists() is False
    finally:
        _restore()


def test_indices_creados():
    _fresh()
    try:
        _record()
        with bsr._connect() as conn:
            nombres = {r["name"] for r in conn.execute("PRAGMA index_list(bidirectional_shadow_log)")}
        assert "idx_bsl_ticker_date" in nombres
        assert "idx_bsl_market_date" in nombres
        assert "idx_bsl_condition" in nombres
    finally:
        _restore()


# --- reporte offline: universo A/B/C bidireccional --------------------------

def _snapshot(ticker="AAA", market_date="2026-08-24", decision="NO_TOCAR", decision_shadow=None,
              shadow_differs=False, knowledge_available=True, validation_state="VALIDACION_ROBUSTA",
              wilson_lower_bound_20_pct=40.0, baseline_pct_20=10.0, computed_as_of="2026-08-20"):
    return {
        "ticker": ticker, "market_date": market_date, "decision": decision,
        "decision_shadow": decision_shadow, "shadow_differs": int(shadow_differs),
        "knowledge_available": int(knowledge_available), "knowledge_reason": None,
        "methodology_version": "v1_direction_timing_volatility_tercile",
        "computed_as_of": computed_as_of, "computed_at": "2026-08-21T00:00:00+00:00",
        "validation_state": validation_state, "sample_size": 600 if validation_state == "VALIDACION_ROBUSTA" else 40,
        "historical_success_pct_20": 5.0,
        "wilson_lower_bound_20_pct": wilson_lower_bound_20_pct, "wilson_upper_bound_20_pct": 60.0,
        "baseline_pct_20": baseline_pct_20, "lift_20": 4.0,
    }


def test_universo_sin_conocimiento_elegible_va_a_grupo_a():
    _fresh()
    try:
        snap = _snapshot(validation_state="MUESTRA_INSUFICIENTE")
        with mock.patch("atlas_live.core.decision_knowledge_registry.list_snapshots", return_value=[snap]), \
             mock.patch("atlas_live.radar.candidate_registry.get_outcome", return_value=None):
            reporte = bsr.full_bidirectional_report()
        universo = reporte["universo_conocimiento"]
        assert universo["A_sin_elegible"]["n_eventos"] == 1
        assert universo["B_elegible_sin_divergencia"]["n_eventos"] == 0
        assert universo["C_elegible_con_divergencia"]["n_eventos"] == 0
    finally:
        _restore()


def test_universo_elegible_sin_ventaja_va_a_grupo_b_no_c():
    _fresh()
    try:
        # ELEGIBLE pero wilson_lower <= baseline -> sin upgrade -> B, no C.
        snap = _snapshot(wilson_lower_bound_20_pct=5.0, baseline_pct_20=10.0)
        with mock.patch("atlas_live.core.decision_knowledge_registry.list_snapshots", return_value=[snap]), \
             mock.patch("atlas_live.radar.candidate_registry.get_outcome", return_value=None):
            reporte = bsr.full_bidirectional_report()
        universo = reporte["universo_conocimiento"]
        assert universo["B_elegible_sin_divergencia"]["n_eventos"] == 1
        assert universo["C_elegible_con_divergencia"]["n_eventos"] == 0
    finally:
        _restore()


def test_universo_elegible_con_upgrade_va_a_grupo_c_y_relaciona_outcome():
    _fresh()
    try:
        snap = _snapshot(wilson_lower_bound_20_pct=40.0, baseline_pct_20=10.0)
        outcome_real = {"is_final": True, "confiable_para_aprendizaje": True, "category": "buena_oportunidad"}
        with mock.patch("atlas_live.core.decision_knowledge_registry.list_snapshots", return_value=[snap]), \
             mock.patch("atlas_live.radar.candidate_registry.get_outcome", return_value=outcome_real):
            reporte = bsr.full_bidirectional_report()
        universo = reporte["universo_conocimiento"]
        assert universo["C_elegible_con_divergencia"]["n_eventos"] == 1
        evento = universo["C_elegible_con_divergencia"]["eventos"][0]
        assert evento["decision_base"] == "NO_TOCAR"
        assert evento["decision_informada"] == "VIGILAR"
        assert evento["upgrade_aplicado"] is True
        # base=NO_TOCAR (negativa) contra buena_oportunidad real -> ERROR
        assert evento["decision_base_veredicto"] == "ERROR"
        # informada=VIGILAR (positiva) contra buena_oportunidad real -> ACIERTO
        assert evento["decision_informada_veredicto"] == "ACIERTO"
    finally:
        _restore()


def test_reporte_nunca_lanza_ante_error(monkeypatch):
    _fresh()
    try:
        with mock.patch(
            "atlas_live.core.decision_knowledge_registry.list_snapshots", side_effect=RuntimeError("db caida"),
        ):
            reporte = bsr.full_bidirectional_report()
        assert reporte["ok"] is False
        assert "RuntimeError" in reporte["error"]
    finally:
        _restore()
