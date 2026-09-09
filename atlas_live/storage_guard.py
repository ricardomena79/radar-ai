"""Kill-switch automático de emergencia por espacio en disco (2026-09-09,
autorizado explícitamente -- "airbag" antes de la prueba de premarket, ver
`.claude/plans/ethereal-mixing-anchor.md`).

Objetivo único: dar a los PRODUCTORES de escritura de `/data` (hoy: el
hilo Shadow del Detector Unificado, `unified_detector.py`) una forma
barata y de solo lectura de preguntar "¿estoy en zona de emergencia de
espacio ahora mismo?" justo antes de escribir -- nunca antes de leer o
evaluar. Este módulo NUNCA decide nada sobre `candidate_gates.py`,
`priority_classifier.py`, H3-H6, la expansión 7.725, ni ninguna regla de
aprendizaje -- solo mide disco y expone un semáforo.

Reutiliza la MISMA fuente de verdad que ya usa `capacity_monitor.py`
(`atlas_live.data_dir_diagnostics.disk_usage_info()`, sobre
`atlas.config.config.data_dir()`) -- nunca una medición nueva ni
distinta. Deliberadamente NO llama a `capacity_monitor.capacity_report()`
(evita su escritura de snapshot -- aunque ya esté throttleada a 30 min --
para que este módulo tenga cero dependencia de esa base de datos: el
kill-switch no puede depender de código que a su vez podría fallar por
falta de espacio).

Histéresis (pedido explícito: "evitar loops de stop/restart"): una vez
que el uso cruza `EMERGENCY_THRESHOLD_PCT` (90%) hacia arriba, el estado
queda "en emergencia" hasta que el uso baje por debajo de
`RESUME_THRESHOLD_PCT` (87% -- banda de 3 puntos, ~270 MB en un volumen
de 9 GB) -- así una sola escritura que empuje el % de 89,9 a 90,1 no
puede producir un ciclo de apagar/prender en cada chequeo sucesivo.

Estado en memoria PURO (variables de módulo + `threading.Lock`) -- nunca
persistido en disco, a propósito: sería absurdo que el mecanismo que
protege contra escrituras excesivas agregue una escritura nueva. Esto
significa que el estado se resetea si el proceso reinicia (aceptable: en
el peor caso, el primer chequeo tras un reinicio vuelve a medir el disco
real y decide de nuevo desde cero, nunca "recuerda" una emergencia vieja
incorrectamente).

Concurrencia: el `threading.Lock()` protege contra llamadas concurrentes
de HILOS dentro del MISMO proceso -- suficiente hoy, porque este servicio
corre con un solo worker de Gunicorn (`--workers 1`, ya documentado en
sesiones anteriores). Si en el futuro hubiera más de un worker (procesos
separados), cada uno mediría el mismo disco compartido de forma
independiente y llegaría al mismo resultado práctico (con, como mucho,
`checked_at` desalineado entre ellos por el intervalo entre sus propios
chequeos) -- no hay coordinación entre procesos acá, declarado
explícitamente en vez de asumido."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from atlas.config.config import data_dir
from atlas_live.data_dir_diagnostics import disk_usage_info

# Umbrales (pedido explícito, distintos a propósito de los de
# `capacity_monitor.py` -- esos son de PRESENTACIÓN/proyección
# [80/90], estos son de ACCIÓN automática [85/90], documentados acá,
# nunca dispersos).
WARNING_THRESHOLD_PCT = 85.0
EMERGENCY_THRESHOLD_PCT = 90.0
RESUME_THRESHOLD_PCT = 87.0

LEVELS = ("OK", "WARNING", "EMERGENCY")

_lock = threading.Lock()
_emergency_active = False
_last_level: Optional[str] = None
_last_checked_at: Optional[str] = None
_last_used_pct: Optional[float] = None
_last_transition_at: Optional[str] = None
_last_transition_reason: Optional[str] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _current_used_pct() -> Optional[float]:
    """`None` explícito si el disco no se puede medir -- nunca se asume
    un porcentaje. Reutiliza `disk_usage_info()` tal cual, sin duplicar
    la llamada a `shutil.disk_usage()`."""
    info = disk_usage_info(data_dir(Path(__file__).parent))
    total = info.get("total_bytes")
    used = info.get("used_bytes")
    if not total or used is None:
        return None
    return round(100.0 * used / total, 4)


def check_and_get_level() -> str:
    """Única función que los productores deben llamar antes de escribir
    -- mide el disco AHORA y actualiza el estado de histéresis. Devuelve
    uno de `LEVELS`. Segura ante llamadas concurrentes (lock).

    Fail-safe explícito: si el disco no se puede medir (`None`), NUNCA se
    activa una emergencia nueva sin evidencia -- pero si ya había una
    emergencia activa, se mantiene (no se "recupera" a ciegas sin poder
    confirmar que el espacio realmente bajó)."""
    global _emergency_active, _last_level, _last_checked_at, _last_used_pct
    global _last_transition_at, _last_transition_reason

    used_pct = _current_used_pct()
    now = _now()

    with _lock:
        _last_checked_at = now
        _last_used_pct = used_pct

        if used_pct is None:
            level = "EMERGENCY" if _emergency_active else "OK"
            _last_level = level
            return level

        if _emergency_active:
            if used_pct < RESUME_THRESHOLD_PCT:
                _emergency_active = False
                _last_transition_at = now
                _last_transition_reason = (
                    f"RESUME: used_pct={used_pct} < RESUME_THRESHOLD_PCT={RESUME_THRESHOLD_PCT}"
                )
                print(f"[STORAGE_GUARD] {_last_transition_reason}", flush=True)
            level = "EMERGENCY" if _emergency_active else "OK"
        else:
            if used_pct >= EMERGENCY_THRESHOLD_PCT:
                _emergency_active = True
                _last_transition_at = now
                _last_transition_reason = (
                    f"EMERGENCY: used_pct={used_pct} >= EMERGENCY_THRESHOLD_PCT={EMERGENCY_THRESHOLD_PCT}"
                )
                print(f"[STORAGE_GUARD] {_last_transition_reason}", flush=True)
                level = "EMERGENCY"
            elif used_pct >= WARNING_THRESHOLD_PCT:
                level = "WARNING"
            else:
                level = "OK"

        _last_level = level
        return level


def is_emergency_active() -> bool:
    """Lectura barata del estado YA calculado por la última
    `check_and_get_level()` -- no mide el disco de nuevo."""
    with _lock:
        return _emergency_active


def status() -> Dict[str, Any]:
    """Snapshot completo de solo lectura -- para un endpoint de
    diagnóstico público. Nunca escribe nada, nunca mide el disco (usa lo
    ya calculado por la última `check_and_get_level()` real)."""
    with _lock:
        return {
            "emergency_active": _emergency_active,
            "last_level": _last_level,
            "last_checked_at": _last_checked_at,
            "last_used_pct": _last_used_pct,
            "last_transition_at": _last_transition_at,
            "last_transition_reason": _last_transition_reason,
            "warning_threshold_pct": WARNING_THRESHOLD_PCT,
            "emergency_threshold_pct": EMERGENCY_THRESHOLD_PCT,
            "resume_threshold_pct": RESUME_THRESHOLD_PCT,
        }
