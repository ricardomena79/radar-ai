"""HITO 5 -- Fase 5.2 (2026-09-04, autorizado explícitamente): tests del
watchdog de auto-recuperación de `radar_worker.py`. Archivo separado de
`test_radar_worker.py` (que cubre el barrido/`_loop()` en sí) -- Fase 5.2
NUNCA mezcla su lógica con la del barrido, mismo criterio pedido para
todo Hito 5.

DB temporal (mismo patrón que `test_radar_worker.py`) para `reg.set_meta()`/
`reg.get_meta()`. Cada test resetea TODO el estado global del módulo
relevante al watchdog (`_thread`, `_watchdog_thread`, `_watchdog_stop`,
`_watchdog_reintentos_consecutivos`) para no contaminar el siguiente test."""

import tempfile
import threading
import time
import uuid as _uuid
from pathlib import Path
from unittest import mock

from atlas_live.radar import candidate_registry as reg
from atlas_live.radar import radar_worker as w

_ORIG_DB = reg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_watchdog_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None
    w._thread = None
    w._watchdog_thread = None
    w._watchdog_reintentos_consecutivos = 0
    w._stop.clear()
    w._watchdog_stop.clear()


def _restore():
    reg.DB_PATH = _ORIG_DB
    w._thread = None
    w._watchdog_thread = None
    w._watchdog_reintentos_consecutivos = 0
    w._stop.clear()
    w._watchdog_stop.clear()


def _hilo_vivo() -> threading.Thread:
    """Hilo real, genuinamente vivo -- nunca un mock de `is_alive()`."""
    ev = threading.Event()
    t = threading.Thread(target=ev.wait, daemon=True)
    t.start()
    return t


def _hilo_muerto() -> threading.Thread:
    """Hilo real que YA terminó -- `is_alive()` real devuelve `False`,
    nunca simulado con un mock."""
    t = threading.Thread(target=lambda: None, daemon=True)
    t.start()
    t.join(timeout=2.0)
    assert not t.is_alive(), "el hilo de fixture no terminó a tiempo -- test inestable"
    return t


# --- 1) sin hilo que vigilar (radar nunca arrancó / deshabilitado) --------

def test_sin_thread_no_hace_nada():
    _fresh()
    try:
        w._thread = None
        resultado = w._watchdog_check_and_restart_if_dead()
        assert resultado["accion"] == "sin_hilo_que_vigilar"
        assert w._watchdog_reintentos_consecutivos == 0
    finally:
        _restore()


# --- 2) camino feliz: hilo vivo, nunca reinicia ---------------------------

def test_hilo_vivo_nunca_reinicia():
    _fresh()
    try:
        t = _hilo_vivo()
        w._thread = t
        try:
            with mock.patch.object(w, "start_universe_radar") as espia_restart:
                for _ in range(5):
                    resultado = w._watchdog_check_and_restart_if_dead()
                    assert resultado["accion"] == "hilo_vivo"
                assert espia_restart.call_count == 0
            assert w._watchdog_reintentos_consecutivos == 0
            assert w._thread is t  # nunca se tocó la referencia real
        finally:
            t.join(timeout=0.1)  # el hilo real sigue "vivo" (esperando el Event) -- no hace falta detenerlo, es daemon
    finally:
        _restore()


# --- 3) hilo muerto -> se reinicia exactamente una vez --------------------

def test_hilo_muerto_dispara_un_reinicio():
    _fresh()
    try:
        w._thread = _hilo_muerto()

        def _fake_restart():
            w._thread = _hilo_vivo()

        with mock.patch.object(w, "start_universe_radar", side_effect=_fake_restart) as espia:
            resultado = w._watchdog_check_and_restart_if_dead()

        assert resultado["accion"] == "reiniciado"
        assert resultado["reintentos"] == 1
        assert espia.call_count == 1
        assert w._watchdog_reintentos_consecutivos == 1
        assert w._thread.is_alive()  # el "reinicio" simulado realmente dejó un hilo vivo

        meta = reg.get_meta()
        assert meta.get("watchdog_state") == "REINICIADO"
        assert meta.get("watchdog_reinicios_total") == 1
        assert meta.get("watchdog_reintentos_consecutivos") == 1
        assert meta.get("watchdog_ultimo_reinicio_at")
    finally:
        _restore()


# --- 4) límite de reintentos: NUNCA reinicio infinito ---------------------

