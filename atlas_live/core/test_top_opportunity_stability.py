"""Tests de la capa de ESTABILIDAD de CURRENT TOP OPPORTUNITY (2026-08-27,
Fase 4/5). DB temporal real, sin red -- mismo patrón que el resto de la
sesión."""

import random
import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.core import current_top_opportunity as ctop
from atlas_live.core import current_top_opportunity_registry as ctop_reg
from atlas_live.core import top_opportunity_stability as stability

_ORIG_DB_PATH = ctop_reg.DB_PATH
_C = ctop.CandidateForSelection
MD = "2026-08-27"


def _fresh():
    ctop_reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_stability_{_uuid.uuid4().hex}.db"


def _restore():
    ctop_reg.DB_PATH = _ORIG_DB_PATH


def _cand(ticker, decision="VIGILAR", ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=50.0, momentum_score=50.0):
    return _C(ticker=ticker, decision=decision, ranking_score=ranking_score, atlas_score=atlas_score, momentum_score=momentum_score)


# --- A: mismo candidato 20 ciclos -> una sola seleccion ---------------------

def test_A_mismo_candidato_20_ciclos_una_sola_seleccion():
    _fresh()
    try:
        candidatos = [_cand("NSSC", "OPORTUNIDAD_PRIORITARIA")]
        for _ in range(20):
            stability.apply_stability(candidatos, MD)
        secuencia = ctop_reg.get_top_opportunity_sequence(MD)
        assert len(secuencia) == 1
        assert secuencia[0]["ticker"] == "NSSC"
    finally:
        _restore()


# --- B/D: fluctuacion de UN ciclo (ruido) -- NO cambia el confirmado -------

def test_B_D_fluctuacion_de_un_ciclo_no_cambia_confirmado():
    _fresh()
    try:
        nssc_gana = [_cand("NSSC", "OPORTUNIDAD_PRIORITARIA", atlas_score=80.0), _cand("COHR", "OPORTUNIDAD_PRIORITARIA", atlas_score=79.0)]
        cohr_gana_un_ciclo = [_cand("NSSC", "OPORTUNIDAD_PRIORITARIA", atlas_score=79.0), _cand("COHR", "OPORTUNIDAD_PRIORITARIA", atlas_score=80.0)]

        secuencia_eventos = [nssc_gana, nssc_gana, cohr_gana_un_ciclo, nssc_gana, nssc_gana]
        acciones = [stability.apply_stability(c, MD)["action"] for c in secuencia_eventos]

        # Nunca se confirma un cambio real -- NSSC sigue siendo el unico confirmado.
        secuencia = ctop_reg.get_top_opportunity_sequence(MD)
        assert len(secuencia) == 1
        assert secuencia[0]["ticker"] == "NSSC"
        assert secuencia[0]["deselected_at"] is None
        # El tercer ciclo (COHR gana momentaneamente) NO genera un CONFIRMADO.
        assert acciones[2] != "CONFIRMADO"
    finally:
        _restore()


# --- C/E: cambio grande y sostenido -> SI cambia, exactamente 2 intervalos -

def test_C_E_cambio_sostenido_confirma_reemplazo():
    _fresh()
    try:
        nssc_gana = [_cand("NSSC", "OPORTUNIDAD_PRIORITARIA", atlas_score=80.0), _cand("COHR", "OPORTUNIDAD_PRIORITARIA", atlas_score=1.0)]
        cohr_gana = [_cand("NSSC", "OPORTUNIDAD_PRIORITARIA", atlas_score=1.0), _cand("COHR", "OPORTUNIDAD_PRIORITARIA", atlas_score=99.0)]

        stability.apply_stability(nssc_gana, MD)  # 09:31 NSSC
        stability.apply_stability(nssc_gana, MD)  # 09:36 NSSC
        r1 = stability.apply_stability(cohr_gana, MD)  # 09:41 COHR (ciclo 1 de confirmacion)
        r2 = stability.apply_stability(cohr_gana, MD)  # 09:46 COHR (ciclo 2 -- CONFIRMA)
        stability.apply_stability(cohr_gana, MD)  # 09:51 COHR sigue

        assert r1["action"] == "MANTENIDO"  # todavia acumulando
        assert r2["action"] == "CONFIRMADO"
        assert r2["reason"] == stability.REASON_TOP1_REEMPLAZADO_POR_SUPERACION

        secuencia = ctop_reg.get_top_opportunity_sequence(MD)
        assert len(secuencia) == 2  # exactamente NSSC -> COHR, nunca de vuelta
        assert secuencia[0]["ticker"] == "NSSC" and secuencia[0]["deselected_at"] is not None
        assert secuencia[1]["ticker"] == "COHR" and secuencia[1]["deselected_at"] is None
    finally:
        _restore()


