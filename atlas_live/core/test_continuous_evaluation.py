"""Tests de `continuous_evaluation.py` (Hito 3, Fase 3.6, 2026-09-03,
autorizado explícitamente en Plan Mode, revisión corregida). Puro, sin DB."""

import inspect

from atlas_live.core import continuous_evaluation as ce
from atlas_live.radar.candidate_registry import META_MUESTRA_MINIMA

_MARKET_DATE = "2026-08-24"


def _clasificar(recent_sample_size=600, recent_wilson_upper_bound_20_pct=25.0,
                 recent_baseline_pct_20=35.0, computed_as_of="2026-08-20"):
    return ce.classify_continuous_evaluation(
        recent_sample_size=recent_sample_size,
        recent_wilson_upper_bound_20_pct=recent_wilson_upper_bound_20_pct,
        recent_baseline_pct_20=recent_baseline_pct_20,
        computed_as_of=computed_as_of, market_date=_MARKET_DATE,
    )


# --- A) robusta, no degradada -> VALIDO ------------------------------------

def test_a_robusta_no_degradada_es_valido():
    r = _clasificar(recent_sample_size=600, recent_wilson_upper_bound_20_pct=25.0, recent_baseline_pct_20=35.0)
    assert r["evaluation_state"] == "VALIDO"
    assert r["revocation_requested"] is False


def test_a_frontera_exacta_n_igual_al_piso_y_wilson_por_debajo_es_valido():
    r = _clasificar(recent_sample_size=META_MUESTRA_MINIMA, recent_wilson_upper_bound_20_pct=34.9, recent_baseline_pct_20=35.0)
    assert r["evaluation_state"] == "VALIDO"
    assert r["revocation_requested"] is False


# --- B) n < META_MUESTRA_MINIMA -> INSUFICIENTE ----------------------------

def test_b_muestra_chica_es_insuficiente_no_revoke():
    r = _clasificar(recent_sample_size=499, recent_wilson_upper_bound_20_pct=90.0, recent_baseline_pct_20=10.0)  # "degradado" en apariencia
    assert r["evaluation_state"] == "INSUFICIENTE"
    assert r["revocation_requested"] is False
    assert "MUESTRA_RECIENTE_INSUFICIENTE" in r["reason"]


def test_b_muestra_chica_nunca_activa_revocacion_aunque_metricas_luzcan_mal():
    r = _clasificar(recent_sample_size=1, recent_wilson_upper_bound_20_pct=99.9, recent_baseline_pct_20=0.1)
    assert r["evaluation_state"] == "INSUFICIENTE"
    assert r["revocation_requested"] is False


# --- C) error/datos faltantes/walk-forward inválido -> NO_EVALUABLE -------

def test_c_sample_size_none_es_no_evaluable():
    r = _clasificar(recent_sample_size=None)
    assert r["evaluation_state"] == "NO_EVALUABLE"
    assert r["revocation_requested"] is False
    assert "DATOS_FALTANTES" in r["reason"]


def test_c_wilson_upper_none_es_no_evaluable():
    r = _clasificar(recent_wilson_upper_bound_20_pct=None)
    assert r["evaluation_state"] == "NO_EVALUABLE"
    assert r["revocation_requested"] is False


def test_c_baseline_none_es_no_evaluable():
    r = _clasificar(recent_baseline_pct_20=None)
    assert r["evaluation_state"] == "NO_EVALUABLE"


def test_c_computed_as_of_none_es_no_evaluable():
    r = _clasificar(computed_as_of=None)
    assert r["evaluation_state"] == "NO_EVALUABLE"
    assert r["walk_forward_ok"] is False


def test_c_walk_forward_igualdad_es_no_evaluable():
    r = _clasificar(computed_as_of=_MARKET_DATE)
    assert r["evaluation_state"] == "NO_EVALUABLE"
    assert "WALK_FORWARD_VIOLATION" in r["reason"]
    assert r["revocation_requested"] is False


def test_c_walk_forward_posterior_es_no_evaluable():
    r = _clasificar(computed_as_of="2026-08-25")
    assert r["evaluation_state"] == "NO_EVALUABLE"
    assert r["revocation_requested"] is False


def test_c_walk_forward_seguro_no_es_no_evaluable():
    r = _clasificar(computed_as_of="2026-08-20")
    assert r["walk_forward_ok"] is True
    assert r["evaluation_state"] != "NO_EVALUABLE"


# --- D) n >= piso y wilson_upper >= baseline -> DEGRADADO ------------------

