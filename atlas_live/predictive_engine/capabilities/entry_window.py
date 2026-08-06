"""Motor Predictivo -- capacidad `entry_window` ("ventana óptima de
entrada"), aprobada 2026-08-06 (ver DECISIONES.md, Fase 1.1).

Pregunta que responde, con evidencia y nunca con un número inventado:
dado un candidato que Radar Explosivo ya marcó `eligible=True`, ¿cuántos
minutos faltan, históricamente, para que comience el movimiento? Dos
hechos distintos, ambos ya derivables de una trayectoria del Exit Journal
(`atlas_live/memory/exit_journal.py`):
  - "señal" = primer muestreo con `eligible=1` (`derive_signal_start`).
  - "movimiento" = primer muestreo con |rendimiento| >= umbral
    (`derive_movement_start`).
La ventana óptima de entrada es la distancia entre esos dos hechos,
agregada (mediana, P25/P75, confianza) sobre muchas trayectorias
históricas agrupadas por `evidence_condition` (decisión explícita del
usuario, 2026-08-05: no dividir todavía por sesión premarket/regular/
after-hours -- reduciría demasiado la muestra).

**Sprint 1 (2026-08-06) -- solo estructura.** `gather_evidence()` y
`compute()` ya tienen la forma final del contrato, pero el algoritmo de
agregación estadística no está implementado todavía: se construye recién
en el Sprint 3, una vez reconstruida la base histórica (Sprint 2). Hasta
entonces, toda predicción responde honestamente `confidence="insuficiente"`
-- nunca un número fabricado para no dejar el campo vacío.
"""

from typing import Any, Dict

from atlas_live.predictive_engine.engine import CapabilityResult

MIN_SAMPLE_SIZE = 5  # umbral mínimo de casos para dar cualquier estimación numérica -- revisado con evidencia real en el Sprint 3


def gather_evidence(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Reúne la evidencia histórica disponible para este candidato. Sprint
    1: placeholder explícito -- todavía no consulta trayectorias
    reconstruidas (no existen hasta el Sprint 2) ni las agrupa por
    `evidence_condition` (algoritmo del Sprint 3). Devuelve una evidencia
    honestamente vacía, no una evidencia falsa."""
    return {"evidence_condition": None, "sample_size": 0, "windows_minutes": []}


class EntryWindowCapability:
    name = "entry_window"

    def compute(self, candidate: Dict[str, Any], evidence: Dict[str, Any]) -> CapabilityResult:
        sample_size = evidence.get("sample_size", 0)
        evidence_condition = evidence.get("evidence_condition")

        if sample_size < MIN_SAMPLE_SIZE:
            return CapabilityResult(
                capability=self.name,
                recommendation=None,
                value=None,
                unit="minutos",
                confidence="insuficiente",
                sample_size=sample_size,
                evidence_condition=evidence_condition,
                explanation=(
                    "Ventana óptima de entrada: sin evidencia histórica suficiente todavía "
                    "(algoritmo de agregación -- mediana, P25/P75 -- pendiente del Sprint 3, "
                    "ver DECISIONES.md)."
                ),
            )

        # Sprint 3 reemplaza este bloque por el cálculo real (mediana, P25,
        # P75, confianza) sobre `evidence["windows_minutes"]`. Con la
        # evidencia siempre vacía del Sprint 1, esta rama nunca se alcanza
        # todavía -- se deja explícita para no fabricar un resultado.
        raise NotImplementedError("Algoritmo de ventana óptima de entrada pendiente (Sprint 3)")
