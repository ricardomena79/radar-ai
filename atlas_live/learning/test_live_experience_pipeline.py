"""Tests de `live_experience_pipeline.py` (2026-08-25, Fase 3/5) --
EXPERIENCIA → CONOCIMIENTO real, de punta a punta, con DB temporales reales
(radar_candidates.db-equivalente + live_experience_knowledge.db-equivalente).
Sin red, sin mocks del cálculo estadístico -- solo se monkeypatchean rutas
de archivo."""

import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.core import activation_registry as areg
from atlas_live.core import continuous_evaluation_registry as cer
from atlas_live.learning import live_experience_knowledge as lek
from atlas_live.learning import live_experience_pipeline as lep
from atlas_live.learning import live_experience_scoring as les
from atlas_live.radar import candidate_registry as reg

_ORIG_REG_DB_PATH = reg.DB_PATH
_ORIG_LEK_DB_PATH = lek.DB_PATH
_ORIG_CER_DB_PATH = cer.DB_PATH
_ORIG_AREG_DB_PATH = areg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_pipeline_reg_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None
    les.DB_PATH = reg.DB_PATH
    lek.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_pipeline_lek_{_uuid.uuid4().hex}.db"
    # Hito 3, Fase 3.6 (2026-09-03): desde que `run_experience_learning_cycle()`
    # dispara el hook event-driven de evaluación continua, CUALQUIER test
    # de este archivo que siembre experiencias walk-forward-válidas
    # también ejercita `continuous_evaluation_registry.py`/`activation_registry.py`
    # -- deben aislarse acá igual que las otras 2 DBs, o los tests
    # existentes escribirían en las rutas reales de desarrollo.
    cer.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_pipeline_cer_{_uuid.uuid4().hex}.db"
    areg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_pipeline_areg_{_uuid.uuid4().hex}.db"


def _restore():
    reg.DB_PATH = _ORIG_REG_DB_PATH
    les.DB_PATH = _ORIG_REG_DB_PATH
    lek.DB_PATH = _ORIG_LEK_DB_PATH
    cer.DB_PATH = _ORIG_CER_DB_PATH
    areg.DB_PATH = _ORIG_AREG_DB_PATH


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


# --- Hito 3, Fase 3.6 (2026-09-03) -- integración event-driven --------------

def test_M_ciclo_real_dispara_continuous_evaluation():
    _fresh()
    try:
        for i in range(5):
            _seed(f"CE{i}", "2026-08-20", "ALCISTA", "al_comienzo", 5.0 + i, 30.0)
        resumen = lep.run_experience_learning_cycle("2026-08-24")
        assert resumen["ok"] is True
        assert "continuous_evaluation" in resumen
        assert resumen["continuous_evaluation"]["ok"] is True
        assert resumen["continuous_evaluation"]["n_condiciones"] >= 1
    finally:
        _restore()


def test_N_sin_experiencias_no_intenta_continuous_evaluation():
    _fresh()
    try:
        reg.get_meta()
        resumen = lep.run_experience_learning_cycle("2026-08-24")
        assert resumen["ok"] is True
        assert resumen["n_experiencias"] == 0
        assert "continuous_evaluation" not in resumen  # tabla=[] -> el bloque no corre
    finally:
        _restore()


def test_O_fallo_de_continuous_evaluation_no_contamina_el_resultado_real_del_ciclo(monkeypatch):
    """El requisito más importante de la integración: un bug en 3.6 NUNCA
    puede hacer que el ciclo de experiencia real (Fase 2/3) se reporte
    como fallido."""
    _fresh()
    try:
        for i in range(5):
            _seed(f"BOOM{i}", "2026-08-20", "ALCISTA", "al_comienzo", 5.0 + i, 30.0)

        def _falla(tabla, as_of_date):
            raise RuntimeError("bug simulado en 3.6")

        monkeypatch.setattr(cer, "evaluate_conditions_from_experience_table", _falla)
        resumen = lep.run_experience_learning_cycle("2026-08-24")

        # El ciclo de experiencia real sigue reportándose exitoso, con sus
        # cifras reales intactas -- el fallo queda contenido en la clave nueva.
        assert resumen["ok"] is True
        assert resumen["n_experiencias"] == 5
        assert resumen["n_insertadas"] > 0
        assert resumen["error"] is None
        assert resumen["continuous_evaluation"]["ok"] is False
        assert "bug simulado en 3.6" in resumen["continuous_evaluation"]["error"]
    finally:
        _restore()


