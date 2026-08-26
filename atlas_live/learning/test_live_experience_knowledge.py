"""Tests de `live_experience_knowledge.py` (2026-08-25, Fase 2/5). Persistencia
REAL sobre SQLite temporal en cada test -- ningún mock de `_connect()`/DB,
para demostrar que esto es memoria persistente en disco, no estado en RAM.
Sin red en ningún test."""

import sqlite3
import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.learning import live_experience_knowledge as lek

_ORIG_DB_PATH = lek.DB_PATH


def _fresh():
    lek.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_lek_{_uuid.uuid4().hex}.db"


def _restore():
    lek.DB_PATH = _ORIG_DB_PATH


def _row(direction="ALCISTA", timing_deteccion="al_comienzo", bucket="poblacion_total",
         n_evaluables=42, n_aciertos_20=19, pct_20=45.2,
         wilson_lower=31.4, wilson_upper=60.1, baseline_pct_20=22.0, lift_20=2.05,
         mediana=18.3, n50=8, pct50=19.0, n100=1, pct100=2.4,
         validation_state="EN_VALIDACION", computed_as_of="2026-08-24", computed_at="2026-08-24T14:03:11+00:00"):
    return {
        "direction": direction, "timing_deteccion": timing_deteccion, "bucket": bucket,
        "n_evaluables": n_evaluables, "n_aciertos_20": n_aciertos_20, "pct_20": pct_20,
        "wilson_lower_bound_20_pct": wilson_lower, "wilson_upper_bound_20_pct": wilson_upper,
        "baseline_pct_20": baseline_pct_20, "lift_20": lift_20,
        "mediana_max_advance_pct": mediana,
        "n_aciertos_50": n50, "pct_50": pct50, "n_aciertos_100": n100, "pct_100": pct100,
        "validation_state": validation_state, "computed_as_of": computed_as_of, "computed_at": computed_at,
    }


# --- A/B: insertar y leer ---------------------------------------------------

def test_A_insertar_conocimiento_nuevo():
    _fresh()
    try:
        n = lek.record_experience_knowledge([_row()])
        assert n == 1
        leido = lek.get_knowledge_for("2026-08-25")
        assert len(leido) == 1
        assert leido[0]["direction"] == "ALCISTA"
        assert leido[0]["pct_20"] == 45.2
    finally:
        _restore()


def test_B_leer_conocimiento_filtrado_por_direction_y_timing():
    _fresh()
    try:
        lek.record_experience_knowledge([
            _row(direction="ALCISTA", timing_deteccion="al_comienzo"),
            _row(direction="BAJISTA", timing_deteccion="agotamiento"),
        ])
        leido = lek.get_knowledge_for("2026-08-25", direction="BAJISTA", timing_deteccion="agotamiento")
        assert len(leido) == 1
        assert leido[0]["direction"] == "BAJISTA"
    finally:
        _restore()


# --- C: dos computed_as_of distintos coexisten ------------------------------

def test_C_dos_computed_as_of_distintos_coexisten():
    _fresh()
    try:
        lek.record_experience_knowledge([_row(computed_as_of="2026-08-20", computed_at="2026-08-20T10:00:00+00:00")])
        lek.record_experience_knowledge([_row(computed_as_of="2026-08-24", computed_at="2026-08-24T10:00:00+00:00")])
        leido = lek.get_knowledge_for("2026-08-25")
        as_of_vistos = {r["computed_as_of"] for r in leido}
        assert as_of_vistos == {"2026-08-20", "2026-08-24"}
    finally:
        _restore()


# --- D: duplicados no se pisan ----------------------------------------------

