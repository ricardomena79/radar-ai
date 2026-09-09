"""Tests de `storage_guard.py` (2026-09-09, kill-switch automático de
emergencia por disco -- "airbag" antes de la prueba de premarket).

Todos los tests mockean `_current_used_pct()` (nunca tocan el filesystem
real) y restauran el estado global del módulo antes/después de cada test
(mismo patrón que el resto del proyecto: aislamiento explícito de estado
compartido entre tests)."""

import inspect
import threading

import pytest

from atlas_live import storage_guard as sg


@pytest.fixture(autouse=True)
def _reset_state():
    """Restaura las variables de módulo antes y después de cada test --
    evita que el orden de ejecución de los tests afecte el resultado."""
    def _clear():
        with sg._lock:
            sg._emergency_active = False
            sg._last_level = None
            sg._last_checked_at = None
            sg._last_used_pct = None
            sg._last_transition_at = None
            sg._last_transition_reason = None
    _clear()
    yield
    _clear()


def _set_pct(monkeypatch, pct):
    monkeypatch.setattr(sg, "_current_used_pct", lambda: pct)


# --- A) Umbrales pedidos explícitamente: 84.9/85/89.9/90/95/99 ------------

def test_84_9_pct_es_OK(monkeypatch):
    _set_pct(monkeypatch, 84.9)
    assert sg.check_and_get_level() == "OK"
    assert sg.is_emergency_active() is False


def test_85_pct_es_WARNING(monkeypatch):
    _set_pct(monkeypatch, 85.0)
    assert sg.check_and_get_level() == "WARNING"
    assert sg.is_emergency_active() is False


def test_89_9_pct_sigue_WARNING_no_EMERGENCY(monkeypatch):
    _set_pct(monkeypatch, 89.9)
    assert sg.check_and_get_level() == "WARNING"
    assert sg.is_emergency_active() is False


def test_90_pct_dispara_EMERGENCY(monkeypatch):
    _set_pct(monkeypatch, 90.0)
    assert sg.check_and_get_level() == "EMERGENCY"
    assert sg.is_emergency_active() is True


def test_95_pct_es_EMERGENCY(monkeypatch):
    _set_pct(monkeypatch, 95.0)
    assert sg.check_and_get_level() == "EMERGENCY"


def test_99_pct_es_EMERGENCY(monkeypatch):
    _set_pct(monkeypatch, 99.0)
    assert sg.check_and_get_level() == "EMERGENCY"


# --- B) Histéresis -- "evitar loops de stop/restart" ----------------------

def test_al_bajar_de_90_pero_sobre_87_sigue_en_EMERGENCY(monkeypatch):
    _set_pct(monkeypatch, 90.5)
    assert sg.check_and_get_level() == "EMERGENCY"
    _set_pct(monkeypatch, 88.0)  # bajó de 90, pero no de RESUME_THRESHOLD_PCT=87
    assert sg.check_and_get_level() == "EMERGENCY", "no debe recuperarse hasta cruzar RESUME_THRESHOLD_PCT"


def test_al_bajar_de_87_se_recupera_a_OK(monkeypatch):
    _set_pct(monkeypatch, 91.0)
    assert sg.check_and_get_level() == "EMERGENCY"
    _set_pct(monkeypatch, 86.9)
    assert sg.check_and_get_level() == "OK"
    assert sg.is_emergency_active() is False


def test_oscilacion_justo_en_90_no_produce_flapping(monkeypatch):
    """Simula el caso real que motivó la histéresis: una escritura que
    empuja el % apenas sobre/bajo 90 en chequeos sucesivos -- sin
    histéresis, esto activaría/desactivaria en cada llamada."""
    secuencia = [89.9, 90.1, 89.95, 90.05, 89.8]
    resultados = []
    for pct in secuencia:
        _set_pct(monkeypatch, pct)
        resultados.append(sg.check_and_get_level())
    # Una vez que entra en EMERGENCY (90.1), debe quedarse ahí durante
    # TODA la oscilación (ninguno de los valores baja de 87).
    idx_primera_emergencia = resultados.index("EMERGENCY")
    assert all(r == "EMERGENCY" for r in resultados[idx_primera_emergencia:])


# --- C) Fail-safe ante medición fallida ------------------------------------

def test_sin_poder_medir_disco_nunca_activa_emergencia_nueva(monkeypatch):
    _set_pct(monkeypatch, None)
    assert sg.check_and_get_level() == "OK"
    assert sg.is_emergency_active() is False


def test_sin_poder_medir_disco_mantiene_emergencia_ya_activa(monkeypatch):
    _set_pct(monkeypatch, 95.0)
    assert sg.check_and_get_level() == "EMERGENCY"
    _set_pct(monkeypatch, None)
    assert sg.check_and_get_level() == "EMERGENCY", "no debe recuperarse a ciegas sin poder confirmar que bajó"


# --- D) Concurrencia -- "seguro si dos procesos detectan simultáneamente" -

def test_llamadas_concurrentes_no_corrompen_el_estado(monkeypatch):
    _set_pct(monkeypatch, 95.0)
    resultados = []
    lock_resultados = threading.Lock()

    def _worker():
        r = sg.check_and_get_level()
        with lock_resultados:
            resultados.append(r)

    hilos = [threading.Thread(target=_worker) for _ in range(20)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert len(resultados) == 20
    assert all(r == "EMERGENCY" for r in resultados)
    assert sg.is_emergency_active() is True


# --- E) status() -- evidencia/log, nunca fuerza una medición nueva --------

def test_status_refleja_la_ultima_medicion_sin_medir_de_nuevo(monkeypatch):
    llamadas = {"n": 0}

    def _fake():
        llamadas["n"] += 1
        return 92.0

    monkeypatch.setattr(sg, "_current_used_pct", _fake)
    sg.check_and_get_level()
    assert llamadas["n"] == 1

    s = sg.status()
    assert llamadas["n"] == 1, "status() no debe volver a medir el disco"
    assert s["emergency_active"] is True
    assert s["last_used_pct"] == 92.0
    assert s["last_transition_reason"] is not None
    assert s["warning_threshold_pct"] == sg.WARNING_THRESHOLD_PCT
    assert s["emergency_threshold_pct"] == sg.EMERGENCY_THRESHOLD_PCT
    assert s["resume_threshold_pct"] == sg.RESUME_THRESHOLD_PCT


def test_status_antes_de_cualquier_chequeo_es_todo_none():
    s = sg.status()
    assert s["emergency_active"] is False
    assert s["last_level"] is None
    assert s["last_checked_at"] is None
    assert s["last_used_pct"] is None


# --- F) Nunca ejecuta DELETE/VACUUM/TRUNCATE/DROP/ALTER -- garantía estructural

def test_modulo_nunca_ejecuta_sql_destructivo():
    fuente = inspect.getsource(sg)
    prohibidas = ("DELETE ", "VACUUM", "TRUNCATE", " DROP ", "ALTER TABLE", "sqlite3.connect")
    encontradas = [p for p in prohibidas if p in fuente.upper()]
    assert not encontradas, f"storage_guard.py no debe contener SQL destructivo ni abrir ninguna DB: {encontradas}"
