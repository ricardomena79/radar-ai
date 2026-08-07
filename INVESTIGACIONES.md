# INVESTIGACIONES.md

> **Nota de reconciliación (2026-08-07):** este proyecto se trabajó en dos
> sesiones paralelas de Claude Code sobre la misma rama, con numeraciones de
> investigación independientes que coincidieron en número pero no en tema.
> Este archivo unifica ambas líneas sin perder ningún registro: primero el
> **registro formal** (Investigación 3 = gate de liquidez; Investigación 4 =
> persistencia del conocimiento); después, la **línea paralela "Cabina /
> Proveedores / Failover"** con su propia numeración. Ninguna reemplaza a la
> otra.

Registro oficial de investigaciones formales de Atlas -- metodología establecida 2026-08-06:

```
PLAN → Aprobación → Implementación → Corrección de errores → Validación completa → Informe final → Aprobación o rechazo
```

Una investigación **NO** se cierra cuando termina la implementación. Se cierra únicamente cuando el problema original ha desaparecido y la validación con evidencia real demuestra que el objetivo se cumplió. Hasta entonces permanece **ABIERTA**, aunque el código ya esté escrito.

Este archivo se creó en la sesión del 2026-08-06, junto con el cierre de la Investigación 3 -- no reconstruye investigaciones anteriores no documentadas acá.

---

## Investigación 3 -- Gate de liquidez/RVOL bloqueaba la elegibilidad en premarket

**Estado: CERRADA** (2026-08-06)

**Disparador**: el Motor Predictivo (Fase 1.1, Sprint 3) validado contra 30 días reales daba una mediana de -330 minutos para la condición `gap_pct >= 10%` -- Atlas aprendía que el movimiento ya había pasado 5.5 horas antes de detectar elegibilidad, contradiciendo el objetivo de anticiparse al movimiento en vez de reaccionar tarde.

**Causa raíz**: Yahoo Finance reporta `Volume = 0` en el 100% de las velas de premarket (confirmado con datos reales -- el precio sí se mueve vela a vela). El gate de liquidez (`dollar_volume = price * volume`) y el gate de RVOL (`volume / average_volume`) dependen de ese campo, bloqueando la elegibilidad estructuralmente durante todo el premarket.

**Solución**: sustituto de liquidez basado en `average_volume * price` (dato histórico real del símbolo, mismo umbral ya calibrado, sin inventar uno nuevo), activado únicamente cuando `market_state` es `PRE` o `PREPRE` (los dos estados reales de premarket que Yahoo reporta, confirmados en producción) y el volumen no está reportado. RVOL se omite en ese caso específico, sin sustituto fabricado. Sesión regular/after-hours/closed sin ningún cambio.

**Validación real**:
- 6 estados de `market_state` verificados directamente con datos reales de momentum: `PRE`/`PREPRE` → ahora elegibles; `REGULAR`/`POST`/`POSTPOST`/`CLOSED`/`None` → sin cambio.
- Reconstrucción completa de 30 días con el fix: 638 trayectorias, 89.786 muestras, auditadas día por día contra lo esperado.
- Motor Predictivo re-validado: mediana -330 min → **0 min**, recomendación `comprar_ahora`, n=78 casos reales.
- Suite de tests: 80/80 en verde, sin regresión.

**Hallazgo colateral**: durante la ejecución se descubrió y corrigió un riesgo real de pérdida de datos -- `test_exit_journal.py`/`test_live_integration.py` borraban `exit_journal.db` (el mismo archivo usado por la reconstrucción real) al correrse directamente sin `ATLAS_DATA_DIR` aislado. El incidente ocurrió una vez durante esta investigación (89.786 muestras reconstruidas se perdieron), causa raíz identificada con certeza, corregido (ambos tests ahora redirigen `DB_PATH` a un directorio temporal propio al importarse) y verificado -- no vuelve a ocurrir.

**Archivos modificados**: `atlas_live/explosive_engine.py`, `atlas_live/backtest/historical_scan.py` (solución), `atlas_live/memory/test_exit_journal.py`, `atlas_live/memory/test_live_integration.py` (aislamiento de datos de test).

**Entrada completa**: ver `DECISION_LOG.md`, sección "2026-08-06 -- Investigación 3".

**Aprobación**: aprobada por el usuario 2026-08-06, con corrección intermedia exigida (cobertura de `PREPRE`, no solo `PRE`) antes del cierre final.

---

## Investigación 4 -- Persistencia y sincronización del conocimiento de Atlas

**Estado: CERRADA** (2026-08-07, aprobada por el usuario)

**Disparador**: la auditoría posterior al cierre de la Investigación 3 encontró que la base histórica real (89.786 muestras) nunca llegó a producción -- el objetivo de la Fase 1.1 seguía sin cumplirse para un usuario real, pese a todo el código ya desplegado.

**Decisión de arquitectura**: la fuente oficial de la verdad es la base SQLite persistente de Railway (no la base local, no la reconstrucción automática -- Yahoo Finance solo conserva ~60 días de historial de 5 minutos).

**Diseño de la sincronización**: formato de intercambio JSONL (elegido sobre CSV y Parquet, con justificación técnica -- ver `DECISION_LOG.md`), exportación de solo el delta, importación estrictamente aditiva (solo `INSERT`, nunca sobrescribe una clave existente), repetible e idempotente, y capaz de reconstruir automáticamente la base oficial si se pierde, corriendo el mismo import al arrancar el servidor. El repositorio nunca almacena `.db` -- regla permanente confirmada explícitamente por el usuario durante esta investigación.