def test_D_duplicados_del_mismo_dia_no_se_pisan():
    _fresh()
    try:
        lek.record_experience_knowledge([_row(pct_20=45.0, computed_at="2026-08-24T10:00:00+00:00")])
        lek.record_experience_knowledge([_row(pct_20=55.0, computed_at="2026-08-24T16:00:00+00:00")])
        leido = lek.get_knowledge_for("2026-08-25")
        assert len(leido) == 2  # ninguna fila se pisó -- las 2 coexisten
        pcts = {r["pct_20"] for r in leido}
        assert pcts == {45.0, 55.0}
        # La lectura "más reciente" resuelve el duplicado sin destruir nada.
        mas_reciente = lek.latest_knowledge_as_of("2026-08-25", "ALCISTA", "al_comienzo", "poblacion_total")
        assert mas_reciente["pct_20"] == 55.0
    finally:
        _restore()


# --- E: aislamiento por metodología (+ coexistencia sin mezclarse) ---------

def test_E_aislamiento_por_metodologia():
    _fresh()
    try:
        lek.record_experience_knowledge([_row(pct_20=45.0)], methodology_version="v1_direction_timing_volatility_tercile")
        lek.record_experience_knowledge([_row(pct_20=99.0)], methodology_version="v2_con_gates_fired")
        v1 = lek.get_knowledge_for("2026-08-25", methodology_version="v1_direction_timing_volatility_tercile")
        v2 = lek.get_knowledge_for("2026-08-25", methodology_version="v2_con_gates_fired")
        assert len(v1) == 1 and v1[0]["pct_20"] == 45.0
        assert len(v2) == 1 and v2[0]["pct_20"] == 99.0
        # Sin filtrar por metodología, las dos coexisten (nunca se pierde
        # ninguna), pero siguen siendo distinguibles por su columna propia.
        todas = lek.get_knowledge_for("2026-08-25", methodology_version=None)
        assert len(todas) == 2
        assert {r["methodology_version"] for r in todas} == {
            "v1_direction_timing_volatility_tercile", "v2_con_gates_fired",
        }
    finally:
        _restore()


def test_dos_metodologias_no_se_mezclan_en_latest_knowledge_as_of():
    _fresh()
    try:
        lek.record_experience_knowledge([_row(pct_20=45.0)], methodology_version="v1")
        lek.record_experience_knowledge([_row(pct_20=99.0)], methodology_version="v2")
        solo_v1 = lek.latest_knowledge_as_of("2026-08-25", "ALCISTA", "al_comienzo", "poblacion_total", methodology_version="v1")
        assert solo_v1["pct_20"] == 45.0  # nunca contaminado por la fila v2
    finally:
        _restore()


# --- F: protección temporal (OBLIGATORIO) -----------------------------------

def test_F_conocimiento_futuro_no_aparece_en_consulta_historica():
    _fresh()
    try:
        lek.record_experience_knowledge([_row(computed_as_of="2026-08-25", computed_at="2026-08-25T10:00:00+00:00")])
        # Se consulta una fecha ANTERIOR a computed_as_of -- el conocimiento
        # "del futuro" nunca debe aparecer.
        leido = lek.get_knowledge_for("2026-08-20")
        assert leido == []
        # Exactamente en el borde (as_of_date == computed_as_of) SÍ debe verse
        # -- el conocimiento calculado "as of" una fecha está disponible ESE
        # mismo día, nunca antes.
        leido_borde = lek.get_knowledge_for("2026-08-25")
        assert len(leido_borde) == 1
    finally:
        _restore()


# --- G: muestra insuficiente se conserva ------------------------------------

def test_G_muestra_insuficiente_se_conserva_explicitamente():
    _fresh()
    try:
        lek.record_experience_knowledge([_row(n_evaluables=5, validation_state="MUESTRA_INSUFICIENTE")])
        leido = lek.get_knowledge_for("2026-08-25")
        assert len(leido) == 1
        assert leido[0]["validation_state"] == "MUESTRA_INSUFICIENTE"  # nunca oculta, nunca eliminada
    finally:
        _restore()


# --- H: baseline=0 -> lift=NULL, nunca división por cero --------------------

def test_H_baseline_cero_persiste_lift_null_sin_excepcion():
    _fresh()
    try:
        n = lek.record_experience_knowledge([_row(baseline_pct_20=0.0, lift_20=None)])
        assert n == 1
        leido = lek.get_knowledge_for("2026-08-25")
        assert leido[0]["baseline_pct_20"] == 0.0
        assert leido[0]["lift_20"] is None  # nunca inf, nunca ZeroDivisionError, nunca inventado
    finally:
        _restore()


