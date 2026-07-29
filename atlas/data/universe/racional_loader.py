"""Carga del universo de instrumentos de Racional desde el snapshot local (racional_universe.json).

racional_universe.json fue generado una única vez a partir del PDF oficial
"Stocks disponibles en Racional Stocks [público]" (55 páginas, sin consultar
Internet). Este módulo solo lee ese snapshot; no vuelve a parsear el PDF en
tiempo de ejecución.
"""

import json
from pathlib import Path
from typing import Dict, List

_DATA_FILE = Path(__file__).parent / "racional_universe.json"


def load_assets() -> List[Dict[str, str]]:
    """Devuelve los registros crudos (symbol, name, type) del snapshot de Racional."""
    with _DATA_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)
