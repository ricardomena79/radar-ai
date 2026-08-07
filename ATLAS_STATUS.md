# ATLAS_STATUS.md

Estado actual del proyecto, snapshot -- no reemplaza `ATLAS_ROADMAP.md` (hoja de ruta completa) ni `DECISION_LOG.md` (historial de decisiones); apunta a ellos para el detalle. Última actualización: 2026-08-06, cierre de la Investigación 3.

---

## Fase 1.1 -- Motor Predictivo de Atlas

Objetivo: que Atlas responda, con evidencia y nunca con un número inventado, qué comprar, cuándo entrar, por qué cree que subirá y qué retorno histórico es razonable esperar.

| Sprint | Contenido | Estado |
|---|---|---|
| 1 | Estructura del Predictive Engine, `prediction_log` append-only, capacidad `entry_window` (estructura) | Desplegado en Railway |
| 2 | Reconstrucción histórica de trayectorias (30 días, `prepost=True`), Exit Journal poblado | Implementado localmente, reconstrucción validada (30/30 días, 638 trayectorias, 89.786 muestras); **pendiente desplegar** |
| 3 | Algoritmo real de ventana óptima (mediana, P25/P75, confianza) | Implementado y validado con datos reales; **pendiente desplegar** |
| 4 | Integración en la Cabina (Dashboard + Oportunidad del día) | Desplegado en Railway (commit `2e5f9d2`) |
| 5 | `grading.py` -- calificación automática de predicciones | Implementado localmente; **pendiente desplegar** |

## Investigaciones formales

| # | Tema | Estado |
|---|---|---|
| 3 | Gate de liquidez/RVOL bloqueaba elegibilidad en premarket (Volume=0 de Yahoo) | **CERRADA** 2026-08-06 -- ver `INVESTIGACIONES.md` / `DECISION_LOG.md` |

## Despliegue

- **Rama activa**: `claude/setup-atlas-live-env-a5425c`.
- **Último commit desplegado en Railway**: `2e5f9d2` (Sprint 3+4).
- **Pendiente de desplegar en este cierre de la Investigación 3**: Sprint 2 (reconstrucción), Sprint 3 (algoritmo, ya en código desde el commit anterior pero sin la base histórica real detrás), Sprint 5 (grading), y la corrección del gate de liquidez/RVOL premarket (`explosive_engine.py`, `historical_scan.py`).
- **No desplegado**: el archivo `exit_journal.db` con las 89.786 muestras reales no viaja en el commit (convención del proyecto: nunca se commitean `.db`) -- se repuebla en producción corriendo `reconstruct_trajectories.py` contra Railway, o se deja que el Exit Journal en vivo lo acumule orgánicamente día a día.

## Salud del sistema (verificado en esta sesión)

- Suite de tests de `atlas_live/memory/`: 80/80 en verde.
- Regresión en sesión regular/after-hours del gate de liquidez: cero (verificado byte-idéntico).
- Riesgo de pérdida de datos en tests (`test_exit_journal.py`/`test_live_integration.py`): identificado, corregido y verificado -- ambos aíslan su propio `DB_PATH` temporal desde el 2026-08-06.

## Pendientes conocidos

- Desplegar Sprints 2, 3 (base real), 5 y el fix de Investigación 3 a Railway (siguiente paso inmediato tras este documento).
- Repoblar `exit_journal.db` en producción tras el despliegue.
- Nivel 2 del Motor Predictivo (señales precursoras antes de la elegibilidad) y la inteligencia de atribución (`attribution.py`) -- arquitectura dejada preparada, explícitamente no implementadas todavía.
