# ATLAS_ROADMAP.md

> **⚠️ Nota de vigencia (Hito 5, Fase 5.4, 2026-09-04)**: este documento no se actualiza desde antes del 2026-08-17 -- no refleja Hito 3 (Fases 3.0-3.6, sistema de aprendizaje-seguro: elegibilidad, activación controlada, evaluación continua/revocación, cerrado y auditado) ni Hito 4 (Fases 4.1-4.4, observabilidad/paneles, cerrado y auditado -- localmente, sin commit/push/deploy todavía, a diferencia de Hito 3 que sí está commiteado y pusheado a esta rama). Para el estado real, ver el código y `.claude/plans/ethereal-mixing-anchor.md`.

Plan maestro del proyecto. Sujeto a [ATLAS_CONSTITUTION.md](ATLAS_CONSTITUTION.md): ninguna fase de este roadmap puede justificarse si no responde a la pregunta oficial ("¿este cambio mejora la capacidad de Atlas para detectar antes las acciones explosivas?").

Estado registrado al 2026-08-01, verificado contra el código real del repositorio (no aspiracional).

---

## PROGRESO GENERAL DEL PROYECTO

**≈ 63%** -- promedio simple (no ponderado) del % estimado de cada una de las 11 fases de abajo (se agregó la fase 11, Memory Engine). Es una estimación de juicio basada en el texto de "Estado" de cada fase, no una métrica calculada automáticamente -- se actualiza a mano cada vez que el estado de una fase cambia. Última actualización: Atlas Alpha 1.0 (Memory Engine) congelado como baseline (2026-08-02).

| Fase | % estimado |
|---|---|
| 1. Arquitectura | 100% |
| 2. Radar Explosivo | 75% (implementado y medido con evidencia completa de 30 días; Cambio Nº1 ya implementado sobre esta base, en validación -- ver fase 5) |
| 3. Validación histórica | **100% (completada)** -- 30/30 días, ver [VALIDATION_RESULTS.md](VALIDATION_RESULTS.md) |
| 4. Explosive DNA | 95% (perfil estadístico completo sobre las 600 observaciones del período completo, documentado en [RADAR_EXPLOSIVO_V2.md](RADAR_EXPLOSIVO_V2.md)) |
| 5. Optimización del Radar | 45% (diseño técnico confirmado con evidencia final; Cambio Nº1 -- RVOL -- ya implementado y en validación V2, pendiente de comparación V1 vs V2 antes de aceptarlo formalmente) |
| 6. Cambio de proveedor de datos | 30% (evaluación comparativa completa; sin decisión ni implementación) |
| 7. Alertas en tiempo real | 100% (alcance aprobado: navegador, sonido, resaltado visual, arquitectura modular) |
| 8. Dashboard profesional | 35% (MVP funcional en uso; falta la versión "profesional" definitiva) |
| 9. IA de apoyo a decisiones | 0% (sin trabajo iniciado) |
| 10. Mission Control | 45% (Entregables 1 -- heartbeat -- y 2 -- Timeline -- implementados y probados; Entregable 2 pendiente de aprobación final; diseño del Entregable 3 en revisión; ver [ATLAS_MISSION_CONTROL.md](ATLAS_MISSION_CONTROL.md)) |
| 11. Memory Engine | 70% (**Atlas Alpha 1.0 congelado** -- Memory Store, Clasificador, backfill, tasas base, propuestas, Ranking Score, Prediction Journal, integración en tiempo real y Modo Interactivo Continuo, todo implementado y probado; pendiente: validación agregada de 30 días, checkpoints intermedios, y la primera prueba en condiciones de mercado reales; ver [MEMORY_ENGINE.md](MEMORY_ENGINE.md)) |

---

## 1. Arquitectura

**Objetivo**: separar Atlas Core (el "cerebro", congelado en v1.0) de las capas experimentales/de presentación, para que ningún desarrollo nuevo pueda romper lo ya validado.

