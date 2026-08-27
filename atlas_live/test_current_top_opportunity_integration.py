"""Tests de integración de CURRENT TOP OPPORTUNITY con el ciclo real
(2026-08-26, Fase 3/5). Ejercitan `scan_worker._update_current_top_opportunity()`
-- la función que `_run_scan_once_locked()` llama en cada ciclo real -- con
`ranked`/`results` sintéticos (mismo estilo que ya usa `test_scan_stability.py`),
sin simular un ciclo de red completo. Sin red, DB temporal real."""

import random
import tempfile
import uuid as _uuid
from pathlib import Path
from types import SimpleNamespace

import atlas_live.scan_worker as sw
from atlas_live.core import current_top_opportunity_registry as ctop_reg
from atlas_live.memory import market_hours

_ORIG_DB_PATH = ctop_reg.DB_PATH


def _fresh():
    ctop_reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_ctop_integ_{_uuid.uuid4().hex}.db"


def _restore():
    ctop_reg.DB_PATH = _ORIG_DB_PATH


def _ranked(ticker, decision, ranking_score=(-1.0, 0, 0.0, 0.0)):
    """`SimpleNamespace` con exactamente los atributos que
    `_update_current_top_opportunity()` lee de un `RankedCandidate` real
    (`symbol`, `ranking_score`, `atlas_decision`) -- duck typing, sin
    acoplarse al dataclass completo de `demo_ranking.py`."""
    return SimpleNamespace(symbol=ticker, ranking_score=ranking_score, atlas_decision={"decision": decision})


def _result(ticker, atlas_score=50.0, momentum_score=50.0):
    return {"symbol": ticker, "atlas_score": atlas_score, "momentum_score": momentum_score}


def _hoy():
    return market_hours.market_date()


# --- A: ciclo real con una candidata -> queda registrada -------------------

def test_A_ciclo_real_con_una_candidata_queda_registrada():
    _fresh()
    try:
        ranked = [_ranked("NSSC", "OPORTUNIDAD_PRIORITARIA")]
        results = [_result("NSSC")]
        serializado = sw._update_current_top_opportunity(results, ranked)

        assert serializado["ticker"] == "NSSC"
        secuencia = ctop_reg.get_top_opportunity_sequence(_hoy())
        assert len(secuencia) == 1
        assert secuencia[0]["ticker"] == "NSSC"
    finally:
        _restore()


# --- B: mismo Top-1 durante multiples ciclos -> una sola fila abierta -----

def test_B_mismo_top1_multiples_ciclos_una_sola_fila():
    _fresh()
    try:
        ranked = [_ranked("NSSC", "OPORTUNIDAD_PRIORITARIA")]
        results = [_result("NSSC")]
        for _ in range(8):
            sw._update_current_top_opportunity(results, ranked)

        secuencia = ctop_reg.get_top_opportunity_sequence(_hoy())
        assert len(secuencia) == 1
        assert secuencia[0]["deselected_at"] is None
    finally:
        _restore()


# --- C/D: A -> B -> A -- cierre/reapertura con nueva secuencia -------------

def test_C_D_secuencia_A_B_A():
    _fresh()
    try:
        A = [_ranked("NSSC", "OPORTUNIDAD_PRIORITARIA")]
        B = [_ranked("COHR", "OPORTUNIDAD_PRIORITARIA", ranking_score=(5.0, 0, 0.0, 0.0))]  # gana por ranking_score
        results_A = [_result("NSSC")]
        results_B = [_result("COHR")]

        # Desde Fase 4/5 (capa de estabilidad), un cambio real exige 2
        # ciclos CONSECUTIVOS ganando -- cada transición se llama 2 veces.
        sw._update_current_top_opportunity(results_A, A)  # A
        sw._update_current_top_opportunity(results_B, B)  # A -> B (ciclo 1/2)
        sw._update_current_top_opportunity(results_B, B)  # A -> B (ciclo 2/2, confirma)
        sw._update_current_top_opportunity(results_A, A)  # B -> A (ciclo 1/2)
        sw._update_current_top_opportunity(results_A, A)  # B -> A (ciclo 2/2, confirma)

        secuencia = ctop_reg.get_top_opportunity_sequence(_hoy())
        assert [s["ticker"] for s in secuencia] == ["NSSC", "COHR", "NSSC"]
        assert [s["selection_sequence"] for s in secuencia] == [1, 2, 3]
        assert secuencia[0]["deselected_at"] is not None
        assert secuencia[1]["deselected_at"] is not None
        assert secuencia[2]["deselected_at"] is None
        assert secuencia[2]["previous_ticker"] == "COHR"
    finally:
        _restore()


