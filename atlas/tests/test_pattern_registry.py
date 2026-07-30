"""Prueba manual de PatternRegistry: identidad persistente de patrones.

Verifica el Principio de Conservación del Conocimiento: un patrón nunca se
borra ni se sobrescribe, solo cambia de estado, y si "reaparece" (se vuelve
a registrar con el mismo pattern_key) reutiliza su historial completo en
vez de duplicarse o reiniciarse.

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

TEST_DB_PATH = Path(__file__).resolve().parents[1] / "cache" / "test_pattern_registry.db"

PATTERN_KEY = "gap_alto_rvol_alto_technology"


def test_pattern_registry() -> None:
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()

    registry = PatternRegistry(db_path=TEST_DB_PATH)

    print("=" * 60)
    print("ATLAS - PATTERN REGISTRY")
    print("=" * 60)

    # 1. Registro inicial: nace "En observación".
    pattern = registry.register_pattern(
        pattern_key=PATTERN_KEY,
        name="Gap > 10% + RVOL > 3x en Technology",
        category="combinacion_factores",
        evidence={"sample_size": 8, "win_rate": 0.62},
    )
    assert pattern.state == PATTERN_OBSERVATION
    assert pattern.created_at == pattern.updated_at
    first_created_at = pattern.created_at
    print(f"1. Registrado: {pattern.name} -> estado={pattern.state}")

    # 2. Vuelve a "registrarse" (ej. Research Lab lo redescubre): no debe duplicar.
    same_pattern = registry.register_pattern(
        pattern_key=PATTERN_KEY,
        name="Gap > 10% + RVOL > 3x en Technology",
        category="combinacion_factores",
        evidence={"sample_size": 999},  # no debería sobreescribir nada al re-registrar
    )
    assert same_pattern.id == pattern.id
    assert same_pattern.created_at == first_created_at
    assert same_pattern.evidence["sample_size"] == 8  # el evidence original, intacto
    print("2. Re-registrar el mismo pattern_key NO duplica ni reinicia el historial (OK).")

    # 3. Transiciones de estado, con evidencia acumulada en cada una.
    registry.transition_state(
        PATTERN_KEY, PATTERN_ACTIVE,
        reason="Cumple los 3 criterios de confiabilidad estadística",
        evidence={"sample_size": 42, "win_rate": 0.71},
    )
    registry.transition_state(
        PATTERN_KEY, PATTERN_DECAYING,
        reason="Ventana reciente cae bajo el umbral de win rate",
        evidence={"recent_win_rate": 0.38},
    )
    registry.transition_state(
        PATTERN_KEY, PATTERN_INACTIVE,
        reason="Decadencia sostenida por 3 períodos consecutivos",
    )

    # 4. "Años después" reaparece: se reactiva usando el historial completo.
    reactivated = registry.transition_state(
        PATTERN_KEY, PATTERN_REACTIVATED,
        reason="Nueva evidencia reciente vuelve a cumplir los criterios de confiabilidad",
        evidence={"recent_sample_size": 35, "recent_win_rate": 0.68},
    )
    assert reactivated.state == PATTERN_REACTIVATED
    assert reactivated.created_at == first_created_at  # nunca cambia
    # La evidencia acumulada conserva lo de antes (sample_size original) más lo nuevo:
    assert reactivated.evidence["sample_size"] == 42
    assert reactivated.evidence["recent_win_rate"] == 0.68
    print(f"3-4. Ciclo completo de transiciones: {PATTERN_OBSERVATION} -> {PATTERN_ACTIVE} -> "
          f"{PATTERN_DECAYING} -> {PATTERN_INACTIVE} -> {PATTERN_REACTIVATED}")

    # 5. El historial completo sigue disponible, nada se borró.
    history = registry.get_transition_history(PATTERN_KEY)
    assert len(history) == 5  # creación + 4 transiciones
    states_in_order = [h.to_state for h in history]
    assert states_in_order == [
        PATTERN_OBSERVATION, PATTERN_ACTIVE, PATTERN_DECAYING, PATTERN_INACTIVE, PATTERN_REACTIVATED,
    ]

    print("\n--- HISTORIAL COMPLETO (nunca se borra) ---")
    for entry in history:
        origin = entry.from_state or "(creación)"
        print(f"  {origin:16} -> {entry.to_state:16} | {entry.reason}")

    # 6. list_patterns() filtra por estado/categoría.
    active_patterns = registry.list_patterns(state=PATTERN_REACTIVATED)
    assert len(active_patterns) == 1
    assert active_patterns[0].pattern_key == PATTERN_KEY

    registry.close()

    print(f"\nEstado final: {reactivated.state}")
    print(f"Evidencia acumulada: {reactivated.evidence}")
    print("=" * 60)
    print("OK: PatternRegistry conserva identidad, estado y todo el historial correctamente.")


if __name__ == "__main__":
    test_pattern_registry()
