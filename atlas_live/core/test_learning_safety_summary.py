"""HITO 4 -- Fase 4.3 (2026-09-04, autorizado explícitamente en Plan Mode):
tests de `learning_safety_summary.py`."""

import tempfile
import uuid as _uuid
from pathlib import Path
from unittest import mock

from atlas_live.core import activation_registry as areg
from atlas_live.core import continuous_evaluation_registry as cer
from atlas_live.core import knowledge_eligibility_registry as ker
from atlas_live.core import learning_safety_summary as lss
from atlas_live.core import shadow_observation_registry as sor

_ORIG_KER_DB = ker.DB_PATH
_ORIG_SOR_DB = sor.DB_PATH
_ORIG_AREG_DB = areg.DB_PATH
_ORIG_CER_DB = cer.DB_PATH


def _fresh():
    ker.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_h4_lss_ker_{_uuid.uuid4().hex}.db"
    sor.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_h4_lss_sor_{_uuid.uuid4().hex}.db"
    areg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_h4_lss_areg_{_uuid.uuid4().hex}.db"
    cer.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_h4_lss_cer_{_uuid.uuid4().hex}.db"


def _restore():
    ker.DB_PATH = _ORIG_KER_DB
    sor.DB_PATH = _ORIG_SOR_DB
    areg.DB_PATH = _ORIG_AREG_DB
    cer.DB_PATH = _ORIG_CER_DB