# --- F: cambio de score del mismo ticker NO reinicia selected_at ----------

def test_F_cambio_de_score_mismo_ticker_no_reinicia_selected_at():
    _fresh()
    try:
        # Se agrega un segundo candidato para que el criterio decisivo sea
        # "atlas_score" (con un score_final real) en vez de "atlas_decision"
        # trivial (unica candidata) -- necesario para poder comparar el
        # score congelado.
        # Mismo nivel de decision (VIGILAR) para que el criterio decisivo
        # caiga en atlas_score, no en atlas_decision.
        relleno = _cand("RELLENO", "VIGILAR", atlas_score=1.0)
        stability.apply_stability([_cand("NSSC", "VIGILAR", atlas_score=72.0), relleno], MD)
        primero = ctop_reg.get_open_top_opportunity(MD)

        # NSSC sigue ganando, pero con un score MUY distinto (72 -> 85) --
        # sigue siendo CASO B (mismo ticker), nunca debe tocar selected_at.
        stability.apply_stability([_cand("NSSC", "VIGILAR", atlas_score=85.0), relleno], MD)
        segundo = ctop_reg.get_open_top_opportunity(MD)

        assert primero["id"] == segundo["id"]
        assert primero["selected_at"] == segundo["selected_at"]
        assert primero["score"] == segundo["score"] == 72.0  # el score queda CONGELADO del momento de confirmacion
    finally:
        _restore()


# --- G: restart del proceso conserva Top-1 e historial, incluyendo pending -

def test_G_restart_conserva_top1_historial_y_pending():
    _fresh()
    try:
        nssc_gana = [_cand("NSSC", "OPORTUNIDAD_PRIORITARIA", atlas_score=80.0), _cand("COHR", "OPORTUNIDAD_PRIORITARIA", atlas_score=1.0)]
        cohr_gana = [_cand("NSSC", "OPORTUNIDAD_PRIORITARIA", atlas_score=1.0), _cand("COHR", "OPORTUNIDAD_PRIORITARIA", atlas_score=99.0)]

        stability.apply_stability(nssc_gana, MD)
        stability.apply_stability(cohr_gana, MD)  # 1er ciclo de COHR -- pending_streak=1, NO confirma todavia

        # "Reinicio" -- nueva conexion explicita.
        conn = ctop_reg._connect()
        conn.close()

        pendiente = ctop_reg.get_pending_state(MD)
        assert pendiente["pending_ticker"] == "COHR"
        assert pendiente["pending_streak"] == 1

        # El ciclo SIGUIENTE, ya "despues del reinicio", debe seguir contando
        # desde 1 (no reiniciar la racha a 0) -- confirma en este segundo ciclo.
        r = stability.apply_stability(cohr_gana, MD)
        assert r["action"] == "CONFIRMADO"
        assert ctop_reg.get_open_top_opportunity(MD)["ticker"] == "COHR"
    finally:
        _restore()


# --- H: desaparicion del Top-1 confirmado -----------------------------------

def test_H_desaparicion_del_top1():
    _fresh()
    try:
        stability.apply_stability([_cand("NSSC", "OPORTUNIDAD_PRIORITARIA")], MD)

        # NSSC ya NO aparece entre las candidatas de este ciclo.
        r = stability.apply_stability([_cand("COHR", "VIGILAR")], MD)
        assert r["reason"] == stability.REASON_TOP1_DESAPARECIO
        # NO se reemplaza de inmediato -- sigue confirmado NSSC.
        assert ctop_reg.get_open_top_opportunity(MD)["ticker"] == "NSSC"

        # Un segundo ciclo consecutivo con COHR como unico candidato SI confirma.
        r2 = stability.apply_stability([_cand("COHR", "VIGILAR")], MD)
        assert r2["action"] == "CONFIRMADO"
        assert r2["reason"] == stability.REASON_TOP1_DESAPARECIO
        assert ctop_reg.get_open_top_opportunity(MD)["ticker"] == "COHR"
    finally:
        _restore()


# --- I: empate -- el propio selector ya lo resuelve, la estabilidad no ----
#        necesita logica propia ------------------------------------------

def test_I_empate_resuelto_por_el_selector_sin_logica_propia():
    _fresh()
    try:
        empatados = [_cand("ZZZZ"), _cand("AAAA"), _cand("MMMM")]  # todo igual -- gana AAAA (alfabetico)
        r = stability.apply_stability(empatados, MD)
        assert r["confirmed_ticker"] == "AAAA"
    finally:
        _restore()