# --- E: reinicio/reapertura de DB conserva historial ------------------------

def test_E_reinicio_conserva_historial():
    _fresh()
    try:
        sw._update_current_top_opportunity([_result("NSSC")], [_ranked("NSSC", "OPORTUNIDAD_PRIORITARIA")])
        # 2 ciclos consecutivos (Fase 4/5, capa de estabilidad) para confirmar COHR.
        for _ in range(2):
            sw._update_current_top_opportunity(
                [_result("COHR")], [_ranked("COHR", "OPORTUNIDAD_PRIORITARIA", ranking_score=(5.0, 0, 0.0, 0.0))],
            )
        # "Reinicio" -- ninguna conexión persiste entre llamadas de por sí
        # (cada _connect() abre una nueva); confirmamos leyendo con una
        # conexión fresca explícita.
        conn = ctop_reg._connect()
        conn.close()
        secuencia = ctop_reg.get_top_opportunity_sequence(_hoy())
        assert [s["ticker"] for s in secuencia] == ["NSSC", "COHR"]
    finally:
        _restore()


# --- F: Cabina y dashboard reciben el MISMO Top-1 canonico -----------------

def test_F_cabina_y_dashboard_reciben_el_mismo_top1():
    _fresh()
    orig_state = sw.STATE.current_top_opportunity
    try:
        ranked = [_ranked("NSSC", "OPORTUNIDAD_PRIORITARIA")]
        results = [_result("NSSC")]
        serializado = sw._update_current_top_opportunity(results, ranked)
        sw.STATE.update(current_top_opportunity=serializado)

        # /api/ranking (snapshot()) y /api/memory-ranking (memory_ranking_snapshot())
        # deben exponer EXACTAMENTE el mismo valor -- una sola fuente,
        # nunca dos objetos calculados por separado.
        desde_ranking = sw.STATE.snapshot()["current_top_opportunity"]
        desde_memory_ranking = sw.STATE.memory_ranking_snapshot()["current_top_opportunity"]
        assert desde_ranking == desde_memory_ranking == serializado
    finally:
        sw.STATE.update(current_top_opportunity=orig_state)
        _restore()


# --- G: orden de llegada de red aleatorio no cambia el Top-1 ---------------

def test_G_orden_de_llegada_aleatorio_no_cambia_el_top1():
    _fresh()
    try:
        base_ranked = [
            _ranked("NSSC", "OPORTUNIDAD_PRIORITARIA"),
            _ranked("ANF", "NO_TOCAR"),
            _ranked("COHR", "VIGILAR"),
            _ranked("CRWD", "VIGILAR"),
        ]
        base_results = [_result("NSSC", 44.7), _result("ANF", 89.8), _result("COHR", 72.3), _result("CRWD", 52.2)]
        results_by_symbol = {r["symbol"]: r for r in base_results}

        rng = random.Random(11)
        ganadores = set()
        for _ in range(15):
            ranked_mezclado = base_ranked[:]
            rng.shuffle(ranked_mezclado)
            results_mezclado = [results_by_symbol[c.symbol] for c in ranked_mezclado]
            rng.shuffle(results_mezclado)  # simula tambien results en otro orden
            serializado = sw._update_current_top_opportunity(results_mezclado, ranked_mezclado)
            ganadores.add(serializado["ticker"])

        assert ganadores == {"NSSC"}  # siempre el mismo, sin importar el orden simulado
        # Y en persistencia -- sigue habiendo UNA sola fila abierta (CASO B
        # en las 15 corridas, nunca se registro un cambio real).
        secuencia = ctop_reg.get_top_opportunity_sequence(_hoy())
        assert len(secuencia) == 1
    finally:
        _restore()