**Estado**: Completada (v1.0 del Core; la capa `atlas_live` sigue activa y en evolución).

**Dependencias**: ninguna (es la base de todo lo demás).

**Criterios de éxito**:
- `atlas/` contiene Data Collector, los 5 motores (Atlas Score, Momentum Engine, Money Flow Engine, Market Context Engine, Decision Engine), Knowledge Base y Learning Engine (8 etapas) — confirmado por el historial de commits (`git log`, fase 6 a Learning Engine Etapa 8, 2026-07-29/30).
- `atlas_live/` consume Atlas Core sin modificarlo (verificado en cada iteración con `git status`).
- Todo desarrollo experimental nuevo (Radar Explosivo, Diagnóstico, validación histórica, Explosive DNA) vive exclusivamente dentro de `atlas_live/`.

---

## 2. Radar Explosivo

**Objetivo**: detectar acciones con alta probabilidad de movimiento explosivo en los próximos 5-10 minutos, de forma independiente de Decision Engine (que responde una pregunta distinta: calidad/confianza de inversión).

**Estado**: En desarrollo (motor implementado y en uso en el dashboard, validado técnicamente sin regresiones. **La evidencia histórica de calidad predictiva ya existe** -- fase 3 completada, 30/30 días -- y muestra que el motor v1 detecta muy pocas oportunidades reales (Recall 2.33%). El motor no se tocó: la evidencia solo confirma que hace falta v2, ver fase 5).

**Dependencias**: Arquitectura.

**Criterios de éxito**:
- Motor propio (`atlas_live/explosive_engine.py`) con etapas de elegibilidad, puntaje ponderado por factores configurables y penalización continua por tamaño — implementado.
- Config centralizada (`atlas_live/explosive_config.json`) editable sin tocar código — implementado.
- Modo Diagnóstico (embudo + tabla de auditoría por símbolo) — implementado.
- Precision@10, Precision@20 y Recall medidos sobre un período representativo (ver fase 3) por encima de un umbral que se definirá con evidencia, no antes.

---

## 3. Validación histórica

**Objetivo**: comprobar con datos reales de mercado, no con opiniones, si el Radar Explosivo efectivamente detecta las acciones que resultaron explosivas.

**Estado**: **Completada.** 30 sesiones de mercado × Universo Racional completo (~2577 símbolos), sin lookahead -- reconstrucción a partir de velas reales de 5 minutos, snapshot a los 10 minutos de la apertura. Resultado: Precision@10 4.67%, Precision@20 2.33%, Recall 2.33%, 586/600 ganadoras reales descartadas. RVOL causó el 56.3% de los descartes; Liquidez el 33.3%. Detalle completo en [VALIDATION_RESULTS.md](VALIDATION_RESULTS.md) y [RADAR_EXPLOSIVO_V2.md](RADAR_EXPLOSIVO_V2.md).

**Dependencias**: Radar Explosivo.

**Criterios de éxito**:
- Informe por día: top 20 ganadoras reales, posición en el radar (o filtro que las descartó), Precision@10, Precision@20, Recall.
- Informe consolidado: promedios sobre el período completo, qué filtros pierden más oportunidades, qué filtros parecen demasiado estrictos o demasiado permisivos.
- Resultado registrado en [VALIDATION_RESULTS.md](VALIDATION_RESULTS.md).

---

## 4. Explosive DNA

**Objetivo**: caracterizar estadísticamente qué tuvieron en común las acciones realmente explosivas del período validado (precio, gap, RVOL, market cap, volatilidad, volumen), comparado contra un grupo de control, para que los futuros ajustes de umbrales se apoyen en evidencia.

