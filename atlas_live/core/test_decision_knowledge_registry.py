"""Tests de `decision_knowledge_registry.py` (Hito 3, Fase 3.0/3.1,
2026-09-03, autorizado explícitamente). DB temporal aislada por test."""

import inspect
import sqlite3
import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.core import decision_knowledge_registry as dkr

_ORIG_DB = dkr.DB_PATH

_LE_DISPONIBLE = {
    "available": True,
    "validation_state": "VALIDACION_ROBUSTA",
    "sample_size": 600,
    "historical_success_pct_20": 42.0,
    "baseline_pct_20": 30.0,
    "lift_20": 12.0,
    "wilson_lower_bound_20_pct": 38.0,
    "wilson_upper_bound_20_pct": 46.0,
    "computed_as_of": "2026-08-20",
    "computed_at": "2026-08-21T00:00:00+00:00",
    "methodology_version": "v1_direction_timing_volatility_tercile",
}

_LE_NO_DISPONIBLE = {"available": False, "reason": "SIN_CONOCIMIENTO_PARA_ESTA_CONDICION"}


def _fresh():
    dkr.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_dkr_{_uuid.uuid4().hex}.db"


def _restore():
    dkr.DB_PATH = _ORIG_DB


_SIN_PASAR = object()  # sentinel -- distingue "no se pasó le" de "se pasó le=None" a propósito


def _record(ticker="AAA", market_date="2026-08-24", ts="2026-08-24T09:31:00+00:00",
            decision="VIGILAR", decision_shadow="PREPARACION", shadow_differs=True,
            le=_SIN_PASAR, direction="ALCISTA", timing="al_comienzo",
            core_mv="v1_wraps_priority_classifier"):
    return dkr.record_decision_knowledge_snapshot(
        ticker, market_date, ts, decision, decision_shadow, shadow_differs,
        _LE_DISPONIBLE if le is _SIN_PASAR else le, direction, timing, core_mv,
    )


# --- persistencia básica -----------------------------------------------

def test_persiste_todos_los_campos_con_conocimiento_disponible():
    _fresh()
    try:
        assert _record() is True
        fila = dkr.latest_snapshot_for("AAA", "2026-08-24")
        assert fila["ticker"] == "AAA"
        assert fila["market_date"] == "2026-08-24"
        assert fila["decision"] == "VIGILAR"
        assert fila["decision_shadow"] == "PREPARACION"
        assert fila["shadow_differs"] == 1
        assert fila["apply_recalibration_active"] == 0
        assert fila["knowledge_available"] == 1
        assert fila["knowledge_reason"] is None
        assert fila["methodology_version"] == "v1_direction_timing_volatility_tercile"
        assert fila["computed_as_of"] == "2026-08-20"
        assert fila["computed_at"] == "2026-08-21T00:00:00+00:00"
        assert fila["validation_state"] == "VALIDACION_ROBUSTA"
        assert fila["sample_size"] == 600
        assert fila["historical_success_pct_20"] == 42.0
        assert fila["baseline_pct_20"] == 30.0
        assert fila["lift_20"] == 12.0
        assert fila["wilson_lower_bound_20_pct"] == 38.0
        assert fila["wilson_upper_bound_20_pct"] == 46.0
        assert fila["core_methodology_version"] == "v1_wraps_priority_classifier"
        assert fila["direction"] == "ALCISTA"
        assert fila["timing_deteccion"] == "al_comienzo"
    finally:
        _restore()


def test_sin_conocimiento_disponible_persiste_reason_y_campos_null():
    _fresh()
    try:
        assert _record(decision_shadow=None, shadow_differs=False, le=_LE_NO_DISPONIBLE) is True
        fila = dkr.latest_snapshot_for("AAA", "2026-08-24")
        assert fila["knowledge_available"] == 0
        assert fila["knowledge_reason"] == "SIN_CONOCIMIENTO_PARA_ESTA_CONDICION"
        assert fila["decision_shadow"] is None
        assert fila["methodology_version"] is None
        assert fila["computed_as_of"] is None
        assert fila["validation_state"] is None
        assert fila["sample_size"] is None
    finally:
        _restore()


def test_conocimiento_inexistente_learned_evidence_none_no_rompe():
    _fresh()
    try:
        assert _record(decision_shadow=None, shadow_differs=False, le=None) is True
        fila = dkr.latest_snapshot_for("AAA", "2026-08-24")
        assert fila["knowledge_available"] == 0
    finally:
        _restore()


# --- transition-only / idempotencia -------------------------------------

def test_mismo_request_repetido_una_sola_representacion():
    _fresh()
    try:
        assert _record(ts="2026-08-24T09:31:00+00:00") is True
        # mismo ticker/fecha/decision/knowledge -- otro "request HTTP" más tarde
        assert _record(ts="2026-08-24T09:40:00+00:00") is False
        assert _record(ts="2026-08-24T09:50:00+00:00") is False
        filas = dkr.get_snapshots_for("AAA", "2026-08-24")
        assert len(filas) == 1
    finally:
        _restore()


def test_multiples_requests_identicos_no_inflan_la_base():
    _fresh()
    try:
        for _ in range(50):
            _record()
        filas = dkr.get_snapshots_for("AAA", "2026-08-24")
        assert len(filas) == 1
    finally:
        _restore()


def test_nueva_fila_cuando_decision_cambia():
    _fresh()
    try:
        _record(decision="VIGILAR", decision_shadow="PREPARACION")
        assert _record(decision="OPORTUNIDAD_PRIORITARIA", decision_shadow="VIGILAR") is True
        filas = dkr.get_snapshots_for("AAA", "2026-08-24")
        assert len(filas) == 2
        assert [f["decision"] for f in filas] == ["VIGILAR", "OPORTUNIDAD_PRIORITARIA"]
    finally:
        _restore()


