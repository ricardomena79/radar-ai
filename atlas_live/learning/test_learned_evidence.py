"""Tests de `learned_evidence.py` (2026-08-25, Fase 4/5). DB temporal real
(misma que Fase 2). Sin red, sin mocks del cálculo -- solo se monkeypatchea
la ruta del archivo."""

import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.learning import learned_evidence as le
from atlas_live.learning import live_experience_knowledge as lek

_ORIG_DB_PATH = lek.DB_PATH


def _fresh():
    lek.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_le_{_uuid.uuid4().hex}.db"


def _restore():
    lek.DB_PATH = _ORIG_DB_PATH


def _knowledge_row(direction="ALCISTA", timing_deteccion="al_comienzo", bucket="poblacion_total",
                    n_evaluables=137, n_aciertos_20=56, pct_20=41.2,
                    wilson_lower=33.3, wilson_upper=49.8, baseline_pct_20=23.1, lift_20=1.78,
                    mediana=15.0, n50=10, pct50=7.3, n100=1, pct100=0.7,
                    validation_state="EN_VALIDACION", computed_as_of="2026-08-24",
                    computed_at="2026-08-24T20:00:00+00:00"):
    return {
        "direction": direction, "timing_deteccion": timing_deteccion, "bucket": bucket,
        "n_evaluables": n_evaluables, "n_aciertos_20": n_aciertos_20, "pct_20": pct_20,
        "wilson_lower_bound_20_pct": wilson_lower, "wilson_upper_bound_20_pct": wilson_upper,
        "baseline_pct_20": baseline_pct_20, "lift_20": lift_20,
        "mediana_max_advance_pct": mediana,
        "n_aciertos_50": n50, "pct_50": pct50, "n_aciertos_100": n100, "pct_100": pct100,
        "validation_state": validation_state, "computed_as_of": computed_as_of, "computed_at": computed_at,
    }


# --- A/B: match correcto / sin conocimiento ---------------------------------

def test_A_candidata_encuentra_su_conocimiento_historico():
    _fresh()
    try:
        lek.record_experience_knowledge([_knowledge_row()])
        r = le.get_learned_evidence("ALCISTA", "al_comienzo", "2026-08-25")
        assert r["available"] is True
        assert r["sample_size"] == 137
        assert r["historical_success_pct_20"] == 41.2
        assert r["baseline_pct_20"] == 23.1
        assert r["lift_20"] == 1.78
        assert r["wilson_lower_bound_20_pct"] == 33.3
        assert r["wilson_upper_bound_20_pct"] == 49.8
        assert r["computed_as_of"] == "2026-08-24"
        assert r["methodology_version"] == lek.METHODOLOGY_VERSION
    finally:
        _restore()


def test_B_candidata_sin_conocimiento_recibe_available_false():
    _fresh()
    try:
        lek.record_experience_knowledge([_knowledge_row(direction="ALCISTA", timing_deteccion="al_comienzo")])
        r = le.get_learned_evidence("BAJISTA", "agotamiento", "2026-08-25")  # condición sin match
        assert r["available"] is False
        assert r["reason"] == "SIN_CONOCIMIENTO_PARA_ESTA_CONDICION"
    finally:
        _restore()


# --- C/D/E: los 3 niveles quedan etiquetados tal cual -----------------------

def test_C_muestra_insuficiente_se_expone_tal_cual_nunca_se_oculta():
    _fresh()
    try:
        lek.record_experience_knowledge([_knowledge_row(validation_state="MUESTRA_INSUFICIENTE", n_evaluables=5)])
        r = le.get_learned_evidence("ALCISTA", "al_comienzo", "2026-08-25")
        assert r["available"] is True  # sigue disponible como INFORMACIÓN
        assert r["validation_state"] == "MUESTRA_INSUFICIENTE"
        assert r["sample_size"] == 5
    finally:
        _restore()


def test_D_en_validacion_queda_etiquetada_correctamente():
    _fresh()
    try:
        lek.record_experience_knowledge([_knowledge_row(validation_state="EN_VALIDACION", n_evaluables=137)])
        r = le.get_learned_evidence("ALCISTA", "al_comienzo", "2026-08-25")
        assert r["validation_state"] == "EN_VALIDACION"
    finally:
        _restore()


def test_E_validacion_robusta_queda_etiquetada_correctamente():
    _fresh()
    try:
        lek.record_experience_knowledge([_knowledge_row(validation_state="VALIDACION_ROBUSTA", n_evaluables=600)])
        r = le.get_learned_evidence("ALCISTA", "al_comienzo", "2026-08-25")
        assert r["validation_state"] == "VALIDACION_ROBUSTA"
        assert r["sample_size"] == 600
    finally:
        _restore()


# --- F/G: no look-ahead, incluyendo el caso intradía ------------------------

def test_F_nunca_se_utiliza_conocimiento_futuro():
    _fresh()
    try:
        lek.record_experience_knowledge([_knowledge_row(computed_as_of="2026-08-26")])  # futuro respecto a la consulta
        r = le.get_learned_evidence("ALCISTA", "al_comienzo", "2026-08-25")
        assert r["available"] is False
        assert r["reason"] == "SIN_CONOCIMIENTO_PARA_ESTA_CONDICION"
    finally:
        _restore()


