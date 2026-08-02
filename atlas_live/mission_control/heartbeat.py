"""Librería de latido (heartbeat) de Mission Control -- Entregable Nº1.

Permite que cualquier proceso de Atlas reporte su propio estado a un
archivo JSON, siguiendo el esquema oficial aprobado en
ATLAS_MISSION_CONTROL.md, sección 2.4. Un proceso instrumentado agrega
tres o cuatro llamadas (`start`, `step` repetido, `finish`/`error`/`cancel`)
en los mismos puntos donde ya tendría un `logger.info(...)` -- no necesita
llevar la cuenta de PID, CPU, memoria, timestamps ni versión de Atlas por
su cuenta, la librería lo hace.

Alcance actualizado (Entregable 2): además del archivo de estado (Entregable
1, sin cambios en ese comportamiento), cada transición de estado REAL
(no cada actualización de progreso dentro del mismo estado) se registra
automáticamente en el Timeline (`timeline.py`) -- el proceso instrumentado
no llama a `timeline.record_event()` directamente, `heartbeat.py` lo hace
por él. Una escritura del latido que repite el mismo estado (ej. progreso
avanzando mientras se sigue "Ejecutándose") actualiza el archivo de estado
pero NO genera un evento nuevo en el Timeline -- evita convertir el
historial en un duplicado de cada tick de progreso.

Todavía NO lee señales de control para pausar/detener (Entregable 8) --
eso viene después, deliberadamente.
"""

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil
from loguru import logger

from atlas_live.mission_control import timeline

# --- Estándar oficial (ATLAS_MISSION_CONTROL.md, sección 2) ---

HEARTBEAT_SCHEMA = "1.0"

VALID_STATES = {
    "Iniciando",
    "Ejecutándose",
    "Pausado",
    "Esperando",
    "Finalizado",
    "Error",
    "Cancelado",
}

VALID_SEVERITIES = {"INFO", "WARNING", "ERROR", "CRITICAL"}

STATUS_DIR = Path(__file__).parent / "status"

# Formato oficial (sección 2.3 del diseño): <ETIQUETA>_<YYYYMMDD>_<HHMM>,
# con <HHMMSS> como alternativa para evitar colisiones en el mismo minuto.
# La ETIQUETA puede tener guiones bajos propios (ej. "VALIDATION_V2") -- el
# patrón no la aísla por nombre, solo exige que el Run ID TERMINE con
# "_YYYYMMDD_" seguido de 4 o 6 dígitos, todo en mayúsculas/dígitos.
_RUN_ID_PATTERN = re.compile(r"^[A-Z0-9_]+_\d{8}_\d{4}(\d{2})?$")

# atlas_live/mission_control/heartbeat.py -> subir 2 niveles llega a la raíz
# del repo. Se resuelve así (no con el cwd del proceso llamante) para que
# `git rev-parse` funcione sin importar desde dónde se lance el proceso.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_version() -> Dict[str, Optional[Any]]:
    """Hash corto del commit vigente + si hay cambios sin confirmar.

    Nunca lanza una excepción -- si git no está disponible por el motivo
    que sea, se reporta como desconocido en vez de tumbar el proceso que
    está siendo monitoreado (el latido nunca debe ser la causa de que algo
    falle)."""
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_REPO_ROOT, stderr=subprocess.DEVNULL, text=True, timeout=5,
        ).strip()
        status_output = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=_REPO_ROOT, stderr=subprocess.DEVNULL, text=True, timeout=5,
        )
        return {"commit": commit, "dirty": bool(status_output.strip())}
    except Exception:
        return {"commit": None, "dirty": None}


def make_run_id(label_tag: str, at: Optional[datetime] = None) -> str:
    """Arma un Run ID con el formato oficial: <ETIQUETA>_<YYYYMMDD>_<HHMM>.

    Si dos procesos con la misma etiqueta arrancan en el mismo minuto,
    quien llama debe pasar `at` con segundos de diferencia o agregar su
    propio sufijo a `label_tag` -- esta función no intenta adivinar
    colisiones, solo aplica el formato."""
    at = at or datetime.now()
    return f"{label_tag.upper()}_{at.strftime('%Y%m%d_%H%M')}"


