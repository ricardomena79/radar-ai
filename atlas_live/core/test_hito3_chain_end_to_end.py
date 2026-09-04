"""HITO 4 -- Fase 4.1 (2026-09-04, autorizado explícitamente en Plan Mode):
test de integración EXTREMO A EXTREMO de la cadena completa de conocimiento
de Hito 3 (3.0 -> 3.6). Cierra el gap señalado en la auditoría global de
cierre de Hito 3: cada fase estaba probada por separado, pero nunca
encadenada en una sola corrida.

Reutiliza ÚNICAMENTE funciones reales de cada fase -- nunca reimplementa
clasificación ni estadística. Construye la evidencia como dicts sintéticos
(mismo patrón ya usado en `test_continuous_evaluation_registry.py` y en
los tests puros de cada fase por separado) en vez de sembrar cientos de
filas reales en `candidate_registry`: las funciones de clasificación de
3.3-3.6 son puras y aceptan `learned_evidence`/filas de ventana como
parámetros directos -- no hace falta reconstruir el pipeline de detección
completo para probar que las fases se ENCADENAN correctamente.

`apply_recalibration=True` se ejecuta acá, en un test aislado sobre DBs
temporales -- nunca toca producción. `activation_registry.set_mechanism_state
("ON_CONTROLADO", ...)` también se llama, pero SOLO contra la DB temporal
de este test (`areg.DB_PATH` queda apuntando a un tempfile único, ver
`_fresh()`) -- jamás la real. Ningún broker, ninguna orden, ningún dinero
real -- no existen en el repo, confirmado repetidamente en toda la sesión.

Este archivo es la ÚNICA pieza de Fase 4.1 -- puro test, no crea ni
modifica ningún módulo de producción."""

import tempfile
import uuid as _uuid
from pathlib import Path
from unittest import mock

from atlas_live.core import activation_gate as ag
from atlas_live.core import activation_registry as areg
from atlas_live.core import atlas_decision_core as adc
from atlas_live.core import continuous_evaluation_registry as cer
from atlas_live.core import decision_knowledge_registry as dk_registry
from atlas_live.core import knowledge_eligibility as ke
from atlas_live.core import knowledge_eligibility_registry as ker
from atlas_live.core import shadow_observation as so
from atlas_live.core import shadow_observation_registry as sor
from atlas_live.learning import live_experience_knowledge as lek

_ORIG_DK_DB = dk_registry.DB_PATH
_ORIG_KER_DB = ker.DB_PATH
_ORIG_SOR_DB = sor.DB_PATH
_ORIG_AREG_DB = areg.DB_PATH
_ORIG_CER_DB = cer.DB_PATH

_DIRECTION = "ALCISTA"
_TIMING = "al_comienzo"
_METHOD = lek.METHODOLOGY_VERSION
_MARKET_DATE = "2026-08-24"
_COMPUTED_AS_OF = "2026-08-20"
_TICKER = "H4E2E"


def _fresh():
    dk_registry.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_h4_dk_{_uuid.uuid4().hex}.db"
    ker.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_h4_ker_{_uuid.uuid4().hex}.db"
    sor.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_h4_sor_{_uuid.uuid4().hex}.db"
    areg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_h4_areg_{_uuid.uuid4().hex}.db"
    cer.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_h4_cer_{_uuid.uuid4().hex}.db"


def _restore():
    dk_registry.DB_PATH = _ORIG_DK_DB
    ker.DB_PATH = _ORIG_KER_DB
    sor.DB_PATH = _ORIG_SOR_DB
    areg.DB_PATH = _ORIG_AREG_DB
    cer.DB_PATH = _ORIG_CER_DB


def _baseline_candidate():
    """Candidata sintética garantizada `OPORTUNIDAD_PRIORITARIA` --
    `stage="INICIO"` + `direction="ALCISTA"` + precio actual OK (ver
    `priority_classifier.classify_final_priority()`, regla 4)."""
    candidate = adc.CandidateSnapshot(ticker=_TICKER, market_date=_MARKET_DATE, tiene_precio_actual=True)
    features = adc.DecisionFeatures(stage="INICIO", direction=_DIRECTION, change_pct_confiable=True)
    return candidate, features


