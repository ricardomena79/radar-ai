"""Radar de universo completo (CAPA 2, Fase Tradier, 2026-08-14).

Barrido continuo de los ~2.575 símbolos del universo Racional vía Tradier
(Hilo A, `radar_worker.py`) + detección de candidatas con múltiples puertas
independientes (`candidate_gates.py`) + seguimiento desde la primera
detección (`candidate_registry.py`) + análisis de 1 minuto para candidatas
(`intraday_analysis.py`) + evaluación de cierre (`eod_report.py`).

Aditivo: no reemplaza AtlasScore, MomentumScore, DecisionEngine,
explosive_engine, Memory Engine ni signal_tracker -- los alimenta con un
watchlist mejor (todo el universo, no una muestra de 200-250), y agrega
una capa de aprendizaje propia (condiciones en detección -> resultado real
posterior) que hoy no existe en Atlas.
"""
