"""Tests de `continuous_evaluation_registry.py` (Hito 3, Fase 3.6,
2026-09-03, autorizado explícitamente en Plan Mode, revisión corregida).
DB temporal aislada por test, mismo patrón que las 4 fases anteriores de
este Hito -- SIN mockear `activation_registry` (Fase 3.5, sin tocar): se
usa la DB real de esa fase, también temporal, para demostrar el cierre
del círculo con código real, no con simulación."""

import inspect
import tempfile
import uuid as _uuid
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from atlas_live.core import activation_gate as ag
from atlas_live.core import activation_registry as areg
from atlas_live.core import continuous_evaluation as ce
from atlas_live.core import continuous_evaluation_registry as cer

_ORIG_CER_DB = cer.DB_PATH
_ORIG_AREG_DB = areg.DB_PATH

_DIRECTION = "ALCISTA"
_TIMING = "al_comienzo"
_METHOD = "v1_direction_timing_volatility_tercile"
_AS_OF = "2026-08-24"


def _fresh():
    cer.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_cer_{_uuid.uuid4().hex}.db"
    areg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_cer_areg_{_uuid.uuid4().hex}.db"


def _restore():
    cer.DB_PATH = _ORIG_CER_DB
    areg.DB_PATH = _ORIG_AREG_DB


def _fila_robusta(n=600, wilson_upper=25.0, baseline=35.0, computed_as_of="2026-08-20"):
    """Fila sintética con la MISMA forma que devuelve
    `live_experience_scoring.compute_own_experience_table()` -- se inyecta
    vía mock de esa función para no depender de datos reales en
    `candidate_registry` durante los tests."""
    return {
        "direction": _DIRECTION, "timing_deteccion": _TIMING, "bucket": "poblacion_total",
        "n_evaluables": n, "n_aciertos_20": int(n * 0.2), "pct_20": 20.0,
        "wilson_lower_bound_20_pct": max(0.0, wilson_upper - 10.0), "wilson_upper_bound_20_pct": wilson_upper,
        "baseline_pct_20": baseline, "lift_20": 1.0, "mediana_max_advance_pct": 10.0,
        "validation_state": "VALIDACION_ROBUSTA" if n >= 500 else "EN_VALIDACION",
        "computed_as_of": computed_as_of, "computed_at": "2026-08-21T00:00:00+00:00",
        "n_aciertos_50": 0, "pct_50": 0.0, "n_aciertos_100": 0, "pct_100": 0.0,
    }


def _evaluar_con_ventana_mockeada(fila, ventana_no_vacia=True, auto_revoke=False, as_of_date=_AS_OF, n_ventana=500):
    """Corre `evaluate_condition()` mockeando SOLO la lectura de la
    ventana reciente (`_recent_condition_rows`, I/O real de
    `candidate_registry.db`, no relevante para probar la lógica de 3.6) y
    el cálculo de la tabla (`compute_own_experience_table`, ya probado en
    su propia suite de Fase 2 -- acá se inyecta su salida directamente).

    FIX (2026-09-14): `fila["baseline_pct_20"]` ya NO determina el
    baseline usado para clasificar -- ese era exactamente el bug real
    (ver `_market_wide_baseline_pct_20()`). Se pasa el mismo valor que
    el test ya construyó en `fila` explícitamente como
    `market_baseline_pct_20`, reproduciendo el camino REAL de producción
    (`evaluate_conditions_from_experience_table()` hace lo mismo: extrae
    el baseline de `tabla` y lo pasa explícito) -- los tests siguen
    probando exactamente el mismo escenario estadístico de antes, ahora
    por el canal correcto."""
    ventana_falsa = [{"market_date": "2026-08-19"}] if ventana_no_vacia else []
    baseline_explicito = fila.get("baseline_pct_20") if fila else None
    with mock.patch.object(cer, "_recent_condition_rows", return_value=ventana_falsa), \
         mock.patch("atlas_live.learning.live_experience_scoring.compute_own_experience_table", return_value=[fila] if fila else []):
        return cer.evaluate_condition(
            direction=_DIRECTION, timing_deteccion=_TIMING, methodology_version=_METHOD,
            as_of_date=as_of_date, n_ventana=n_ventana, auto_revoke=auto_revoke,
            market_baseline_pct_20=baseline_explicito,
        )


# --- A) robusta no degradada -> VALIDO, no revoke ---------------------------
# FIX (2026-09-14): la dirección estaba invertida en continuous_evaluation.py
# -- "no degradada" exige wilson_upper >= baseline, ver docstring del módulo.

def test_a_valido_no_llama_a_revoke():
    _fresh()
    try:
        with mock.patch.object(areg, "revoke") as espia_revoke:
            snap = _evaluar_con_ventana_mockeada(_fila_robusta(n=600, wilson_upper=45.0, baseline=35.0), auto_revoke=True)
        assert snap["evaluation_state"] == "VALIDO"
        assert espia_revoke.call_count == 0
        assert areg.is_revoked(_DIRECTION, _TIMING, _METHOD) is False
    finally:
        _restore()


# --- B) n < 500 -> INSUFICIENTE, no revoke ----------------------------------