**Estado**: **Completada.** Perfil estadístico corrido sobre las 600 observaciones explosivas reales de los 30 días completos. Separación fuerte confirmada en Cambio% (98.3%), Gap% (97.0%), RVOL (88.5%), Volatilidad (76.6%); débil en Market Cap (22.6%, con 73.2% de dato faltante -- vacío de datos sin resolver) y Precio (17.7%, relación inversa). Detalle en [RADAR_EXPLOSIVO_V2.md](RADAR_EXPLOSIVO_V2.md).

**Dependencias**: Validación histórica.

**Criterios de éxito**:
- Perfil estadístico (mediana, percentiles, separación contra el grupo de control) para cada característica, sobre el período completo validado.
- El perfil debe poder responder, con números, si cada umbral actual de `explosive_config.json` está alineado con lo que realmente distingue a una acción explosiva.

---

## 5. Optimización del Radar

**Objetivo**: ajustar los umbrales/pesos de `explosive_config.json` basándose en la evidencia de las fases 3 y 4 -- nunca antes.

**Estado**: **Diseño confirmado con evidencia final -- Pendiente de aprobación e implementación.** La validación de 30 días cerró y confirmó, con muestra completa, la conclusión preliminar: quitar a RVOL del rol de filtro excluyente multiplica Precision@10 (~6,5x), Precision@20 (~8,6x) y Recall (~20x) simultáneamente. Las 12 mejoras priorizadas y la arquitectura propuesta en [RADAR_EXPLOSIVO_V2.md](RADAR_EXPLOSIVO_V2.md) siguen vigentes tal como estaban diseñadas -- la validación completa no cambió el diagnóstico, lo reforzó. **Radar Explosivo v2 sigue sin implementarse** -- ningún cambio se aplica sin pasar antes por la METODOLOGÍA DE PROPUESTAS y tu aprobación explícita.

**Dependencias**: Validación histórica, Explosive DNA.

**Criterios de éxito**:
- Cada cambio de parámetro debe citar el dato de Explosive DNA o de Validation Results que lo justifica.
- Cada cambio debe volver a validarse (fase 3) para confirmar una mejora medible en Precision@10, Precision@20 o Recall antes de considerarse adoptado.
- Registrado en [DECISION_LOG.md](DECISION_LOG.md).

---

## 6. Cambio de proveedor de datos

**Objetivo**: poder reemplazar Yahoo Finance sin tocar el motor (Principio 5 de la Constitución: "El proveedor de datos nunca podrá estar acoplado al motor").

**Estado**: **Evaluación preliminar completada.** Comparación técnica de 5 proveedores (Polygon/Massive, Databento, Alpaca, Finnhub, Intrinio) contra los 16 requerimientos reales de Atlas, documentada en [DATA_PROVIDER_EVALUATION.md](DATA_PROVIDER_EVALUATION.md), con una recomendación preliminar (Alpaca Market Data como proveedor primario + fuente secundaria sin decidir para short interest/float). **Sin decisión tomada todavía** -- se revisará y verificará la información antes de aprobar cualquier migración. Ningún proveedor nuevo implementado, ninguna cuenta creada.

**Dependencias**: ninguna técnica; groundwork parcial existente (`atlas/data/providers/` ya define la interfaz `DataProvider` abstracta con `YahooFinanceProvider` como única implementación actual).

**Criterios de éxito**:
- Un segundo `DataProvider` implementado y pasando las mismas pruebas que `YahooFinanceProvider`.
- Cero cambios requeridos en `atlas/engine/`, `atlas_live/explosive_engine.py` ni en el Radar Explosivo al cambiar de proveedor.

---

## 7. Alertas en tiempo real

**Objetivo**: notificar activamente cuando el Radar Explosivo detecta una oportunidad, en vez de depender de que el usuario tenga el dashboard abierto.