def test_d_degradado_dispara_solicitud_de_revocacion():
    r = _clasificar(recent_sample_size=600, recent_wilson_upper_bound_20_pct=40.0, recent_baseline_pct_20=35.0)
    assert r["evaluation_state"] == "DEGRADADO"
    assert r["revocation_requested"] is True
    assert "DEGRADACION_DETECTADA" in r["reason"]


def test_d_frontera_exacta_wilson_upper_igual_a_baseline_es_degradado():
    # ">=" estricto -- empate cuenta como "ya no le gana a la base".
    r = _clasificar(recent_sample_size=600, recent_wilson_upper_bound_20_pct=35.0, recent_baseline_pct_20=35.0)
    assert r["evaluation_state"] == "DEGRADADO"
    assert r["revocation_requested"] is True


def test_d_frontera_exacta_n_justo_en_el_piso_permite_degradado():
    r = _clasificar(recent_sample_size=META_MUESTRA_MINIMA, recent_wilson_upper_bound_20_pct=40.0, recent_baseline_pct_20=35.0)
    assert r["evaluation_state"] == "DEGRADADO"


# --- 5) diferencia clara entre los 4 estados -------------------------------

def test_solo_degradado_marca_revocation_requested_true():
    for estado_esperado, kwargs in [
        ("VALIDO", dict(recent_sample_size=600, recent_wilson_upper_bound_20_pct=25.0, recent_baseline_pct_20=35.0)),
        ("INSUFICIENTE", dict(recent_sample_size=50, recent_wilson_upper_bound_20_pct=25.0, recent_baseline_pct_20=35.0)),
        ("NO_EVALUABLE", dict(recent_sample_size=None, recent_wilson_upper_bound_20_pct=25.0, recent_baseline_pct_20=35.0)),
        ("DEGRADADO", dict(recent_sample_size=600, recent_wilson_upper_bound_20_pct=40.0, recent_baseline_pct_20=35.0)),
    ]:
        r = _clasificar(**kwargs)
        assert r["evaluation_state"] == estado_esperado
        assert r["revocation_requested"] == (estado_esperado == "DEGRADADO")


def test_revocado_nunca_aparece_como_evaluation_state():
    # REVOCADO es un estado operacional de activation_registry (Fase 3.5),
    # nunca un valor posible de evaluation_state en este módulo.
    assert "REVOCADO" not in ce.EVALUATION_STATES


# --- reutilización de META_MUESTRA_MINIMA, no un número nuevo -------------

def test_usa_meta_muestra_minima_oficial_no_un_numero_nuevo():
    fuente = inspect.getsource(ce)
    assert "from atlas_live.radar.candidate_registry import META_MUESTRA_MINIMA" in fuente
    assert "META_MUESTRA_MINIMA" in inspect.getsource(ce.classify_continuous_evaluation)
    assert META_MUESTRA_MINIMA == 500  # confirmación de la constante oficial real


# --- fail-safe / aislamiento ------------------------------------------------

def test_firma_solo_acepta_primitivos_nunca_objetos_de_decision():
    firma = str(inspect.signature(ce.classify_continuous_evaluation))
    for tipo_prohibido in ("AtlasDecision", "CandidateSnapshot", "DecisionFeatures", "DecisionScores", "DecisionEvidence"):
        assert tipo_prohibido not in firma


def test_modulo_nunca_ejecuta_apply_recalibration_ni_llama_a_revoke():
    # El docstring SÍ menciona `revoke()` en prosa (explicando el alcance)
    # -- se descarta la docstring y se revisa solo el cuerpo ejecutable,
    # mismo criterio de falsos positivos ya resuelto varias veces en este Hito.
    fn = ce.classify_continuous_evaluation
    fuente_completa = inspect.getsource(fn)
    # La docstring es el primer '"""..."""' -- todo lo que viene DESPUÉS
    # de su cierre es código ejecutable real.
    cuerpo_ejecutable = fuente_completa.split('"""', 2)[-1]
    assert "apply_recalibration" not in cuerpo_ejecutable
    assert "revoke(" not in cuerpo_ejecutable
    assert "adc.decide" not in cuerpo_ejecutable


def test_sin_vocabulario_de_ejecucion_financiera():
    fuente = inspect.getsource(ce).lower()
    for palabra in ("broker", "place_order", "execute_trade", "buy(", "sell("):
        assert palabra not in fuente


def test_misma_entrada_produce_siempre_el_mismo_resultado():
    kwargs = dict(recent_sample_size=600, recent_wilson_upper_bound_20_pct=25.0,
                  recent_baseline_pct_20=35.0, computed_as_of="2026-08-20", market_date=_MARKET_DATE)
    assert ce.classify_continuous_evaluation(**kwargs) == ce.classify_continuous_evaluation(**kwargs)