def _learned_evidence(wilson_upper, baseline, sample_size=600, computed_as_of=_COMPUTED_AS_OF):
    """Misma forma exacta que devuelve `learned_evidence.get_learned_evidence()`
    -- construida a mano (mismo criterio ya usado en `test_knowledge_eligibility.py`/
    `test_shadow_observation.py`), nunca sembrando 600 filas reales."""
    return {
        "available": True,
        "validation_state": "VALIDACION_ROBUSTA",
        "sample_size": sample_size,
        "wilson_lower_bound_20_pct": max(0.0, wilson_upper - 10.0),
        "wilson_upper_bound_20_pct": wilson_upper,
        "baseline_pct_20": baseline,
        "lift_20": round(wilson_upper / baseline, 2) if baseline else None,
        "computed_as_of": computed_as_of,
        "computed_at": f"{computed_as_of}T12:00:00+00:00",
        "methodology_version": _METHOD,
    }


def _fila_ventana(n, wilson_upper, baseline, computed_as_of=_COMPUTED_AS_OF):
    """Misma forma que `live_experience_scoring.compute_own_experience_table()`
    -- inyectada vía mock (mismo patrón ya usado en
    `test_continuous_evaluation_registry.py::_fila_robusta`)."""
    return {
        "direction": _DIRECTION, "timing_deteccion": _TIMING, "bucket": "poblacion_total",
        "n_evaluables": n, "n_aciertos_20": int(n * 0.2), "pct_20": 20.0,
        "wilson_lower_bound_20_pct": max(0.0, wilson_upper - 10.0), "wilson_upper_bound_20_pct": wilson_upper,
        "baseline_pct_20": baseline, "lift_20": 1.0, "mediana_max_advance_pct": 10.0,
        "validation_state": "VALIDACION_ROBUSTA" if n >= 500 else "EN_VALIDACION",
        "computed_as_of": computed_as_of, "computed_at": f"{computed_as_of}T12:00:00+00:00",
        "n_aciertos_50": 0, "pct_50": 0.0, "n_aciertos_100": 0, "pct_100": 0.0,
    }


# --- 1) cadena feliz: sin divergencia, sin observación, sin activación ------