def test_limite_de_reintentos_detiene_los_reinicios_automaticos():
    _fresh()
    try:
        # Cada "reinicio" simulado deja el hilo MUERTO de nuevo -- el peor
        # caso real: una causa de muerte que persiste.
        with mock.patch.object(w, "start_universe_radar", side_effect=lambda: setattr(w, "_thread", _hilo_muerto())) as espia:
            w._thread = _hilo_muerto()
            resultados = [w._watchdog_check_and_restart_if_dead() for _ in range(w.WATCHDOG_MAX_REINTENTOS + 5)]

        acciones = [r["accion"] for r in resultados]
        n_reiniciados = acciones.count("reiniciado")
        n_limite = acciones.count("limite_reintentos_alcanzado")

        # Exactamente WATCHDOG_MAX_REINTENTOS reinicios reales -- nunca más.
        assert n_reiniciados == w.WATCHDOG_MAX_REINTENTOS
        assert espia.call_count == w.WATCHDOG_MAX_REINTENTOS
        # El resto de las corridas (5 de sobra) todas cayeron en el límite,
        # NINGUNA volvió a intentar reiniciar -- la prueba central de que
        # nunca hay un bucle infinito de reinicios.
        assert n_limite == 5
        assert w._watchdog_reintentos_consecutivos == w.WATCHDOG_MAX_REINTENTOS

        meta = reg.get_meta()
        assert meta.get("watchdog_state") == "DETENIDO_LIMITE_REINTENTOS"
        assert meta.get("watchdog_detenido_at")
    finally:
        _restore()


# --- 5) el contador se resetea si el hilo vuelve a estar vivo -------------

def test_contador_se_resetea_cuando_el_hilo_revive():
    _fresh()
    try:
        w._thread = _hilo_muerto()
        with mock.patch.object(w, "start_universe_radar", side_effect=lambda: setattr(w, "_thread", _hilo_muerto())):
            w._watchdog_check_and_restart_if_dead()
            w._watchdog_check_and_restart_if_dead()
        assert w._watchdog_reintentos_consecutivos == 2

        # El hilo vuelve a estar vivo (ej. alguien lo revivió manualmente).
        w._thread = _hilo_vivo()
        resultado = w._watchdog_check_and_restart_if_dead()
        assert resultado["accion"] == "hilo_vivo"
        assert w._watchdog_reintentos_consecutivos == 0

        meta = reg.get_meta()
        assert meta.get("watchdog_state") == "OK"
        assert meta.get("watchdog_reintentos_consecutivos") == 0
    finally:
        _restore()


# --- 6) backoff: el intervalo entre chequeos crece con los reintentos ----

def test_watchdog_loop_backoff_crece_con_reintentos(monkeypatch):
    """No corre el bucle real (sería lento) -- espía `_watchdog_stop.wait`
    para confirmar el intervalo exacto que `_watchdog_loop()` calcula en
    cada vuelta, mockeando `_watchdog_check_and_restart_if_dead()` para
    devolver un conteo de reintentos creciente."""
    _fresh()
    try:
        secuencia = [
            {"accion": "hilo_vivo"},
            {"accion": "reiniciado", "reintentos": 1},
            {"accion": "reiniciado", "reintentos": 2},
            {"accion": "limite_reintentos_alcanzado", "reintentos": 5},
        ]
        intervalos_usados = []

        def _fake_check():
            return secuencia.pop(0)

        def _fake_wait(intervalo):
            intervalos_usados.append(intervalo)
            return len(secuencia) == 0  # corta el bucle en la última vuelta

        monkeypatch.setattr(w, "_watchdog_check_and_restart_if_dead", _fake_check)
        monkeypatch.setattr(w._watchdog_stop, "wait", _fake_wait)
        monkeypatch.setattr(w._watchdog_stop, "is_set", lambda: False)

        w._watchdog_loop()

        assert intervalos_usados == [
            w.WATCHDOG_CHECK_SECONDS + w.WATCHDOG_BACKOFF_SECONDS * 0,
            w.WATCHDOG_CHECK_SECONDS + w.WATCHDOG_BACKOFF_SECONDS * 1,
            w.WATCHDOG_CHECK_SECONDS + w.WATCHDOG_BACKOFF_SECONDS * 2,
            w.WATCHDOG_CHECK_SECONDS + w.WATCHDOG_BACKOFF_SECONDS * 5,
        ]
        assert intervalos_usados == sorted(intervalos_usados[:3]) + [intervalos_usados[3]]  # creciente
    finally:
        _restore()


def test_watchdog_loop_nunca_se_cae_por_una_excepcion_del_chequeo(monkeypatch):
    _fresh()
    try:
        llamadas = {"n": 0}

        def _falla():
            llamadas["n"] += 1
            raise RuntimeError("chequeo roto a propósito")

        def _fake_wait(intervalo):
            return True  # corta el bucle en la primera vuelta

        monkeypatch.setattr(w, "_watchdog_check_and_restart_if_dead", _falla)
        monkeypatch.setattr(w._watchdog_stop, "wait", _fake_wait)
        monkeypatch.setattr(w._watchdog_stop, "is_set", lambda: False)

        w._watchdog_loop()  # NO debe propagar la excepción

        assert llamadas["n"] == 1
    finally:
        _restore()


# --- 7) arranque respeta las banderas de entorno --------------------------

