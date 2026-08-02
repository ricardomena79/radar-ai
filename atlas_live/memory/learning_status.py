"""Estructura preparada para el Learning Engine (fase futura, todavía sin
propuesta de arquitectura aprobada -- decisión del 2026-08-02: "el
aprendizaje continuo... será una nueva fase, con su propia propuesta de
arquitectura y validación").

Este módulo **no calcula nada real todavía**. Expone únicamente el
contrato (forma de los datos) que van a tener los dos indicadores
permanentes de la barra superior de la Cabina del Piloto -- 🧠 Aprendizaje
y 🎯 Confianza de Atlas -- aprobados el 2026-08-02 con la instrucción
explícita de no implementar el cálculo definitivo todavía, solo
incorporar los indicadores en la interfaz y dejar la estructura lista
para que el futuro Learning Comparator la complete.

Por qué el estado es siempre "Observando" y las cantidades son 0 hoy: el
Memory Store vigente (30 días, Atlas Alpha 1.0, congelado) no recibe
observaciones nuevas todavía -- `live_integration.py` alimenta el
Prediction Journal y el Exit Journal, pero no escribe de vuelta en el
Memory Store (hallazgo verificado el 2026-08-02, documentado en
PRIMER_DIA_OPERACION_ATLAS_ALPHA.md, sección 6). Sin una fuente real de
"observaciones nuevas" todavía, días acumulados y progreso son
honestamente cero -- no un valor inventado ni un placeholder decorativo.

Reglas de funcionamiento ya aprobadas para cuando exista el cálculo real
(máquina de estados del ciclo de aprendizaje, requisito de explicabilidad
de la Confianza) -- ver `LEARNING_ENGINE.md` en la raíz del repo. Este
módulo debe seguir esas reglas cuando se implemente; no las repite acá
para no duplicar la fuente de verdad.
"""

from typing import Any, Dict

LEARNING_STATES = ("Observando", "Aprendiendo", "Listo para comparar")


def get_learning_status() -> Dict[str, Any]:
    """Progreso del aprendizaje continuo. **No representa inteligencia ni
    precisión** -- solo cuánta evidencia nueva se acumuló respecto de un
    umbral todavía sin definir (depende del diseño aprobado del Learning
    Engine, que no existe todavía)."""
    return {
        "state": "Observando",
        "progress_pct": None,
        "days_accumulated": 0,
        "new_observations": 0,
        "threshold_days": None,
        "note": (
            "Learning Store no implementado todavía -- estructura preparada "
            "para el Learning Engine (fase futura, con su propia propuesta "
            "de arquitectura y validación). El Memory Store vigente (Atlas "
            "Alpha 1.0, 30 días) no recibe observaciones nuevas por ahora."
        ),
    }


def get_atlas_confidence() -> Dict[str, Any]:
    """Confianza global del sistema. Nunca debe depender de una sola
    operación ni de un solo día -- sin el Learning Comparator (no
    implementado), no hay base para calcular nada, así que se reporta
    explícitamente como no disponible en vez de un número inventado."""
    return {
        "available": False,
        "value_pct": None,
        "state": "No disponible",
        "note": (
            "Se calculará comparando Memory Store (memoria oficial), "
            "Learning Store (evidencia reciente), Prediction Journal y "
            "Exit Journal -- Learning Comparator, no implementado todavía."
        ),
    }
