"""HITO 5 -- Fase 5.3 (2026-09-04, autorizado explícitamente): tests de la
reutilización de conexión SQLite POR HILO en `candidate_registry._connect()`.
Archivo separado de `test_candidate_registry.py` (que cubre la lógica de
negocio de cada tabla) -- Fase 5.3 solo prueba el MECANISMO de conexión en
sí: reutilización, aislamiento entre hilos, comportamiento idéntico, y
concurrencia real."""

import sqlite3
import tempfile
import threading
import time
import uuid as _uuid
from pathlib import Path
from typing import Dict

from atlas_live.radar import candidate_registry as reg

_ORIG_DB = reg.DB_PATH


def _fresh(pre_calentar=True):
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_connreuse_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None
    # Cada test arranca sin conexión cacheada en el hilo principal de pytest.
    reg._thread_local.conn = None
    reg._thread_local.conn_path = None
    if pre_calentar:
        # Mismo patrón ya establecido en `test_candidate_registry.py::
        # test_migracion_concurrente_no_colisiona`: crea el archivo real +
        # corre la migración UNA vez, sin concurrencia, y recién después
        # resetea `_schema_ready_for` -- así los tests de ESTA fase (que
        # prueban reutilización/aislamiento de conexión) no se contaminan
        # con la carrera de "SQLite inicializando WAL en un archivo
        # completamente nuevo bajo hilos simultáneos" -- confirmado real y
        # PRE-EXISTENTE (reproducido idéntico con el `_connect()` anterior
        # a esta fase), pero es un fenómeno DISTINTO al que Fase 5.3 prueba.
        reg._connect()
        reg._thread_local.conn = None
        reg._thread_local.conn_path = None


def _restore():
    reg.DB_PATH = _ORIG_DB
    reg._thread_local.conn = None
    reg._thread_local.conn_path = None


# --- 1) misma conexión reutilizada dentro del mismo hilo -------------------

def test_llamadas_sucesivas_en_el_mismo_hilo_devuelven_la_misma_conexion():
    _fresh()
    try:
        c1 = reg._connect()
        c2 = reg._connect()
        c3 = reg._connect()
        assert c1 is c2 is c3
    finally:
        _restore()


# --- 2) comportamiento observable idéntico: WAL/busy_timeout/row_factory --

def test_pragmas_y_row_factory_siguen_iguales():
    _fresh()
    try:
        conn = reg._connect()
        assert isinstance(conn, sqlite3.Connection)
        assert conn.row_factory is sqlite3.Row
        modo = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert modo.lower() == "wal"
    finally:
        _restore()


# --- 3) lectura ve lo que otra llamada (misma conexión) escribió ----------

def test_lectura_ve_la_escritura_de_una_llamada_anterior_en_el_mismo_hilo():
    _fresh()
    try:
        reg.set_meta(prueba_reuso="valor_1")
        assert reg.get_meta().get("prueba_reuso") == "valor_1"
        reg.set_meta(prueba_reuso="valor_2")
        assert reg.get_meta().get("prueba_reuso") == "valor_2"
    finally:
        _restore()


# --- 4) DB_PATH cambia a mitad de hilo -> conexión vieja se cierra, se abre nueva --

def test_cambio_de_db_path_en_el_mismo_hilo_abre_una_conexion_nueva():
    _fresh()
    try:
        conn_vieja = reg._connect()
        reg.set_meta(marca="db_1")

        ruta_2 = Path(tempfile.gettempdir()) / f"atlas_test_connreuse_2_{_uuid.uuid4().hex}.db"
        reg.DB_PATH = ruta_2
        reg._schema_ready_for = None

        conn_nueva = reg._connect()
        assert conn_nueva is not conn_vieja

        # La conexión vieja quedó cerrada -- usarla debe fallar.
        raised = False
        try:
            conn_vieja.execute("SELECT 1")
        except sqlite3.ProgrammingError:
            raised = True
        assert raised, "la conexión vieja debería estar cerrada tras el cambio de DB_PATH"

        # La nueva DB es genuinamente distinta -- sin la marca de la vieja.
        assert reg.get_meta().get("marca") is None
        reg.set_meta(marca="db_2")
        assert reg.get_meta().get("marca") == "db_2"
    finally:
        try:
            Path(ruta_2).unlink(missing_ok=True)
        except Exception:
            pass
        _restore()


