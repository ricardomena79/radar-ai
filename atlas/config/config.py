"""Configuración centralizada de Atlas.

Hoy solo resuelve un punto: dónde vive el directorio de datos persistentes
(las bases SQLite de Mission Control, Memory Engine, Prediction Journal y
Exit Journal). Centralizado acá para que un Volume de Railway sea un
cambio de una sola variable de entorno, no una búsqueda por todo el código.

Diseño deliberadamente conservador (2026-08-06, ver DECISIONES.md): a
diferencia de otras implementaciones de este mismo mecanismo, acá el
default NO es una carpeta compartida nueva -- es la ubicación exacta que
cada módulo ya usa hoy (`Path(__file__).parent`), pasada explícitamente
por quien llama. Sin `ATLAS_DATA_DIR` seteada, el comportamiento es
idéntico, byte a byte, al que existía antes de este módulo -- ningún dato
ya escrito (Mission Control, Memory Engine, Prediction Journal, Exit
Journal) puede quedar "perdido" por apuntar a una carpeta nueva vacía.
Cada store sigue siendo responsable de su propio archivo dentro del
directorio que resulte -- este módulo no abre conexiones ni sabe nada de SQL.
"""

import os
import shutil
from pathlib import Path


def data_dir(default: Path) -> Path:
    """Directorio donde un módulo persiste sus datos (SQLite hoy).

    En Railway, ATLAS_DATA_DIR debe apuntar al mount path de un Volume
    persistente para que sobreviva a los redeploys. Sin esa variable, cae
    a `default` -- la ubicación de siempre para ese módulo en particular
    (cada llamador pasa la suya, típicamente `Path(__file__).parent`).
    """
    raw = os.environ.get("ATLAS_DATA_DIR")
    path = Path(raw) if raw else default
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path(filename: str, default: Path) -> Path:
    """Ruta final de un archivo de base de datos, con migración automática
    de una sola vez (2026-08-06, ver DECISIONES.md).

    Varios de estos `.db` (Memory Engine, Mission Control, los Journals)
    están commiteados en Git con datos reales ya cargados (ej. las 73.123
    observaciones del Memory Engine) en `default` -- la ubicación de
    siempre, al lado de cada módulo. Un Volume de Railway recién creado
    (o cualquier `ATLAS_DATA_DIR` nuevo) arranca vacío: sin esta
    migración, Atlas perdería de vista esos datos reales al redirigir a
    un directorio que nunca los tuvo.

    Condiciones (pedidas explícitamente, 2026-08-06):
      - Solo migra si el destino está vacío (`target_file` no existe).
      - Nunca sobrescribe una base ya existente en el destino -- si el
        Volume ya tiene datos propios (de una corrida real anterior),
        esos nunca se pisan con la copia vieja de Git.
      - No migra nada si `ATLAS_DATA_DIR` no está seteada (mismo
        comportamiento de siempre, sin este mecanismo en juego).
      - Registra en el log cuándo ocurrió la migración, para que quede
        visible en Railway sin tener que adivinar si pasó o no.
    """
    target_dir = data_dir(default)
    target_file = target_dir / filename
    source_file = default / filename

    if target_dir != default and not target_file.exists() and source_file.exists():
        shutil.copy2(source_file, target_file)
        print(f"[atlas.config] Migración automática: {source_file} -> {target_file}")

    return target_file