def test_nueva_fila_cuando_knowledge_cambia_aunque_decision_no_cambie():
    _fresh()
    try:
        _record(decision="VIGILAR")
        le_recalculado = dict(_LE_DISPONIBLE, computed_at="2026-08-22T00:00:00+00:00", sample_size=800)
        assert _record(decision="VIGILAR", le=le_recalculado) is True
        filas = dkr.get_snapshots_for("AAA", "2026-08-24")
        assert len(filas) == 2
        assert filas[0]["sample_size"] == 600
        assert filas[1]["sample_size"] == 800
    finally:
        _restore()


def test_transicion_a_b_a_registra_las_tres_transiciones():
    _fresh()
    try:
        r1 = _record(ts="2026-08-24T09:31:00+00:00", decision="VIGILAR")
        r2 = _record(ts="2026-08-24T10:00:00+00:00", decision="OPORTUNIDAD_PRIORITARIA")
        r3 = _record(ts="2026-08-24T10:30:00+00:00", decision="VIGILAR")
        assert (r1, r2, r3) == (True, True, True)
        filas = dkr.get_snapshots_for("AAA", "2026-08-24")
        assert len(filas) == 3
        assert [f["decision"] for f in filas] == ["VIGILAR", "OPORTUNIDAD_PRIORITARIA", "VIGILAR"]
        # la tercera fila (vuelta a VIGILAR) es una fila NUEVA, no la misma que la primera.
        assert filas[0]["id"] != filas[2]["id"]
    finally:
        _restore()


def test_repetir_el_ultimo_evento_de_una_secuencia_a_b_a_no_duplica():
    _fresh()
    try:
        _record(ts="2026-08-24T09:31:00+00:00", decision="VIGILAR")
        _record(ts="2026-08-24T10:00:00+00:00", decision="OPORTUNIDAD_PRIORITARIA")
        _record(ts="2026-08-24T10:30:00+00:00", decision="VIGILAR")
        # repetir el ÚLTIMO evento (vuelta a VIGILAR) -- no debe duplicar
        assert _record(ts="2026-08-24T10:45:00+00:00", decision="VIGILAR") is False
        filas = dkr.get_snapshots_for("AAA", "2026-08-24")
        assert len(filas) == 3
    finally:
        _restore()


# --- lectura -------------------------------------------------------------

def test_get_snapshots_for_orden_cronologico():
    _fresh()
    try:
        _record(ts="2026-08-24T09:31:00+00:00", decision="VIGILAR")
        _record(ts="2026-08-24T10:00:00+00:00", decision="OPORTUNIDAD_PRIORITARIA")
        filas = dkr.get_snapshots_for("AAA", "2026-08-24")
        assert filas[0]["decision_timestamp"] < filas[1]["decision_timestamp"]
    finally:
        _restore()


def test_list_snapshots_filtra_por_market_date_direction_timing_y_respeta_limit():
    _fresh()
    try:
        _record(ticker="AAA", market_date="2026-08-24", direction="ALCISTA", timing="al_comienzo")
        _record(ticker="BBB", market_date="2026-08-24", direction="BAJISTA", timing="al_comienzo")
        _record(ticker="CCC", market_date="2026-08-25", direction="ALCISTA", timing="al_comienzo")

        solo_24 = dkr.list_snapshots(market_date="2026-08-24")
        assert {f["ticker"] for f in solo_24} == {"AAA", "BBB"}

        solo_alcista = dkr.list_snapshots(market_date="2026-08-24", direction="ALCISTA")
        assert {f["ticker"] for f in solo_alcista} == {"AAA"}

        acotado = dkr.list_snapshots(limit=1)
        assert len(acotado) == 1
    finally:
        _restore()


def test_apply_recalibration_active_siempre_persiste_como_0_por_defecto():
    _fresh()
    try:
        _record()
        fila = dkr.latest_snapshot_for("AAA", "2026-08-24")
        assert fila["apply_recalibration_active"] == 0
    finally:
        _restore()


# --- índices ---------------------------------------------------------------

def test_indices_creados():
    _fresh()
    try:
        _record()
        with dkr._connect() as conn:
            nombres = {r["name"] for r in conn.execute("PRAGMA index_list(decision_knowledge_snapshot)")}
        assert "idx_dks_ticker_date" in nombres
        assert "idx_dks_market_date" in nombres
        assert "idx_dks_condition" in nombres
    finally:
        _restore()


# --- inmutabilidad -- escaneo estático --------------------------------------

def test_modulo_nunca_escribe_UPDATE_ni_DELETE_sobre_decision_knowledge_snapshot():
    fuente = inspect.getsource(dkr)
    assert "UPDATE decision_knowledge_snapshot" not in fuente
    assert "DELETE FROM decision_knowledge_snapshot" not in fuente


def test_modulo_no_contiene_vacuum_ni_checkpoint():
    fuente = inspect.getsource(dkr).upper()
    assert "VACUUM" not in fuente
    assert "CHECKPOINT" not in fuente


def test_modulo_nunca_pasa_apply_recalibration_true():
    fuente = inspect.getsource(dkr)
    assert "apply_recalibration=True" not in fuente
    assert "apply_recalibration = True" not in fuente


def test_firma_record_no_expone_apply_recalibration_true_por_defecto():
    firma = inspect.signature(dkr.record_decision_knowledge_snapshot)
    assert firma.parameters["apply_recalibration_active"].default is False