# --- 5) hilos distintos -> conexiones distintas, NUNCA compartidas --------

def test_hilos_distintos_obtienen_conexiones_distintas():
    _fresh()
    try:
        conexiones = {}
        lock = threading.Lock()

        def _worker(nombre):
            c = reg._connect()
            with lock:
                conexiones[nombre] = c

        hilos = [threading.Thread(target=_worker, args=(f"hilo_{i}",)) for i in range(5)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=5.0)

        ids_conexiones = {id(c) for c in conexiones.values()}
        assert len(conexiones) == 5
        assert len(ids_conexiones) == 5, "dos hilos terminaron compartiendo el mismo objeto Connection"
    finally:
        _restore()


# --- 6a) concurrencia real, patrón realista: metadata (radar_meta) --------
# `set_meta()`/`get_meta()` SÍ se llaman de verdad desde múltiples hilos en
# producción hoy (el hilo del radar + el watchdog de Fase 5.2 + acciones
# admin) -- volumen bajo, mismo criterio que la contención real que existe.

def test_concurrencia_real_metadata_multiples_hilos_sin_errores():
    _fresh()
    try:
        N_HILOS = 8
        N_OPERACIONES_POR_HILO = 30
        errores = []

        def _worker(indice):
            try:
                for i in range(N_OPERACIONES_POR_HILO):
                    reg.set_meta(**{f"prueba_hilo_{indice}": i})
                    reg.get_meta()
            except Exception as exc:
                errores.append((indice, repr(exc)))

        hilos = [threading.Thread(target=_worker, args=(i,)) for i in range(N_HILOS)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=30.0)

        assert not errores, f"errores reales durante la concurrencia de metadata: {errores}"
        meta_final = reg.get_meta()
        for i in range(N_HILOS):
            assert meta_final.get(f"prueba_hilo_{i}") == N_OPERACIONES_POR_HILO - 1
    finally:
        _restore()


# --- 6b) concurrencia EXTREMA de escritura -- hallazgo real documentado ---