def test_b_insuficiente_no_llama_a_revoke():
    _fresh()
    try:
        with mock.patch.object(areg, "revoke") as espia_revoke:
            snap = _evaluar_con_ventana_mockeada(_fila_robusta(n=200, wilson_upper=90.0, baseline=10.0), auto_revoke=True)
        assert snap["evaluation_state"] == "INSUFICIENTE"
        assert espia_revoke.call_count == 0
        assert areg.is_revoked(_DIRECTION, _TIMING, _METHOD) is False
    finally:
        _restore()


# --- C) error/datos faltantes/walk-forward invalido -> NO_EVALUABLE, no revoke --

def test_c_sin_evidencia_reciente_es_no_evaluable_no_revoke():
    _fresh()
    try:
        with mock.patch.object(areg, "revoke") as espia_revoke:
            snap = _evaluar_con_ventana_mockeada(None, ventana_no_vacia=False, auto_revoke=True)
        assert snap["evaluation_state"] == "NO_EVALUABLE"
        assert "SIN_EVIDENCIA_RECIENTE" in snap["reason"]
        assert espia_revoke.call_count == 0
    finally:
        _restore()


def test_c_error_de_lectura_es_no_evaluable_no_revoke():
    _fresh()
    try:
        with mock.patch.object(cer, "_recent_condition_rows", side_effect=RuntimeError("DB caida")), \
             mock.patch.object(areg, "revoke") as espia_revoke:
            snap = cer.evaluate_condition(
                direction=_DIRECTION, timing_deteccion=_TIMING, methodology_version=_METHOD,
                as_of_date=_AS_OF, auto_revoke=True,
            )
        assert snap["evaluation_state"] == "NO_EVALUABLE"
        assert "ERROR_EVALUACION" in snap["reason"]
        assert snap["error_detalle"] is not None
        assert espia_revoke.call_count == 0
    finally:
        _restore()


def test_c_walk_forward_invalido_via_evaluate_condition_no_revoke():
    # FIX 2026-09-12: `computed_as_of` debe quedar en o después de la fecha
    # en la que esta ventana sería consultada (`as_of_date + 1 día`) para
    # que sea un caso GENUINO de violación -- antes del fix del bug real
    # (`market_date = as_of_date`, confirmado con datos de producción:
    # 78/78 evaluaciones reales terminaban en NO_EVALUABLE sin importar la
    # evidencia), `computed_as_of == as_of_date` ya bastaba para disparar
    # esto siempre, incluso en casos que debían ser válidos -- ver
    # `test_d_degradado_revoca_exactamente_una_vez`, que confirma el
    # camino feliz con `computed_as_of` anterior a `as_of_date`.
    _fresh()
    try:
        computed_as_of_en_el_futuro = (date.fromisoformat(_AS_OF) + timedelta(days=1)).isoformat()
        fila_walk_forward_invalida = _fila_robusta(
            n=600, wilson_upper=40.0, baseline=35.0, computed_as_of=computed_as_of_en_el_futuro,
        )
        with mock.patch.object(areg, "revoke") as espia_revoke:
            snap = _evaluar_con_ventana_mockeada(fila_walk_forward_invalida, auto_revoke=True, as_of_date=_AS_OF)
        assert snap["evaluation_state"] == "NO_EVALUABLE"
        assert snap["walk_forward_ok"] is False
        assert espia_revoke.call_count == 0
    finally:
        _restore()


def test_c2_walk_forward_valido_cuando_computed_as_of_es_anterior_al_dia_siguiente():
    # Caso real de producción (bug confirmado y corregido 2026-09-12):
    # `computed_as_of == as_of_date` (el caso normal -- el ciclo de EOD de
    # HOY genera conocimiento fechado HOY, para ser usado a partir de
    # MAÑANA) debe ser walk-forward-SEGURO, nunca NO_EVALUABLE por este
    # motivo.
    _fresh()
    try:
        fila = _fila_robusta(n=600, wilson_upper=25.0, baseline=35.0, computed_as_of=_AS_OF)
        snap = _evaluar_con_ventana_mockeada(fila, auto_revoke=False, as_of_date=_AS_OF)
        assert snap["walk_forward_ok"] is True
        assert snap["evaluation_state"] == "DEGRADADO"
    finally:
        _restore()


# --- D) n>=500 y wilson_upper<baseline -> DEGRADADO, revoke exactamente 1 vez --
# FIX (2026-09-14): dirección corregida -- degradado exige wilson_upper
# confidentemente POR DEBAJO del baseline (ver continuous_evaluation.py).

def test_d_degradado_revoca_exactamente_una_vez():
    _fresh()
    try:
        snap = _evaluar_con_ventana_mockeada(_fila_robusta(n=600, wilson_upper=25.0, baseline=35.0), auto_revoke=True)
        assert snap["evaluation_state"] == "DEGRADADO"
        assert snap["revocation_requested"] is True
        assert snap["revocation_result"] == "OK"
        assert areg.is_revoked(_DIRECTION, _TIMING, _METHOD) is True
        # confirmar que la revocacion real quedo en la DB de Fase 3.5, sin mockear esa parte
        revocaciones = areg.list_revocations()
        assert len(revocaciones) == 1
        assert revocaciones[0]["scope"] == "CONDICION"
        assert revocaciones[0]["direction"] == _DIRECTION
    finally:
        _restore()


