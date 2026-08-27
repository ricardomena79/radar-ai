"""Tests de `current_top_opportunity_registry.py` (2026-08-26, FASE 2/5).
DB temporal real (SQLite en disco, mismo patrón que el resto de la sesión)
-- sin red, sin fakes de infraestructura."""

import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.core import current_top_opportunity as ctop
from atlas_live.core import current_top_opportunity_registry as reg

_ORIG_DB_PATH = reg.DB_PATH
_C = ctop.CandidateForSelection


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_ctop_{_uuid.uuid4().hex}.db"


def _restore():
    reg.DB_PATH = _ORIG_DB_PATH


def _sel(ticker="NSSC", decision="OPORTUNIDAD_PRIORITARIA", atlas_score=80.0):
    """Corre el selector real (Fase 1/5, sin modificar) sobre dos
    candidatas sintéticas -- el registro nunca decide, solo persiste lo
    que el selector ya decidió."""
    candidatos = [
        _C(ticker=ticker, decision=decision, ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=atlas_score, momentum_score=atlas_score),
        _C(ticker="RELLENO", decision="NO_TOCAR", ranking_score=(-1.0, 0, 0.0, 0.0), atlas_score=1.0, momentum_score=1.0),
    ]
    return ctop.select_current_top_opportunity(candidatos)


# --- creacion de tabla / DB vacia --------------------------------------------

def test_creacion_de_tabla_y_db_vacia():
    _fresh()
    try:
        assert reg.get_top_opportunity_sequence("2026-08-26") == []
        assert reg.get_top_opportunity_at("2026-08-26", "2026-08-26T14:00:00+00:00") is None
    finally:
        _restore()


# --- CASO A: primera seleccion -----------------------------------------------

def test_caso_A_primera_seleccion():
    _fresh()
    try:
        r = reg.register_top_opportunity(_sel(ticker="NSSC"), "2026-08-26")
        assert r["action"] == "CREADO"
        assert r["previous_ticker"] is None
        assert r["selection_sequence"] == 1

        secuencia = reg.get_top_opportunity_sequence("2026-08-26")
        assert len(secuencia) == 1
        assert secuencia[0]["ticker"] == "NSSC"
        assert secuencia[0]["deselected_at"] is None
        assert secuencia[0]["previous_ticker"] is None
    finally:
        _restore()


# --- CASO B: mismo ganador repetido (10 ciclos) -- idempotencia -----------

def test_caso_B_mismo_ganador_repetido_no_duplica():
    _fresh()
    try:
        for _ in range(10):
            r = reg.register_top_opportunity(_sel(ticker="NSSC"), "2026-08-26")
        assert r["action"] == "SIN_CAMBIOS"

        secuencia = reg.get_top_opportunity_sequence("2026-08-26")
        assert len(secuencia) == 1  # sigue existiendo UNA sola seleccion
    finally:
        _restore()


# --- mismo ciclo ejecutado 2 veces exactas -- no duplica --------------------

def test_mismo_ciclo_ejecutado_dos_veces_no_duplica():
    _fresh()
    try:
        seleccion = _sel(ticker="NSSC")
        reg.register_top_opportunity(seleccion, "2026-08-26")
        r2 = reg.register_top_opportunity(seleccion, "2026-08-26")  # el MISMO objeto, otra vez
        assert r2["action"] == "SIN_CAMBIOS"
        assert len(reg.get_top_opportunity_sequence("2026-08-26")) == 1
    finally:
        _restore()


# --- CASO C: A -> B -> continua B -------------------------------------------

def test_caso_C_cambio_A_a_B_y_B_continua():
    _fresh()
    try:
        reg.register_top_opportunity(_sel(ticker="NSSC"), "2026-08-26")
        r_cambio = reg.register_top_opportunity(_sel(ticker="COHR"), "2026-08-26")
        assert r_cambio["action"] == "REEMPLAZADO"
        assert r_cambio["previous_ticker"] == "NSSC"
        assert r_cambio["selection_sequence"] == 2

        # B continua -- NO debe crear una tercera fila.
        r_continua = reg.register_top_opportunity(_sel(ticker="COHR"), "2026-08-26")
        assert r_continua["action"] == "SIN_CAMBIOS"

        secuencia = reg.get_top_opportunity_sequence("2026-08-26")
        assert len(secuencia) == 2
        assert secuencia[0]["ticker"] == "NSSC"
        assert secuencia[0]["deselected_at"] is not None  # cerrado
        assert secuencia[1]["ticker"] == "COHR"
        assert secuencia[1]["deselected_at"] is None  # sigue abierto
        assert secuencia[1]["previous_ticker"] == "NSSC"
        assert secuencia[1]["replacement_reason"]  # motivo no vacio
    finally:
        _restore()


# --- A -> B -> C completo, exactamente 3 filas -------------------------------

def test_secuencia_completa_A_B_C():
    _fresh()
    try:
        reg.register_top_opportunity(_sel(ticker="NSSC"), "2026-08-26")
        reg.register_top_opportunity(_sel(ticker="COHR"), "2026-08-26")
        reg.register_top_opportunity(_sel(ticker="ANF"), "2026-08-26")

        secuencia = reg.get_top_opportunity_sequence("2026-08-26")
        assert [s["ticker"] for s in secuencia] == ["NSSC", "COHR", "ANF"]
        assert [s["selection_sequence"] for s in secuencia] == [1, 2, 3]
        # Solo el ultimo sigue abierto.
        assert secuencia[0]["deselected_at"] is not None
        assert secuencia[1]["deselected_at"] is not None
        assert secuencia[2]["deselected_at"] is None
        assert secuencia[2]["previous_ticker"] == "COHR"
    finally:
        _restore()


