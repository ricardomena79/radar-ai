"""Prueba manual de Calibration Manager.

Verifica dos flujos completos:
1. Una recomendación sobre un motor (ENGINE_CALIBRATION): se versiona, se
   revisa, se aprueba, se rechaza en otro caso, y se registra el resultado
   -- sin que Calibration Manager edite ningún código de motor (eso sigue
   siendo humano).
2. Una recomendación sobre un patrón (PATTERN_STATE_CHANGE): al llegar a
   "Implementada", Calibration Manager es quien aplica el cambio de estado
   real en Pattern Store -- la única puerta de entrada para modificar
   conocimiento permanente.

Usa bases SQLite de prueba separadas, no las reales.
"""

from pathlib import Path

from atlas.calibration_manager import (
    APPROVED,
    ENGINE_CALIBRATION,
    IMPLEMENTED,
    PATTERN_STATE_CHANGE,
    PENDING,
    REJECTED,
    REVIEWED,
    CalibrationManager,
)
from atlas.knowledge import PATTERN_ACTIVE, PATTERN_OBSERVATION, PatternRegistry

CALIBRATION_TEST_DB = Path(__file__).resolve().parents[1] / "cache" / "test_calibration_manager.db"
PATTERN_TEST_DB = Path(__file__).resolve().parents[1] / "cache" / "test_calibration_patterns.db"


def test_engine_calibration_flow() -> None:
    if CALIBRATION_TEST_DB.exists():
        CALIBRATION_TEST_DB.unlink()

    manager = CalibrationManager(db_path=CALIBRATION_TEST_DB)

    print("=" * 60)
    print("ATLAS - CALIBRATION MANAGER: recomendación sobre un motor")
    print("=" * 60)

    key = "momentum_engine.rvol_weight"
    rec = manager.submit_recommendation(
        recommendation_key=key,
        category=ENGINE_CALIBRATION,
        target="momentum_engine.WEIGHTS.relative_volume",
        proposed_by="Learning Engine / Calibration Advisor",
        title="Subir el peso de relative_volume de 0.20 a 0.25",
        description="En 340 casos, RVOL>3 se asoció a un 40% más de ganancia promedio que con el peso actual.",
        evidence={"sample_size": 340, "avg_gain_lift_pct": 40.0},
        sample_size=340,
        expected_improvement="Win rate esperado +5 a +8 puntos porcentuales",
        risks="Podría sobreponderar RVOL en mercados de baja liquidez general",
    )
    assert rec.version == 1
    assert rec.status == PENDING
    print(f"1. Creada v{rec.version}: '{rec.title}' -> estado={rec.status}")

    # Se revisa y se rechaza esta primera versión.
    manager.review(key, notes="Revisada por el operador el 2026-07-31")
    rejected = manager.reject(key, notes="Riesgo de sobreponderar RVOL no está suficientemente acotado")
    assert rejected.status == REJECTED
    print(f"2. v{rejected.version} rechazada: {rejected.status}")

    # Se resubmite con más evidencia -> nueva versión, la anterior queda intacta.
    rec_v2 = manager.submit_recommendation(
        recommendation_key=key,
        category=ENGINE_CALIBRATION,
        target="momentum_engine.WEIGHTS.relative_volume",
        proposed_by="Learning Engine / Calibration Advisor",
        title="Subir el peso de relative_volume de 0.20 a 0.23 (acotado)",
        description="Versión acotada tras el rechazo anterior, con tope de +0.03.",
        evidence={"sample_size": 512, "avg_gain_lift_pct": 34.0},
        sample_size=512,
        expected_improvement="Win rate esperado +3 a +5 puntos porcentuales",
        risks="Riesgo reducido respecto a la propuesta anterior",
    )
    assert rec_v2.version == 2
    assert rec_v2.status == PENDING  # una versión nueva vuelve a empezar el ciclo

    manager.review(key)
    approved = manager.approve(key, notes="Aprobada para aplicar manualmente en score_engine.py")
    assert approved.status == APPROVED
    assert approved.version == 2

    # Acá, en la vida real, un humano edita momentum_engine.py a mano.
    implemented = manager.implement(
        key, result={"win_rate_before": 0.51, "win_rate_after": 0.55}, notes="Aplicado el 2026-08-01"
    )
    assert implemented.status == IMPLEMENTED
    print(f"3. v{implemented.version} implementada: {implemented.status}")

    all_versions = manager.get_all_versions(key)
    assert len(all_versions) == 2
    assert all_versions[0].status == REJECTED  # v1 sigue existiendo, sin tocar
    assert all_versions[1].status == IMPLEMENTED  # v2

    history = manager.get_status_history(key)
    print(f"\n--- HISTORIAL COMPLETO ({len(history)} entradas, nunca se borran) ---")
    for entry in history:
        origin = entry.from_status or "(creación)"
        print(f"  v{entry.version} | {origin:12} -> {entry.to_status:12} | {entry.notes or ''}")

    manager.close()
    print("\nOK: la v1 rechazada sigue intacta; la v2 fue la que se aprobó e implementó.")