def test_d_degradado_con_auto_revoke_false_no_revoca():
    _fresh()
    try:
        snap = _evaluar_con_ventana_mockeada(_fila_robusta(n=600, wilson_upper=25.0, baseline=35.0), auto_revoke=False)
        assert snap["evaluation_state"] == "DEGRADADO"
        assert snap["revocation_requested"] is True
        assert snap["revocation_result"] == "NO_SOLICITADA"
        assert areg.is_revoked(_DIRECTION, _TIMING, _METHOD) is False
    finally:
        _restore()


# --- E) evaluacion repetida de la misma evidencia -- idempotente ----------

def test_e_repeticion_no_duplica_revocacion_ni_efectos():
    _fresh()
    try:
        fila = _fila_robusta(n=600, wilson_upper=25.0, baseline=35.0)
        _evaluar_con_ventana_mockeada(fila, auto_revoke=True)
        _evaluar_con_ventana_mockeada(fila, auto_revoke=True)
        _evaluar_con_ventana_mockeada(fila, auto_revoke=True)

        assert len(areg.list_revocations()) == 1  # nunca se duplica la revocacion real
        assert len(cer.get_evaluations_for(_DIRECTION, _TIMING, _METHOD)) == 1  # transition-only, sin duplicar
    finally:
        _restore()


def test_e_tercera_llamada_ve_ya_revocada_previamente():
    _fresh()
    try:
        fila = _fila_robusta(n=600, wilson_upper=25.0, baseline=35.0)
        _evaluar_con_ventana_mockeada(fila, auto_revoke=True)
        # segunda evaluacion con evidencia LIGERAMENTE distinta (fuerza una fila nueva en el log)
        # pero la condicion YA esta revocada -- debe verlo y no reintentar revoke().
        fila2 = _fila_robusta(n=601, wilson_upper=26.0, baseline=35.0)
        with mock.patch.object(areg, "revoke") as espia_revoke:
            snap2 = _evaluar_con_ventana_mockeada(fila2, auto_revoke=True)
        assert snap2["evaluation_state"] == "DEGRADADO"
        assert snap2["revocation_result"] == "YA_REVOCADA_PREVIAMENTE"
        assert espia_revoke.call_count == 0
    finally:
        _restore()


# --- F) evidencia posterior buena -- puede volver a VALIDO, revocacion permanece --

def test_f_evaluacion_posterior_valida_no_deshace_la_revocacion():
    _fresh()
    try:
        # ventana 1: DEGRADADO, revoca
        fila_mala = _fila_robusta(n=600, wilson_upper=25.0, baseline=35.0, computed_as_of="2026-08-18")
        snap1 = _evaluar_con_ventana_mockeada(fila_mala, auto_revoke=True, as_of_date="2026-08-20")
        assert snap1["evaluation_state"] == "DEGRADADO"
        assert areg.is_revoked(_DIRECTION, _TIMING, _METHOD) is True

        # ventana 2, mas fresca: vuelve a ser VALIDO estadisticamente
        fila_buena = _fila_robusta(n=600, wilson_upper=45.0, baseline=35.0, computed_as_of="2026-08-22")
        snap2 = _evaluar_con_ventana_mockeada(fila_buena, auto_revoke=True, as_of_date="2026-08-24")
        assert snap2["evaluation_state"] == "VALIDO"

        # la revocacion OPERACIONAL sigue vigente -- sin auto-unrevoke.
        assert areg.is_revoked(_DIRECTION, _TIMING, _METHOD) is True
        assert len(areg.list_revocations()) == 1

        filas_log = cer.get_evaluations_for(_DIRECTION, _TIMING, _METHOD)
        assert [f["evaluation_state"] for f in filas_log] == ["DEGRADADO", "VALIDO"]
    finally:
        _restore()


def test_f_no_existe_ninguna_funcion_de_des_revocar():
    fuente_areg = inspect.getsource(areg)
    fuente_cer = inspect.getsource(cer)
    for fuente in (fuente_areg, fuente_cer):
        assert "def unrevoke" not in fuente
        assert "def des_revocar" not in fuente
        assert "DELETE FROM activation_revocation_log" not in fuente


# --- G) el gate REAL de 3.5 impide llegar a apply_recalibration=True -------

def test_g_gate_real_de_3_5_bloquea_conocimiento_revocado():
    _fresh()
    try:
        # 1) Disparar una revocacion real via 3.6.
        fila_mala = _fila_robusta(n=600, wilson_upper=25.0, baseline=35.0)
        snap = _evaluar_con_ventana_mockeada(fila_mala, auto_revoke=True)
        assert snap["revocation_result"] == "OK"

        # 2) Llamar al gate REAL de Fase 3.5 (activation_gate.classify_activation,
        #    sin mockear NADA de esa funcion) con el mecanismo ON y elegibilidad
        #    ELEGIBLE -- el escenario que, sin la revocacion, daria ACTIVADO.
        is_revoked_real = areg.is_revoked(_DIRECTION, _TIMING, _METHOD)
        assert is_revoked_real is True  # precondicion real, no supuesta

        gate = ag.classify_activation(
            mechanism_state="ON_CONTROLADO", eligibility_state="ELEGIBLE",
            is_revoked=is_revoked_real, computed_as_of="2026-08-20", market_date="2026-08-24",
        )
        assert gate["activation_state"] == "REVOCADO"
        assert gate["activation_state"] != "ACTIVADO"
    finally:
        _restore()