**Estado**: **100% completado (alcance aprobado)** -- infraestructura de notificación implementada (excepción controlada a la metodología, aprobada el 2026-08-01, ejecutada en paralelo a la validación histórica sin tocar `explosive_engine.py`, `explosive_config.json` ni `/atlas`). Implementado en `atlas_live/static/notifications.js` + integración en `app.js`/`index.html`/`style.css`:
- Detección de oportunidades genuinamente nuevas (compara el set de símbolos elegibles entre polls, dispara solo ante altas nuevas, nunca repite el mismo símbolo) -- verificado con pruebas en el navegador.
- 3 canales activos: notificación del navegador (Web Notifications API, con manejo de permiso), sonido local configurable (on/off), resaltado visual (clase CSS + badge "NUEVA", se apaga solo a los 8s).
- Arquitectura modular (`NOTIFICATION_CHANNELS`, mismo patrón de registro que `explosive_factors.py`): agregar push/correo/webhook/Telegram/Discord a futuro es una función nueva en el registro, sin tocar la lógica de detección.
- Preferencias persistidas en `localStorage`, sin necesidad de backend para esta parte.

**Push al celular, correo, webhooks, Telegram y Discord quedan explícitamente planificados para una fase posterior** -- no forman parte de este objetivo ya cerrado, no cuentan en contra de este 100%. Cuando se prioricen, se agregan como funciones nuevas al registro `NOTIFICATION_CHANNELS`, sin reabrir ni modificar lo ya construido. El paquete `atlas/alerts/` en Atlas Core sigue vacío y sin uso -- esta infraestructura vive enteramente en `atlas_live`, no en el Core.

**Dependencias**: ninguna técnica para la infraestructura ya construida (no depende de que el Radar Explosivo esté validado -- reacciona al mismo campo `explosive.eligible` que ya existe, sea la señal buena o mala). La pregunta de si *conviene confiar* en las alertas para operar sigue dependiendo de cerrar la validación de 30 días -- eso no cambió.

**Criterios de éxito**: cumplidos para el alcance acotado aprobado (3 canales simples, arquitectura extensible, sin duplicar avisos). Canales adicionales quedan pendientes de priorización futura.

---

## 8. Dashboard profesional

**Objetivo**: evolucionar Atlas Live desde el MVP actual (lenguaje natural para usuario final, 4 secciones: Radar Explosivo, Radar General, Watchlist, Diagnóstico) hacia una herramienta de nivel profesional.

**Estado**: En desarrollo (MVP funcional en `atlas_live/static/` y `atlas_live/server.py`; sirve datos en vivo, no es todavía la versión definitiva).

**Dependencias**: Radar Explosivo, Alertas en tiempo real (para valor agregado real, no solo estético).

**Criterios de éxito**: a definir cuando se priorice esta fase.

---

## 9. IA de apoyo a decisiones

**Objetivo**: asistencia basada en IA generativa para interpretar las señales de Atlas (distinto del Learning Engine ya existente, que es calibración estadística/basada en reglas, no un asistente conversacional).

**Estado**: Pendiente (sin trabajo iniciado).

**Dependencias**: Radar Explosivo validado, Explosive DNA -- una IA de apoyo sobre un motor sin evidencia solo amplificaría el ruido.

**Criterios de éxito**: a definir cuando se priorice esta fase.

---

## 10. Mission Control (Centro de Operaciones de Atlas)

**Objetivo**: dar visibilidad y control unificados sobre todo proceso de larga duración de Atlas (validaciones, escaneo en vivo, y a futuro Paper Trading, IA, escaneo de noticias) -- estado en tiempo real, historial permanente (Timeline) y detección proactiva de anomalías (Supervisión Inteligente), en vez de diagnóstico manual por cada consulta.

**Estado**: **Diseño aprobado -- Pendiente de implementación.** Documento técnico completo en [ATLAS_MISSION_CONTROL.md](ATLAS_MISSION_CONTROL.md): arquitectura de 3 piezas (latido/heartbeat, Timeline, panel), estados estandarizados (7), severidad de eventos (4 niveles), Run ID como identificador único de trazabilidad, Supervisión Inteligente con 6 detectores (diseño, no implementación), y consideraciones explícitas de escalabilidad a varios años. Sin código escrito todavía.