# --- I: recalcular no destruye conocimiento anterior ------------------------

def test_I_recalcular_no_destruye_conocimiento_anterior():
    _fresh()
    try:
        lek.record_experience_knowledge([_row(pct_20=30.0, computed_at="2026-08-24T09:00:00+00:00")])  # cálculo A
        a_recuperable = lek.get_knowledge_for("2026-08-25")
        assert len(a_recuperable) == 1 and a_recuperable[0]["pct_20"] == 30.0

        lek.record_experience_knowledge([_row(pct_20=70.0, computed_at="2026-08-24T18:00:00+00:00")])  # cálculo B
        ambos_recuperables = lek.get_knowledge_for("2026-08-25")
        assert len(ambos_recuperables) == 2
        pcts = {r["pct_20"] for r in ambos_recuperables}
        assert pcts == {30.0, 70.0}  # A sigue recuperable, B también -- ninguno se destruyó
    finally:
        _restore()


# --- J: no modifica ningún módulo protegido ---------------------------------

def test_J_no_importa_ningun_modulo_protegido():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(lek))
    modulos_importados = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modulos_importados.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modulos_importados.add(node.module)
            modulos_importados.update(alias.name for alias in node.names)

    for prohibido in (
        "candidate_gates", "priority_classifier", "decision_engine",
        "candidate_tracker", "catalyst_score", "candidate_registry",
    ):
        assert not any(prohibido in m for m in modulos_importados), (
            f"live_experience_knowledge.py no debe importar nada relacionado con {prohibido}"
        )


# --- Persistencia REAL: cerrar y reabrir la conexión conserva los datos ----

def test_persistencia_real_cerrar_y_reabrir_conexion():
    """Prueba explícita pedida: NO usa ninguna función de este módulo para
    releer -- abre una conexión sqlite3 CRUDA, nueva, directamente sobre el
    archivo en disco, después de que todas las conexiones anteriores ya se
    cerraron. Si esto pasa, es memoria persistente real, no estado en RAM."""
    _fresh()
    try:
        lek.record_experience_knowledge([_row(pct_20=63.0)])
        # `record_experience_knowledge` ya cerró su conexión (context manager
        # `with _connect() as conn`). Se abre una conexión NUEVA, cruda, sin
        # pasar por ninguna función del módulo.
        conn_cruda = sqlite3.connect(lek.DB_PATH)
        try:
            fila = conn_cruda.execute(
                "SELECT direction, pct_20 FROM live_experience_knowledge WHERE pct_20 = ?", (63.0,)
            ).fetchone()
        finally:
            conn_cruda.close()
        assert fila is not None
        assert fila[0] == "ALCISTA"
        assert fila[1] == 63.0
    finally:
        _restore()


def test_archivo_db_existe_fisicamente_en_disco():
    _fresh()
    try:
        assert not Path(lek.DB_PATH).exists()  # todavía no se creó nada
        lek.record_experience_knowledge([_row()])
        assert Path(lek.DB_PATH).exists()  # ahora sí -- archivo real, no RAM
    finally:
        _restore()


# --- Migración segura: CREATE TABLE/INDEX IF NOT EXISTS, nunca destructiva -

def test_migracion_es_idempotente_no_destruye_datos_existentes():
    _fresh()
    try:
        lek.record_experience_knowledge([_row(pct_20=88.0)])
        # Simula un segundo arranque del proceso -- nueva conexión, mismo
        # archivo, vuelve a correr `_SCHEMA` (CREATE ... IF NOT EXISTS).
        conn2 = lek._connect()
        conn2.close()
        leido = lek.get_knowledge_for("2026-08-25")
        assert len(leido) == 1
        assert leido[0]["pct_20"] == 88.0  # el dato sigue ahí, nada se recreó/borró
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