def test_P_activation_mechanism_permanece_off_tras_un_ciclo_real():
    _fresh()
    try:
        for i in range(5):
            _seed(f"OFF{i}", "2026-08-20", "ALCISTA", "al_comienzo", 5.0 + i, 30.0)
        assert areg.get_mechanism_state() == "OFF"
        lep.run_experience_learning_cycle("2026-08-24")
        assert areg.get_mechanism_state() == "OFF"  # el ciclo de experiencia nunca lo enciende
    finally:
        _restore()


# --- Hito 3, Fase 3.6 (2026-09-03) -- correcciones post-auditoría pre-commit -

def test_Q_fallo_de_continuous_evaluation_no_dispara_revocacion(monkeypatch):
    """Extiende test_O: además de no contaminar el resultado, un bug en 3.6
    tampoco puede haber disparado ninguna revocación real -- se verifica
    contra `activation_registry` real (Fase 3.5), sin mockear esa parte."""
    _fresh()
    try:
        for i in range(5):
            _seed(f"BOOMREV{i}", "2026-08-20", "ALCISTA", "al_comienzo", 5.0 + i, 30.0)

        def _falla(tabla, as_of_date):
            raise RuntimeError("bug simulado en 3.6")

        monkeypatch.setattr(cer, "evaluate_conditions_from_experience_table", _falla)
        resumen = lep.run_experience_learning_cycle("2026-08-24")

        assert resumen["ok"] is True
        assert resumen["continuous_evaluation"]["ok"] is False
        assert areg.list_revocations() == []
        assert areg.is_revoked("ALCISTA", "al_comienzo", lek.METHODOLOGY_VERSION) is False
    finally:
        _restore()


def test_R_fallo_de_db_de_3_6_no_dispara_revocacion_y_deja_evidencia_auditable():
    """Simula un fallo real de la DB de 3.6 (ruta inválida/no escribible,
    mismo patrón ya usado por `test_falla_interna_no_se_propaga...` para la
    DB de Fase 2) -- confirma: (1) el resultado principal de Hito 2 no se
    altera; (2) ninguna revocación ocurre; (3) el fallo queda como evidencia
    auditable dentro del propio `resumen` (el diseño actual no puede
    persistirlo en `continuous_evaluation_log` -- la escritura ES lo que
    falla -- pero sí lo devuelve en el dict que consumen el hilo del radar
    y el endpoint admin)."""
    _fresh()
    try:
        for i in range(5):
            _seed(f"DBFALLA{i}", "2026-08-20", "ALCISTA", "al_comienzo", 5.0 + i, 30.0)
        cer.DB_PATH = Path("Z:\\ruta\\que\\no\\existe\\nunca\\continuous_evaluation.db")
        resumen = lep.run_experience_learning_cycle("2026-08-24")

        assert resumen["ok"] is True
        assert resumen["n_experiencias"] == 5
        assert resumen["n_insertadas"] > 0
        assert resumen["error"] is None

        assert areg.list_revocations() == []
        assert areg.is_revoked("ALCISTA", "al_comienzo", lek.METHODOLOGY_VERSION) is False

        ce_resultado = resumen["continuous_evaluation"]
        assert ce_resultado["n_condiciones"] >= 1
        assert any(
            ev.get("error") for ev in ce_resultado["evaluaciones"]
        ), f"se esperaba evidencia del fallo de DB en el resultado: {ce_resultado}"
    finally:
        _restore()


def test_S_ciclo_hito_2_fallido_no_dispara_continuous_evaluation(monkeypatch):
    """Corrección post-auditoría (2026-09-03): `record_experience_knowledge()`
    corre DESPUÉS de que `tabla` ya se calculó (líneas 83-87) -- si lanza,
    `tabla` queda poblada pero `resumen["ok"]` queda False. Antes de esta
    corrección, `if tabla:` solo, el hook event-driven se ejecutaba de
    todos modos sobre una tabla que nunca llegó a persistirse como
    conocimiento real. El guard corregido es `if resumen["ok"] and tabla:`
    -- este test prueba que el hook ahora ni siquiera se intenta."""
    _fresh()
    try:
        for i in range(5):
            _seed(f"CICLOFALLA{i}", "2026-08-20", "ALCISTA", "al_comienzo", 5.0 + i, 30.0)

        def _falla(tabla):
            raise RuntimeError("fallo simulado en la persistencia de Hito 2")

        monkeypatch.setattr(lek, "record_experience_knowledge", _falla)
        resumen = lep.run_experience_learning_cycle("2026-08-24")

        assert resumen["ok"] is False
        assert resumen["error"] is not None
        assert "continuous_evaluation" not in resumen  # el hook nunca se intentó
        assert areg.list_revocations() == []
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
