"""Tests de catalyst_score.py (2026-08-23). Puros, sin DB/red."""

from atlas_live.catalyst import catalyst_score as cs
from atlas_live.catalyst.mrna_pattern import MRNA_PATTERN

# ---------------------------------------------------------------------------
# CATALYST_SCORE
# ---------------------------------------------------------------------------

def test_catalyst_score_alta_importancia_inminente_confirmado_da_alto():
    score = cs.catalyst_score(
        catalyst_type="FDA_PDUFA", importance="alta", lifecycle_state="INMINENTE",
        direction="ALCISTA", gates_fired_count=4, relative_volume_at_detection=3.0,
        change_pct_at_detection=6.0, price_change_since_published_pct=5.0,
    )
    assert score >= 80.0


def test_catalyst_score_financing_dilution_penalizado_siempre_bajo():
    """Pedido explícito del plan: un FINANCING_DILUTION debe dar
    catalyst_score <= 40, sin importar el resto de la evidencia."""
    score = cs.catalyst_score(
        catalyst_type="FINANCING_DILUTION", importance="media", lifecycle_state="OCURRIDO",
        direction="BAJISTA", gates_fired_count=2, relative_volume_at_detection=2.5,
        change_pct_at_detection=-4.0, price_change_since_published_pct=-3.0,
    )
    assert score <= 40.0


def test_catalyst_score_extendida_baja_por_lifecycle():
    score_inminente = cs.catalyst_score(
        catalyst_type="CLINICAL_TRIAL", importance="alta", lifecycle_state="INMINENTE", direction="NEUTRAL",
    )
    score_extendida = cs.catalyst_score(
        catalyst_type="CLINICAL_TRIAL", importance="alta", lifecycle_state="EXTENDIDA", direction="NEUTRAL",
    )
    assert score_extendida < score_inminente


def test_catalyst_score_nunca_sale_del_rango_0_100():
    score = cs.catalyst_score(
        catalyst_type="FINANCING_DILUTION", importance="baja", lifecycle_state="EXTENDIDA", direction="BAJISTA",
    )
    assert 0.0 <= score <= 100.0


def test_direction_alignment_score_desalineado_da_cero():
    assert cs.direction_alignment_score("ALCISTA", -5.0) == 0.0
    assert cs.direction_alignment_score("BAJISTA", 5.0) == 0.0


def test_direction_alignment_score_sin_dato_es_neutral():
    assert cs.direction_alignment_score("ALCISTA", None) == 50.0
    assert cs.direction_alignment_score("NEUTRAL", 5.0) == 50.0


def test_technical_confirmation_score_todo_confirmado_da_100():
    assert cs.technical_confirmation_score(4, 3.0, 6.0) == 100.0


def test_technical_confirmation_score_sin_nada_da_cero():
    assert cs.technical_confirmation_score(0, None, None) == 0.0


# ---------------------------------------------------------------------------
# MRNA_SIMILARITY_SCORE
# ---------------------------------------------------------------------------

def test_mrna_similarity_vector_real_de_mrna_da_score_alto():
    """El propio vector real de MRNA comparado contra sí mismo debe dar
    una similitud muy alta -- prueba de sanidad del vector congelado."""
    score = cs.mrna_similarity_score(
        catalyst_type="FDA_PDUFA",  # transformational
        gates_fired_count=MRNA_PATTERN["gates_fired_count"],
        relative_volume_at_detection=MRNA_PATTERN["relative_volume_at_detection"],
        relative_volume_hoy_peak=MRNA_PATTERN["relative_volume_hoy_peak"],
        direction=MRNA_PATTERN["direction"],
        retroceso_desde_maximo_pct=MRNA_PATTERN["retroceso_desde_maximo_pct_max"],
    )
    assert score >= 95.0


def test_mrna_similarity_caso_opuesto_da_score_bajo():
    """Analyst action, RVOL ya alto (sin sorpresa), sin volumen acelerando,
    bajista, retroceso fuerte -- estructuralmente nada parecido a MRNA."""
    score = cs.mrna_similarity_score(
        catalyst_type="ANALYST_ACTION",
        gates_fired_count=0,
        relative_volume_at_detection=1.5,
        relative_volume_hoy_peak=1.0,
        direction="BAJISTA",
        retroceso_desde_maximo_pct=45.0,
    )
    assert score <= 15.0


