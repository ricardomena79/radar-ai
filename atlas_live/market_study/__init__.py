"""Estudio amplio del mercado (2026-08-10).

MERCADO = fuente de aprendizaje · RACIONAL = filtro de operabilidad.

Acumula evidencia histórica de explosiones (+30/+50/+100/+150/+200%) sobre un
universo AMPLIO de acciones US (no solo el universo de Racional), como un job
BATCH/OFFLINE con checkpointing -- separado del scanner operativo 24/7 para no
competir por recursos ni tumbar Atlas. Reutiliza la misma infraestructura
(config.db_path, yfinance) pero en su propia base `market_study.db`.

Separación física, igual que signal_registry (cero leakage):
  explosion_features = lo conocido ANTES/EN la detección (gap de apertura,
                       volumen previo, market cap, disponibilidad en Racional).
  explosion_outcome  = el RESULTADO (máximo intradía, bandas alcanzadas) --
                       nunca una feature de la señal.
"""