# --- H: ninguna decision legacy puede sobrescribir el Top-1 canonico ------

def test_H_decision_legacy_no_puede_sobrescribir_top1():
    _fresh()
    try:
        # decision_engine.is_top_pick/rank (legacy, huerfano) marcaria a
        # ANF como "rank=1" por (confidence, atlas_score) -- pero el
        # selector canonico debe ganar por atlas_decision, no por eso.
        results = [
            {"symbol": "ANF", "atlas_score": 99.0, "momentum_score": 99.0, "confidence": 99.0, "rank": 1, "is_top_pick": True},
            {"symbol": "NSSC", "atlas_score": 10.0, "momentum_score": 10.0, "confidence": 10.0, "rank": 2, "is_top_pick": False},
        ]
        ranked = [_ranked("ANF", "NO_TOCAR"), _ranked("NSSC", "OPORTUNIDAD_PRIORITARIA")]
        serializado = sw._update_current_top_opportunity(results, ranked)
        assert serializado["ticker"] == "NSSC"  # NO "ANF", pese a is_top_pick=True/rank=1
    finally:
        _restore()


# --- I: el registry nunca decide, solo registra (ya cubierto en Fase 2/5,
#        se reconfirma acá con el flujo real de integracion) --------------

def test_I_registry_no_decide_solo_registra():
    _fresh()
    try:
        # Con una unica candidata NO_TOCAR, el registry debe reflejar
        # EXACTAMENTE lo que el selector decidio (NO_TOCAR gana por ser la
        # unica) -- el registry no tiene ninguna regla propia para
        # "mejorar" o descartar esa decision.
        ranked = [_ranked("SOLO", "NO_TOCAR")]
        results = [_result("SOLO")]
        serializado = sw._update_current_top_opportunity(results, ranked)
        assert serializado["decision"] == "NO_TOCAR"
        fila = ctop_reg.get_top_opportunity_sequence(_hoy())[0]
        assert fila["ticker"] == "SOLO"
    finally:
        _restore()


# --- J: no existen dos selectores activos del Top-1 (verificacion estructural) -

def test_J_un_solo_call_site_de_register_top_opportunity():
    import subprocess

    # --untracked: los archivos nuevos de estas fases (top_opportunity_stability.py,
    # current_top_opportunity_registry.py) nunca se comittearon -- `git grep`
    # sin este flag los ignora por completo y el test daría un falso "0
    # call sites" en vez de encontrar el real.
    resultado = subprocess.run(
        ["git", "grep", "--untracked", "-n", "register_top_opportunity("],
        capture_output=True, text=True, cwd=".",
    )
    lineas = resultado.stdout.splitlines()

    # Desde Fase 4/5, el ÚNICO call site REAL (no comentario/docstring, no
    # test, no la propia definición) debe ser `top_opportunity_stability.py`
    # -- `scan_worker.py` ya no llama a `register_top_opportunity()`
    # directo, delega en `apply_stability()` (que es quien decide CUÁNDO
    # confirmar). Sigue habiendo un único punto de escritura real.
    def _es_llamada_real(linea: str) -> bool:
        try:
            codigo = linea.split(":", 2)[2]
        except IndexError:
            return False
        codigo_sin_espacios = codigo.strip()
        return codigo_sin_espacios.startswith("ctop_reg.register_top_opportunity(") or \
            codigo_sin_espacios.startswith("register_top_opportunity(")

    call_sites = [l for l in lineas if ".py:" in l and "test_" not in l and _es_llamada_real(l)]
    # apply_stability() llama a register_top_opportunity() en 2 ramas
    # propias (CASO A -- primera selección, y confirmación tras N ciclos
    # consecutivos) -- ambas dentro del MISMO módulo/función, no dos
    # selectores distintos. Lo que importa es que sea un único ARCHIVO.
    archivos = {l.split(":", 1)[0] for l in call_sites}
    assert archivos == {"atlas_live/core/top_opportunity_stability.py"}, \
        f"se esperaba un unico archivo productivo, se encontraron: {call_sites}"