def test_1_cadena_feliz_sin_divergencia_no_escribe_shadow_observation():
    _fresh()
    try:
        candidate, features = _baseline_candidate()
        baseline = adc.decide(candidate, features)
        assert baseline.decision == "OPORTUNIDAD_PRIORITARIA"

        # wilson_upper >= baseline -> el shadow NO baja de escalón.
        le = _learned_evidence(wilson_upper=40.0, baseline=35.0)
        shadow = adc.decide(candidate, features, learned_evidence=le)
        assert shadow.decision_shadow == baseline.decision
        assert shadow.shadow_differs is False

        # 3.0/3.1 -- snapshot SIEMPRE, sin importar divergencia.
        assert dk_registry.record_decision_knowledge_snapshot(
            ticker=_TICKER, market_date=_MARKET_DATE,
            decision_timestamp=baseline.decision_timestamp.isoformat(),
            decision=baseline.decision, decision_shadow=shadow.decision_shadow,
            shadow_differs=shadow.shadow_differs, learned_evidence=le,
            direction=_DIRECTION, timing_deteccion=_TIMING,
            core_methodology_version=baseline.methodology_version,
        ) is True

        # 3.3 -- ELEGIBLE (VALIDACION_ROBUSTA).
        eligibilidad = ke.classify_eligibility(le, _MARKET_DATE)
        assert eligibilidad["eligibility_state"] == "ELEGIBLE"
        ker.record_eligibility_snapshot(
            direction=_DIRECTION, timing_deteccion=_TIMING,
            evaluated_as_of=_MARKET_DATE, eligibility_result=eligibilidad,
        )

        # 3.4 -- shadow_differs=False -> observado=False -> NO se escribe fila.
        observacion = so.classify_shadow_observation(
            decision=baseline.decision, decision_shadow=shadow.decision_shadow,
            shadow_differs=shadow.shadow_differs, eligibility_state=eligibilidad["eligibility_state"],
            computed_as_of=le["computed_as_of"], market_date=_MARKET_DATE,
        )
        assert observacion["observado"] is False
        escrito = sor.record_shadow_observation(
            ticker=_TICKER, market_date=_MARKET_DATE,
            decision_timestamp=baseline.decision_timestamp.isoformat(),
            direction=_DIRECTION, timing_deteccion=_TIMING,
            core_methodology_version=baseline.methodology_version,
            observation=observacion, learned_evidence=le,
        )
        assert escrito is False
        assert sor.get_observations_for(_TICKER, _MARKET_DATE) == []

        # 3.5 -- mecanismo sigue OFF por defecto (nadie lo encendió) -> NO_ACTIVO.
        assert areg.get_mechanism_state() == "OFF"
        gate = ag.classify_activation(
            mechanism_state=areg.get_mechanism_state(), eligibility_state=eligibilidad["eligibility_state"],
            is_revoked=areg.is_revoked(_DIRECTION, _TIMING, _METHOD),
            computed_as_of=le["computed_as_of"], market_date=_MARKET_DATE,
        )
        assert gate["activation_state"] == "NO_ACTIVO"

        # El baseline nunca se movió durante todo el recorrido.
        assert baseline.decision == "OPORTUNIDAD_PRIORITARIA"
    finally:
        _restore()


# --- 2) cadena con divergencia -> observación real -> activación controlada -

def test_2_divergencia_real_dispara_observacion_y_activacion_controlada():
    _fresh()
    try:
        candidate, features = _baseline_candidate()
        baseline = adc.decide(candidate, features)
        assert baseline.decision == "OPORTUNIDAD_PRIORITARIA"

        # wilson_upper < baseline -> downgrade de un escalón (shadow real).
        le = _learned_evidence(wilson_upper=25.0, baseline=35.0)
        shadow = adc.decide(candidate, features, learned_evidence=le)
        assert shadow.decision_shadow == "VIGILAR"
        assert shadow.shadow_differs is True

        dk_registry.record_decision_knowledge_snapshot(
            ticker=_TICKER, market_date=_MARKET_DATE,
            decision_timestamp=baseline.decision_timestamp.isoformat(),
            decision=baseline.decision, decision_shadow=shadow.decision_shadow,
            shadow_differs=shadow.shadow_differs, learned_evidence=le,
            direction=_DIRECTION, timing_deteccion=_TIMING,
            core_methodology_version=baseline.methodology_version,
        )

        eligibilidad = ke.classify_eligibility(le, _MARKET_DATE)
        assert eligibilidad["eligibility_state"] == "ELEGIBLE"
        ker.record_eligibility_snapshot(
            direction=_DIRECTION, timing_deteccion=_TIMING,
            evaluated_as_of=_MARKET_DATE, eligibility_result=eligibilidad,
        )
        # Veredicto REAL leído de vuelta, tal como hace server.py -- nunca
        # se reutiliza el dict `eligibilidad` calculado arriba directamente.
        veredicto_3_3 = ker.latest_eligibility_for(_DIRECTION, _TIMING, _METHOD)
        assert veredicto_3_3["eligibility_state"] == "ELEGIBLE"

        observacion = so.classify_shadow_observation(
            decision=baseline.decision, decision_shadow=shadow.decision_shadow,
            shadow_differs=shadow.shadow_differs, eligibility_state=veredicto_3_3["eligibility_state"],
            computed_as_of=le["computed_as_of"], market_date=_MARKET_DATE,
        )
        assert observacion["observado"] is True
        assert sor.record_shadow_observation(
            ticker=_TICKER, market_date=_MARKET_DATE,
            decision_timestamp=baseline.decision_timestamp.isoformat(),
            direction=_DIRECTION, timing_deteccion=_TIMING,
            core_methodology_version=baseline.methodology_version,
            observation=observacion, learned_evidence=le,
        ) is True
        assert len(sor.get_observations_for(_TICKER, _MARKET_DATE)) == 1

        # 3.5 -- mecanismo encendido SOLO en la DB temporal de este test.
        assert areg.set_mechanism_state("ON_CONTROLADO", "Hito 4, Fase 4.1 -- test E2E aislado") is True
        assert areg.get_mechanism_state() == "ON_CONTROLADO"
        is_revoked = areg.is_revoked(_DIRECTION, _TIMING, _METHOD)
        assert is_revoked is False

        gate = ag.classify_activation(
            mechanism_state="ON_CONTROLADO", eligibility_state=veredicto_3_3["eligibility_state"],
            is_revoked=is_revoked, computed_as_of=le["computed_as_of"], market_date=_MARKET_DATE,
        )
        assert gate["activation_state"] == "ACTIVADO"

        # ÚNICA llamada de este test con apply_recalibration=True -- aislada,
        # sobre datos sintéticos, nunca toca producción ni ningún broker.
        controlada = adc.decide(candidate, features, learned_evidence=le, apply_recalibration=True)
        assert controlada.decision == "VIGILAR"

        assert areg.record_activation_state(
            ticker=_TICKER, market_date=_MARKET_DATE,
            decision_timestamp=baseline.decision_timestamp.isoformat(),
            direction=_DIRECTION, timing_deteccion=_TIMING,
            core_methodology_version=baseline.methodology_version,
            mechanism_state="ON_CONTROLADO", eligibility_state=veredicto_3_3["eligibility_state"],
            gate=gate, decision_controlada=controlada.decision, learned_evidence=le,
        ) is True

        # El baseline real -- el que hubiera visto el usuario -- nunca cambió,
        # ni siquiera después de que `apply_recalibration=True` se ejecutó.
        assert baseline.decision == "OPORTUNIDAD_PRIORITARIA"
        assert controlada.decision != baseline.decision
    finally:
        _restore()