def test_g_sin_la_revocacion_el_mismo_escenario_hubiera_dado_activado():
    # Control: confirma que el escenario de arriba SI daria ACTIVADO sin
    # la revocacion -- prueba que la revocacion es la causa real del bloqueo,
    # no una coincidencia de otros parametros.
    gate = ag.classify_activation(
        mechanism_state="ON_CONTROLADO", eligibility_state="ELEGIBLE",
        is_revoked=False, computed_as_of="2026-08-20", market_date="2026-08-24",
    )
    assert gate["activation_state"] == "ACTIVADO"


# --- dedupe del hook event-driven -------------------------------------------

def test_evaluate_conditions_from_experience_table_dedupe():
    _fresh()
    try:
        tabla = [_fila_robusta(n=600, wilson_upper=25.0, baseline=35.0)]
        r1 = cer.evaluate_conditions_from_experience_table(tabla, _AS_OF)
        r2 = cer.evaluate_conditions_from_experience_table(tabla, _AS_OF)
        assert r1["ok"] is True
        assert r2["ok"] is True
        assert r1["n_condiciones"] == 1
        # misma tabla -> misma evaluacion -> transition-only no duplica.
        assert len(cer.get_evaluations_for(_DIRECTION, _TIMING, _METHOD)) == 1
    finally:
        _restore()


def test_evaluate_conditions_from_experience_table_ignora_buckets_no_poblacion_total():
    tabla = [
        {**_fila_robusta(n=600, wilson_upper=25.0, baseline=35.0), "bucket": "alto"},
        {**_fila_robusta(n=600, wilson_upper=25.0, baseline=35.0), "bucket": "bajo"},
    ]
    _fresh()
    try:
        r = cer.evaluate_conditions_from_experience_table(tabla, _AS_OF)
        assert r["n_condiciones"] == 0  # ningun bucket es poblacion_total
    finally:
        _restore()


def test_evaluate_conditions_from_experience_table_nunca_lanza_ante_tabla_malformada():
    # Fila sin "direction"/"timing_deteccion" -- falla al construir el
    # conjunto de condiciones (antes de la contencion por-condicion).
    # El contrato es "nunca lanza", no "siempre ok=True": acá se confirma
    # que devuelve un dict con el error, en vez de propagar una excepción.
    _fresh()
    try:
        r = cer.evaluate_conditions_from_experience_table([{"bucket": "poblacion_total"}], _AS_OF)
        assert isinstance(r, dict)
        assert r["ok"] is False
        assert r["error"] is not None
    finally:
        _restore()


def test_evaluate_conditions_from_experience_table_contiene_error_por_condicion():
    # Con la tabla bien formada (direction/timing_deteccion presentes),
    # un fallo DENTRO de evaluate_condition() para esa condición puntual
    # queda contenido -- el ciclo completo sigue reportando ok=True.
    _fresh()
    try:
        tabla = [_fila_robusta(n=600, wilson_upper=25.0, baseline=35.0)]
        with mock.patch.object(cer, "evaluate_condition", side_effect=RuntimeError("fallo puntual")):
            r = cer.evaluate_conditions_from_experience_table(tabla, _AS_OF)
        assert r["ok"] is True
        assert r["n_condiciones"] == 1
        assert r["evaluaciones"][0]["evaluation_state"] == "NO_EVALUABLE"
        assert "fallo puntual" in r["evaluaciones"][0]["error"]
    finally:
        _restore()


# --- persistencia / reconstruccion del snapshot -----------------------------

def test_snapshot_completo_reconstruible():
    _fresh()
    try:
        fila = _fila_robusta(n=600, wilson_upper=25.0, baseline=35.0, computed_as_of="2026-08-20")
        _evaluar_con_ventana_mockeada(fila, auto_revoke=True, n_ventana=500)
        registro = cer.get_evaluations_for(_DIRECTION, _TIMING, _METHOD)[0]
        assert registro["direction"] == _DIRECTION
        assert registro["timing_deteccion"] == _TIMING
        assert registro["methodology_version"] == _METHOD
        assert registro["market_date"] == _AS_OF
        assert registro["n_ventana"] == 500
        assert registro["recent_sample_size"] == 600
        assert registro["recent_wilson_upper_bound_20_pct"] == 25.0
        assert registro["recent_baseline_pct_20"] == 35.0
        assert registro["computed_as_of"] == "2026-08-20"
        assert registro["walk_forward_ok"] == 1
        assert registro["evaluation_state"] == "DEGRADADO"
        assert "DEGRADACION_DETECTADA" in registro["reason"]
        assert registro["revocation_requested"] == 1
        assert registro["revocation_result"] == "OK"
        assert registro["error_detalle"] is None
    finally:
        _restore()


def test_transicion_a_traves_del_tiempo_registra_ambas_filas():
    _fresh()
    try:
        _evaluar_con_ventana_mockeada(_fila_robusta(n=200), auto_revoke=False)  # INSUFICIENTE
        _evaluar_con_ventana_mockeada(_fila_robusta(n=600, wilson_upper=45.0, baseline=35.0), auto_revoke=False)  # VALIDO
        filas = cer.get_evaluations_for(_DIRECTION, _TIMING, _METHOD)
        assert [f["evaluation_state"] for f in filas] == ["INSUFICIENTE", "VALIDO"]
    finally:
        _restore()


