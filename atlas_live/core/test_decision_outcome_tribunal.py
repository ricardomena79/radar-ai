"""Tests de `decision_outcome_tribunal.py` (Hito 3, Fase 3.2, 2026-09-03,
autorizado explícitamente). DBs temporales aisladas -- `decision_knowledge_registry`
Y `candidate_registry` (esta última SOLO por lectura, `get_outcome`/
`wilson_confidence_interval`/`precision_validation_state`, nunca se le
escribe nada nuevo desde este módulo)."""

import inspect
import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.core import decision_knowledge_registry as dkr
from atlas_live.core import decision_outcome_tribunal as tribunal
from atlas_live.radar import candidate_registry as reg

_ORIG_DKR_DB = dkr.DB_PATH
_ORIG_REG_DB = reg.DB_PATH

_LE = {
    "available": True, "validation_state": "VALIDACION_ROBUSTA", "sample_size": 600,
    "historical_success_pct_20": 42.0, "baseline_pct_20": 30.0, "lift_20": 12.0,
    "wilson_lower_bound_20_pct": 38.0, "wilson_upper_bound_20_pct": 46.0,
    "computed_as_of": "2026-08-20", "computed_at": "2026-08-21T00:00:00+00:00",
    "methodology_version": "v1_direction_timing_volatility_tercile",
}


def _fresh():
    dkr.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_trib_dkr_{_uuid.uuid4().hex}.db"
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_trib_reg_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None


def _restore():
    dkr.DB_PATH = _ORIG_DKR_DB
    reg.DB_PATH = _ORIG_REG_DB


def _snapshot(ticker="AAA", market_date="2026-08-24", ts="2026-08-24T09:31:00+00:00",
              decision="VIGILAR", decision_shadow="PREPARACION", shadow_differs=True,
              le=None, direction="ALCISTA", timing="al_comienzo"):
    dkr.record_decision_knowledge_snapshot(
        ticker, market_date, ts, decision, decision_shadow, shadow_differs,
        le if le is not None else _LE, direction, timing, "v1_wraps_priority_classifier",
    )


def _outcome(ticker="AAA", market_date="2026-08-24", category="mejor_oportunidad",
             is_final=True, confiable=True, reached_20=True):
    # `record_detection` primero -- `candidate_outcome`/`get_outcome` no exigen
    # una fila en `candidate_detection`, pero se incluye para reflejar el
    # patrón real de producción (mismo estilo que otros tests del proyecto).
    reg.record_outcome(
        ticker, market_date,
        run_up_before_detection_pct=0.0, max_price_after_detection=15.0,
        max_return_after_detection_pct=50.0, minutes_to_max=30.0,
        reached_20=reached_20, reached_50=False, reached_100=False,
        category=category, confiable_para_aprendizaje=confiable, is_final=is_final,
    )


# --- construcción básica del reporte ---------------------------------------

def test_reporte_ok_sin_snapshots_no_rompe():
    _fresh()
    try:
        rep = tribunal.full_tribunal_report(market_date="2026-08-24")
        assert rep["ok"] is True
        assert rep["n_snapshots"] == 0
        assert rep["agregado_por_condicion"] == []
    finally:
        _restore()


# --- corrección 2026-09-02 -- Tribunal ante DB inexistente ------------------

def test_db_inexistente_tribunal_no_la_crea_y_responde_sin_excepcion():
    """Reproduce el escenario real de producción (disk I/O error): la DB de
    snapshots todavía no existe (ningún /api/radar-oportunidades corrió
    todavía) -- el Tribunal debe responder ok=True, n_snapshots=0, sin
    lanzar, y sin crear el archivo por el solo hecho de ser consultado."""
    _fresh()
    try:
        assert dkr._db_exists() is False
        rep = tribunal.full_tribunal_report(market_date="2026-08-24")
        assert rep["ok"] is True
        assert rep["error"] is None
        assert rep["n_snapshots"] == 0
        assert rep["eventos"] == []
        assert dkr._db_exists() is False  # el Tribunal NUNCA la creó
    finally:
        _restore()