def _assert_sin_clave_eventos(obj, camino="raiz"):
    if isinstance(obj, dict):
        assert "eventos" not in obj, f"clave 'eventos' filtrada al público en {camino}: {obj.keys()}"
        for k, v in obj.items():
            _assert_sin_clave_eventos(v, f"{camino}.{k}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _assert_sin_clave_eventos(item, f"{camino}[{i}]")


# --- 1) DBs recién creadas (sin datos) -- todo en 0, mecanismo OFF ----------

def test_dbs_vacias_conteos_en_cero_mecanismo_off():
    _fresh()
    try:
        resumen = lss.build_safety_summary()
        assert resumen["activation_mechanism_state"] == "OFF"
        assert resumen["eligibilidad"]["ok"] is True
        assert resumen["eligibilidad"]["n_eventos"] == 0
        assert resumen["shadow_observation"]["ok"] is True
        assert resumen["shadow_observation"]["n_observaciones"] == 0
        assert resumen["activacion"]["ok"] is True
        assert resumen["activacion"]["n_eventos"] == 0
        assert resumen["activacion"]["n_revocaciones_registradas"] == 0
        assert resumen["evaluacion_continua"]["ok"] is True
        assert resumen["evaluacion_continua"]["n_eventos"] == 0
        assert resumen["evaluacion_continua"]["n_revocaciones_disparadas"] == 0
    finally:
        _restore()


# --- 2) nunca fuga detalle por condición/ticker, ni con datos sintéticos ---

def _reporte_eligibilidad_con_detalle():
    return {
        "ok": True, "n_eventos": 3,
        "conteos_por_estado": {"NO_ELEGIBLE": 1, "INSUFICIENTE": 1, "ELEGIBLE": 1},
        "eventos": [{"ticker_secreto": "XYZ", "wilson_upper_bound_20_pct": 12.3}],
    }


def _reporte_shadow_con_detalle():
    return {
        "ok": True, "n_observaciones": 2, "eventos": [{"ticker": "ABC"}],
        "agregado_por_elegibilidad": {"ELEGIBLE": {"n_eventos": 2}},
        "universo_conocimiento": {
            "A_sin_elegible": {"n_eventos": 5, "eventos": [{"ticker": "AAA"}]},
            "B_elegible_sin_divergencia": {"n_eventos": 3, "eventos": [{"ticker": "BBB"}]},
            "C_elegible_con_divergencia": {"n_eventos": 2, "eventos": [{"ticker": "CCC"}]},
        },
    }


def _reporte_activacion_con_detalle():
    return {
        "ok": True, "n_eventos": 4, "mechanism_state_actual": "OFF",
        "conteos_por_estado": {"NO_ACTIVO": 2, "ACTIVADO": 1, "BLOQUEADO": 1, "REVOCADO": 0},
        "eventos": [{"ticker": "DDD", "decision_controlada": "VIGILAR"}],
    }


def _reporte_continua_con_detalle():
    return {
        "ok": True, "n_eventos": 6, "n_revocaciones_disparadas": 1,
        "conteos_por_estado": {"VALIDO": 4, "DEGRADADO": 1, "INSUFICIENTE": 1, "NO_EVALUABLE": 0},
        "eventos": [{"direction": "ALCISTA", "recent_wilson_upper_bound_20_pct": 40.0}],
    }


def test_nunca_filtra_la_clave_eventos_ni_detalle_por_condicion():
    with mock.patch.object(ker, "full_eligibility_report", return_value=_reporte_eligibilidad_con_detalle()), \
         mock.patch.object(sor, "full_shadow_observation_report", return_value=_reporte_shadow_con_detalle()), \
         mock.patch.object(areg, "full_activation_report", return_value=_reporte_activacion_con_detalle()), \
         mock.patch.object(areg, "get_mechanism_state", return_value="OFF"), \
         mock.patch.object(areg, "list_revocations", return_value=[]), \
         mock.patch.object(cer, "full_continuous_evaluation_report", return_value=_reporte_continua_con_detalle()):
        resumen = lss.build_safety_summary()

    _assert_sin_clave_eventos(resumen)
    # Los conteos SÍ deben pasar -- no es que todo se vacíe, solo el detalle.
    assert resumen["eligibilidad"]["conteos_por_estado"]["ELEGIBLE"] == 1
    assert resumen["shadow_observation"]["universo_conocimiento_conteos"]["C_elegible_con_divergencia"] == 2
    assert resumen["activacion"]["conteos_por_estado"]["ACTIVADO"] == 1
    assert resumen["evaluacion_continua"]["n_revocaciones_disparadas"] == 1


# --- 3) fail-safe por capa: un fallo en una no vacía las otras 3 -----------

def test_fallo_en_una_capa_no_afecta_a_las_demas():
    with mock.patch.object(ker, "full_eligibility_report", side_effect=RuntimeError("DB caida")):
        _fresh()
        try:
            resumen = lss.build_safety_summary()
        finally:
            _restore()
    assert resumen["eligibilidad"]["ok"] is False
    assert resumen["eligibilidad"]["n_eventos"] == 0
    # Las demás capas siguen funcionando (DBs temporales frescas, sin mock).
    assert resumen["shadow_observation"]["ok"] is True
    assert resumen["activacion"]["ok"] is True
    assert resumen["evaluacion_continua"]["ok"] is True


def test_build_safety_summary_nunca_lanza_ante_las_4_capas_rotas():
    with mock.patch.object(ker, "full_eligibility_report", side_effect=RuntimeError("x")), \
         mock.patch.object(sor, "full_shadow_observation_report", side_effect=RuntimeError("x")), \
         mock.patch.object(areg, "get_mechanism_state", side_effect=RuntimeError("x")), \
         mock.patch.object(cer, "full_continuous_evaluation_report", side_effect=RuntimeError("x")):
        resumen = lss.build_safety_summary()
    assert resumen["activation_mechanism_state"] == "OFF"
    assert all(not resumen[k]["ok"] for k in ("eligibilidad", "shadow_observation", "activacion", "evaluacion_continua"))


# --- 4) estructuras inesperadas: None explícito (no solo clave ausente) ----
# Hallazgo real durante la auditoría de validación (2026-09-04): `dict.get
# (key, default)` NO protege contra un valor explícitamente `None` presente
# para esa clave (solo contra la clave ausente) -- reproducido con mocks
# antes de corregir `learning_safety_summary._get()`.

def test_conteos_por_estado_none_explicito_no_se_propaga():
    with mock.patch.object(ker, "full_eligibility_report",
                            return_value={"ok": True, "n_eventos": 3, "conteos_por_estado": None}):
        resumen = lss.build_safety_summary()
    assert resumen["eligibilidad"]["conteos_por_estado"] == {}


def test_reporte_completo_none_no_se_propaga():
    with mock.patch.object(ker, "full_eligibility_report", return_value=None):
        resumen = lss.build_safety_summary()
    assert resumen["eligibilidad"] == {"ok": False, "n_eventos": 0, "conteos_por_estado": {}}


def test_grupo_de_universo_conocimiento_none_no_se_propaga():
    with mock.patch.object(sor, "full_shadow_observation_report", return_value={
        "ok": True, "n_observaciones": 1, "universo_conocimiento": {"A_sin_elegible": None},
    }):
        resumen = lss.build_safety_summary()
    assert resumen["shadow_observation"]["universo_conocimiento_conteos"]["A_sin_elegible"] == 0


def test_mechanism_state_none_y_list_revocations_none_no_se_propagan():
    with mock.patch.object(areg, "get_mechanism_state", return_value=None), \
         mock.patch.object(areg, "list_revocations", return_value=None), \
         mock.patch.object(areg, "full_activation_report", return_value={"ok": True}):
        resumen = lss.build_safety_summary()
    assert resumen["activation_mechanism_state"] == "OFF"
    assert resumen["activacion"]["n_revocaciones_registradas"] == 0
