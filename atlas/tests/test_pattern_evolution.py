"""Prueba manual de Pattern Evolution (Learning Engine).

Registra patrones en distintos estados con evidencia acumulada conocida
(los mismos números fueron verificados a mano contra la fórmula de Wilson
antes de escribir esta prueba), y confirma que PatternEvolution propone
exactamente la transición esperada para cada estado -- y que nunca aplica
el cambio él mismo: el estado en PatternRegistry queda intacto después de
evaluar.

Usa una base SQLite de prueba separada, no la real.
"""

from pathlib import Path

from atlas.knowledge import (
    PATTERN_ACTIVE,
    PATTERN_DECAYING,
    PATTERN_INACTIVE,
    PATTERN_OBSERVATION,
    PATTERN_REACTIVATED,
    PatternRegistry,
)
from atlas.learning import PatternEvolution

TEST_DB = Path(__file__).resolve().parents[1] / "cache" / "test_pattern_evolution.db"


def _seed(registry: PatternRegistry) -> None:
    # P1: En observación, con evidencia sólida -> debería proponerse ACTIVO.
    registry.register_pattern(
        "p1_observacion", "Patrón 1", "combinacion_factores",
        evidence={"sample_size": 50, "win_rate": 0.70, "recent_sample_size": 20,
                  "recent_win_rate": 0.68, "baseline_win_rate": 0.50},
    )

    # P2: Activo, pero la ventana reciente cayó fuerte -> debería proponerse EN_DECADENCIA.
    registry.register_pattern("p2_activo_decae", "Patrón 2", "combinacion_factores",
                               evidence={"win_rate": 0.70})
    registry.transition_state("p2_activo_decae", PATTERN_ACTIVE, reason="setup de prueba",
                               evidence={"recent_win_rate": 0.40, "recent_sample_size": 15, "baseline_win_rate": 0.50})

    # P3: En decadencia, pero con evidencia reciente sólida -> debería proponerse REACTIVADO.
    registry.register_pattern("p3_decadencia_recupera", "Patrón 3", "combinacion_factores",
                               evidence={"sample_size": 60, "win_rate": 0.75})
    registry.transition_state("p3_decadencia_recupera", PATTERN_DECAYING, reason="setup de prueba",
                               evidence={"recent_sample_size": 25, "recent_win_rate": 0.70, "baseline_win_rate": 0.50})

    # P4: En decadencia y sigue sin recuperarse -> debería proponerse INACTIVO.
    registry.register_pattern("p4_decadencia_sostenida", "Patrón 4", "combinacion_factores",
                               evidence={"sample_size": 60, "win_rate": 0.55})
    registry.transition_state("p4_decadencia_sostenida", PATTERN_DECAYING, reason="setup de prueba",
                               evidence={"recent_sample_size": 20, "recent_win_rate": 0.30, "baseline_win_rate": 0.50})

    # P5: Inactivo, sin evidencia suficiente para reactivarse -> debería quedar sin cambio.
    registry.register_pattern("p5_inactivo_sigue", "Patrón 5", "combinacion_factores",
                               evidence={"sample_size": 40, "win_rate": 0.45})
    registry.transition_state("p5_inactivo_sigue", PATTERN_DECAYING, reason="setup de prueba")
    registry.transition_state("p5_inactivo_sigue", PATTERN_INACTIVE, reason="setup de prueba",
                               evidence={"recent_sample_size": 15, "recent_win_rate": 0.40, "baseline_win_rate": 0.50})

    # P6: Activo y sano -> debería quedar sin cambio.
    registry.register_pattern("p6_activo_sano", "Patrón 6", "combinacion_factores",
                               evidence={"win_rate": 0.65})
    registry.transition_state("p6_activo_sano", PATTERN_ACTIVE, reason="setup de prueba",
                               evidence={"recent_win_rate": 0.65, "recent_sample_size": 100, "baseline_win_rate": 0.50})

    # P7: recién descubierto, todavía sin evidencia -> debería quedar sin cambio (insuficiente).
    registry.register_pattern("p7_sin_evidencia", "Patrón 7", "combinacion_factores")


def test_pattern_evolution() -> None:
    if TEST_DB.exists():
        TEST_DB.unlink()

    registry = PatternRegistry(db_path=TEST_DB)
    _seed(registry)
    evolution = PatternEvolution(registry)

    print("=" * 60)
    print("ATLAS - PATTERN EVOLUTION")
    print("=" * 60)

    expected = {
        "p1_observacion": PATTERN_ACTIVE,
        "p2_activo_decae": PATTERN_DECAYING,
        "p3_decadencia_recupera": PATTERN_REACTIVATED,
        "p4_decadencia_sostenida": PATTERN_INACTIVE,
        "p5_inactivo_sigue": None,
        "p6_activo_sano": None,
        "p7_sin_evidencia": None,
    }

    reports = evolution.evaluate_all()
    reports_by_key = {r.pattern_key: r for r in reports}
    assert len(reports) == 7

    for pattern_key, expected_proposal in expected.items():
        report = reports_by_key[pattern_key]
        print(f"\n{pattern_key}  ({report.current_state})")
        print(f"  propuesta: {report.proposed_state}")
        print(f"  motivo: {report.reason}")
        assert report.proposed_state == expected_proposal, (
            f"{pattern_key}: esperaba propuesta={expected_proposal}, obtuve={report.proposed_state}"
        )

    # Lo más importante: PatternEvolution NUNCA debe haber tocado PatternRegistry.
    # El estado real de cada patrón debe seguir siendo el que tenía antes de evaluar.
    unchanged_states = {
        "p1_observacion": PATTERN_OBSERVATION,
        "p2_activo_decae": PATTERN_ACTIVE,
        "p3_decadencia_recupera": PATTERN_DECAYING,
        "p4_decadencia_sostenida": PATTERN_DECAYING,
        "p5_inactivo_sigue": PATTERN_INACTIVE,
        "p6_activo_sano": PATTERN_ACTIVE,
        "p7_sin_evidencia": PATTERN_OBSERVATION,
    }
    for pattern_key, state_before in unchanged_states.items():
        current = registry.get_pattern(pattern_key)
        assert current.state == state_before, (
            f"{pattern_key}: PatternEvolution no debía cambiar el estado, pero pasó de "
            f"{state_before} a {current.state}"
        )

    registry.close()

    print("\n" + "=" * 60)
    print("OK: Pattern Evolution propone las transiciones correctas y nunca las aplica")
    print("    (el estado real en Pattern Store quedó intacto en los 7 patrones).")


if __name__ == "__main__":
    test_pattern_evolution()