**Dependencias**: ninguna técnica -- diseñado para funcionar en modo heredado (inferencia externa) con procesos ya existentes sin instrumentarlos, y en modo instrumentado con los que adopten la librería de latido a futuro.

**Criterios de éxito**:
- Panel principal muestra estado, progreso, CPU/memoria, último heartbeat y último log de cualquier proceso activo, instrumentado o heredado.
- Timeline registra permanentemente inicio/fin/error/alertas/cambios de estado de cada ejecución, consultable por Run ID.
- Al menos los 6 detectores de Supervisión Inteligente documentados funcionando (con la salvedad ya documentada de que "APIs lentas" depende de que el proceso reporte su propia latencia).
- Cero cambios requeridos en procesos ya en ejecución para que Mission Control empiece a dar valor.

---

## 11. Memory Engine

**Objetivo**: que Atlas estudie todo el mercado escaneado a diario (no solo las explosiones) para recalibrar el Ranking de Radar Explosivo con tasas base reales de la población completa, y valide esa recalibración primero con evidencia histórica y después en tiempo real -- pasos 6 y 7 de la Metodología de Propuestas de la Constitución.

**Estado**: **Atlas Alpha 1.0 congelado como baseline (2026-08-02).** Diseño formal aprobado ([MEMORY_ENGINE.md](MEMORY_ENGINE.md)). Implementado y probado: Memory Store, Clasificador de Resultado (validado sobre 73.123 observaciones reales de los 30 días de `results_v1/`), carga histórica retroactiva, motor de tasas base, generador de propuestas de recalibración, Ranking Score de desempate (validado: mejora medible de posición y de Precision@10/@20 sobre datos históricos), Prediction Journal (registro dinámico + sellado inmutable + calificación automática + tiempo de anticipación), integración en tiempo real con `scan_worker.py` (detección de sesión, snapshot en premarket, sellado antes de la apertura, calificación al cierre), y Modo Interactivo Continuo (recalibración diaria automática, apagado prolijo sin pérdida de estado). Todo probado con datos sintéticos y/o históricos reales -- **todavía no verificado en condiciones de mercado reales**.

**Dependencias**: Radar Explosivo (fase 2, lee sus métricas y su score), Validación histórica (fase 3, es la fuente de los datos con los que se entrenó la evidencia).

**Criterios de éxito**:
- Memory Store captura y clasifica el universo escaneado con reglas explícitas y auditables -- cumplido.
- Al menos una recalibración candidata, validada retroactivamente, mejora Precision@10/@20 sin degradar Recall -- cumplido (Ranking Score).
- El sistema genera, sella y califica predicciones automáticamente durante una sesión de mercado real, con tiempo de anticipación medido -- diseñado e implementado, pendiente de la primera corrida en condiciones reales.
- Ninguna recalibración se aplica a `explosive_config.json`/`explosive_factors.py` de forma permanente sin pasar, cada vez, por su propia propuesta formal -- respetado en todo el desarrollo.

---

## Fuera de este roadmap (documentado en `ROADMAP.md` original)

El `ROADMAP.md` original (fases 1-3, IE/scanners de premarket/aprendizaje automático) describe el plan previo a esta reorganización. No se elimina ni se contradice: varias de sus piezas ya se completaron por otra vía (Decision Engine, Money Flow Engine, Learning Engine de 8 etapas, Knowledge Base) y quedan reflejadas en la fase 1 de este documento. El Índice de Explosión (IE) que ese roadmap proponía dentro de Atlas Core (`atlas/engine/explosion_index.py`, todavía un stub vacío) fue deliberadamente reemplazado por el Radar Explosivo dentro de `atlas_live/` -- ver [DECISION_LOG.md](DECISION_LOG.md).
