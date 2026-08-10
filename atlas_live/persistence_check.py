"""Chequeo de persistencia de los datos de aprendizaje (2026-08-10).

Motivo: el 2026-08-10 se registraron 166 señales reales durante la sesión y un
redeploy las dejó en 0 porque `signal_registry.db` estaba en almacenamiento
EFÍMERO del contenedor (ATLAS_DATA_DIR no apuntaba a un Volume). Atlas NO puede
perder nunca las señales ya registradas.

Este módulo no adivina si un path es un Volume (no hay API portable para eso):
lo verifica EMPÍRICAMENTE con un "canario". En cada arranque escribe/actualiza
`persistence_canary.json` en el MISMO directorio donde vive la base de señales,
llevando la cuenta de arranques. Si el canario de un arranque anterior sigue
ahí, el almacenamiento sobrevivió a un reinicio -> persistencia PROBADA con
evidencia real (no una afirmación). Si el directorio aparece vacío en cada
arranque, es efímero.

Además exige `ATLAS_DATA_DIR` en producción y, con
`ATLAS_REQUIRE_PERSISTENCE=true`, ABORTA el arranque si la persistencia no está
bien configurada -- para NO continuar como si todo estuviera bien.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

_CANARY_FILE = "persistence_canary.json"
_log = logging.getLogger("atlas.persistence")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _signals_dir() -> Path:
    """Directorio real donde vive signal_registry.db (co-ubica el canario ahí,
    para que sobrevivir el canario implique sobrevivir las señales)."""
    from atlas_live.signals import signal_registry
    return Path(signal_registry.DB_PATH).resolve().parent


def status() -> Dict[str, Any]:
    """Estado de persistencia con evidencia. Efecto lateral seguro: actualiza
    el canario (cuenta de arranques). No toca ninguna base de datos real."""
    env_dir = os.environ.get("ATLAS_DATA_DIR")
    require = os.environ.get("ATLAS_REQUIRE_PERSISTENCE", "").strip().lower() in ("1", "true", "yes", "on")

    data_dir = _signals_dir()
    canary_path = data_dir / _CANARY_FILE

    prev: Dict[str, Any] = {}
    if canary_path.exists():
        try:
            prev = json.loads(canary_path.read_text(encoding="utf-8"))
        except Exception:
            prev = {}

    boots = int(prev.get("boots", 0)) + 1
    record = {
        "boots": boots,
        "first_boot_at": prev.get("first_boot_at") or _now(),
        "last_boot_at": _now(),
    }

    writable = True
    write_error = None
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        canary_path.write_text(json.dumps(record), encoding="utf-8")
    except Exception as exc:  # almacenamiento no escribible
        writable = False
        write_error = f"{type(exc).__name__}: {exc}"

    # Persistencia PROBADA solo si había un canario previo y ahora sobrevivió
    # (boots > 1). Con boots == 1 está configurado pero aún sin prueba.
    survived_restart = bool(prev) and boots > 1
    critical = (not env_dir) or (not writable)

    if critical:
        level = "CRITICAL"
    elif survived_restart:
        level = "OK"
    else:
        level = "PENDING_PROOF"

    messages = {
        "CRITICAL": ("PERSISTENCIA MAL CONFIGURADA: los datos de aprendizaje se "
                     "PERDERÁN en el próximo redeploy. " +
                     ("ATLAS_DATA_DIR no está seteada. " if not env_dir else "") +
                     ("Directorio de datos no escribible. " if not writable else "")),
        "PENDING_PROOF": ("Persistencia configurada (ATLAS_DATA_DIR presente y "
                          "escribible), pero AÚN NO probada contra un reinicio. "
                          "Se confirma tras el próximo redeploy (boots > 1)."),
        "OK": (f"Persistencia PROBADA: el almacenamiento sobrevivió {boots - 1} "
               f"reinicio(s) reales (canario desde {record['first_boot_at']})."),
    }

    return {
        "level": level,
        "critical": critical,
        "require_persistence": require,
        "atlas_data_dir_set": bool(env_dir),
        "atlas_data_dir": env_dir,
        "data_directory": str(data_dir),
        "signals_db": str(_signals_dir() / "signal_registry.db"),
        "writable": writable,
        "write_error": write_error,
        "boots_recorded": boots,
        "survived_at_least_one_restart": survived_restart,
        "first_boot_at": record["first_boot_at"],
        "last_boot_at": record["last_boot_at"],
        "message": messages[level],
    }


def enforce() -> Dict[str, Any]:
    """Se llama UNA vez al arrancar el server. Loguea el estado y, si la
    persistencia es crítica y ATLAS_REQUIRE_PERSISTENCE=true, LEVANTA para NO
    arrancar como si todo estuviera bien. Devuelve el estado para exponerlo."""
    st = status()
    if st["critical"]:
        _log.critical("ATLAS PERSISTENCIA -> %s | %s", st["level"], st["message"])
        if st["require_persistence"]:
            raise RuntimeError(
                "ARRANQUE ABORTADO: " + st["message"] +
                " Configurá ATLAS_DATA_DIR a un Volume persistente de Railway. "
                "(Para arrancar igual, quitá ATLAS_REQUIRE_PERSISTENCE.)"
            )
    elif st["level"] == "PENDING_PROOF":
        _log.warning("ATLAS PERSISTENCIA -> PENDING_PROOF | %s", st["message"])
    else:
        _log.info("ATLAS PERSISTENCIA -> OK | %s", st["message"])
    return st