**Implementado**: `atlas_live/backtest/export_seed_delta.py`, `atlas_live/backtest/seed_import.py`, endpoint de solo lectura `/api/exit-journal/inventory`, importación conectada al arranque de `server.py`.

**Validado**: 6 pruebas nuevas (`test_seed_sync.py`) más la suite completa del proyecto -- 86/86 en verde. Prueba funcional end-to-end con datos sintéticos superpuestos sobre la base real local (89.786 muestras reales, sin tocarlas), confirmando delta correcto, importación aditiva y reintento idempotente sin duplicados.

**Validación operativa final (producción real)**: primer seed real generado y comiteado (638 pares, 89.786 filas, validado antes de enviarse). Desplegado, importado automáticamente al arrancar el servidor. Verificado en la URL pública: `/api/exit-journal/inventory` reporta 638 pares en producción -- coincide exacto con desarrollo. Sin regresión en `/api/memory-engine`, `/api/mission-control`, `/api/prediction-journal`, `/api/exit-journal`, `/api/ranking`. Mecanismo confirmado idempotente y aditivo tanto en pruebas como en la corrida real. Única salvedad, no un bloqueo: observar una predicción real *logueada* en vivo depende de que el reloj real del mercado entre en sesión premarket/regular -- no de ningún problema del sistema.

**Cierre**: aprobado por el usuario 2026-08-07. No se reabre por mejoras futuras -- un comportamiento distinto al esperado en producción se registra como investigación nueva.

**Entrada completa**: ver `DECISION_LOG.md`, sección "2026-08-06 -- Investigación 4".

---

# Línea de trabajo paralela -- Cabina / Proveedores / Failover (sesión 2026-08-06/07)

> Numeración propia de esta línea, independiente de la de arriba (se conserva
> tal cual se llevó durante la sesión). No se mezcla con el registro formal:
> los números que coinciden (3, 4) se refieren a temas distintos.

## Estado

| # | Investigación | Estado |
|---|---|---|
| 1 | Ramas / Repositorio | ✅ Cerrada |
| 2 | Motor Predictivo | ✅ Cerrada |
| 3 | Yahoo Premarket (mensaje honesto en la Cabina) | ✅ Cerrada |
| 4 | Arquitectura del proveedor (separar precios/fundamentales) | ✅ Cerrada -- conclusión: no separar, no aporta beneficio con la arquitectura actual |
| 5 | WebSocket Yahoo | ✅ Cerrada -- conclusión: descartado por ahora |
| 6 | Radar / Failover (`YFRateLimitError` no activaba Finnhub) | ✅ Cerrada |
| 7 | Cabina -- validación end-to-end post-failover | 🔄 Abierta -- optimización del ciclo Finnhub pendiente (diseño aprobado, sin implementar) |

## Notas por investigación

**3 -- Yahoo Premarket**: cerrada. Cuando Yahoo no reporta `preMarketPrice`, la
fila Premarket de la Cabina muestra "No disponible / Proveedor: Yahoo Finance /
Motivo: no reportó precio de premarket en esta consulta" en vez de un `--`
desnudo. Se validó (opción C, failover a Finnhub para premarket) y se descartó
con evidencia real: Finnhub no entrega premarket, devuelve el precio regular.
Ver `DECISION_LOG.md`.

**4 -- Arquitectura del proveedor**: cerrada. Se auditó si separar "precios" y
"fundamentales" aportaba beneficio. Conclusión con evidencia: no -- Yahoo ya
entrega ambos en una sola llamada `.info`; el cuello de botella real es la
ausencia de caché entre ciclos, no la mezcla de campos.

**5 -- WebSocket Yahoo**: cerrada. El WebSocket de Yahoo soporta streaming real
(probado), pero no entrega fundamentales ni históricos, requiere una
arquitectura push incompatible con el diseño pull actual, y depende de un
endpoint no oficial sin reconexión garantizada. Descartado por ahora.

**6 -- Radar / Failover**: **cerrada** (2026-08-06). Causa raíz: `YFRateLimitError`
de `yfinance` no se convertía en `ProviderError` dentro de
`YahooFinanceProvider.get_quotes()`, así que `MultiProvider` nunca llegaba a
intentar Finnhub -- el ciclo completo de escaneo se caía en vez de activar el
failover. Corregido: nueva `RateLimitError(ProviderError)` en `base.py`,
`get_quotes()` la captura explícitamente (solo `YFRateLimitError`, sin
`except Exception` genérico) y la relanza de inmediato. Validado con 7
pruebas de evidencia real (forzada y natural, en producción, mercado
abierto). Ver `DECISION_LOG.md`.

**7 -- Cabina**: abierta. Se validó en vivo que la Cabina se actualiza por
ciclo, el ranking/precios cambian, el reloj avanza, y Motor Predictivo y Radar
Explosivo funcionan. Se encontró y corrigió un guard faltante en `/api/rescan`
(evitaba dos escaneos concurrentes). Queda pendiente la optimización del ciclo
cuando el failover completo cae a Finnhub (diagnóstico terminado: Finnhub
soporta concurrencia y tiene techo de 60 req/min; diseño de la solución
aprobado, implementación aún no hecha).