# --- 3) degradación robusta -> revocación real -> bloqueo del gate REAL ----

def test_3_degradacion_revoca_y_el_gate_real_de_3_5_queda_bloqueado():
    _fresh()
    try:
        le = _learned_evidence(wilson_upper=25.0, baseline=35.0)
        eligibilidad = ke.classify_eligibility(le, _MARKET_DATE)
        assert eligibilidad["eligibility_state"] == "ELEGIBLE"
        ker.record_eligibility_snapshot(
            direction=_DIRECTION, timing_deteccion=_TIMING,
            evaluated_as_of=_MARKET_DATE, eligibility_result=eligibilidad,
        )
        veredicto_3_3 = ker.latest_eligibility_for(_DIRECTION, _TIMING, _METHOD)

        areg.set_mechanism_state("ON_CONTROLADO", "Hito 4, Fase 4.1 -- test E2E aislado")

        # Control ANTES de degradar: sin revocación, el gate real da ACTIVADO
        # (mismo patrón de control que test_g de continuous_evaluation_registry.py).
        gate_antes = ag.classify_activation(
            mechanism_state="ON_CONTROLADO", eligibility_state=veredicto_3_3["eligibility_state"],
            is_revoked=areg.is_revoked(_DIRECTION, _TIMING, _METHOD),
            computed_as_of=le["computed_as_of"], market_date=_MARKET_DATE,
        )
        assert gate_antes["activation_state"] == "ACTIVADO"

        # 3.6 -- ventana reciente DEGRADADA (n>=500, wilson_upper>=baseline),
        # inyectada vía mock (mismo patrón que test_continuous_evaluation_registry.py
        # -- no hace falta sembrar 600 filas reales en candidate_registry).
        fila_degradada = _fila_ventana(n=600, wilson_upper=40.0, baseline=35.0)
        with mock.patch.object(cer, "_recent_condition_rows", return_value=[{"market_date": "2026-08-19"}]), \
             mock.patch("atlas_live.learning.live_experience_scoring.compute_own_experience_table", return_value=[fila_degradada]):
            snap = cer.evaluate_condition(
                direction=_DIRECTION, timing_deteccion=_TIMING, methodology_version=_METHOD,
                as_of_date=_MARKET_DATE, auto_revoke=True,
            )
        assert snap["evaluation_state"] == "DEGRADADO"
        assert snap["revocation_result"] == "OK"
        assert areg.is_revoked(_DIRECTION, _TIMING, _METHOD) is True

        # El veredicto ACUMULADO de 3.3 sigue diciendo ELEGIBLE (nunca se
        # recalcula por una degradación reciente) -- y aun así el gate REAL
        # de 3.5, sin mockear, ahora bloquea por la revocación.
        veredicto_3_3_tras_degradacion = ker.latest_eligibility_for(_DIRECTION, _TIMING, _METHOD)
        assert veredicto_3_3_tras_degradacion["eligibility_state"] == "ELEGIBLE"

        gate_despues = ag.classify_activation(
            mechanism_state="ON_CONTROLADO", eligibility_state=veredicto_3_3_tras_degradacion["eligibility_state"],
            is_revoked=areg.is_revoked(_DIRECTION, _TIMING, _METHOD),
            computed_as_of=le["computed_as_of"], market_date=_MARKET_DATE,
        )
        assert gate_despues["activation_state"] == "REVOCADO"
    finally:
        _restore()