def test_mrna_similarity_sin_datos_da_cero_nunca_inventa():
    score = cs.mrna_similarity_score(catalyst_type="OTHER_MATERIAL")
    assert score == 0.0


def test_mrna_similarity_nunca_sale_del_rango_0_100():
    score = cs.mrna_similarity_score(
        catalyst_type="FDA_PDUFA", gates_fired_count=10,
        relative_volume_at_detection=0.0, relative_volume_hoy_peak=999.0,
        direction="ALCISTA", retroceso_desde_maximo_pct=0.0,
    )
    assert 0.0 <= score <= 100.0


# ---------------------------------------------------------------------------
# CATALYST_OPPORTUNITY_SCORE (Segunda Fase, 2026-08-24)
# ---------------------------------------------------------------------------

def test_opportunity_score_caso_completo_tipo_nssc_da_alto():
    """RVOL fuerte, gap fuerte, evento mañana, catalyst_score/technical
    altos -- debe dar un score de oportunidad alto (caso "NSSC")."""
    score = cs.catalyst_opportunity_score(
        catalyst_score=85.0, technical_confirmation=80.0, catalyst_type="EARNINGS",
        relative_volume=2.8, gap_pct=3.4, dias_al_evento=1, mrna_similarity=70.0,
        retroceso_desde_maximo_pct=5.0,
    )
    assert score >= 70.0


def test_opportunity_score_degradacion_parcial_nunca_da_none():
    """Falta retroceso_desde_maximo_pct (ticker sin detección técnica hoy)
    -- debe seguir devolviendo un score numérico renormalizado, nunca
    lanzar ni devolver None."""
    score = cs.catalyst_opportunity_score(
        catalyst_score=70.0, technical_confirmation=0.0, catalyst_type="EARNINGS",
        relative_volume=None, gap_pct=None, dias_al_evento=None, mrna_similarity=None,
        retroceso_desde_maximo_pct=None,
    )
    assert isinstance(score, float)
    assert 0.0 <= score <= 100.0


def test_opportunity_score_sin_ningun_dato_de_mercado_sigue_calculable():
    """Caso extremo: solo catalyst_score/technical_confirmation
    disponibles (piso mínimo que SIEMPRE existe) -- debe dar un score
    bajo-pero-real, nunca None ni excepción."""
    score = cs.catalyst_opportunity_score(
        catalyst_score=40.0, technical_confirmation=0.0, catalyst_type="OTHER_MATERIAL",
    )
    assert score == 40.0 * cs.W_OPP_CATALYST_SCORE / (cs.W_OPP_CATALYST_SCORE + cs.W_OPP_TECHNICAL_CONFIRMATION)


def test_opportunity_score_extension_alta_penaliza():
    base_kwargs = dict(catalyst_score=80.0, technical_confirmation=80.0, catalyst_type="EARNINGS")
    score_bajo_retroceso = cs.catalyst_opportunity_score(**base_kwargs, retroceso_desde_maximo_pct=3.0)
    score_alto_retroceso = cs.catalyst_opportunity_score(**base_kwargs, retroceso_desde_maximo_pct=30.0)
    assert score_alto_retroceso < score_bajo_retroceso


def test_opportunity_score_financing_dilution_penalizado():
    score_normal = cs.catalyst_opportunity_score(
        catalyst_score=80.0, technical_confirmation=80.0, catalyst_type="EARNINGS",
    )
    score_dilucion = cs.catalyst_opportunity_score(
        catalyst_score=80.0, technical_confirmation=80.0, catalyst_type="FINANCING_DILUTION",
    )
    assert score_dilucion < score_normal


def test_opportunity_score_nunca_sale_del_rango_0_100():
    score = cs.catalyst_opportunity_score(
        catalyst_score=100.0, technical_confirmation=100.0, catalyst_type="FINANCING_DILUTION",
        relative_volume=999.0, gap_pct=999.0, dias_al_evento=-50, mrna_similarity=100.0,
        retroceso_desde_maximo_pct=999.0,
    )
    assert 0.0 <= score <= 100.0


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
            p += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
            traceback.print_exc()
            f += 1
    print(f"--- {p} passed, {f} failed ---")
