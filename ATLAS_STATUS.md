# ATLAS_STATUS.md

Estado actual del proyecto, snapshot -- no reemplaza `ATLAS_ROADMAP.md` (hoja de ruta completa) ni `DECISION_LOG.md` (historial de decisiones); apunta a ellos para el detalle. Última actualización: 2026-08-07, cierre de la Investigación 4.

---

## Fase 1.1 -- Motor Predictivo de Atlas

Objetivo: que Atlas responda, con evidencia y nunca con un número inventado, qué comprar, cuándo entrar, por qué cree que subirá y qué retorno histórico es razonable esperar.

| Sprint | Contenido | Estado |
|---|---|---|
| 1 | Estructura del Predictive Engine, `prediction_log` append-only, capacidad `entry_window` (estructura) | Desplegado en Railway |
| 2 | Reconstrucción histórica de trayectorias (30 días, `prepost=True`), Exit Journal poblado | **Desplegado y sincronizado en producción** -- 638 trayectorias, 89.786 muestras, confirmado en la URL pública (Investigación 4) |
| 3 | Algoritmo real de ventana óptima (mediana, P25/P75, confianza) | Desplegado en Railway, validado con datos reales (Investigación 3) y ahora con la base histórica real disponible en producción (Investigación 4) |
| 4 | Integración en la Cabina (Dashboard + Oportunidad del día) | Desplegado en Railway |
| 5 | `grading.py` -- calificación automática de predicciones | Desplegado en Railway |

## Investigaciones formales

Tablero con evidencia verificable únicamente -- ningún estado se infiere ni se reconstruye de memoria. Las investigaciones cerradas no se reabren por mejoras futuras; un comportamiento nuevo distinto al esperado se registra como investigación aparte.

| # | Tema | Estado |
|---|---|---|
| 1 | -- | Sin registro disponible |
| 2 | -- | Sin registro disponible |
| 3 | Gate de liquidez/RVOL bloqueaba elegibilidad en premarket (Volume=0 de Yahoo) | **CERRADA** 2026-08-06 |
| 4 | Persistencia y sincronización del conocimiento de Atlas | **CERRADA** 2026-08-07 |
| 5 | -- | Sin registro disponible |
| 6 | -- | Sin registro disponible |
| 7 | -- | Sin registro disponible |

## Despliegue

- **Rama activa**: `claude/setup-atlas-live-env-a5425c`.
- **Último commit desplegado en Railway**: `29c680f` (primer seed real de la Investigación 4), verificado (`Last-Modified` coincide exacto con el timestamp del commit).
- **Base histórica real**: sincronizada en producción -- 638 pares símbolo/día, 89.786 muestras, confirmado en `/api/exit-journal/inventory` sobre la URL pública, coincide exacto con desarrollo.
- **Repositorio libre de archivos `.db`** (regla permanente) -- el intercambio dev↔producción es JSONL, vía `atlas_live/backtest/seeds/`.

## Salud del sistema (verificado en esta sesión)

- Suite completa del proyecto: 86/86 verificaciones en verde.
- Sin regresión en ningún endpoint principal (`/api/memory-engine`, `/api/mission-control`, `/api/prediction-journal`, `/api/exit-journal`, `/api/ranking`), verificado sobre la URL pública tras el despliegue.
- Mecanismo de sincronización (Investigación 4): aditivo e idempotente, verificado en pruebas y en la corrida real contra producción.
- Gate de liquidez/RVOL premarket (Investigación 3): sin regresión en sesión regular/after-hours, verificado byte-idéntico.

## Pendientes conocidos

- Confirmar visualmente en la Cabina, durante una sesión de premarket/regular real, que el Motor Predictivo muestra una recomendación real (no "evidencia insuficiente") -- el mecanismo y los datos ya están listos; falta que el reloj real del mercado entre en esa ventana para observarlo en vivo (no es un bloqueo, ver cierre de la Investigación 4).
- Nivel 2 del Motor Predictivo (señales precursoras antes de la elegibilidad) y la inteligencia de atribución (`attribution.py`) -- arquitectura dejada preparada, explícitamente no implementadas todavía.
- Los 6 paneles MOCK de la Cabina (barra de actividad, Atlas Opina, Alertas, ¿Por qué NO?, ETF, Configuración) -- sin cambios desde el 2026-08-03.
- `learning_status.py` -- estructura honesta sin Learning Engine/Comparator detrás; siempre reporta "Observando"/"No disponible".
- Investigaciones 1, 2, 5, 6, 7 -- sin registro disponible; se documentan cuando se retomen, con evidencia real, no reconstruidas de memoria.