# --- 4) el baseline nunca cambia por nada de lo que ocurre aguas abajo -----

def test_4_baseline_decision_nunca_cambia_por_ningun_paso_downstream():
    _fresh()
    try:
        candidate, features = _baseline_candidate()
        baseline = adc.decide(candidate, features)
        decision_original = baseline.decision

        le = _learned_evidence(wilson_upper=25.0, baseline=35.0)
        shadow = adc.decide(candidate, features, learned_evidence=le)
        eligibilidad = ke.classify_eligibility(le, _MARKET_DATE)
        so.classify_shadow_observation(
            decision=baseline.decision, decision_shadow=shadow.decision_shadow,
            shadow_differs=shadow.shadow_differs, eligibility_state=eligibilidad["eligibility_state"],
            computed_as_of=le["computed_as_of"], market_date=_MARKET_DATE,
        )
        areg.set_mechanism_state("ON_CONTROLADO", "test")
        gate = ag.classify_activation(
            mechanism_state="ON_CONTROLADO", eligibility_state=eligibilidad["eligibility_state"],
            is_revoked=False, computed_as_of=le["computed_as_of"], market_date=_MARKET_DATE,
        )
        if gate["activation_state"] == "ACTIVADO":
            adc.decide(candidate, features, learned_evidence=le, apply_recalibration=True)

        # `AtlasDecision` es un dataclass frozen -- nada de lo anterior pudo
        # mutar el objeto `baseline` ya calculado al principio.
        assert baseline.decision == decision_original == "OPORTUNIDAD_PRIORITARIA"
    finally:
        _restore()


# --- 5) walk-forward violado bloquea en cada eslabón, incluso con ELEGIBLE --