# --- K/A: caso real CRM -- decision viva diverge de la congelada ----------
# (2026-08-27, autorizado explícitamente, auditoría post-cierre): mientras
# un ticker sigue "MANTENIDO" (CASO B, sin nueva escritura), `decision`
# debe reflejar la decisión Atlas VIVA de ESE ciclo, nunca la congelada en
# el momento en que se confirmó -- que queda disponible aparte en
# `decision_at_confirmation`, para auditoría, sin perderse.

def test_K_caso_crm_decision_viva_diverge_de_la_confirmada():
    _fresh()
    try:
        # Ciclo 1: CRM es la única candidata, confirmado con
        # OPORTUNIDAD_PRIORITARIA (CASO A -- primera selección).
        sw._update_current_top_opportunity([_result("CRM")], [_ranked("CRM", "OPORTUNIDAD_PRIORITARIA")])

        # Ciclo 2: CRM sigue en el ciclo, pero su decisión Atlas VIVA ya
        # cambió a NO_TOCAR (ej. paso a etapa NO_PERSEGUIR); aparece TEAM
        # como nueva candidata OPORTUNIDAD_PRIORITARIA -- gana la selección
        # CRUDA de este ciclo, pero la estabilidad (2 ciclos, sin tocar)
        # todavía no la confirma con un solo ciclo -- CRM sigue siendo el
        # ticker CONFIRMADO (CASO C, "ACUMULANDO_CONFIRMACION").
        ranked_2 = [_ranked("CRM", "NO_TOCAR"), _ranked("TEAM", "OPORTUNIDAD_PRIORITARIA", ranking_score=(5.0, 0, 0.0, 0.0))]
        results_2 = [_result("CRM"), _result("TEAM")]
        serializado = sw._update_current_top_opportunity(results_2, ranked_2)

        assert serializado["ticker"] == "CRM"  # sigue siendo el confirmado -- la estabilidad no lo reemplazó
        assert serializado["stability_action"] == "MANTENIDO"
        assert serializado["pending_ticker"] == "TEAM"  # acumulando confirmación, sin reemplazar todavía
        assert serializado["decision"] == "NO_TOCAR"  # recomendabilidad VIVA -- ya no se presenta como recomendación
        assert serializado["decision_at_confirmation"] == "OPORTUNIDAD_PRIORITARIA"  # historial intacto
    finally:
        _restore()


# --- L/B: caso normal -- decision viva == decision historica --------------

def test_L_caso_normal_decision_viva_igual_a_la_confirmada():
    _fresh()
    try:
        ranked = [_ranked("NSSC", "OPORTUNIDAD_PRIORITARIA")]
        results = [_result("NSSC")]
        # Varios ciclos idénticos -- CASO B, ninguna divergencia real.
        for _ in range(3):
            serializado = sw._update_current_top_opportunity(results, ranked)

        assert serializado["decision"] == "OPORTUNIDAD_PRIORITARIA"
        assert serializado["decision_at_confirmation"] == "OPORTUNIDAD_PRIORITARIA"
        assert serializado["decision"] == serializado["decision_at_confirmation"]
    finally:
        _restore()


# --- M/C: ticker confirmado desaparece del ciclo actual --------------------

