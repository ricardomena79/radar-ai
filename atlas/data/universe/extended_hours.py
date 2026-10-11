"""Instrumentos de Racional habilitados para operar en horario extendido.

Fuente: `racional_extended_hours.json` (lista pública de Racional, filas
marcadas "Está = Sí"). Solo lectura. Se cruza con el universo operativo
(`racional_universe.json`): un símbolo del listado que ya no esté en ese
universo se ignora, y el universo nunca se modifica desde acá.
"""

import json
from pathlib import Path
from typing import List, Optional, Set

from atlas.data.universe.universe import Asset, load_universe

EXTENDED_HOURS_FILE = Path(__file__).parent / "racional_extended_hours.json"

_symbols_cache: Optional[Set[str]] = None


def load_extended_hours_symbols() -> Set[str]:
    global _symbols_cache
    if _symbols_cache is None:
        with open(EXTENDED_HOURS_FILE, encoding="utf-8") as f:
            _symbols_cache = {s.strip() for s in json.load(f)["symbols"] if s and s.strip()}
    return set(_symbols_cache)


def is_extended_hours(symbol: str) -> bool:
    return symbol in load_extended_hours_symbols()


def get_extended_hours_assets() -> List[Asset]:
    """Instrumentos del universo Racional que están en el listado de
    horario extendido, ordenados por símbolo."""
    ext = load_extended_hours_symbols()
    return sorted((a for s, a in load_universe().items() if s in ext), key=lambda a: a.symbol)
