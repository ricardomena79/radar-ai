"""Tests de `live_experience_pipeline.py` (2026-08-25, Fase 3/5) --
EXPERIENCIA → CONOCIMIENTO real, de punta a punta, con DB temporales reales
(radar_candidates.db-equivalente + live_experience_knowledge.db-equivalente).
Sin red, sin mocks del cálculo estadístico -- solo se monkeypatchean rutas
de archivo."""

import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.learning import live_experience_knowledge as lek
from atlas_live.learning import live_experience_pipeline as lep
from atlas_live.learning import live_experience_scoring as les
from atlas_live.radar import candidate_registry as reg

_ORIG_REG_DB_PATH = reg.DB_PATH
_ORIG_LEK_DB_PATH = lek.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_pipeline_reg_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None
    les.DB_PATH = reg.DB_PATH
    lek.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_pipeline_lek_{_uuid.uuid4().hex}.db"


def _restore():
    reg.DB_PATH = _ORIG_REG_DB_PATH
    les.DB_PATH = _ORIG_REG_DB_PATH
    lek.DB_PATH = _ORIG_LEK_DB_PATH


def _seed(ticker, market_date, direction, timing, volatility_14d_pct, max_advance_pct,
          confiable=True, is_final=True):
    reg.record_detection(
        ticker, market_date, "regular", f"{market_date}T14:00:00Z", "s1",
        10.0, 5.0, 10000, 5000, 2.0, 100_000.0, [{"name": "cambio_de_precio", "reason": "x", "value": 5.0}],
    )
    reg.set_phase_tag(ticker, market_date, timing, direction_at_detection=direction)
    reg.set_experimental_signals(ticker, market_date, volatility_14d_pct=volatility_14d_pct)
    reg.record_outcome(
        ticker, market_date, 0.0, 10.0 * (1 + max_advance_pct / 100), max_advance_pct, 30.0,
        max_advance_pct >= 20, max_advance_pct >= 50, max_advance_pct >= 100, "EXPLOSION",
        confiable_para_aprendizaje=confiable, is_final=is_final,
    )


# --- A: experiencias reales -> conocimiento persistido ----------------------

def test_A_experiencias_reales_producen_conocimiento_persistido():
    _fresh()
    try:
        for i in range(5):
            _seed(f"AAA{i}", "2026-08-20", "ALCISTA", "al_comienzo", 5.0 + i, 30.0)
        resumen = lep.run_experience_learning_cycle("2026-08-24")
        assert resumen["ok"] is True
        assert resumen["n_experiencias"] == 5
        assert resumen["n_insertadas"] > 0
        persistido = lek.get_knowledge_for("2026-08-25")
        assert len(persistido) == resumen["n_insertadas"]
    finally:
        _restore()


# --- B: solo outcomes finales y confiables participan -----------------------

def test_B_solo_outcomes_finales_y_confiables_participan():
    _fresh()
    try:
        _seed("FINAL_OK", "2026-08-20", "ALCISTA", "al_comienzo", 5.0, 30.0, confiable=True, is_final=True)
        _seed("EN_CURSO", "2026-08-20", "ALCISTA", "al_comienzo", 5.0, 90.0, confiable=True, is_final=False)
        _seed("SOSPECHOSO", "2026-08-20", "ALCISTA", "al_comienzo", 5.0, 90.0, confiable=False, is_final=True)
        resumen = lep.run_experience_learning_cycle("2026-08-24")
        assert resumen["n_experiencias"] == 1  # solo FINAL_OK
    finally:
        _restore()


# --- C: no entra ninguna fecha futura ---------------------------------------

def test_C_no_entra_ninguna_fecha_futura():
    _fresh()
    try:
        _seed("PASADO", "2026-08-20", "ALCISTA", "al_comienzo", 5.0, 30.0)
        _seed("FUTURO", "2026-08-25", "ALCISTA", "al_comienzo", 5.0, 90.0)
        resumen = lep.run_experience_learning_cycle("2026-08-24")
        assert resumen["n_experiencias"] == 1  # FUTURO (market_date >= as_of_date) excluida
    finally:
        _restore()


# --- D: ejecutar dos veces no destruye conocimiento anterior ----------------

def test_D_ejecutar_dos_veces_no_destruye_conocimiento_anterior():
    _fresh()
    try:
        for i in range(3):
            _seed(f"BBB{i}", "2026-08-15", "ALCISTA", "al_comienzo", 5.0, 30.0)
        resumen1 = lep.run_experience_learning_cycle("2026-08-20")
        assert resumen1["ok"] is True and resumen1["n_insertadas"] > 0

        for i in range(3):
            _seed(f"CCC{i}", "2026-08-22", "BAJISTA", "agotamiento", 8.0, 10.0)
        resumen2 = lep.run_experience_learning_cycle("2026-08-24")
        assert resumen2["ok"] is True and resumen2["n_insertadas"] > 0

        todo = lek.get_knowledge_for("2026-08-25")
        as_of_vistos = {r["computed_as_of"] for r in todo}
        assert as_of_vistos == {"2026-08-20", "2026-08-24"}  # ambas ejecuciones siguen ahí
    finally:
        _restore()


# --- F: muestra insuficiente queda registrada --------------------------------

def test_F_muestra_insuficiente_queda_registrada_correctamente():
    _fresh()
    try:
        for i in range(3):  # muy por debajo de VALIDACION_MUESTRA_INSUFICIENTE_MAX
            _seed(f"POCA{i}", "2026-08-20", "NEUTRAL", "indeterminado", 5.0, 10.0)
        resumen = lep.run_experience_learning_cycle("2026-08-24")
        assert resumen["ok"] is True
        persistido = lek.get_knowledge_for("2026-08-25", direction="NEUTRAL")
        assert len(persistido) >= 1
        assert all(r["validation_state"] == "MUESTRA_INSUFICIENTE" for r in persistido)
        assert resumen["n_grupos_robustos"] == 0
    finally:
        _restore()