# --- activation mechanism permanece OFF durante toda la suite --------------

def test_activation_mechanism_permanece_off_durante_toda_la_evaluacion():
    _fresh()
    try:
        assert areg.get_mechanism_state() == "OFF"
        _evaluar_con_ventana_mockeada(_fila_robusta(n=600, wilson_upper=25.0, baseline=35.0), auto_revoke=True)
        # revocar (Fase 3.6) nunca toca el interruptor maestro (Fase 3.5).
        assert areg.get_mechanism_state() == "OFF"
    finally:
        _restore()


# --- reporte -----------------------------------------------------------------

def test_full_continuous_evaluation_report_agrega_por_estado():
    _fresh()
    try:
        _evaluar_con_ventana_mockeada(_fila_robusta(n=600, wilson_upper=25.0, baseline=35.0), auto_revoke=True)
        reporte = cer.full_continuous_evaluation_report()
        assert reporte["ok"] is True
        assert reporte["n_eventos"] == 1
        assert reporte["conteos_por_estado"]["DEGRADADO"] == 1
        assert reporte["n_revocaciones_disparadas"] == 1
    finally:
        _restore()


def test_full_continuous_evaluation_report_nunca_lanza_sin_datos():
    _fresh()
    try:
        reporte = cer.full_continuous_evaluation_report()
        assert reporte["ok"] is True
        assert reporte["n_eventos"] == 0
    finally:
        _restore()


# --- list_eligible_conditions (camino manual) -------------------------------

def test_list_eligible_conditions_reduce_a_distintas_y_solo_elegible():
    from atlas_live.core import knowledge_eligibility_registry as ker

    orig_ker_db = ker.DB_PATH
    ker.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_cer_ker_{_uuid.uuid4().hex}.db"
    try:
        ker.record_eligibility_snapshot(
            direction="ALCISTA", timing_deteccion="al_comienzo", evaluated_as_of="2026-08-20",
            eligibility_result={"eligibility_state": "INSUFICIENTE", "reasons": ["x"], "methodology_version": _METHOD},
        )
        ker.record_eligibility_snapshot(
            direction="ALCISTA", timing_deteccion="al_comienzo", evaluated_as_of="2026-08-24",
            eligibility_result={"eligibility_state": "ELEGIBLE", "reasons": ["y"], "methodology_version": _METHOD},
        )
        ker.record_eligibility_snapshot(
            direction="BAJISTA", timing_deteccion="agotamiento", evaluated_as_of="2026-08-24",
            eligibility_result={"eligibility_state": "NO_ELEGIBLE", "reasons": ["z"], "methodology_version": _METHOD},
        )
        condiciones = cer.list_eligible_conditions()
        assert condiciones == [("ALCISTA", "al_comienzo", _METHOD)]
    finally:
        ker.DB_PATH = orig_ker_db


# --- inmutabilidad -- escaneo estático --------------------------------------

def test_modulo_nunca_escribe_UPDATE_ni_DELETE():
    fuente = inspect.getsource(cer)
    assert "UPDATE continuous_evaluation_log" not in fuente
    assert "DELETE FROM continuous_evaluation_log" not in fuente


def test_modulo_no_contiene_vacuum_ni_checkpoint():
    fuente = inspect.getsource(cer).upper()
    assert "VACUUM" not in fuente
    assert "CHECKPOINT" not in fuente


def test_funciones_de_lectura_nunca_contienen_escritura_alguna_escaneo_estatico():
    for func in (cer.list_evaluations, cer.get_evaluations_for):
        fuente = inspect.getsource(func)
        fuente_upper = fuente.upper()
        assert "JOURNAL_MODE=WAL" not in fuente_upper.replace(" ", "")
        assert "CREATE TABLE" not in fuente_upper
        assert "INSERT" not in fuente_upper
        assert "UPDATE" not in fuente_upper
        assert "DELETE" not in fuente_upper
        assert "VACUUM" not in fuente_upper


def test_ro_connect_usa_mode_ro_y_query_only():
    fuente = inspect.getsource(cer._ro_connect)
    assert "mode=ro" in fuente
    assert 'conn.execute("PRAGMA query_only=ON")' in fuente
    assert 'conn.execute("PRAGMA journal_mode' not in fuente
    assert "conn.executescript" not in fuente


def test_recent_condition_rows_es_read_only():
    fuente = inspect.getsource(cer._recent_condition_rows)
    assert "mode=ro" in fuente
    assert "PRAGMA query_only=ON" in fuente
    assert "INSERT" not in fuente.upper()


def test_sin_vocabulario_de_ejecucion_financiera():
    for func in (cer.evaluate_condition, cer.evaluate_conditions_from_experience_table, cer.record_continuous_evaluation):
        fuente = inspect.getsource(func).lower()
        for palabra in ("broker", "place_order", "execute_trade"):
            assert palabra not in fuente


# ---------------------------------------------------------------------------
# FIX 2026-09-12 -- fuente="stage" (misión "RESOLVER LA DESCONEXIÓN ENTRE
# APRENDIZAJE Y DECISIÓN")
# ---------------------------------------------------------------------------

