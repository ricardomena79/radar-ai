"""Registro de versiones de los motores de Atlas.

No modifica ningún motor existente: las versiones se declaran acá, de
forma centralizada, y se adjuntan como metadata al guardar un evento o una
predicción. Esto permite comparar resultados históricos cuando alguno de
los algoritmos cambie -- por ejemplo, saber que una decisión de hace tres
meses la tomó un Decision Engine "1.0" con reglas distintas a las de hoy.

Cuando cambie la lógica de un motor de forma perceptible (nuevos umbrales,
nuevos factores, nueva forma de combinar puntajes), su versión debería
subir aquí. No hay automatismo: es responsabilidad de quien modifica el
motor recordar actualizar su versión.
"""

import json
from typing import Dict

ATLAS_CORE = "atlas_core"
MOMENTUM_ENGINE = "momentum_engine"
MONEY_FLOW_ENGINE = "money_flow_engine"
DECISION_ENGINE = "decision_engine"
KNOWLEDGE_ENGINE = "knowledge_engine"

CURRENT_ENGINE_VERSIONS: Dict[str, str] = {
    ATLAS_CORE: "1.0",
    MOMENTUM_ENGINE: "1.0",
    MONEY_FLOW_ENGINE: "1.0",
    DECISION_ENGINE: "1.0",
    KNOWLEDGE_ENGINE: "1.0",
}


def current_versions_json() -> str:
    """Serializa las versiones actuales de los motores, listas para guardar en la base."""
    return json.dumps(CURRENT_ENGINE_VERSIONS, ensure_ascii=False, sort_keys=True)


def parse_versions_json(raw: str) -> Dict[str, str]:
    """Convierte el JSON guardado en la base de vuelta a un diccionario de versiones."""
    if not raw:
        return {}
    return json.loads(raw)