def test_concurrencia_extrema_de_escritura_nunca_corrompe_datos_ni_comparte_conexiones():
    """HALLAZGO REAL encontrado durante la implementación de esta fase
    (2026-09-04): bajo concurrencia de escritura SINTÉTICA y EXTREMA (12
    hilos x 20 escrituras simultáneas contra `candidate_detection` -- MÁS
    agresivo que cualquier patrón real de producción, donde esa tabla casi
    siempre se escribe desde el ÚNICO hilo del radar, nunca 12 a la vez),
    SQLite puede rechazar algunas escrituras con `OperationalError: database
    is locked` incluso con `busy_timeout=15000`.

    CONFIRMADO, reproduciendo el MISMO escenario contra el `_connect()`
    ANTERIOR a esta fase (conexión nueva en cada llamada, sin reutilización):
    el mismo error ocurre IDÉNTICO -- esto YA EXISTÍA antes de Fase 5.3, no
    es una regresión de la reutilización por-hilo. Queda documentado acá,
    deliberadamente NO corregido -- arreglar la contención de escritura en
    sí (ej. una cola de escritura de un solo hilo, o WAL checkpoint tuning)
    es un cambio de diseño distinto, fuera del alcance de "reutilización de
    conexión", y requeriría su propia autorización aparte.

    Lo que SÍ debe seguir siendo verdad SIEMPRE, y es lo que esta fase
    realmente garantiza: (1) ninguna fila queda parcial/corrupta -- el
    conteo real en la DB coincide exactamente con las escrituras que
    `record_detection()` reportó como exitosas; (2) cada hilo usó su PROPIA
    conexión, nunca compartida con otro."""
    _fresh()
    try:
        N_HILOS = 12
        N_ESCRITURAS_POR_HILO = 20
        resultados_lock = threading.Lock()
        resultados = {"ok": 0, "lock_error": 0, "otro_error": []}
        conexion_por_hilo: Dict[int, int] = {}

        def _worker(indice):
            for i in range(N_ESCRITURAS_POR_HILO):
                try:
                    reg.record_detection(
                        f"CONC{indice}_{i}", "2026-09-04", "regular",
                        "2026-09-04T14:00:00Z", "s1", 10.0, 5.0, 10000, 5000, 2.0,
                        100_000.0, [{"name": "cambio_de_precio", "reason": "x", "value": 5.0}],
                    )
                    with resultados_lock:
                        resultados["ok"] += 1
                except sqlite3.OperationalError as exc:
                    with resultados_lock:
                        if "locked" in str(exc).lower():
                            resultados["lock_error"] += 1
                        else:
                            resultados["otro_error"].append(repr(exc))
                except Exception as exc:
                    with resultados_lock:
                        resultados["otro_error"].append(repr(exc))
            conexion_por_hilo[indice] = id(reg._connect())

        hilos = [threading.Thread(target=_worker, args=(i,)) for i in range(N_HILOS)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=30.0)

        # Cero errores de un tipo DISTINTO a "database is locked" -- ese es
        # el único modo de fallo conocido/pre-existente; cualquier otro tipo
        # de excepción sí sería un defecto real nuevo de esta fase.
        assert not resultados["otro_error"], f"errores inesperados (no lock): {resultados['otro_error']}"

        # Garantía 1: sin corrupción -- el conteo real coincide EXACTO con
        # las escrituras reportadas como exitosas.
        with sqlite3.connect(reg.DB_PATH) as verificacion:
            n_reales = verificacion.execute("SELECT COUNT(*) FROM candidate_detection").fetchone()[0]
        assert n_reales == resultados["ok"], (
            f"el conteo real de filas ({n_reales}) debe coincidir EXACTO con las "
            f"escrituras que record_detection() reportó como exitosas ({resultados['ok']}) "
            f"-- cualquier discrepancia sería corrupción real, no solo contención"
        )

        # Garantía 2 (la que esta fase realmente introduce): cada hilo
        # terminó con su PROPIA conexión, nunca compartida con otro.
        assert len(set(conexion_por_hilo.values())) == N_HILOS

        print(
            f"\n[Fase 5.3] concurrencia extrema: {resultados['ok']} escrituras OK, "
            f"{resultados['lock_error']} rechazadas por lock pre-existente "
            f"(de {N_HILOS * N_ESCRITURAS_POR_HILO} intentos totales) -- cero corrupción, "
            f"cero conexiones compartidas entre hilos."
        )
    finally:
        _restore()


# --- 7) benchmark antes/después (informativo, con evidencia real) --------

def test_benchmark_reutilizacion_es_mas_rapida_que_abrir_conexion_nueva_cada_vez(capsys):
    """No es un assert de umbral fijo (el tiempo absoluto depende de la
    máquina) -- corre el mismo número de operaciones con conexión SIEMPRE
    NUEVA (comportamiento ANTES de Fase 5.3, reproducido explícitamente
    acá mismo, no supuesto) vs. con `_connect()` real (comportamiento
    DESPUÉS, ya con reutilización), e imprime ambos tiempos + la razón de
    velocidad para que quede como evidencia real en el reporte."""
    _fresh()
    try:
        N = 300

        # "ANTES": exactamente lo que _connect() hacía previamente, línea
        # por línea (conexión nueva + PRAGMA en cada llamada).
        t0 = time.perf_counter()
        for _ in range(N):
            c = sqlite3.connect(reg.DB_PATH, timeout=15)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=15000")
            c.execute("SELECT 1")
            c.close()
        t_antes = time.perf_counter() - t0

        # "DESPUÉS": _connect() real, reutilizando desde la 2da llamada.
        t0 = time.perf_counter()
        for _ in range(N):
            c = reg._connect()
            c.execute("SELECT 1")
        t_despues = time.perf_counter() - t0

        razon = t_antes / t_despues if t_despues > 0 else float("inf")
        with capsys.disabled():
            print(
                f"\n[Fase 5.3 benchmark] {N} operaciones -- "
                f"ANTES (conexión nueva cada vez): {t_antes:.4f}s -- "
                f"DESPUÉS (reutilizada por-hilo): {t_despues:.4f}s -- "
                f"{razon:.1f}x más rápido"
            )
        assert t_despues < t_antes, (
            f"la reutilización debería ser más rápida que abrir una conexión nueva "
            f"cada vez -- antes={t_antes:.4f}s, después={t_despues:.4f}s"
        )
    finally:
        _restore()
