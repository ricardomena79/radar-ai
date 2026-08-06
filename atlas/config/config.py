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