def _event_type_for_transition(previous_state: Optional[str], new_state: str) -> Optional[str]:
    """Traduce una transición de estado al event_type del Timeline
    correspondiente (ATLAS_MISSION_CONTROL.md, sección 4). Devuelve `None`
    si no hubo una transición real (mismo estado repetido) -- ese caso no
    se registra en el Timeline, solo actualiza el archivo de latido."""
    if previous_state is None:
        return "process_started"
    if new_state == previous_state:
        return None
    if new_state == "Finalizado":
        return "process_completed"
    if new_state == "Error":
        return "process_error"
    if new_state == "Cancelado":
        return "process_stopped"
    if new_state == "Pausado":
        return "process_paused"
    if new_state == "Ejecutándose" and previous_state == "Pausado":
        return "process_resumed"
    return "state_changed"


class Heartbeat:
    """Una instancia por ejecución (por Run ID). No se comparte entre
    procesos -- cada proceso instrumentado crea la suya."""

    def __init__(self, run_id: str, process_type: str, label: str,
                 total: Optional[int] = None, unit: str = "") -> None:
        if not _RUN_ID_PATTERN.match(run_id):
            raise ValueError(
                f"Run ID inválido: {run_id!r}. Formato esperado: "
                f"<ETIQUETA>_<YYYYMMDD>_<HHMM o HHMMSS> "
                f"(ej. VALIDATION_V2_20260802_1530) -- usar make_run_id() para generarlo."
            )
        self.run_id = run_id
        self.process_type = process_type
        self.label = label
        self.total = total
        self.unit = unit

        self._pid = os.getpid()
        self._started_at = _now_iso()
        self._atlas_version = _git_version()
        self._done = 0
        self._previous_state: Optional[str] = None  # None = todavía no hubo ninguna escritura

        STATUS_DIR.mkdir(parents=True, exist_ok=True)
        self._path = STATUS_DIR / f"{self.run_id}.json"

        # Primera lectura de psutil para un mismo Process: siempre da 0.0
        # (no hay "desde la última vez" todavía). Se descarta acá para que
        # las lecturas reales en step() no bloqueen esperando una medición
        # -- psutil.cpu_percent() sin `interval` es no bloqueante siempre
        # que se reutilice el mismo objeto Process entre llamadas.
        self._process = psutil.Process(self._pid)
        self._process.cpu_percent()

    def _write(self, state: str, done: int, severity: str, message: str) -> None:
        if state not in VALID_STATES:
            raise ValueError(f"Estado inválido: {state!r}. Debe ser uno de {sorted(VALID_STATES)}")
        if severity not in VALID_SEVERITIES:
            raise ValueError(f"Severidad inválida: {severity!r}. Debe ser una de {sorted(VALID_SEVERITIES)}")

        data = {
            "heartbeat_schema": HEARTBEAT_SCHEMA,
            "run_id": self.run_id,
            "process_type": self.process_type,
            "label": self.label,
            "state": state,
            "started_at": self._started_at,
            "last_heartbeat": _now_iso(),
            "progress": {"done": done, "total": self.total, "unit": self.unit},
            "pid": self._pid,
            "cpu_percent": self._process.cpu_percent(),
            "memory_mb": round(self._process.memory_info().rss / (1024 * 1024), 1),
            "severity": severity,
            "last_message": message,
            "atlas_version": self._atlas_version,
        }

        # Escritura atómica: se escribe a un archivo temporal y se
        # reemplaza de un solo golpe, para que nadie leyendo el archivo de
        # estado en simultáneo (Mission Control, en entregables futuros)
        # pueda encontrarse un JSON a medio escribir.
        tmp_path = self._path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self._path)

        # Timeline: solo si hubo una transición de estado real (nunca en
        # cada tick de progreso dentro del mismo estado). Reutiliza la
        # MISMA severidad que se acaba de escribir en el archivo de
        # latido -- no es un dato aparte, es la autoevaluación del proceso
        # en el momento de esta llamada.
        event_type = _event_type_for_transition(self._previous_state, state)
        if event_type is not None:
            try:
                timeline.record_event(
                    run_id=self.run_id, process_type=self.process_type, label=self.label,
                    event_type=event_type, severity=severity, message=message,
                    metadata={"done": done, "total": self.total, "unit": self.unit},
                )
            except Exception as exc:
                # El Timeline nunca debe ser la causa de que el proceso
                # monitoreado falle -- mismo criterio que _git_version().
                logger.warning(f"No se pudo registrar evento en el Timeline para {self.run_id}: {exc}")

        self._done = done
        self._previous_state = state

    def start(self, message: str = "Arrancando") -> "Heartbeat":
        self._write(state="Iniciando", done=0, severity="INFO", message=message)
        return self

    def step(self, done: int, message: str = "", severity: str = "INFO",
              state: str = "Ejecutándose") -> None:
        """Actualiza el latido tras completar una unidad de trabajo.
        `severity` es la autoevaluación del propio proceso (sección 2.4) --
        por defecto INFO; el proceso la puede subir si nota algo raro en su
        propia ejecución. No tiene relación con las alertas que la
        Supervisión Inteligente detectaría desde afuera (entregable futuro)."""
        self._write(state=state, done=done, severity=severity, message=message)

    def waiting(self, message: str) -> None:
        """Atajo para el estado 'Esperando' (ej. en pausa de reintento tras
        un rate-limit, o esperando un disparador) sin que cuente como
        progreso nuevo."""
        self._write(state="Esperando", done=self._done, severity="INFO", message=message)

    def finish(self, message: str = "Completado") -> None:
        self._write(state="Finalizado", done=self._done, severity="INFO", message=message)

    def error(self, message: str) -> None:
        self._write(state="Error", done=self._done, severity="ERROR", message=message)

    def milestone(self, message: str, severity: str = "INFO",
                   metadata: Optional[Dict[str, Any]] = None) -> None:
        """Registra un evento puntual en el Timeline (ej. "día 15/30
        alcanzado", "Cambio Nº1 aplicado") sin cambiar el estado del
        proceso ni tocar el archivo de latido -- es un hito, no una
        transición. Parte del catálogo de event_type ya aprobado en el
        diseño (sección 4), no es una funcionalidad nueva."""
        if severity not in VALID_SEVERITIES:
            raise ValueError(f"Severidad inválida: {severity!r}. Debe ser una de {sorted(VALID_SEVERITIES)}")
        try:
            timeline.record_event(
                run_id=self.run_id, process_type=self.process_type, label=self.label,
                event_type="milestone", severity=severity, message=message, metadata=metadata,
            )
        except Exception as exc:
            logger.warning(f"No se pudo registrar el hito en el Timeline para {self.run_id}: {exc}")

    def cancel(self, message: str = "Cancelado por el usuario") -> None:
        self._write(state="Cancelado", done=self._done, severity="INFO", message=message)