def test_5_walk_forward_violado_bloquea_en_cada_eslabon():
    _fresh()
    try:
        # computed_as_of == market_date -> violación estricta.
        le_vencida = _learned_evidence(wilson_upper=25.0, baseline=35.0, computed_as_of=_MARKET_DATE)

        eligibilidad = ke.classify_eligibility(le_vencida, _MARKET_DATE)
        assert eligibilidad["eligibility_state"] == "NO_ELEGIBLE"
        assert "WALK_FORWARD_VIOLATION" in eligibilidad["reasons"][0]

        observacion = so.classify_shadow_observation(
            decision="OPORTUNIDAD_PRIORITARIA", decision_shadow="VIGILAR", shadow_differs=True,
            eligibility_state=eligibilidad["eligibility_state"],
            computed_as_of=le_vencida["computed_as_of"], market_date=_MARKET_DATE,
        )
        assert observacion["walk_forward_violation"] is True

        # Incluso si, hipotéticamente, la elegibilidad fuera ELEGIBLE, el
        # gate de 3.5 reverifica walk-forward de forma independiente y
        # bloquea igual.
        gate = ag.classify_activation(
            mechanism_state="ON_CONTROLADO", eligibility_state="ELEGIBLE",
            is_revoked=False, computed_as_of=le_vencida["computed_as_of"], market_date=_MARKET_DATE,
        )
        assert gate["activation_state"] == "BLOQUEADO"
        assert gate["walk_forward_violation"] is True
        assert gate["reason"] == "WALK_FORWARD_VIOLATION"
    finally:
        _restore()


# --- 6) LA cadena completa, en UNA sola ejecución continua sin resets ------