# --- G: ejecutable para una fecha específica --------------------------------

def test_G_ejecutable_para_fecha_especifica():
    _fresh()
    try:
        _seed("XYZ", "2026-08-10", "ALCISTA", "al_comienzo", 5.0, 30.0)
        resumen_temprano = lep.run_experience_learning_cycle("2026-08-12")
        assert resumen_temprano["as_of_date"] == "2026-08-12"
        assert resumen_temprano["n_experiencias"] == 1

        resumen_muy_temprano = lep.run_experience_learning_cycle("2026-08-01")
        assert resumen_muy_temprano["n_experiencias"] == 0  # antes de que existiera la experiencia
    finally:
        _restore()


# --- H: computed_as_of/computed_at/methodology_version presentes -----------

def test_H_conocimiento_tiene_metadatos_completos():
    _fresh()
    try:
        for i in range(3):
            _seed(f"META{i}", "2026-08-20", "ALCISTA", "al_comienzo", 5.0, 30.0)
        lep.run_experience_learning_cycle("2026-08-24")
        persistido = lek.get_knowledge_for("2026-08-25")
        assert len(persistido) >= 1
        for fila in persistido:
            assert fila["computed_as_of"] == "2026-08-24"
            assert fila["computed_at"]  # no vacío
            assert fila["methodology_version"] == lek.METHODOLOGY_VERSION
    finally:
        _restore()


# --- I: no importa ni modifica ningún módulo protegido ----------------------

def test_I_no_importa_ningun_modulo_protegido():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(lep))
    modulos_importados = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modulos_importados.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modulos_importados.add(node.module)
            modulos_importados.update(alias.name for alias in node.names)

    for prohibido in (
        "candidate_gates", "priority_classifier", "decision_engine",
        "candidate_tracker", "catalyst_score",
    ):
        assert not any(prohibido in m for m in modulos_importados), (
            f"live_experience_pipeline.py no debe importar nada relacionado con {prohibido}"
        )


# --- J: el conocimiento NO es leído por ningún motor de decisión -----------

def test_J_ningun_motor_de_decision_lee_el_conocimiento():
    """Chequea, sobre el código REAL de los 5 módulos de decisión, que
    ninguno referencia el conocimiento nuevo (`live_experience_knowledge`/
    `live_experience_pipeline`/`get_knowledge_for`/`latest_knowledge_as_of`)
    -- guarda de regresión: si algún día alguien conecta esto sin
    autorización, este test lo detecta."""
    import inspect

    from atlas.engine import decision_engine
    from atlas_live.catalyst import catalyst_score
    from atlas_live.radar import candidate_gates, candidate_tracker, priority_classifier

    prohibidos = ("live_experience_knowledge", "live_experience_pipeline", "get_knowledge_for", "latest_knowledge_as_of")
    for modulo in (candidate_gates, candidate_tracker, priority_classifier, decision_engine, catalyst_score):
        src = inspect.getsource(modulo)
        for p in prohibidos:
            assert p not in src, f"{modulo.__name__} no debe referenciar {p} todavía (Fase 3/5, no conectado)"


# --- K: sin experiencias válidas, termina limpiamente -----------------------

def test_K_sin_experiencias_termina_limpiamente():
    _fresh()
    try:
        reg.get_meta()  # fuerza la creación del esquema en la DB temporal, sin sembrar ninguna fila
        resumen = lep.run_experience_learning_cycle("2026-08-24")
        assert resumen["ok"] is True
        assert resumen["n_experiencias"] == 0
        assert resumen["n_insertadas"] == 0
        assert resumen["error"] is None
        assert lek.get_knowledge_for("2026-08-25") == []
    finally:
        _restore()


# --- L: persiste tras cerrar y reiniciar el proceso -------------------------

def test_L_conocimiento_disponible_tras_cerrar_y_reabrir():
    import sqlite3

    _fresh()
    try:
        for i in range(3):
            _seed(f"PERS{i}", "2026-08-20", "ALCISTA", "al_comienzo", 5.0, 30.0)
        resumen = lep.run_experience_learning_cycle("2026-08-24")
        assert resumen["n_insertadas"] > 0

        # Conexión CRUDA, nueva, directa sobre el archivo -- ninguna función
        # del módulo, simula un reinicio completo del proceso.
        conn = sqlite3.connect(lek.DB_PATH)
        try:
            n = conn.execute("SELECT COUNT(*) FROM live_experience_knowledge").fetchone()[0]
        finally:
            conn.close()
        assert n == resumen["n_insertadas"]
    finally:
        _restore()


# --- Falla aislada: una excepción interna no se propaga --------------------

def test_falla_interna_no_se_propaga_resultado_marca_error():
    """Simula una falla real (DB de conocimiento apuntando a una ruta
    inválida/no escribible) -- confirma que `run_experience_learning_cycle`
    la atrapa, nunca la relanza, y deja `ok=False` + `error` explícito."""
    _fresh()
    try:
        for i in range(3):
            _seed(f"FALLA{i}", "2026-08-20", "ALCISTA", "al_comienzo", 5.0, 30.0)
        # Ruta inválida a propósito -- directorio inexistente sin permisos de creación.
        lek.DB_PATH = Path("Z:\\ruta\\que\\no\\existe\\nunca\\conocimiento.db")
        resumen = lep.run_experience_learning_cycle("2026-08-24")  # NO debe lanzar
        assert resumen["ok"] is False
        assert resumen["error"] is not None
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