def test_db_existente_tribunal_lee_correctamente_tras_la_correccion():
    _fresh()
    try:
        _snapshot(decision="VIGILAR", decision_shadow="PREPARACION", shadow_differs=True)
        _outcome(category="mejor_oportunidad")
        rep = tribunal.full_tribunal_report(market_date="2026-08-24")
        assert rep["ok"] is True
        assert rep["n_snapshots"] == 1
    finally:
        _restore()


def test_decision_real_nunca_es_la_shadow_siempre_la_baseline():
    _fresh()
    try:
        _snapshot(decision="VIGILAR", decision_shadow="PREPARACION", shadow_differs=True)
        _outcome(category="mejor_oportunidad")
        rep = tribunal.full_tribunal_report(market_date="2026-08-24")
        evento = rep["eventos"][0]
        assert evento["decision_real"] == evento["decision_baseline"] == "VIGILAR"
        assert evento["decision_real"] != evento["decision_shadow"]
        assert evento["apply_recalibration_active"] is False
    finally:
        _restore()


# --- walk-forward ------------------------------------------------------

def test_walk_forward_estricto_computed_as_of_menor_a_market_date_ok():
    _fresh()
    try:
        _snapshot(market_date="2026-08-24", le=dict(_LE, computed_as_of="2026-08-20"))
        _outcome(market_date="2026-08-24")
        rep = tribunal.full_tribunal_report(market_date="2026-08-24")
        assert rep["walk_forward_violations"] == 0
    finally:
        _restore()


def test_walk_forward_violacion_detectada_y_excluida_del_agregado():
    _fresh()
    try:
        # computed_as_of >= market_date -- violación sintética (nunca debería
        # ocurrir en producción por el filtro de learned_evidence.py, pero el
        # tribunal debe detectarla de forma independiente, no confiar ciegamente).
        _snapshot(market_date="2026-08-24", le=dict(_LE, computed_as_of="2026-08-24"))
        _outcome(market_date="2026-08-24")
        rep = tribunal.full_tribunal_report(market_date="2026-08-24")
        assert rep["walk_forward_violations"] == 1
        assert rep["agregado_por_condicion"] == []  # nunca se usa esa fila en el agregado
    finally:
        _restore()


# --- exclusión de outcomes no evaluables ------------------------------------

def test_excluye_outcome_no_final():
    _fresh()
    try:
        _snapshot()
        _outcome(is_final=False)
        rep = tribunal.full_tribunal_report(market_date="2026-08-24")
        assert rep["eventos"][0]["outcome_evaluable"] is False
        assert rep["n_con_outcome_evaluable"] == 0
        assert rep["agregado_por_condicion"] == []
    finally:
        _restore()


def test_excluye_no_confiable_para_aprendizaje():
    _fresh()
    try:
        _snapshot()
        _outcome(confiable=False)
        rep = tribunal.full_tribunal_report(market_date="2026-08-24")
        assert rep["eventos"][0]["outcome_evaluable"] is False
        assert rep["n_con_outcome_evaluable"] == 0
    finally:
        _restore()


def test_sin_outcome_en_absoluto_no_rompe():
    _fresh()
    try:
        _snapshot()
        rep = tribunal.full_tribunal_report(market_date="2026-08-24")
        assert rep["ok"] is True
        assert rep["eventos"][0]["outcome_evaluable"] is False
        assert rep["eventos"][0]["decision_baseline_veredicto"] == "SIN_OUTCOME"
    finally:
        _restore()


# --- conocimiento inexistente ------------------------------------------

def test_conocimiento_inexistente_no_rompe_el_tribunal_y_queda_fuera_del_shadow():
    _fresh()
    try:
        le_no_disp = {"available": False, "reason": "SIN_CONOCIMIENTO_PARA_ESTA_CONDICION"}
        _snapshot(decision="VIGILAR", decision_shadow=None, shadow_differs=False, le=le_no_disp)
        _outcome(category="mejor_oportunidad")
        rep = tribunal.full_tribunal_report(market_date="2026-08-24")
        assert rep["ok"] is True
        evento = rep["eventos"][0]
        assert evento["knowledge_snapshot"]["available"] is False
        assert evento["decision_shadow_veredicto"] is None
        agregado = rep["agregado_por_condicion"][0]
        assert agregado["baseline"]["n_acierto"] == 1   # sigue contando en baseline
        assert agregado["shadow"]["n_evaluables"] == 0   # nunca en shadow
    finally:
        _restore()