def test_6_cadena_completa_en_una_sola_ejecucion_continua():
    """Los tests 2 y 3 ya prueban, por separado, "candidato -> ... ->
    activación controlada" y "elegibilidad -> ... -> gate bloqueado" --
    correctos en aislamiento, pero ninguno por sí solo demuestra las 10
    etapas pedidas en la auditoría de Fase 4.1 en un solo flujo
    ininterrumpido. Este test existe específicamente para eso: candidato
    sintético -> baseline -> shadow -> decision snapshot -> elegibilidad
    -> shadow observation -> activación controlada real -> degradación ->
    revocación real -> gate REAL de 3.5 bloqueado, sin ningún `_fresh()`
    intermedio -- para descartar cualquier dependencia oculta entre
    tramos que la separación en tests independientes pudiera esconder."""
    _fresh()
    try:
        # 1) candidato sintético
        candidate, features = _baseline_candidate()

        # 2) baseline
        baseline = adc.decide(candidate, features)
        assert baseline.decision == "OPORTUNIDAD_PRIORITARIA"

        # 3) shadow
        le = _learned_evidence(wilson_upper=25.0, baseline=35.0)
        shadow = adc.decide(candidate, features, learned_evidence=le)
        assert shadow.shadow_differs is True

        # 4) decision snapshot (3.0/3.1)
        assert dk_registry.record_decision_knowledge_snapshot(
            ticker=_TICKER, market_date=_MARKET_DATE,
            decision_timestamp=baseline.decision_timestamp.isoformat(),
            decision=baseline.decision, decision_shadow=shadow.decision_shadow,
            shadow_differs=shadow.shadow_differs, learned_evidence=le,
            direction=_DIRECTION, timing_deteccion=_TIMING,
            core_methodology_version=baseline.methodology_version,
        ) is True

        # 5) elegibilidad (3.3), veredicto releído tal como hace server.py
        eligibilidad = ke.classify_eligibility(le, _MARKET_DATE)
        assert eligibilidad["eligibility_state"] == "ELEGIBLE"
        ker.record_eligibility_snapshot(
            direction=_DIRECTION, timing_deteccion=_TIMING,
            evaluated_as_of=_MARKET_DATE, eligibility_result=eligibilidad,
        )
        veredicto_3_3 = ker.latest_eligibility_for(_DIRECTION, _TIMING, _METHOD)
        assert veredicto_3_3["eligibility_state"] == "ELEGIBLE"

        # 6) shadow observation (3.4)
        observacion = so.classify_shadow_observation(
            decision=baseline.decision, decision_shadow=shadow.decision_shadow,
            shadow_differs=shadow.shadow_differs, eligibility_state=veredicto_3_3["eligibility_state"],
            computed_as_of=le["computed_as_of"], market_date=_MARKET_DATE,
        )
        assert observacion["observado"] is True
        assert sor.record_shadow_observation(
            ticker=_TICKER, market_date=_MARKET_DATE,
            decision_timestamp=baseline.decision_timestamp.isoformat(),
            direction=_DIRECTION, timing_deteccion=_TIMING,
            core_methodology_version=baseline.methodology_version,
            observation=observacion, learned_evidence=le,
        ) is True

        # 7) activación controlada REAL (3.5) -- gate real, sin mockear
        assert areg.set_mechanism_state("ON_CONTROLADO", "Hito 4, Fase 4.1 -- test E2E continuo") is True
        gate_activado = ag.classify_activation(
            mechanism_state=areg.get_mechanism_state(), eligibility_state=veredicto_3_3["eligibility_state"],
            is_revoked=areg.is_revoked(_DIRECTION, _TIMING, _METHOD),
            computed_as_of=le["computed_as_of"], market_date=_MARKET_DATE,
        )
        assert gate_activado["activation_state"] == "ACTIVADO"
        # ÚNICA llamada con apply_recalibration=True de este test -- pura,
        # sin DB, sin red, nunca pasa por server.py ni por ningún camino
        # de producción.
        controlada = adc.decide(candidate, features, learned_evidence=le, apply_recalibration=True)
        assert controlada.decision == "VIGILAR"
        assert areg.record_activation_state(
            ticker=_TICKER, market_date=_MARKET_DATE,
            decision_timestamp=baseline.decision_timestamp.isoformat(),
            direction=_DIRECTION, timing_deteccion=_TIMING,
            core_methodology_version=baseline.methodology_version,
            mechanism_state="ON_CONTROLADO", eligibility_state=veredicto_3_3["eligibility_state"],
            gate=gate_activado, decision_controlada=controlada.decision, learned_evidence=le,
        ) is True

        # 8) degradación (3.6) -- MISMA condición, sin resetear nada del
        # estado acumulado arriba. Solo la lectura de la ventana reciente
        # se inyecta (I/O real de candidate_registry.db, no relevante para
        # esta prueba de integración) -- la clasificación y la revocación
        # son las funciones reales, sin mockear.
        fila_degradada = _fila_ventana(n=600, wilson_upper=40.0, baseline=35.0)
        with mock.patch.object(cer, "_recent_condition_rows", return_value=[{"market_date": "2026-08-19"}]), \
             mock.patch("atlas_live.learning.live_experience_scoring.compute_own_experience_table", return_value=[fila_degradada]):
            snap = cer.evaluate_condition(
                direction=_DIRECTION, timing_deteccion=_TIMING, methodology_version=_METHOD,
                as_of_date=_MARKET_DATE, auto_revoke=True,
            )
        assert snap["evaluation_state"] == "DEGRADADO"

        # 9) revocación real -- confirmada por 2 vías independientes: el
        # resultado que devolvió evaluate_condition() Y una relectura
        # fresca de is_revoked() contra la misma DB temporal.
        assert snap["revocation_result"] == "OK"
        assert areg.is_revoked(_DIRECTION, _TIMING, _METHOD) is True

        # 10) gate REAL de 3.5, reevaluado -- bloqueado por la revocación
        # aunque 3.3 (releído de nuevo, sin tocar nada) siga en ELEGIBLE, y
        # aunque este mismo gate ya había dado ACTIVADO en el paso 7 para
        # la MISMA condición -- la única variable que cambió es `is_revoked`.
        veredicto_final = ker.latest_eligibility_for(_DIRECTION, _TIMING, _METHOD)
        assert veredicto_final["eligibility_state"] == "ELEGIBLE"
        gate_final = ag.classify_activation(
            mechanism_state="ON_CONTROLADO", eligibility_state=veredicto_final["eligibility_state"],
            is_revoked=areg.is_revoked(_DIRECTION, _TIMING, _METHOD),
            computed_as_of=le["computed_as_of"], market_date=_MARKET_DATE,
        )
        assert gate_final["activation_state"] == "REVOCADO"

        # El baseline original -- calculado en el paso 2 -- nunca cambió,
        # ni una sola vez, en las 10 etapas que siguieron.
        assert baseline.decision == "OPORTUNIDAD_PRIORITARIA"
    finally:
        _restore()