def test_fuente_default_timing_deteccion_comportamiento_identico():
    # Sin pasar `fuente`, debe llamar a `_recent_condition_rows` (v1),
    # NUNCA a la variante por stage -- cero cambio de comportamiento.
    _fresh()
    try:
        with mock.patch.object(cer, "_recent_condition_rows", return_value=[]) as espia_v1, \
             mock.patch.object(cer, "_recent_condition_rows_by_stage") as espia_v2:
            cer.evaluate_condition(
                direction=_DIRECTION, timing_deteccion=_TIMING, methodology_version=_METHOD, as_of_date=_AS_OF,
            )
        assert espia_v1.call_count == 1
        assert espia_v2.call_count == 0
    finally:
        _restore()


def test_fuente_stage_usa_recent_condition_rows_by_stage():
    _fresh()
    try:
        with mock.patch.object(cer, "_recent_condition_rows") as espia_v1, \
             mock.patch.object(cer, "_recent_condition_rows_by_stage", return_value=[]) as espia_v2:
            cer.evaluate_condition(
                direction=_DIRECTION, timing_deteccion="ALERTA_FUERTE", methodology_version="v2_direction_alert_stage",
                as_of_date=_AS_OF, fuente="stage",
            )
        assert espia_v1.call_count == 0
        assert espia_v2.call_count == 1
    finally:
        _restore()


def test_recent_condition_rows_by_stage_lee_alert_stage_log_real():
    from atlas_live.radar import candidate_registry as reg

    orig_reg_db = reg.DB_PATH
    _fresh()
    try:
        reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_cer_reg_{_uuid.uuid4().hex}.db"
        reg._schema_ready_for = None
        reg.record_detection(
            "AAA", "2026-08-01", "regular", "2026-08-01T14:00:00Z", "s1",
            10.0, 5.0, 10000, 5000, 2.0, 100_000.0, [{"name": "cambio_de_precio", "reason": "x", "value": 5.0}],
        )
        reg.record_alert_stage(
            "AAA", "2026-08-01", "2026-08-01T14:00:00Z", "ALERTA_FUERTE",
            volatility_14d_pct=12.0, direction="ALCISTA",
        )
        reg.record_outcome(
            "AAA", "2026-08-01", 0.0, 13.0, 30.0, 30.0, True, False, False, "EXPLOSION",
            confiable_para_aprendizaje=True, is_final=True,
        )
        filas = cer._recent_condition_rows_by_stage("ALCISTA", "ALERTA_FUERTE", 500, "2026-08-02")
        assert len(filas) == 1
        assert filas[0]["ticker"] == "AAA"
        # Walk-forward: mismo día -> excluida.
        assert cer._recent_condition_rows_by_stage("ALCISTA", "ALERTA_FUERTE", 500, "2026-08-01") == []
    finally:
        reg.DB_PATH = orig_reg_db
        _restore()


def test_evaluate_conditions_from_experience_table_fuente_stage_usa_methodology_v2():
    _fresh()
    try:
        tabla = [{
            "direction": "ALCISTA", "timing_deteccion": "ALERTA_FUERTE", "bucket": "poblacion_total",
            "n_evaluables": 600, "n_aciertos_20": 300, "pct_20": 50.0,
        }]
        with mock.patch.object(cer, "evaluate_condition", return_value={"evaluation_state": "VALIDO"}) as espia:
            resultado = cer.evaluate_conditions_from_experience_table(tabla, "2026-08-24", fuente="stage")
        assert resultado["n_condiciones"] == 1
        _, kwargs = espia.call_args
        assert kwargs["methodology_version"] == "v2_direction_alert_stage"
        assert kwargs["fuente"] == "stage"
    finally:
        _restore()


def test_evaluate_conditions_from_experience_table_default_usa_methodology_v1():
    _fresh()
    try:
        tabla = [{
            "direction": "ALCISTA", "timing_deteccion": "al_comienzo", "bucket": "poblacion_total",
            "n_evaluables": 600, "n_aciertos_20": 300, "pct_20": 50.0,
        }]
        with mock.patch.object(cer, "evaluate_condition", return_value={"evaluation_state": "VALIDO"}) as espia:
            cer.evaluate_conditions_from_experience_table(tabla, "2026-08-24")
        _, kwargs = espia.call_args
        assert kwargs["methodology_version"] == "v1_direction_timing_volatility_tercile"
        assert kwargs["fuente"] == "timing_deteccion"
    finally:
        _restore()


def test_recent_condition_rows_by_stage_es_read_only():
    fuente = inspect.getsource(cer._recent_condition_rows_by_stage)
    assert "mode=ro" in fuente
    assert "PRAGMA query_only=ON" in fuente
    assert "INSERT" not in fuente.upper()


# ---------------------------------------------------------------------------
# FIX 2026-09-14 (autorizado explícitamente) -- bug real confirmado con
# datos de producción: `evaluate_condition()` calculaba su "baseline" a
# partir de `ventana` (ya filtrada a UNA condición), no del mercado
# completo -- comparando cada condición contra sí misma. Caso real usado
# como ancla (producción, 2026-09-14): NEUTRAL/antes_del_movimiento tenía
# recent_pct_20=0.0, recent_wilson_upper_bound_20_pct=0.8, y el baseline
# BUGGY (calculado sobre la ventana angosta) también daba 0.0 -- con el
# baseline REAL de mercado del mismo día (3.17, leído de
# live_experience_knowledge, methodology_version=v2_direction_alert_stage)
# el resultado cambia de forma verificable: 0.8 >= 0.0 (buggy, DEGRADADO)
# vs. 0.8 >= 3.17 (corregido, False -> VALIDO).
# ---------------------------------------------------------------------------

