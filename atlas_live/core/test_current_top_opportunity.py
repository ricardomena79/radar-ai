"""Tests de `current_top_opportunity.py` (2026-08-26, FASE 1/5). Puros --
sin red, sin DB, sin fakes de infraestructura."""

import random

from atlas_live.core import current_top_opportunity as ctop

_C = ctop.CandidateForSelection


# --- A: mismo input -> mismo Top-1 -------------------------------------------

def test_A_mismo_input_mismo_top1():
    candidatos = [
        _C(ticker="NSSC", decision="OPORTUNIDAD_PRIORITARIA", ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=80.0, momentum_score=70.0),
        _C(ticker="ANF", decision="VIGILAR", ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=95.0, momentum_score=90.0),
    ]
    r1 = ctop.select_current_top_opportunity(candidatos)
    r2 = ctop.select_current_top_opportunity(candidatos)
    assert r1.ticker == r2.ticker == "NSSC"  # decision manda, aunque ANF tenga scores mas altos


# --- B: cambiar el orden de la lista de entrada -> mismo Top-1 --------------

def test_B_orden_de_entrada_no_cambia_el_ganador():
    candidatos = [
        _C(ticker="NSSC", decision="OPORTUNIDAD_PRIORITARIA", ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=80.0),
        _C(ticker="ANF", decision="VIGILAR", ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=95.0),
        _C(ticker="COHR", decision="PREPARACION", ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=99.0),
    ]
    invertidos = list(reversed(candidatos))
    r1 = ctop.select_current_top_opportunity(candidatos)
    r2 = ctop.select_current_top_opportunity(invertidos)
    assert r1.ticker == r2.ticker == "NSSC"


# --- C: distinto orden de llegada de red simulado -> mismo Top-1 -----------

def test_C_orden_de_llegada_de_red_simulado_no_afecta():
    base = [
        _C(ticker="NSSC", decision="OPORTUNIDAD_PRIORITARIA", ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=80.0),
        _C(ticker="ANF", decision="OPORTUNIDAD_PRIORITARIA", ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=75.0),
        _C(ticker="COHR", decision="VIGILAR", ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=99.0),
        _C(ticker="CRWD", decision="PREPARACION", ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=50.0),
    ]
    rng = random.Random(42)
    resultados = set()
    for _ in range(20):
        permutado = base[:]
        rng.shuffle(permutado)  # simula distinto orden de as_completed() cada corrida
        resultados.add(ctop.select_current_top_opportunity(permutado).ticker)
    assert resultados == {"NSSC"}  # SIEMPRE el mismo ganador, sin importar el orden de "llegada"


# --- D: ranking_score empatado -> gana atlas_score ---------------------------

def test_D_ranking_score_empatado_gana_atlas_score():
    candidatos = [
        _C(ticker="AAA", decision="VIGILAR", ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=60.0, momentum_score=90.0),
        _C(ticker="BBB", decision="VIGILAR", ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=85.0, momentum_score=10.0),
    ]
    r = ctop.select_current_top_opportunity(candidatos)
    assert r.ticker == "BBB"
    assert r.criterio_decisivo == "atlas_score"
    assert r.score_final == 85.0


# --- E: atlas_score empatado -> gana momentum_score --------------------------

def test_E_atlas_score_empatado_gana_momentum_score():
    candidatos = [
        _C(ticker="AAA", decision="VIGILAR", ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=70.0, momentum_score=40.0),
        _C(ticker="BBB", decision="VIGILAR", ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=70.0, momentum_score=88.0),
    ]
    r = ctop.select_current_top_opportunity(candidatos)
    assert r.ticker == "BBB"
    assert r.criterio_decisivo == "momentum_score"
    assert r.score_final == 88.0


# --- F: todo empatado -> gana ticker alfabetico ------------------------------

def test_F_todo_empatado_gana_ticker_alfabetico():
    candidatos = [
        _C(ticker="ZZZZ", decision="VIGILAR", ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=50.0, momentum_score=50.0),
        _C(ticker="AAAA", decision="VIGILAR", ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=50.0, momentum_score=50.0),
        _C(ticker="MMMM", decision="VIGILAR", ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=50.0, momentum_score=50.0),
    ]
    r = ctop.select_current_top_opportunity(candidatos)
    assert r.ticker == "AAAA"
    assert r.criterio_decisivo == "alfabetico"
    assert r.score_final is None


# --- G: OPORTUNIDAD_PRIORITARIA supera VIGILAR con score tecnico mayor -----