def test_pattern_state_change_flow() -> None:
    if CALIBRATION_TEST_DB.exists():
        CALIBRATION_TEST_DB.unlink()
    if PATTERN_TEST_DB.exists():
        PATTERN_TEST_DB.unlink()

    pattern_registry = PatternRegistry(db_path=PATTERN_TEST_DB)
    manager = CalibrationManager(db_path=CALIBRATION_TEST_DB, pattern_registry=pattern_registry)

    print("\n" + "=" * 60)
    print("ATLAS - CALIBRATION MANAGER: recomendación sobre un patrón")
    print("=" * 60)

    pattern_key = "gap_alto_rvol_alto_energy"
    pattern = pattern_registry.register_pattern(
        pattern_key=pattern_key,
        name="Gap > 8% + RVOL > 2.5x en Energy",
        category="combinacion_factores",
        evidence={"sample_size": 12},
    )
    assert pattern.state == PATTERN_OBSERVATION
    print(f"Patrón inicial: {pattern.name} -> estado={pattern.state}")

    key = f"pattern.{pattern_key}.activate"
    rec = manager.submit_recommendation(
        recommendation_key=key,
        category=PATTERN_STATE_CHANGE,
        target=pattern_key,
        proposed_by="Learning Engine / Pattern Evolution",
        title="Activar patrón: cumple los 3 criterios de confiabilidad",
        description="Muestra suficiente, significancia estadística y consistencia temporal confirmadas.",
        evidence={"sample_size": 45, "win_rate": 0.69},
        sample_size=45,
        expected_improvement="Habilita el patrón para futuras comparaciones de Research Lab",
        risks="Ninguno relevante detectado",
        proposed_new_state=PATTERN_ACTIVE,
    )
    assert rec.status == PENDING

    manager.review(key)
    manager.approve(key, notes="Evidencia sólida, aprobado")
    implemented = manager.implement(key, result={"applied": True})
    assert implemented.status == IMPLEMENTED
    print(f"Recomendación implementada: {implemented.title}")

    # El cambio real en Pattern Store lo aplicó Calibration Manager, no el test.
    updated_pattern = pattern_registry.get_pattern(pattern_key)
    assert updated_pattern.state == PATTERN_ACTIVE
    print(f"Estado del patrón después de implementar: {updated_pattern.state}")

    pattern_history = pattern_registry.get_transition_history(pattern_key)
    assert len(pattern_history) == 2  # creación + esta transición
    assert "Calibration Manager aplicó" in pattern_history[-1].reason
    print(f"Motivo de la transición en Pattern Store: {pattern_history[-1].reason}")

    manager.close()
    pattern_registry.close()

    print("\nOK: Calibration Manager aplicó el cambio de estado; Pattern Store quedó actualizado y trazable.")


if __name__ == "__main__":
    test_engine_calibration_flow()
    test_pattern_state_change_flow()
    print("\n" + "=" * 60)
    print("OK: Calibration Manager funciona correctamente en ambos flujos.")