# --- clasificación ACIERTO/ERROR/AMBIGUO ------------------------------------

def test_clasificacion_oportunidad_prioritaria_con_buen_resultado_es_acierto():
    _fresh()
    try:
        _snapshot(decision="OPORTUNIDAD_PRIORITARIA", decision_shadow="OPORTUNIDAD_PRIORITARIA", shadow_differs=False)
        _outcome(category="buena_oportunidad")
        rep = tribunal.full_tribunal_report(market_date="2026-08-24")
        assert rep["eventos"][0]["decision_baseline_veredicto"] == "ACIERTO"
    finally:
        _restore()


def test_clasificacion_oportunidad_prioritaria_con_falsa_senal_es_error():
    _fresh()
    try:
        _snapshot(decision="OPORTUNIDAD_PRIORITARIA", decision_shadow="OPORTUNIDAD_PRIORITARIA", shadow_differs=False)
        _outcome(category="falsa_senal")
        rep = tribunal.full_tribunal_report(market_date="2026-08-24")
        assert rep["eventos"][0]["decision_baseline_veredicto"] == "ERROR"
    finally:
        _restore()


def test_clasificacion_no_tocar_con_falsa_senal_es_acierto():
    _fresh()
    try:
        _snapshot(decision="NO_TOCAR", decision_shadow="NO_TOCAR", shadow_differs=False)
        _outcome(category="falsa_senal")
        rep = tribunal.full_tribunal_report(market_date="2026-08-24")
        assert rep["eventos"][0]["decision_baseline_veredicto"] == "ACIERTO"
    finally:
        _restore()


def test_clasificacion_no_tocar_con_buen_resultado_es_error():
    _fresh()
    try:
        _snapshot(decision="NO_TOCAR", decision_shadow="NO_TOCAR", shadow_differs=False)
        _outcome(category="mejor_oportunidad")
        rep = tribunal.full_tribunal_report(market_date="2026-08-24")
        assert rep["eventos"][0]["decision_baseline_veredicto"] == "ERROR"
    finally:
        _restore()


def test_clasificacion_preparacion_nunca_recibe_veredicto_forzado():
    _fresh()
    try:
        _snapshot(decision="PREPARACION", decision_shadow="PREPARACION", shadow_differs=False)
        _outcome(category="mejor_oportunidad")
        rep = tribunal.full_tribunal_report(market_date="2026-08-24")
        assert rep["eventos"][0]["decision_baseline_veredicto"] == "AMBIGUO"
    finally:
        _restore()


def test_clasificacion_categoria_ambigua_no_se_fuerza_a_acierto_ni_error():
    _fresh()
    try:
        _snapshot(decision="OPORTUNIDAD_PRIORITARIA", decision_shadow="OPORTUNIDAD_PRIORITARIA", shadow_differs=False)
        _outcome(category="oportunidad_moderada")
        rep = tribunal.full_tribunal_report(market_date="2026-08-24")
        assert rep["eventos"][0]["decision_baseline_veredicto"] == "AMBIGUO"
    finally:
        _restore()


# --- agregado por condición / reutilización estadística ---------------------

def test_agregado_por_condicion_usa_wilson_confidence_interval_real():
    _fresh()
    try:
        for i in range(5):
            _snapshot(ticker=f"T{i}", decision="OPORTUNIDAD_PRIORITARIA",
                      decision_shadow="OPORTUNIDAD_PRIORITARIA", shadow_differs=False)
            _outcome(ticker=f"T{i}", category="mejor_oportunidad" if i < 4 else "falsa_senal")
        rep = tribunal.full_tribunal_report(market_date="2026-08-24")
        agregado = rep["agregado_por_condicion"][0]
        esperado = reg.wilson_confidence_interval(4, 5)
        assert agregado["baseline"]["wilson_ci_acierto_pct"] == list(esperado)
        assert agregado["baseline"]["validation_state"] == reg.precision_validation_state(5)
    finally:
        _restore()