# --- reinicio/reapertura de la DB -------------------------------------------

def test_reinicio_reapertura_db_conserva_estado():
    _fresh()
    try:
        reg.register_top_opportunity(_sel(ticker="NSSC"), "2026-08-26")
        reg.register_top_opportunity(_sel(ticker="COHR"), "2026-08-26")

        # Simula "reinicio de proceso" -- ninguna conexion en memoria
        # persiste entre llamadas (cada `_connect()` abre una nueva), asi
        # que una lectura fresca YA prueba esto -- pero lo hacemos
        # explicito reabriendo la conexion.
        conn = reg._connect()
        conn.close()

        secuencia = reg.get_top_opportunity_sequence("2026-08-26")
        assert [s["ticker"] for s in secuencia] == ["NSSC", "COHR"]
        assert secuencia[1]["deselected_at"] is None
    finally:
        _restore()


# --- selection_sequence monotonico -------------------------------------------

def test_selection_sequence_monotonico():
    _fresh()
    try:
        tickers = ["NSSC", "COHR", "ANF", "CRWD", "OKTA"]
        for t in tickers:
            reg.register_top_opportunity(_sel(ticker=t), "2026-08-26")
        secuencia = reg.get_top_opportunity_sequence("2026-08-26")
        secuencias = [s["selection_sequence"] for s in secuencia]
        assert secuencias == sorted(secuencias)
        assert secuencias == list(range(1, len(tickers) + 1))
    finally:
        _restore()


# --- previous_ticker correcto -------------------------------------------------

def test_previous_ticker_correcto():
    _fresh()
    try:
        reg.register_top_opportunity(_sel(ticker="NSSC"), "2026-08-26")
        reg.register_top_opportunity(_sel(ticker="COHR"), "2026-08-26")
        r3 = reg.register_top_opportunity(_sel(ticker="ANF"), "2026-08-26")
        assert r3["previous_ticker"] == "COHR"  # no NSSC -- el INMEDIATO anterior
    finally:
        _restore()


# --- runner_up / score_components / methodology_version congelados --------

def test_runner_up_score_components_y_metodologia_congelados():
    _fresh()
    try:
        seleccion = _sel(ticker="NSSC", atlas_score=80.0)
        reg.register_top_opportunity(seleccion, "2026-08-26")

        fila = reg.get_top_opportunity_sequence("2026-08-26")[0]
        assert fila["runner_up_ticker"] == seleccion.runner_up_ticker
        assert fila["runner_up_score"] == seleccion.runner_up_score
        assert fila["score_components"]["atlas_score"] == 80.0
        assert fila["score_components"]["decision"] == "OPORTUNIDAD_PRIORITARIA"
        assert fila["score_components"]["criterio_decisivo"] == seleccion.criterio_decisivo
        assert fila["methodology_version"] == ctop.CORE_METHODOLOGY_VERSION

        # Aunque NSSC siga #1 con datos "distintos" en una llamada
        # posterior, la fila ORIGINAL nunca se actualiza (CASO B no
        # escribe nada) -- queda congelada al momento de la seleccion.
        otra_seleccion_mismo_ticker = _sel(ticker="NSSC", atlas_score=999.0)
        reg.register_top_opportunity(otra_seleccion_mismo_ticker, "2026-08-26")
        fila_de_nuevo = reg.get_top_opportunity_sequence("2026-08-26")[0]
        assert fila_de_nuevo["score_components"]["atlas_score"] == 80.0  # sin cambios
    finally:
        _restore()


# --- reconstruccion: consulta del Top-1 a una hora determinada -------------

def test_consulta_top1_a_hora_determinada():
    _fresh()
    try:
        reg.register_top_opportunity(_sel(ticker="NSSC"), "2026-08-26")
        antes = reg.get_top_opportunity_at("2026-08-26", "2026-08-26T00:00:00+00:00")
        assert antes is None  # no existia todavia a esa hora

        reg.register_top_opportunity(_sel(ticker="COHR"), "2026-08-26")

        # "Ahora" (futuro respecto a ambos registros) -- debe devolver el vigente.
        import datetime as _dt
        ahora = _dt.datetime.now(_dt.timezone.utc).isoformat()
        vigente = reg.get_top_opportunity_at("2026-08-26", ahora)
        assert vigente["ticker"] == "COHR"
    finally:
        _restore()


# --- manejo de datos invalidos ------------------------------------------------

def test_datos_invalidos_no_escriben_nada():
    _fresh()
    try:
        r1 = reg.register_top_opportunity(None, "2026-08-26")  # sin candidatos, ver Fase 1/5
        assert r1["action"] == "SIN_SELECCION"

        r2 = reg.register_top_opportunity(_sel(ticker="NSSC"), "")  # market_date vacio
        assert r2["action"] == "DATOS_INVALIDOS"

        assert reg.get_top_opportunity_sequence("2026-08-26") == []
    finally:
        _restore()


# --- aislamiento estructural: nunca decide, nunca importa logica de decision -

def test_registro_nunca_importa_logica_de_decision():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(reg))
    modulos = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modulos.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modulos.add(node.module)

    prohibidos = ("priority_classifier", "decision_engine", "candidate_gates", "candidate_tracker",
                  "explosive_engine", "historical_scoring", "catalyst_score")
    for m in modulos:
        for p in prohibidos:
            assert p not in m, f"import prohibido: {m}"
    # Unico import propio permitido: el TIPO de resultado ya decidido.
    assert any("current_top_opportunity" in m and "registry" not in m for m in modulos)


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
