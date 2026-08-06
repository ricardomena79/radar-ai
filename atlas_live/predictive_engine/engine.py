"""Núcleo del Motor Predictivo -- arquitectura aprobada 2026-08-06 (ver
DECISIONES.md, Fase 1.1).

Decisión explícita del usuario: "No quiero crear cinco motores distintos.
Quiero un único motor predictivo que vaya creciendo con el aprendizaje."
Por eso este módulo no sabe nada de `entry_window` ni de ninguna otra
pregunta concreta -- solo define el contrato común (`Capability`,
`CapabilityResult`) y un registro (`PredictionEngine`) donde cada
capacidad futura (probabilidad de éxito, movimiento esperado, duración,
riesgo, mejor punto de salida -- ninguna construida todavía) se agrega sin
tocar las que ya existen ni esta clase.

Principio explícito del usuario: ninguna predicción se fabrica sin
evidencia. `confidence="insuficiente"` con `sample_size` bajo es una
respuesta honesta, nunca un número inventado para rellenar el hueco.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class CapabilityResult:
    """Sobre (envelope) común a cualquier capacidad del motor, presente y
    futura -- mismo esquema para `entry_window` hoy que para "probabilidad
    de éxito" o "mejor punto de salida" el día que se construyan."""

    capability: str
    recommendation: Optional[str]
    value: Optional[float]
    unit: Optional[str]
    confidence: str  # "alta" | "media" | "baja" | "insuficiente"
    sample_size: int
    range_low: Optional[float] = None
    range_high: Optional[float] = None
    evidence_condition: Optional[str] = None
    explanation: str = ""


@runtime_checkable
class Capability(Protocol):
    """Contrato que debe cumplir cualquier capacidad registrada. `evidence`
    se recibe ya reunida por quien llama (ej. `entry_window.gather_evidence`)
    -- una capacidad nunca construye su propia evidencia desde cero, para
    que el motor pueda, en el futuro, compartir o cachear evidencia entre
    capacidades sin rediseñar el contrato."""

    name: str

    def compute(self, candidate: Dict[str, Any], evidence: Dict[str, Any]) -> CapabilityResult:
        ...


class PredictionEngine:
    """Registro de capacidades. Agregar una capacidad nueva es una llamada
    a `register()` -- nunca requiere modificar esta clase ni las
    capacidades ya registradas."""

    def __init__(self) -> None:
        self._capabilities: Dict[str, Capability] = {}

    def register(self, capability: Capability) -> None:
        self._capabilities[capability.name] = capability

    def compute(self, name: str, candidate: Dict[str, Any], evidence: Dict[str, Any]) -> CapabilityResult:
        capability = self._capabilities.get(name)
        if capability is None:
            raise KeyError(f"Capacidad no registrada en el Motor Predictivo: {name!r}")
        return capability.compute(candidate, evidence)

    def compute_all(self, candidate: Dict[str, Any], evidence_by_capability: Dict[str, Dict[str, Any]]) -> Dict[str, CapabilityResult]:
        return {
            name: cap.compute(candidate, evidence_by_capability.get(name, {}))
            for name, cap in self._capabilities.items()
        }