def test_M_ticker_confirmado_desaparece_del_ciclo_actual():
    _fresh()
    try:
        # Ciclo 1: NSSC confirmado con OPORTUNIDAD_PRIORITARIA.
        sw._update_current_top_opportunity([_result("NSSC")], [_ranked("NSSC", "OPORTUNIDAD_PRIORITARIA")])

        # Ciclo 2: NSSC ni siquiera aparece entre las candidatas de este
        # ciclo (ej. dejó de cumplir alguna puerta de detección) -- otra
        # candidata (XYZ) gana la selección cruda, pero la estabilidad (sin
        # tocar) todavía no la confirma con un solo ciclo -- NSSC sigue
        # siendo el ticker CONFIRMADO.
        ranked_2 = [_ranked("XYZ", "VIGILAR", ranking_score=(5.0, 0, 0.0, 0.0))]
        results_2 = [_result("XYZ")]
        serializado = sw._update_current_top_opportunity(results_2, ranked_2)

        assert serializado["ticker"] == "NSSC"  # sigue confirmado
        assert serializado["decision"] == "NO_TOCAR"  # fallback conservador -- no se puede verificar, no se recomienda
        assert serializado["decision_at_confirmation"] == "OPORTUNIDAD_PRIORITARIA"  # historial intacto
    finally:
        _restore()


# --- N/D: la fila persistida (score_components) no cambia ------------------

def test_N_persistencia_score_components_no_cambia():
    _fresh()
    try:
        sw._update_current_top_opportunity([_result("CRM")], [_ranked("CRM", "OPORTUNIDAD_PRIORITARIA")])
        fila_antes = ctop_reg.get_open_top_opportunity(_hoy())
        componentes_antes = dict(fila_antes["score_components"])

        # Mismo escenario de staleness que test_K -- CRM sigue confirmado,
        # su decisión viva ya es NO_TOCAR.
        ranked_2 = [_ranked("CRM", "NO_TOCAR"), _ranked("TEAM", "OPORTUNIDAD_PRIORITARIA", ranking_score=(5.0, 0, 0.0, 0.0))]
        results_2 = [_result("CRM"), _result("TEAM")]
        sw._update_current_top_opportunity(results_2, ranked_2)

        fila_despues = ctop_reg.get_open_top_opportunity(_hoy())
        # La fila sigue siendo la MISMA (CASO B/C -- sin reemplazo todavia,
        # `apply_stability`/el registry no se tocaron) y `score_components`
        # persistido conserva EXACTAMENTE el valor original -- el fix vive
        # solo en qué se sirve, nunca en qué se guarda.
        assert fila_despues["ticker"] == "CRM"
        assert dict(fila_despues["score_components"]) == componentes_antes
        assert fila_despues["score_components"]["decision"] == "OPORTUNIDAD_PRIORITARIA"
    finally:
        _restore()


# --- O/E: selector canonico y estabilidad sin modificaciones (estructural) -

def test_O_selector_y_estabilidad_sin_modificaciones():
    """Mismo patrón que test_J -- verificación estructural con `git diff`,
    no una suposición: el fix de staleness (2026-08-27) vive enteramente en
    `scan_worker.py`. Confirma que ningún archivo protegido (selector
    canónico, estabilidad, registry, Decision Core, priority_classifier,
    decision_engine, radar_worker) tiene un diff pendiente."""
    import subprocess

    protegidos = [
        "atlas_live/core/current_top_opportunity.py",
        "atlas_live/core/top_opportunity_stability.py",
        "atlas_live/core/current_top_opportunity_registry.py",
        "atlas_live/core/atlas_decision_core.py",
        "atlas_live/radar/priority_classifier.py",
        "atlas/engine/decision_engine.py",
        "atlas_live/radar/radar_worker.py",
    ]
    resultado = subprocess.run(
        ["git", "diff", "--stat", "--"] + protegidos,
        capture_output=True, text=True, cwd=".",
    )
    assert resultado.stdout.strip() == "", f"archivos protegidos con diff pendiente: {resultado.stdout}"


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