def test_G_leakage_intradia_conocimiento_del_mismo_dia_se_excluye():
    """Caso explícito pedido: conocimiento con computed_as_of IGUAL al
    market_date de la candidata (ej. generado por un recálculo manual a
    media sesión) NO debe usarse ese mismo día -- filtro más estricto que
    Fase 2 (`<`, no `<=`). Recién disponible desde el día siguiente."""
    _fresh()
    try:
        lek.record_experience_knowledge([_knowledge_row(computed_as_of="2026-08-25", computed_at="2026-08-25T10:00:00+00:00")])
        # Misma fecha -- debe quedar excluido (a diferencia de Fase 2, que
        # con `<=` SÍ lo hubiera aceptado en el borde).
        r_mismo_dia = le.get_learned_evidence("ALCISTA", "al_comienzo", "2026-08-25")
        assert r_mismo_dia["available"] is False

        # Al día siguiente, el MISMO conocimiento SÍ está disponible.
        r_dia_siguiente = le.get_learned_evidence("ALCISTA", "al_comienzo", "2026-08-26")
        assert r_dia_siguiente["available"] is True
        assert r_dia_siguiente["computed_as_of"] == "2026-08-25"
    finally:
        _restore()


# --- H: selecciona el cálculo más reciente -----------------------------------

def test_H_selecciona_el_calculo_mas_reciente_entre_varios():
    _fresh()
    try:
        lek.record_experience_knowledge([_knowledge_row(
            pct_20=30.0, computed_as_of="2026-08-20", computed_at="2026-08-20T20:00:00+00:00",
        )])
        lek.record_experience_knowledge([_knowledge_row(
            pct_20=55.0, computed_as_of="2026-08-24", computed_at="2026-08-24T20:00:00+00:00",
        )])
        r = le.get_learned_evidence("ALCISTA", "al_comienzo", "2026-08-25")
        assert r["historical_success_pct_20"] == 55.0  # el más reciente, no el primero
        assert r["computed_as_of"] == "2026-08-24"
    finally:
        _restore()


# --- I: no modifica decision/confidence/score/priority/ranking/gates -------

def test_I_no_importa_ningun_modulo_de_decision():
    import inspect

    from atlas.engine import decision_engine
    from atlas_live.catalyst import catalyst_score
    from atlas_live.radar import candidate_gates, candidate_tracker, priority_classifier

    prohibidos = ("learned_evidence", "get_learned_evidence")
    for modulo in (candidate_gates, candidate_tracker, priority_classifier, decision_engine, catalyst_score):
        src = inspect.getsource(modulo)
        for p in prohibidos:
            assert p not in src, f"{modulo.__name__} no debe referenciar {p} todavía (Fase 4/5, no conectado)"


# --- J: sin volatility válida, nunca un bucket inventado --------------------

def test_J_sin_volatility_no_inventa_bucket():
    _fresh()
    try:
        lek.record_experience_knowledge([_knowledge_row(bucket="poblacion_total")])
        r_sin_vol = le.get_learned_evidence("ALCISTA", "al_comienzo", "2026-08-25", volatility_14d_pct=None)
        r_con_vol = le.get_learned_evidence("ALCISTA", "al_comienzo", "2026-08-25", volatility_14d_pct=7.3)
        # Con o sin volatilidad, el resultado es IDÉNTICO -- siempre
        # consulta "poblacion_total", nunca inventa alto/medio/bajo sin
        # los cortes reales.
        assert r_sin_vol == r_con_vol
    finally:
        _restore()


# --- K: errores de la capa de conocimiento no tumban el radar --------------

def test_K_error_de_conocimiento_no_propaga_excepcion():
    _fresh()
    try:
        lek.DB_PATH = Path("Z:\\ruta\\inexistente\\jamas\\conocimiento.db")
        r = le.get_learned_evidence("ALCISTA", "al_comienzo", "2026-08-25")  # NO debe lanzar
        assert r["available"] is False
        assert "ERROR_CONSULTA" in r["reason"]
    finally:
        _restore()


# --- N/O: sin gate nueva, sin import desde candidate_gates.py --------------

def test_N_no_se_introduce_ninguna_gate_nueva():
    from atlas_live.radar import candidate_gates as gates

    assert len(gates.ALL_GATES) == 7


def test_O_learned_evidence_no_se_importa_desde_candidate_gates():
    import ast
    import inspect

    from atlas_live.radar import candidate_gates as gates

    tree = ast.parse(inspect.getsource(gates))
    modulos_importados = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modulos_importados.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modulos_importados.add(node.module)
            modulos_importados.update(alias.name for alias in node.names)
    assert not any("learned_evidence" in m for m in modulos_importados)


# --- P: el conocimiento sigue siendo append-only ----------------------------

def test_P_get_learned_evidence_nunca_escribe():
    """Chequeo estructural: el módulo no llama a ninguna función de
    escritura (`record_experience_knowledge`, `INSERT`, `UPDATE`, `DELETE`)
    -- es estrictamente de solo lectura."""
    import inspect

    src = inspect.getsource(le)
    for prohibido in ("record_experience_knowledge", "INSERT INTO", "UPDATE ", "DELETE FROM"):
        assert prohibido not in src


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