_LEK_DIRECTION = "NEUTRAL"
_LEK_TIMING = "antes_del_movimiento"
_LEK_METHOD = "v2_direction_alert_stage"


def _fresh_lek():
    from atlas_live.learning import live_experience_knowledge as lek

    orig = lek.DB_PATH
    lek.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_cer_lek_{_uuid.uuid4().hex}.db"
    return orig


def _restore_lek(orig):
    from atlas_live.learning import live_experience_knowledge as lek

    lek.DB_PATH = orig


def test_market_wide_baseline_none_sin_ninguna_db_nunca_inventa_un_numero():
    """Sin ningún snapshot walk-forward-seguro (DB recién creada, o
    inexistente) -- `None` explícito, NUNCA un número inventado."""
    orig = _fresh_lek()
    try:
        assert cer._market_wide_baseline_pct_20(_AS_OF, _LEK_METHOD) is None
    finally:
        _restore_lek(orig)


def test_market_wide_baseline_lee_el_valor_real_walk_forward_seguro():
    """Caso real (reconstruido con los mismos nombres/tabla que
    `live_experience_scoring.compute_own_experience_table()` persiste vía
    `live_experience_knowledge.record_experience_knowledge()`): el
    baseline correcto (3.17, mismo valor confirmado en producción el
    2026-09-14) se lee walk-forward-seguro (`computed_as_of < as_of_date`)."""
    from atlas_live.learning import live_experience_knowledge as lek

    orig = _fresh_lek()
    try:
        tabla = [{
            "direction": _LEK_DIRECTION, "timing_deteccion": _LEK_TIMING, "bucket": "poblacion_total",
            "n_evaluables": 500, "n_aciertos_20": 0, "pct_20": 0.0,
            "wilson_lower_bound_20_pct": 0.0, "wilson_upper_bound_20_pct": 0.8,
            "baseline_pct_20": 3.17, "lift_20": 0.0, "mediana_max_advance_pct": 1.0,
            "validation_state": "VALIDACION_ROBUSTA",
            "computed_as_of": "2026-09-13", "computed_at": "2026-09-13T20:00:00+00:00",
            "n_aciertos_50": 0, "pct_50": 0.0, "n_aciertos_100": 0, "pct_100": 0.0,
            "feature_cut_low": None, "feature_cut_high": None,
        }]
        lek.record_experience_knowledge(tabla, methodology_version=_LEK_METHOD)
        assert cer._market_wide_baseline_pct_20("2026-09-14", _LEK_METHOD) == 3.17
        # walk-forward: el mismo snapshot no debe usarse para un as_of_date <= su computed_as_of.
        assert cer._market_wide_baseline_pct_20("2026-09-13", _LEK_METHOD) is None
    finally:
        _restore_lek(orig)