def test_G_decision_gana_sobre_score_tecnico():
    candidatos = [
        _C(ticker="LOW_SCORE_PRIORITARIA", decision="OPORTUNIDAD_PRIORITARIA", ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=10.0, momentum_score=10.0),
        _C(ticker="HIGH_SCORE_VIGILAR", decision="VIGILAR", ranking_score=(50.0, 3, 0.9, 90.0), atlas_score=99.0, momentum_score=99.0),
    ]
    r = ctop.select_current_top_opportunity(candidatos)
    assert r.ticker == "LOW_SCORE_PRIORITARIA"
    assert r.criterio_decisivo == "atlas_decision"


# --- H: NO_TOCAR nunca gana si existe otra candidata valida -----------------

def test_H_no_tocar_nunca_gana_si_hay_alternativa():
    candidatos = [
        _C(ticker="NO_TOCAR_ALTO_SCORE", decision="NO_TOCAR", ranking_score=(50.0, 5, 1.0, 100.0), atlas_score=100.0, momentum_score=100.0),
        _C(ticker="PREPARACION_BAJO_SCORE", decision="PREPARACION", ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=1.0, momentum_score=1.0),
    ]
    r = ctop.select_current_top_opportunity(candidatos)
    assert r.ticker == "PREPARACION_BAJO_SCORE"

    # Si NO_TOCAR es la UNICA candidata, gana trivialmente -- no hay alternativa.
    solo_no_tocar = [_C(ticker="SOLO", decision="NO_TOCAR", ranking_score=(-1.0, 0, 0.0, 0.0))]
    r2 = ctop.select_current_top_opportunity(solo_no_tocar)
    assert r2.ticker == "SOLO"


# --- I/J/K: sin red, sin DB, sin estado global mutable -----------------------

def test_I_J_K_sin_red_sin_db_sin_estado_global():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(ctop))
    modulos = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modulos.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modulos.add(node.module)

    prohibidos_red_db = ("requests", "urllib", "http.client", "sqlite3", "yfinance", "socket")
    for m in modulos:
        for p in prohibidos_red_db:
            assert p not in m, f"import prohibido: {m}"

    # Solo imports de stdlib puro (dataclasses/typing) -- confirma
    # aislamiento total de proveedores/DB.
    assert modulos <= {"dataclasses", "typing", "__future__"}

    # Sin `global`/estado mutable a nivel de módulo fuera de las constantes
    # inmutables (CORE_METHODOLOGY_VERSION, _DECISION_PRIORITY, ambas
    # nunca reasignadas en tiempo de ejecución).
    src = inspect.getsource(ctop)
    assert "global " not in src


# --- L: metodologia versionada -----------------------------------------------

def test_L_metodologia_versionada():
    candidatos = [_C(ticker="X", decision="VIGILAR", ranking_score=(-1.0, 0, 0.0, 0.0))]
    r = ctop.select_current_top_opportunity(candidatos)
    assert r.methodology_version == ctop.CORE_METHODOLOGY_VERSION

    r2 = ctop.select_current_top_opportunity(candidatos, methodology_version="v2_experimental")
    assert r2.methodology_version == "v2_experimental"


# --- M: snapshot completo de los componentes usados --------------------------

def test_M_snapshot_completo_de_componentes():
    ganador = _C(ticker="WIN", decision="OPORTUNIDAD_PRIORITARIA", ranking_score=(1.0, 2, 0.5, 60.0), atlas_score=77.0, momentum_score=66.0)
    otro = _C(ticker="OTRO", decision="VIGILAR", ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=10.0, momentum_score=10.0)
    r = ctop.select_current_top_opportunity([ganador, otro])

    assert r.componentes_utilizados == {
        "decision": "OPORTUNIDAD_PRIORITARIA",
        "ranking_score": (1.0, 2, 0.5, 60.0),
        "atlas_score": 77.0,
        "momentum_score": 66.0,
    }
    assert len(r.ranking_completo) == 2
    assert r.ranking_completo[0].ticker == "WIN" and r.ranking_completo[0].posicion == 1
    assert r.ranking_completo[1].ticker == "OTRO" and r.ranking_completo[1].posicion == 2
    assert r.candidatos_considerados == 2
    assert r.runner_up_ticker == "OTRO"


# --- extra: sin candidatos -> None, nunca un ganador inventado -------------

def test_sin_candidatos_devuelve_none():
    assert ctop.select_current_top_opportunity([]) is None


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
