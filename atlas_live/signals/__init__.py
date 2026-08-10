"""Registro de Señales de Atlas -- validación en vivo (2026-08-09).

Consume los resultados del scanner EXISTENTE (no crea un segundo scanner) para
registrar oportunidades reales de premarket/apertura, seguir su trayectoria y,
al cerrar, medir el resultado. Detección y resultado viven separados: cero
data leakage. Ver signal_registry.py (esquema) y signal_tracker.py (flujo).
"""
