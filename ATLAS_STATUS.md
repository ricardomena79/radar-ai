# ATLAS_STATUS.md

Estado actual del proyecto, snapshot -- no reemplaza `ATLAS_ROADMAP.md` (hoja de ruta completa) ni `DECISION_LOG.md` (historial de decisiones); apunta a ellos para el detalle. Última actualización: 2026-08-06, cierre de la Investigación 3 y despliegue del commit `2e4d9a9`.

---

## Fase 1.1 -- Motor Predictivo de Atlas

Objetivo: que Atlas responda, con evidencia y nunca con un número inventado, qué comprar, cuándo entrar, por qué cree que subirá y qué retorno histórico es razonable esperar.

| Sprint | Contenido | Estado |
|---|---|---|
| 1 | Estructura del Predictive Engine, `prediction_log` append-only, capacidad `entry_window` (estructura) | Desplegado en Railway |
| 2 | Reconstrucción histórica de trayectorias (30 días, `prepost=True`), Exit Journal poblado | Código desplegado en Railway (commit `2e4d9a9`); reconstrucción validada localmente (30/30 días, 638 trayectorias, 89.786 muestras) -- base de datos real todavía no repoblada en producción, ver "Despliegue" |
| 3 | Algoritmo real de ventana óptima (mediana, P25/P75, confianza) | Desplegado en Railway (commit `2e4d9a9`), validado con datos reales tras la corrección de la Investigación 3 |
| 4 | Integración en la Cabina (Dashboard + Oportunidad del día) | Desplegado en Railway (commit `2e5f9d2`) |
| 5 | `grading.py` -- calificación automática de predicciones | Desplegado en Railway (commit `2e4d9a9`) |

## Investigaciones formales

Tablero con evidencia verificable únicamente -- ningún estado se infiere ni se reconstruye de memoria. Ver criterio acordado 2026-08-06: no reabrir investigaciones cerradas por mejoras futuras; un problema nuevo de premarket se registra como investigación nueva, no como reapertura de la 3.

| # | Tema | Estado |
|---|---|---|
| 1 | -- | Sin registro disponible |
| 2 | -- | Sin registro disponible |
| 3 | Gate de liquidez/RVOL bloqueaba elegibilidad en premarket (Volume=0 de Yahoo) | **CERRADA** 2026-08-06 (aprobada por el usuario) -- ver `INVESTIGACIONES.md` / `DECISION_LOG.md` |
| 4 | -- | Sin registro disponible |
| 5 | -- | Sin registro disponible |
| 6 | -- | Sin registro disponible |
| 7 | -- | Sin registro disponible |

## Despliegue

- **Rama activa**: `claude/setup-atlas-live-env-a5425c`.
- **Último commit desplegado en Railway**: `2e4d9a9` (Investigación 3 + Sprint 5), verificado (`Last-Modified` coincide exacto con el timestamp del commit).
- **No desplegado**: el archivo `exit_journal.db` con las 89.786 muestras reales no viaja en ningún commit (convención del proyecto: nunca se commitean `.db`) -- se repuebla en producción corriendo `reconstruct_trajectories.py` contra Railway, o se deja que el Exit Journal en vivo lo acumule orgánicamente día a día.

## Salud del sistema (verificado en esta sesión)

- Suite de tests de `atlas_live/memory/`: 80/80 en verde.
- Regresión en sesión regular/after-hours del gate de liquidez: cero (verificado byte-idéntico).
- Riesgo de pérdida de datos en tests (`test_exit_journal.py`/`test_live_integration.py`): identificado, corregido y verificado -- ambos aíslan su propio `DB_PATH` temporal desde el 2026-08-06.

## Pendientes conocidos

- Repoblar `exit_journal.db` en producción (Railway) con la base histórica real de 30 días -- todavía no ejecutado contra producción.
- Nivel 2 del Motor Predictivo (señales precursoras antes de la elegibilidad) y la inteligencia de atribución (`attribution.py`) -- arquitectura dejada preparada, explícitamente no implementadas todavía.
- Investigaciones 1, 2, 4, 5, 6, 7 -- sin registro disponible; se documentan cuando se retomen, con evidencia real, no reconstruidas de memoria.