def test_reconstruye_poblacion_por_condicion_agrupando_varios_tickers():
    _fresh()
    try:
        for i in range(3):
            _snapshot(ticker=f"A{i}", direction="ALCISTA", timing="al_comienzo",
                      decision="VIGILAR", decision_shadow="VIGILAR", shadow_differs=False)
            _outcome(ticker=f"A{i}", category="buena_oportunidad")
        for i in range(2):
            _snapshot(ticker=f"B{i}", direction="BAJISTA", timing="agotamiento",
                      decision="NO_TOCAR", decision_shadow="NO_TOCAR", shadow_differs=False)
            _outcome(ticker=f"B{i}", category="falsa_senal")
        rep = tribunal.full_tribunal_report(market_date="2026-08-24")
        condiciones = {(a["direction"], a["timing_deteccion"]) for a in rep["agregado_por_condicion"]}
        assert condiciones == {("ALCISTA", "al_comienzo"), ("BAJISTA", "agotamiento")}
        n_por_condicion = {(a["direction"], a["timing_deteccion"]): a["n_eventos"] for a in rep["agregado_por_condicion"]}
        assert n_por_condicion[("ALCISTA", "al_comienzo")] == 3
        assert n_por_condicion[("BAJISTA", "agotamiento")] == 2
    finally:
        _restore()


def test_cada_outcome_se_asocia_inequivocamente_con_su_knowledge_snapshot():
    _fresh()
    try:
        le_a = dict(_LE, sample_size=111, computed_at="2026-08-21T00:00:00+00:00")
        le_b = dict(_LE, sample_size=222, computed_at="2026-08-21T01:00:00+00:00")
        _snapshot(ticker="AAA", le=le_a)
        _snapshot(ticker="BBB", le=le_b)
        _outcome(ticker="AAA", category="mejor_oportunidad")
        _outcome(ticker="BBB", category="falsa_senal")
        rep = tribunal.full_tribunal_report(market_date="2026-08-24")
        por_ticker = {e["ticker"]: e for e in rep["eventos"]}
        assert por_ticker["AAA"]["knowledge_snapshot"]["sample_size"] == 111
        assert por_ticker["AAA"]["outcome"]["category"] == "mejor_oportunidad"
        assert por_ticker["BBB"]["knowledge_snapshot"]["sample_size"] == 222
        assert por_ticker["BBB"]["outcome"]["category"] == "falsa_senal"
    finally:
        _restore()


# --- aislamiento / no lanza excepciones --------------------------------

def test_tribunal_nunca_lanza_ante_un_registro_roto(monkeypatch):
    _fresh()
    try:
        def _boom(*a, **k):
            raise RuntimeError("fallo sintetico")
        monkeypatch.setattr(tribunal.registry, "list_snapshots", _boom)
        rep = tribunal.full_tribunal_report(market_date="2026-08-24")
        assert rep["ok"] is False
        assert "fallo sintetico" in rep["error"]
    finally:
        _restore()


def test_reporte_incluye_nota_de_alcance():
    _fresh()
    try:
        rep = tribunal.full_tribunal_report()
        assert "apply_recalibration" in rep["nota"]
    finally:
        _restore()


# --- escaneo estático ----------------------------------------------------

def test_modulo_nunca_pasa_apply_recalibration_true():
    fuente = inspect.getsource(tribunal)
    assert "apply_recalibration=True" not in fuente
    assert "apply_recalibration = True" not in fuente


def test_modulo_no_escribe_en_candidate_registry():
    fuente = inspect.getsource(tribunal)
    assert "reg.record_" not in fuente
    assert "reg._connect" not in fuente
    assert "VACUUM" not in fuente.upper()
    assert "DELETE FROM" not in fuente.upper()