def test_fix_reproduce_caso_real_neutral_antes_del_movimiento_buggy_vs_corregido():
    """Reproduce EXACTAMENTE el caso real de producción (2026-09-14) que
    confirmó el bug de baseline: con el baseline BUGGY (calculado sobre
    la ventana angosta, igual al propio pct_20 de la condición) el
    resultado real observado en producción fue DEGRADADO.

    HONESTIDAD EXPLÍCITA (2026-09-14, mismo día en que se encontró un
    SEGUNDO bug, de dirección, en `continuous_evaluation.py`): con SOLO
    el fix de baseline aplicado (baseline real=3.17, pero la comparación
    todavía invertida), el resultado hubiera cambiado a VALIDO -- así lo
    documentaba una versión anterior de este mismo test. Pero con AMBOS
    fixes aplicados (el que corresponde a este archivo), el resultado
    real y correcto es DEGRADADO: `wilson_upper=0.8` está confidentemente
    POR DEBAJO del baseline real `3.17` (0% de éxito observado, ni
    siquiera el límite optimista de la muestra alcanza al baseline) --
    esto SÍ es una condición genuinamente mala, y debe marcarse
    DEGRADADO. La diferencia con el bug original: antes, `DEGRADADO`
    salía de comparar la condición contra sí misma (0.8 >= 0.0, un
    absurdo tautológico); ahora sale de compararla correctamente contra
    el baseline real del mercado (0.8 < 3.17, una comparación con
    sentido) -- mismo estado final (`DEGRADADO`), pero por primera vez
    por la razón correcta."""
    _fresh()
    try:
        fila_real = {
            "direction": _LEK_DIRECTION, "timing_deteccion": _LEK_TIMING, "bucket": "poblacion_total",
            "n_evaluables": 500, "n_aciertos_20": 0, "pct_20": 0.0,
            "wilson_lower_bound_20_pct": 0.0, "wilson_upper_bound_20_pct": 0.8,
            "baseline_pct_20": 0.0,  # el valor BUGGY real observado (== su propio pct_20)
            "lift_20": None, "mediana_max_advance_pct": 0.0,
            "validation_state": "VALIDACION_ROBUSTA",
            "computed_as_of": "2026-09-13", "computed_at": "2026-09-14T00:00:00+00:00",
            "n_aciertos_50": 0, "pct_50": 0.0, "n_aciertos_100": 0, "pct_100": 0.0,
        }
        ventana_falsa = [{"market_date": "2026-09-12"}]

        # 1) SIN el fix aplicado (sin pasar market_baseline_pct_20, y sin
        #    ningún live_experience_knowledge disponible): el bug histórico
        #    quedaría reproducido si `evaluate_condition()` todavía leyera
        #    fila["baseline_pct_20"] -- confirmamos que YA NO lo hace (cae
        #    a NO_EVALUABLE en vez de al DEGRADADO falso, fail-safe).
        orig_lek = _fresh_lek()
        try:
            with mock.patch.object(cer, "_recent_condition_rows_by_stage", return_value=ventana_falsa), \
                 mock.patch("atlas_live.learning.live_experience_scoring.compute_own_experience_table", return_value=[fila_real]):
                snap_sin_baseline_real = cer.evaluate_condition(
                    direction=_LEK_DIRECTION, timing_deteccion=_LEK_TIMING, methodology_version=_LEK_METHOD,
                    as_of_date="2026-09-14", auto_revoke=False, fuente="stage",
                )
            assert snap_sin_baseline_real["evaluation_state"] == "NO_EVALUABLE"
            assert "DATOS_FALTANTES" in snap_sin_baseline_real["reason"]
            assert snap_sin_baseline_real["recent_baseline_pct_20"] != 0.0  # NUNCA el valor buggy
        finally:
            _restore_lek(orig_lek)

        # 2) CON el baseline real de mercado (3.17, el mismo confirmado en
        #    producción el mismo día) pasado explícito -- exactamente como
        #    lo hace `evaluate_conditions_from_experience_table()` en el
        #    camino real event-driven.
        with mock.patch.object(cer, "_recent_condition_rows_by_stage", return_value=ventana_falsa), \
             mock.patch("atlas_live.learning.live_experience_scoring.compute_own_experience_table", return_value=[fila_real]):
            snap_corregido = cer.evaluate_condition(
                direction=_LEK_DIRECTION, timing_deteccion=_LEK_TIMING, methodology_version=_LEK_METHOD,
                as_of_date="2026-09-14", auto_revoke=False, fuente="stage",
                market_baseline_pct_20=3.17,
            )
        assert snap_corregido["recent_baseline_pct_20"] == 3.17
        # DEGRADADO, correctamente: 0.8 (wilson_upper) < 3.17 (baseline real)
        # -- ver docstring de arriba, no es el mismo DEGRADADO buggy de antes.
        assert snap_corregido["evaluation_state"] == "DEGRADADO"
        assert "0.8" in snap_corregido["reason"] and "3.17" in snap_corregido["reason"]
    finally:
        _restore()


def test_fix_event_driven_extrae_baseline_de_tabla_sin_consulta_redundante():
    """`evaluate_conditions_from_experience_table()` debe extraer el
    baseline UNA vez de `tabla` (ya calculado correctamente por Fase 2
    sobre el mercado completo) y pasarlo explícito -- nunca debe disparar
    una consulta nueva a `live_experience_knowledge.db` por cada
    condición (contención de costo, además de corrección)."""
    _fresh()
    try:
        tabla = [
            {**_fila_robusta(n=600, wilson_upper=25.0, baseline=3.17), "direction": "ALCISTA", "timing_deteccion": "INICIO"},
            {**_fila_robusta(n=600, wilson_upper=25.0, baseline=3.17), "direction": "BAJISTA", "timing_deteccion": "NO_PERSEGUIR"},
        ]
        with mock.patch.object(cer, "_market_wide_baseline_pct_20") as espia_lookup, \
             mock.patch.object(cer, "_recent_condition_rows", return_value=[{"market_date": "2026-08-19"}]), \
             mock.patch("atlas_live.learning.live_experience_scoring.compute_own_experience_table", return_value=tabla):
            cer.evaluate_conditions_from_experience_table(tabla, _AS_OF)
        assert espia_lookup.call_count == 0  # el baseline vino de `tabla`, nunca de una consulta nueva
    finally:
        _restore()


def test_fix_evaluate_condition_manual_usa_lookup_real_cuando_no_se_provee_baseline():
    """Camino manual (`POST /api/admin/continuous-evaluation-run`, sin
    `tabla` a mano): SIN `market_baseline_pct_20` explícito, debe llamar a
    `_market_wide_baseline_pct_20()` -- nunca reintroducir el bug leyendo
    `fila['baseline_pct_20']`."""
    _fresh()
    try:
        fila = _fila_robusta(n=600, wilson_upper=25.0, baseline=999.0)  # valor BUGGY deliberadamente absurdo
        with mock.patch.object(cer, "_market_wide_baseline_pct_20", return_value=3.17) as espia_lookup, \
             mock.patch.object(cer, "_recent_condition_rows", return_value=[{"market_date": "2026-08-19"}]), \
             mock.patch("atlas_live.learning.live_experience_scoring.compute_own_experience_table", return_value=[fila]):
            snap = cer.evaluate_condition(
                direction=_DIRECTION, timing_deteccion=_TIMING, methodology_version=_METHOD,
                as_of_date=_AS_OF, auto_revoke=False,
            )
        assert espia_lookup.call_count == 1
        assert snap["recent_baseline_pct_20"] == 3.17  # nunca el 999.0 de fila["baseline_pct_20"]
    finally:
        _restore()