def start(run_id: str, process_type: str, label: str,
          total: Optional[int] = None, unit: str = "", message: str = "Arrancando") -> Heartbeat:
    """Punto de entrada principal: crea el latido de una ejecución nueva y
    escribe su primer estado ('Iniciando')."""
    hb = Heartbeat(run_id=run_id, process_type=process_type, label=label, total=total, unit=unit)
    hb.start(message=message)
    return hb


def read_status(run_id: str) -> Optional[Dict[str, Any]]:
    """Lee el archivo de estado de un Run ID, agregando los campos
    derivados que el estándar define pero que nunca se guardan: tiempo
    transcurrido (`elapsed_seconds`) y tiempo estimado restante
    (`eta_seconds`, `None` si no hay `total` conocido o todavía no se
    completó ninguna unidad de trabajo -- nunca un número inventado)."""
    path = STATUS_DIR / f"{run_id}.json"
    if not path.exists():
        return None

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    started = datetime.fromisoformat(data["started_at"])
    now = datetime.now(timezone.utc)
    elapsed_seconds = (now - started).total_seconds()
    data["elapsed_seconds"] = round(elapsed_seconds, 1)

    progress = data.get("progress") or {}
    done, total = progress.get("done"), progress.get("total")
    if total is not None and done:
        data["eta_seconds"] = round(elapsed_seconds / done * (total - done), 1)
    else:
        data["eta_seconds"] = None

    return data


def list_active_processes() -> List[Dict[str, Any]]:
    """Todos los Run IDs con archivo de estado (Cabina del Piloto, Panel
    12) -- recorre `STATUS_DIR` y reusa `read_status` por cada uno, para
    que los campos derivados (elapsed_seconds/eta_seconds) salgan
    calculados igual que en una lectura individual. Solo lectura; nunca
    modifica ningún archivo. Si `STATUS_DIR` no existe todavía (ningún
    proceso instrumentado corrió nunca en esta máquina), devuelve []."""
    if not STATUS_DIR.exists():
        return []
    procesos = []
    for path in sorted(STATUS_DIR.glob("*.json")):
        estado = read_status(path.stem)
        if estado is not None:
            procesos.append(estado)
    return procesos