def test_start_radar_watchdog_no_arranca_si_radar_deshabilitado(monkeypatch):
    _fresh()
    try:
        monkeypatch.setattr(w, "RADAR_ENABLED", False)
        monkeypatch.setattr(w, "WATCHDOG_ENABLED", True)
        w.start_radar_watchdog()
        assert w._watchdog_thread is None
    finally:
        _restore()


def test_start_radar_watchdog_no_arranca_si_watchdog_deshabilitado(monkeypatch):
    _fresh()
    try:
        monkeypatch.setattr(w, "RADAR_ENABLED", True)
        monkeypatch.setattr(w, "WATCHDOG_ENABLED", False)
        w.start_radar_watchdog()
        assert w._watchdog_thread is None
    finally:
        _restore()


def test_start_radar_watchdog_arranca_una_sola_vez(monkeypatch):
    _fresh()
    try:
        monkeypatch.setattr(w, "RADAR_ENABLED", True)
        monkeypatch.setattr(w, "WATCHDOG_ENABLED", True)
        try:
            w.start_radar_watchdog()
            primer_hilo = w._watchdog_thread
            assert primer_hilo is not None
            assert primer_hilo.is_alive()
            w.start_radar_watchdog()  # segunda llamada -- no debe crear otro hilo
            assert w._watchdog_thread is primer_hilo
        finally:
            w.request_stop()
            if w._watchdog_thread is not None:
                w._watchdog_thread.join(timeout=2.0)
    finally:
        _restore()


# --- 8) request_stop() también detiene al watchdog ------------------------

def test_request_stop_detiene_tambien_al_watchdog():
    _fresh()
    try:
        assert w._watchdog_stop.is_set() is False
        w.request_stop()
        assert w._watchdog_stop.is_set() is True
        assert w._stop.is_set() is True
    finally:
        _restore()


# --- 8b) apagado intencional: el watchdog NUNCA "gana la carrera" contra
# un request_stop() -- aunque encuentre el hilo muerto (que es justo lo
# que se espera tras un apagado intencional), no debe reiniciarlo ---------

def test_hilo_muerto_tras_stop_intencional_no_se_reinicia():
    _fresh()
    try:
        w._thread = _hilo_muerto()
        w._stop.set()  # simula que _loop() ya salió por un stop pedido
        with mock.patch.object(w, "start_universe_radar") as espia:
            resultado = w._watchdog_check_and_restart_if_dead()
        assert resultado["accion"] == "apagado_intencional_no_reinicia"
        assert espia.call_count == 0
        assert w._watchdog_reintentos_consecutivos == 0
    finally:
        _restore()


def test_hilo_muerto_tras_watchdog_stop_directo_no_se_reinicia():
    _fresh()
    try:
        # Cubre el caso en que solo _watchdog_stop está seteado (sin pasar
        # por _stop) -- ambos disparan el mismo guard, por separado.
        w._thread = _hilo_muerto()
        w._watchdog_stop.set()
        with mock.patch.object(w, "start_universe_radar") as espia:
            resultado = w._watchdog_check_and_restart_if_dead()
        assert resultado["accion"] == "apagado_intencional_no_reinicia"
        assert espia.call_count == 0
    finally:
        _restore()


# --- 9) integración real: hilo watchdog real detecta y reinicia ----------

def test_integracion_real_el_hilo_watchdog_detecta_la_muerte_y_reinicia(monkeypatch):
    """Único test que corre el hilo watchdog REAL (no solo la función de
    chequeo aislada) -- prueba que `_watchdog_loop()` corriendo en su
    propio `threading.Thread` de verdad detecta un hilo muerto y dispara
    el reinicio, con un intervalo de chequeo corto SOLO para que el test
    no tarde minutos (`WATCHDOG_CHECK_SECONDS` bajado vía monkeypatch,
    nunca el valor de producción)."""
    _fresh()
    try:
        monkeypatch.setattr(w, "WATCHDOG_CHECK_SECONDS", 0.05)
        monkeypatch.setattr(w, "WATCHDOG_BACKOFF_SECONDS", 0.05)
        monkeypatch.setattr(w, "RADAR_ENABLED", True)
        monkeypatch.setattr(w, "WATCHDOG_ENABLED", True)

        w._thread = _hilo_muerto()
        reinicios = {"n": 0}

        def _fake_restart():
            reinicios["n"] += 1
            w._thread = _hilo_vivo()

        monkeypatch.setattr(w, "start_universe_radar", _fake_restart)

        try:
            w.start_radar_watchdog()
            deadline = time.time() + 3.0
            while reinicios["n"] == 0 and time.time() < deadline:
                time.sleep(0.02)
        finally:
            w.request_stop()
            if w._watchdog_thread is not None:
                w._watchdog_thread.join(timeout=2.0)

        assert reinicios["n"] >= 1, "el watchdog real nunca detectó ni reinició el hilo muerto"
        assert w._thread.is_alive()
    finally:
        _restore()