# --- J: orden de llegada de red aleatorio no cambia el resultado ----------

def test_J_orden_aleatorio_no_cambia_resultado():
    _fresh()
    try:
        base = [_cand("NSSC", "OPORTUNIDAD_PRIORITARIA", atlas_score=80.0), _cand("ANF", "NO_TOCAR", atlas_score=99.0)]
        rng = random.Random(3)
        for _ in range(20):
            mezclado = base[:]
            rng.shuffle(mezclado)
            stability.apply_stability(mezclado, MD)
        assert ctop_reg.get_open_top_opportunity(MD)["ticker"] == "NSSC"
        assert len(ctop_reg.get_top_opportunity_sequence(MD)) == 1
    finally:
        _restore()


# --- K: Cabina y dashboard reciben el mismo Top-1 confirmado ---------------

def test_K_misma_fuente_para_cabina_y_dashboard():
    _fresh()
    try:
        stability.apply_stability([_cand("NSSC", "OPORTUNIDAD_PRIORITARIA")], MD)
        confirmado = ctop_reg.get_open_top_opportunity(MD)
        # Ambas superficies (server.py) leen del MISMO STATE.current_top_opportunity,
        # que a su vez se arma leyendo este MISMO registro -- ver
        # scan_worker._update_current_top_opportunity(). Acá se confirma
        # que el registro es una unica fuente consultable.
        assert confirmado["ticker"] == "NSSC"
    finally:
        _restore()


# --- L: Plan B = runner_up del selector canonico ---------------------------

def test_L_runner_up_congelado_es_el_plan_b():
    _fresh()
    try:
        stability.apply_stability(
            [_cand("NSSC", "OPORTUNIDAD_PRIORITARIA", atlas_score=80.0), _cand("COHR", "VIGILAR", atlas_score=1.0)], MD,
        )
        confirmado = ctop_reg.get_open_top_opportunity(MD)
        assert confirmado["runner_up_ticker"] == "COHR"
    finally:
        _restore()


# --- M/O: ninguna otra funcion puede decidir el Top-1 -----------------------

def test_M_O_sin_segundo_selector_activo():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(stability))
    modulos = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modulos.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modulos.add(node.module)
            modulos.update(a.name for a in node.names)

    prohibidos = ("candidate_gates", "candidate_tracker", "decision_engine", "priority_classifier",
                  "explosive_engine", "historical_scoring", "catalyst_score")
    for m in modulos:
        for p in prohibidos:
            assert p not in m, f"import prohibido: {m}"
    # El unico criterio de orden que usa es el del selector de Fase 1/5.
    assert "current_top_opportunity" in modulos

    # La funcion nunca define su propia logica de comparacion de scores --
    # solo compara TICKERS/streaks, nunca reordena candidatos.
    src = inspect.getsource(stability.apply_stability)
    assert ".sort(" not in src
    assert "sorted(" not in src


# --- N: label ya no depende de explosive.score (verificacion de texto) ----

def test_N_label_ya_no_depende_de_explosive_score():
    app_js = Path(__file__).parent.parent / "static" / "app.js"
    src = app_js.read_text(encoding="utf-8")
    assert "Oportunidad más explosiva" not in src
    assert "Oportunidad Principal" in src
    assert "renderHero(eligibleExplosive[0] || null)" not in src
    assert "data.current_top_opportunity" in src


# --- prueba especial: ANF/COHR/CRWD/NSSC, 100 permutaciones ----------------

def test_prueba_especial_100_permutaciones_anf_cohr_crwd_nssc():
    _fresh()
    try:
        base = [
            _cand("NSSC", "OPORTUNIDAD_PRIORITARIA", atlas_score=44.7, momentum_score=45.4),
            _cand("ANF", "NO_TOCAR", atlas_score=89.8, momentum_score=80.6),
            _cand("COHR", "VIGILAR", atlas_score=72.3, momentum_score=68.0),
            _cand("CRWD", "VIGILAR", atlas_score=52.2, momentum_score=52.2),
        ]
        rng = random.Random(2026)
        confirmados_vistos = set()
        for _ in range(100):
            mezclado = base[:]
            rng.shuffle(mezclado)
            stability.apply_stability(mezclado, MD)
            confirmados_vistos.add(ctop_reg.get_open_top_opportunity(MD)["ticker"])

        assert confirmados_vistos == {"NSSC"}  # estable en las 100 permutaciones
        assert len(ctop_reg.get_top_opportunity_sequence(MD)) == 1  # una sola fila, nunca fluctuo
    finally:
        _restore()


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
