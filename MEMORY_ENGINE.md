# MEMORY_ENGINE.md

Documento técnico oficial del Memory Engine de Atlas. Sujeto a [ATLAS_CONSTITUTION.md](ATLAS_CONSTITUTION.md) -- ninguna pieza de este diseño se implementa sin pasar antes por la METODOLOGÍA DE PROPUESTAS, y **el diseño de este documento está congelado** (aprobado el 2026-08-02): no se amplía la arquitectura salvo decisión explícita futura. Lo que sigue abierto es únicamente la implementación, entregable por entregable, cada uno con su propia aprobación.

Vive enteramente en `atlas_live/memory/` -- nunca en `/atlas`. No reemplaza a Radar Explosivo; lo alimenta.

---

## ATLAS ALPHA 1.0 -- BASELINE OFICIAL CONGELADA (2026-08-02)

Esta versión queda registrada como **la primera versión funcional del Memory Engine**, capaz de:

1. **Generar rankings** -- Ranking Score de 4 niveles (`ranking_score.py`), sin pesos inventados.
2. **Aprender** -- Memory Store + Clasificador de Resultado + Motor de tasas base, validados sobre 73.123 observaciones reales.
3. **Registrar predicciones** -- Prediction Journal, snapshots dinámicos durante el premarket.
4. **Sellar predicciones** -- una sola vez por día, verificablemente inmutable (`AlreadySealedError`).
5. **Calificarlas automáticamente** -- contra la cotización real al cierre, con tiempo de anticipación (`AlreadyGradedError` protege contra recalificación).
6. **Recalibrarse diariamente** -- la evidencia (tasas base/propuestas) se recalcula automáticamente en cada día de mercado nuevo.
7. **Mantener el Memory Engine durante toda la sesión de trabajo** -- Modo Interactivo Continuo, con apagado prolijo sin pérdida de estado.

Detalle completo, con pruebas, de cada componente: sección "PLAN DE IMPLEMENTACIÓN" (Entregables 1-8), "RANKING SCORE DE DESEMPATE", "INTEGRACIÓN EN TIEMPO REAL" y "MODO INTERACTIVO CONTINUO" más abajo en este documento.

**No incluido en esta baseline, explícitamente**: validación agregada de Precision@10/@20/Recall sobre los 30 días completos (Entregable 6 solo parcialmente cerrado); checkpoints intermedios (Entregable 8); verificación en condiciones de mercado reales (todo se probó con datos sintéticos y/o históricos); ejecución de órdenes, gestión de riesgo con capital real, o cualquier otra pieza del camino a producción (ver `ATLAS_MASTER_DOCUMENT.md`, sección 18 bloque M -- sigue sin resolver, sin relación con esta baseline).

**Regla vigente a partir de ahora**: cualquier mejora futura al Memory Engine debe demostrar una mejora medible respecto a Atlas Alpha 1.0 antes de incorporarse -- mismo principio de evolución por evidencia que rige todo Atlas (Constitución, Principio 2), aplicado explícitamente a esta baseline como punto de comparación obligatorio. Registrado también en [DECISION_LOG.md](DECISION_LOG.md).

---

# PROPUESTA APROBADA -- DISEÑO DEL MEMORY ENGINE

## PROBLEMA

Radar Explosivo (y el diseño conceptual previo del Memory Engine, sección 8 de `ATLAS_MASTER_DOCUMENT.md`) solo guardan y comparan **explosiones** -- los ~30-100 casos ganadores de cada período. Atlas nunca estudia sistemáticamente el otro 99.9%+ del universo escaneado cada día: acciones normales, débiles, perdedoras, y -- el caso más peligroso -- falsas rupturas (símbolos que parecían explosivos en el snapshot temprano y no lo sostuvieron). Sin esa memoria, Radar Explosivo no puede saber si una combinación de condiciones que hoy parece explosiva (RVOL alto, gap alto) tiende, en la población real, a resolver en explosión genuina o en falsa ruptura.

## HIPÓTESIS

Si Atlas construye una memoria diaria de **todo** el universo escaneado -- cada símbolo, cada día, clasificado en un número reducido de categorías de resultado real, con su contexto completo -- puede calcular tasas base reales por combinación de condiciones y usarlas para recalibrar el Ranking con evidencia de la población completa, no solo de los casos ya ganadores. Se espera mejora en Precision@10/@20 y en falsos positivos; no se espera impacto directo en Recall (no toca la Etapa A de elegibilidad).

## PRINCIPIOS DE LA CONSTITUCIÓN QUE RESPALDAN ESTA PROPUESTA

- **Principio 1**: reemplaza el supuesto implícito por tasas base medidas.
- **Principio 2**: cada recalibración propuesta se valida contra Precision@10/@20/Recall.
- **Principio 4**: mecanismo determinista y auditable (tasas base + significancia estadística), no una caja negra.
- **Principio 6**: existe exclusivamente para mejorar el ranking de Radar Explosivo.
- **Principio 7**: no agrega factores nuevos al score -- reutiliza métricas que Radar Explosivo ya calcula, opera como capa de recalibración offline.
- **Principio 3**: determina dónde vive (ver Arquitectura) -- nunca en Atlas Core sin validación previa.

## ARQUITECTURA

Vive enteramente en `atlas_live/memory/`, independiente de Radar Explosivo -- lo alimenta, no lo reemplaza:

- **Captura Diaria del Universo**: extiende el patrón ya probado de `historical_scan.py` (que ya guarda TODO el universo reconstruido, elegible o no) para correr también en producción diaria.
- **Clasificador de Resultado**: reglas explícitas y documentadas (no ML) que asignan una de 5 categorías por símbolo/día, apoyadas en las categorías que Atlas Core ya define en `event_store.py` (`EXPLOSION`, `COLLAPSE`≈`LOSER`, `FALSE_BREAKOUT`, `NORMAL`), extendidas con `WEAK` (débil, no perdedora, que Core no distingue hoy).
- **Memory Store**: Event Store nativo de Radar Explosivo (SQLite/WAL, mismo patrón de `event_store.py`, con el feature set real del Radar).
- **Motor de Recalibración (batch, nocturno)**: recalcula tasas base por combinación de condiciones/sector/horario/tamaño, reutilizando la validación estadística de tres condiciones de `PatternEvolution` (tamaño de muestra, intervalo de Wilson, consistencia temporal).
- **Generador de Propuestas**: produce recalibraciones candidatas en el mismo formato que `CalibrationAdvisor` de Core -- **nunca aplica nada solo**.

## FLUJO DE DATOS

1. El escaneo diario recorre el universo (completo o en vivo, según viabilidad operativa).
2. Por símbolo: métricas crudas que Radar Explosivo ya calcula + contexto nuevo (sector/industry si disponible, hora/sesión de mercado, bucket de market cap).
3. En uno o más checkpoints posteriores se mide el resultado real -- hallazgo clave de la planificación (ver Entregable 2 abajo): **FALSE_BREAKOUT ya es clasificable hoy** con solo el snapshot de 10 min + el cierre (`ground_truth_change_pct`), sin checkpoints intermedios nuevos.
4. El Clasificador asigna la categoría según umbrales explícitos y documentados.
5. Se persiste una fila por símbolo/día en el Memory Store.
6. El Motor de Recalibración corre de noche sobre la memoria acumulada.
7. El Generador de Propuestas emite (si corresponde) una recalibración candidata con su evidencia -- queda pendiente de revisión humana.

## QUÉ APRENDE

Tasas base reales de cada combinación de condiciones cuantitativas, por categoría de resultado, segmentadas por sector, tamaño y horario.

## QUÉ GUARDA

Por símbolo/día: `symbol`, `date`, `checkpoint_minutes`, `category`, métricas crudas de Radar Explosivo, `sector`/`industry`, `market_cap_bucket`, `session`, resultado real por horizonte de tiempo capturado, versión de config de origen (trazabilidad), y **`market_context`** (ver Consideración de diseño agregada abajo).

## QUÉ DESCARTA

Filas con error de reconstrucción de datos; observaciones sospechadas de ser artefactos de datos (mismo patrón ya documentado en Validación 1); duplicados exactos; símbolos fuera del Universo Racional.

## CÓMO EVOLUCIONA

La memoria crece un día a la vez, nunca se reescribe retroactivamente (Capa 3 de Atlas Core: "nunca se borra conocimiento"). Las tasas base agregadas se recalculan cada noche. Un patrón puede pasar de "evidencia insuficiente" a "confiable" a medida que se acumula muestra -- reutiliza el modelo de estados de `PatternEvolution`/`PatternRegistry`.

## IMPACTO ESPERADO

Precision@10, Precision@20 y falsos positivos. No se espera impacto directo en Recall.

## RIESGOS

Costo de captura del universo completo a diario (Medio-Alto); checkpoints intermedios no capturados hoy (Medio, mitigado -- ver hallazgo del Entregable 2); sobreajuste a un régimen de mercado (Medio, mitigado por consistencia temporal); taxonomía de 5 categorías como simplificación del continuo real (Bajo-Medio); acoplar al Ranking sin validación (mitigado por diseño: solo propone, nunca aplica).

## CÓMO SE VALIDARÁ

Histórica primero, sobre los 30 días ya validados, en simulación retroactiva (mismo patrón que `whatif_simulator.py`). Solo después, validación en tiempo real antes de considerar cualquier recalibración permanente.

## CRITERIOS DE ÉXITO

1. Memory Store captura el universo con tasa de descarte documentada.
2. Clasificador con reglas explícitas y auditables.
3. Al menos una recalibración propuesta mejora Precision@10/@20 sin degradar Recall más allá de un margen acordado, validada retroactivamente.
4. Ninguna recalibración se aplica a `explosive_config.json`/`explosive_factors.py` sin pasar por esta misma Metodología de Propuestas.

---

## CONSIDERACIÓN DE DISEÑO AGREGADA (2026-08-02, antes de iniciar el Entregable 1)

El esquema del Memory Store debe quedar preparado desde el Entregable 1 para incorporar en el futuro información de **contexto general del mercado** (`market_context`) -- por ejemplo, condición del mercado general (SPY/QQQ/VIX), sector líder del día, o cualquier variable de contexto que hoy calcula `market_context_engine.py` en Atlas Core pero que este diseño no usa todavía. El campo puede permanecer vacío o sin uso inicialmente. **Esto no cambia el alcance de ningún entregable ni la arquitectura aprobada arriba** -- es, exclusivamente, una columna reservada en el esquema para evitar una migración costosa más adelante.

---

# PLAN DE IMPLEMENTACIÓN

Aprobado el 2026-08-02. 8 entregables independientes, cada uno terminado, probado y aprobado antes de empezar el siguiente. Ninguno toca `/atlas`.

## Entregable 1 -- Memory Store (esquema + librería de lectura/escritura)

- **Objetivo**: almacenamiento persistente funcionando, vacío, antes de meterle ningún dato real.
- **Archivos**: `atlas_live/memory/__init__.py` (nuevo), `atlas_live/memory/store.py` (nuevo).
- **Dependencias**: ninguna.
- **Riesgos**: Bajo -- código nuevo, aislado. Cuidado: definir el esquema completo desde el principio (incluye ahora `market_context`, ver Consideración de diseño).
- **Cómo se validará**: escribir y leer filas sintéticas cubriendo las 5 categorías, verificar persistencia exacta y que la base nunca se sobrescribe (append-only).
- **Criterio de aprobación**: un script de prueba escribe N observaciones sintéticas y las recupera exactamente iguales, sin pérdida de datos.
- **Estado**: ✅ **Completado y aprobado** (2026-08-02). Archivos creados: `atlas_live/memory/__init__.py`, `atlas_live/memory/store.py`. Probado con 7 observaciones sintéticas cubriendo las 5 categorías (incluida una con `market_context` poblado), filtros por `symbol`/`date`/`category` verificados por separado, rechazo correcto de categoría inválida, y append-only confirmado explícitamente (una escritura nueva no altera las filas anteriores). Artefactos de prueba (`memory_store.db`) eliminados tras validar -- el Memory Store queda vacío, listo para el Entregable 3.

## Entregable 2 -- Clasificador de Resultado (reglas explícitas, sin captura nueva)

- **Objetivo**: función pura que clasifica una fila ya existente de `historical_scan.py` en una de las 5 categorías. **FALSE_BREAKOUT ya es clasificable con los datos que existen hoy** (snapshot de 10 min + cierre) -- los checkpoints intermedios quedan como refinamiento posterior (Entregable 8), no como bloqueo.
- **Archivos**: `atlas_live/memory/classifier.py` (nuevo), `atlas_live/memory/classifier_config.json` (nuevo).
- **Dependencias**: ninguna técnica.
- **Riesgos**: Medio -- los umbrales de cada categoría son decisiones de diseño documentadas, no intuitivas.
- **Cómo se validará**: correr contra los 30 días de `results_v1/`, comparar una muestra a mano contra las 14 detecciones reales y los 5 artefactos de datos ya conocidos.
- **Criterio de aprobación**: corre sin errores sobre los 30 días, produce una distribución de categorías con sentido frente a lo ya sabido.
- **Estado**: ✅ **Completado y aprobado** (2026-08-02). Archivos creados: `atlas_live/memory/classifier.py`, `atlas_live/memory/classifier_config.json`. Umbrales documentados en el docstring del módulo (EXPLOSION ≥10%, FALSE_BREAKOUT: elegible en el snapshot y <5%, LOSER ≤-5%, WEAK <2% en cualquier dirección, NORMAL el resto), evaluados en orden de prioridad sin ambigüedad. Probado sobre los 30 días completos de `results_v1/` (73.123 filas, 0 descartadas por dato faltante): las 14 detecciones reales conocidas clasificaron las 14 como EXPLOSION; los 5 artefactos de datos sospechosos ya identificados en Validación 1 también clasificaron como EXPLOSION -- **límite de alcance esperado y documentado**, no un error: filtrar artefactos es tarea del Entregable 3 (backfill), no de esta regla. Distribución resultante: WEAK 67.8%, NORMAL 27.2%, LOSER 4.2%, EXPLOSION 0.86%, FALSE_BREAKOUT 0.015% (11 filas) -- consistente con los 15 falsos positivos y 14 detecciones reales ya conocidos de Validación 1.
- **Prueba de regresión permanente agregada** (2026-08-02, antes de iniciar el Entregable 3, sin cambiar la lógica de clasificación): `atlas_live/memory/test_classifier_golden.py` -- 41 casos congelados (las 14 detecciones reales, los 5 artefactos de datos conocidos, 3 ejemplos reales de cada una de las otras 4 categorías, 9 casos sintéticos de borde que fijan el comportamiento inclusive/exclusivo de cada umbral y el orden de prioridad entre reglas, y 1 caso de dato faltante). Debe correrse (`python -m atlas_live.memory.test_classifier_golden`) en cada modificación futura de `classifier.py` para detectar cualquier regresión.

## Entregable 3 -- Carga histórica retroactiva al Memory Store

- **Objetivo**: primera vez que el Memory Store tiene datos reales.
- **Archivos**: `atlas_live/memory/backfill.py` (nuevo, CLI similar a `run_validation.py`).
- **Dependencias**: Entregables 1 y 2.
- **Riesgos**: Bajo -- solo lectura sobre datos ya guardados.
- **Cómo se validará**: el número de filas coincide exactamente con los símbolos reconstruidos, descartes contabilizados y reportados.
- **Criterio de aprobación**: Memory Store poblado con los 30 días completos, con reporte de filas guardadas/descartadas.
- **Estado**: ✅ **Completado y aprobado** (2026-08-02). `atlas_live/memory/backfill.py`. 30 días, 73.123 filas leídas, 73.123 guardadas, 0 descartadas -- coincide exacto con el reporte y con `store.count_observations()`. Integridad verificada: las 14 detecciones reales quedaron persistidas con categoría EXPLOSION y `source_version='v1'`; append-only confirmado con una escritura real de prueba (no alteró ninguna fila histórica, luego retirada). Tiempo de ejecución real: ~25-30 minutos (una conexión SQLite nueva por fila -- lento; anotado como riesgo a resolver en el Entregable 7 si la captura diaria en producción lo necesita más rápido).

## Entregable 4 -- Motor de tasas base + validación estadística

- **Objetivo**: tasas base reales por combinación de condiciones/sector/tamaño/horario, con significancia (reutiliza `PatternEvolution`).
- **Archivos**: `atlas_live/memory/base_rates.py` (nuevo).
- **Dependencias**: Entregable 3.
- **Riesgos**: Bajo -- análisis de solo lectura.
- **Cómo se validará**: verificar manualmente 2-3 combinaciones ya conocidas de la auditoría de Radar Explosivo v2.
- **Criterio de aprobación**: tasas base auditables (con muestra e intervalo de confianza visibles) sin contradecir la evidencia ya validada.
- **Estado**: ✅ **Completado y aprobado** (2026-08-02). 17 pruebas unitarias sintéticas en verde antes de tocar datos reales. Sobre las 73.123 observaciones reales: `relative_volume>=2.5x` (rango de las 14 detecciones reales) da 23.4% de tasa de EXPLOSION (confiable, muestra=77) vs. 0.86% de baseline poblacional; `gap_pct>=5%` da 30.9% (confiable, muestra=844); `price<=$5` como control da una señal real pero débil (3.1%, confiable, muestra=5853) -- consistente en dirección y magnitud relativa con la auditoría ya cerrada de Radar Explosivo v2 (RVOL y Gap como señales fuertes, precio como señal débil).

## Entregable 5 -- Generador de Propuestas de recalibración

- **Objetivo**: candidatas de recalibración con evidencia, formato `CalibrationProposal`-like. Sigue sin aplicar nada.
- **Archivos**: `atlas_live/memory/calibration_advisor.py` (nuevo).
- **Dependencias**: Entregable 4.
- **Riesgos**: Bajo -- solo genera datos/reportes.
- **Cómo se validará**: cada propuesta cita la tasa base y muestra que la sustenta.
- **Criterio de aprobación**: al menos una propuesta candidata con evidencia trazable, formato legible para revisión humana.
- **Estado**: ✅ **Completado y aprobado** (2026-08-02). Grilla de 14 condiciones (bandas de `relative_volume`, `gap_pct`, `volatility_score`, `price`, `market_cap`, más una combinación ya sugerida por la auditoría), evaluada contra las 73.123 observaciones reales. **10 de 14 resultaron confiables**, la más fuerte `gap_pct>=10.0` (64.80% de tasa de EXPLOSION, 75.3x el baseline, muestra=179), la más débil `volatility_score>=90` (2.64%, 3.1x el baseline, muestra=21.130). Cada `CalibrationProposal` incluye la `Condition` reutilizable (no solo su descripción) para poder aplicarse a candidatos nuevos.

## Entregable 6 -- Validación retroactiva de una propuesta candidata

- **Objetivo**: responder si el Memory Engine mejora algo de verdad -- simulación sobre los 30 días (patrón `whatif_simulator.py`).
- **Archivos**: `atlas_live/memory/proposal_validator.py` (nuevo, reutiliza `validation_report.py`).
- **Dependencias**: Entregable 5.
- **Riesgos**: Bajo -- simulación de solo lectura.
- **Cómo se validará**: comparar Precision@10/@20/Recall con y sin la recalibración simulada.
- **Criterio de aprobación**: al menos una propuesta muestra mejora medible sin degradar Recall más allá de un margen acordado -- o, si ninguna lo logra, el entregable igual se cumple (la herramienta funciona).
- **Estado**: 🟡 **Parcial -- demo de un día ejecutada dos veces (antes y después del Ranking Score de desempate, ver más abajo), validación agregada de 30 días (`proposal_validator.py`, Precision@10/@20/Recall sobre el período completo) todavía no construida.** Desarrollo detenido aquí por instrucción explícita, a la espera de la primera prueba funcional del usuario.

---

## RANKING SCORE DE DESEMPATE -- propuesta aprobada e implementada (2026-08-02)

**Alcance explícito, documentado por instrucción directa del usuario**: este mecanismo **NO reemplaza a Radar Explosivo ni modifica la detección**. No cambia qué símbolo es `eligible`, no cambia ningún gate ni peso de `explosive_config.json`/`explosive_factors.py`, no agrega ninguna fuente de datos nueva. Su única responsabilidad es **desempatar** entre candidatos que ya tienen la misma probabilidad histórica (Nivel 1) -- no cambia si un candidato tiene evidencia ni cuál es su probabilidad reportada.

**Archivo**: `atlas_live/memory/ranking_score.py` (nuevo), integrado en `atlas_live/memory/demo_ranking.py`.

**Diseño**: orden de prioridad estricto de 4 niveles (no una suma ponderada -- evita inventar coeficientes sin evidencia, mismo error ya identificado y corregido con los pesos originales de RVOL):
1. Límite inferior de Wilson de la mejor condición confiable (sin cambios).
2. Cantidad de condiciones confiables adicionales que el símbolo matchea a la vez.
3. Percentil de la métrica de la condición ganadora dentro de la distribución de esa métrica entre los símbolos que ya matchearon esa condición en el Memory Store.
4. Score real de Radar Explosivo (Etapa B), si el símbolo es elegible -- no participa si no lo es.

**Resultado de la validación** (criterios de la propuesta aprobada):

| Día | Símbolo | Posición antes (alfabético) | Posición después (Ranking Score) |
|---|---|---|---|
| 2026-07-30 | XRX | #39 | **#1** |
| 2026-07-30 | NUWE | #25 | **#2** |
| 2026-06-23 | SOXS | -- (no medido antes) | #1 |
| 2026-06-23 | UVIX | -- | #2 |
| 2026-06-23 | BLZE | -- | #4 |
| 2026-07-13 | AGEN | -- | #1 |

Precision@10/@20 sobre 2026-07-30 (mismo día, tres versiones comparables):
- Radar Explosivo original (solo 3 elegibles ese día): Precision@10=20.0%, Precision@20=10.0%.
- Memory Engine sin Ranking Score (desempate alfabético): Precision@10=30.0%, Precision@20=40.0%.
- Memory Engine con Ranking Score: **Precision@10=70.0%, Precision@20=60.0%**.

Confirmado sobre 2 días adicionales (2026-06-23, 2026-07-13) que el mecanismo generaliza, no es un ajuste al caso conocido: Precision@10 de 70.0% y 30.0% respectivamente (ambos por encima del 4.67% de la validación original de 30 días).

**Los 4 criterios de éxito de la propuesta se cumplieron**: (1) NUWE y XRX mejoraron de posición de forma medible y sustancial; (2) el orden del Nivel 1 no se alteró (comparación lexicográfica por diseño); (3) confirmado en 2 días adicionales; (4) cada nivel del desempate sigue siendo explicable (`tie_break_note` en cada candidato). **Propuesta validada.**

---

## INTEGRACIÓN EN TIEMPO REAL (premarket → cierre) -- propuesta aprobada el 2026-08-02

Diseño completo (PROBLEMA/HIPÓTESIS/ARQUITECTURA/FLUJO DE DATOS/RIESGOS/VALIDACIÓN/CRITERIOS) entregado y aprobado en la conversación, con 3 ajustes de arquitectura pedidos al aprobar:

1. **Dos flujos de ranking**: dinámico (múltiples snapshots durante el premarket, informativo) + oficial sellado (uno solo por día, inmutable, el que se califica).
2. **Prediction Journal, no solo Log**: cada predicción sellada guarda la explicación completa que la generó, no solo el símbolo y su posición.
3. **Nueva métrica**: tiempo de anticipación entre la primera detección (en cualquier snapshot dinámico) y el movimiento confirmado.

### Primer componente implementado: Prediction Journal (almacenamiento)

- **Archivo**: `atlas_live/memory/prediction_journal.py`.
- **`record_dynamic_snapshot()`**: append-only, sin restricción -- se puede llamar tantas veces como se quiera durante el premarket.
- **`seal_ranking()`**: una sola vez por fecha -- una segunda llamada levanta `AlreadySealedError` sin tocar el sellado original. Garantía técnica real, no una promesa.
- **`grade_sealed_prediction()`**: completa el resultado real de una predicción ya sellada, una sola vez (`AlreadyGradedError` en el segundo intento), y calcula `anticipation_minutes` automáticamente a partir del snapshot dinámico más temprano en que apareció ese símbolo -- `None` si nunca apareció en un snapshot dinámico, nunca un valor inventado.
- **Probado con datos sintéticos**: 8 snapshots dinámicos en 3 momentos distintos; sellado único con rechazo verificado del segundo intento (y confirmación de que el original queda intacto); calificación con cálculo correcto de anticipación (100 minutos en el caso de prueba); rechazo de una segunda calificación; caso sin snapshot dinámico previo → `anticipation_minutes=None`, no inventado. Artefactos de prueba (`prediction_journal.db`) eliminados tras validar.

### Conexión con `scan_worker.py` -- completada (2026-08-02)

- **`atlas_live/memory/market_hours.py`** (nuevo): detección de sesión (premarket/regular/afterhours/closed) por huso horario de Nueva York, sin dependencias nuevas (`zoneinfo`, stdlib). Limitación documentada explícitamente: no contempla feriados de mercado -- un feriado se trata como día hábil.
- **`atlas_live/memory/live_integration.py`** (nuevo): punto de entrada único `run_live_cycle(results, now)`. En premarket arma el ranking (Memory Engine + Ranking Score) sobre `results` y lo guarda como snapshot dinámico; en la ventana de sellado (09:25-09:30) sella el ranking oficial del día una sola vez; en afterhours/closed califica las predicciones selladas pendientes contra la cotización real y calcula el tiempo de anticipación. Nunca lanza una excepción hacia el llamador.
- **`atlas_live/memory/demo_ranking.py`** refactorizado (sin cambiar ningún resultado ya validado -- regresión verificada: XRX sigue #1, NUWE sigue #2 en la demo de 2026-07-30): se extrajo `build_ranked_candidate()` para que la integración en vivo reutilice exactamente la misma lógica que ya se validó, en vez de duplicarla.
- **`scan_worker.py`**: una única modificación aditiva, 6 líneas, envuelta en su propio `try/except` (mismo patrón ya usado para `recorder.record_decision()` -- "nunca debe tumbar el escaneo"). No se tocó ninguna otra línea del archivo. No se modificó Radar Explosivo ni ningún archivo de `/atlas`.
- **Pruebas**: `test_market_hours.py` (7 casos: los 4 límites de sesión, fin de semana, ventana de sellado, huso horario) y `test_live_integration.py` (5 casos, con `results` sintético y un `DataCollector` falso para no golpear la red real: ranking en vivo prioriza señal fuerte; snapshot dinámico fuera de la ventana de sellado; sellado único aunque se llame varias veces dentro de la ventana; calificación al cierre con cálculo correcto de anticipación, incluida la confirmación de que un símbolo sin cotización disponible queda sin calificar en vez de inventarse un resultado; sesión regular no hace nada). Todas en verde.

### Pendiente, explícitamente no resuelto todavía

- **Calidad de datos de premarket con Yahoo Finance, sin verificar** -- riesgo ya señalado en la propuesta, sigue abierto; depende de la Fase 6 del roadmap general (proveedor apto para producción).
- Acumular los 10 días mínimos de experimento antes de sacar cualquier conclusión sobre si el sistema anticipa oportunidades reales.
- El proceso de `scan_worker.py` debe estar corriendo durante el premarket real para que esto funcione -- no se verificó todavía en condiciones de mercado reales (la primera prueba completa en tiempo real queda pendiente, según lo acordado).

---

## MODO INTERACTIVO CONTINUO -- decisión de arquitectura (2026-08-02)

Atlas V1 escanea, actualiza el Ranking, el Memory Engine y el Prediction Journal de forma continua **mientras la aplicación esté abierta** -- no un servicio 24/7 (explícitamente fuera de alcance, versión futura, sin modificar la arquitectura actual). Al cerrar, guarda el estado de forma segura.

**Lo que ya existía y satisface esto sin cambios**: `scan_worker.start_background_refresh()` ya corría en un hilo de fondo continuo desde antes de esta sesión, escaneando cada `REFRESH_INTERVAL_SECONDS` (300s) mientras el proceso viva; con la integración en tiempo real de esta misma etapa, cada ciclo ya actualiza Ranking + Memory Engine + Prediction Journal automáticamente.

**Lo que faltaba y se agregó, de forma aditiva, sin restructurar nada**:
- **"Recalibrar sus estadísticas" (aprendizaje)**: `live_integration._evidence()` recalculaba la evidencia (tasas base + propuestas confiables) **una sola vez por proceso** y quedaba fija. Ahora se recalcula automáticamente **una vez por cada día de mercado nuevo** (no en cada ciclo de 5 minutos -- sería carísimo sobre decenas de miles de observaciones) -- probado explícitamente: mismo día no recalcula, día distinto sí.
- **"Guardar todo el estado de forma segura al cerrar"**: el hilo de fondo era `daemon=True` sin ningún mecanismo de apagado -- un cierre de la app lo mataba a mitad de ciclo, sin garantía de terminar de escribir. Se agregó `scan_worker.request_stop()` / `wait_until_stopped(timeout)` (usa `threading.Event` en vez de `sleep()`, así el hilo despierta enseguida al pedirle que pare) y `server.py` ahora envuelve `app.run()` en `try/finally` para invocarlos al cerrar. Probado: el hilo termina en milisegundos al pedir el apagado, sin esperar el intervalo completo.
- **Nota de diseño, no un cambio de código**: el estado que realmente importa (Memory Store, Prediction Journal) ya vivía en SQLite con `commit()` inmediato por escritura -- ya era seguro por construcción. El único riesgo real era el hilo `daemon` cortando un ciclo a mitad de una escritura de varias filas; eso es lo que se corrigió. El caché de ranking en memoria (`scan_worker.STATE`) NO se persiste a propósito -- es un caché re-derivable en el siguiente ciclo, no conocimiento aprendido; persistirlo sería complejidad sin beneficio (Principio 7).

**Archivos modificados** (todos aditivos, sin restructurar): `atlas_live/scan_worker.py` (`_stop_event`, `request_stop()`, `wait_until_stopped()`), `atlas_live/server.py` (`try/finally` en `main()`), `atlas_live/memory/live_integration.py` (`_evidence()` con recalibración diaria). Ninguno toca Radar Explosivo ni `/atlas`.

## Entregable 7 -- Captura diaria en producción

- **Objetivo**: la memoria sigue creciendo hacia adelante, no solo con los 30 días ya guardados.
- **Archivos**: `atlas_live/memory/daily_capture.py` (nuevo) + llamada aditiva opcional desde `scan_worker.py`.
- **Dependencias**: Entregables 1, 2, 3.
- **Riesgos**: Medio -- primer entregable que toca (aditivamente) un archivo en uso activo.
- **Cómo se validará**: correr sobre un día real, confirmar calidad igual a la carga retroactiva.
- **Criterio de aprobación**: un día de captura real sin errores, sin afectar el escaneo en vivo existente.
- **Estado**: ✅ **Completado, por una vía distinta a la planeada.** No se construyó `daily_capture.py` por separado -- se resolvió directamente vía la sección "INTEGRACIÓN EN TIEMPO REAL" (`live_integration.py`, conectado a `scan_worker.py`), que ya cumple el objetivo (la memoria crece hacia adelante en cada premarket real) con el mismo nivel de riesgo previsto (toca `scan_worker.py` de forma aditiva).

## Entregable 8 -- Checkpoints intermedios (refinamiento, no bloqueante)

- **Objetivo**: mediciones a 10/30/60 minutos para refinar la distinción explosión sostenida vs. pico momentáneo.
- **Archivos**: extensión de `daily_capture.py` y `classifier.py`.
- **Dependencias**: Entregable 7.
- **Riesgos**: Medio-Alto -- único entregable con costo real de descargas adicionales.
- **Cómo se validará**: comparar clasificación con y sin checkpoints intermedios.
- **Criterio de aprobación**: el Clasificador usa los checkpoints nuevos cuando existen, sigue funcionando sin ellos (compatibilidad hacia atrás).
- **Estado**: ⬜ No iniciado.

---

## EXIT JOURNAL -- nueva fase, propuesta aprobada con modificación (2026-08-02)

**No es un algoritmo de salida.** No modifica Atlas Alpha 1.0 (sigue vigente como baseline, sin cambios) -- es una fase nueva, adicional, que construye memoria histórica de cómo evoluciona una oportunidad una vez detectada, para que un futuro **Exit Pattern Engine** (fase posterior, no construida) descubra con evidencia si existen umbrales naturales de salida, en vez de que el proyecto los fije a mano.

**Modificación explícita del usuario al aprobar**: no se fija ningún umbral de "pérdida de fuerza" (X) ni "fin del impulso" (N) todavía. En consecuencia, el diseño quedó en dos niveles:

1. **Guardado (objetivo, sin ningún umbral)**: `atlas_live/memory/exit_journal.py` -- `trajectory_samples` (la serie cruda completa de rendimiento observado, un punto por ciclo de `scan_worker.py`, append-only, nunca se resume) y `exit_summary` (una fila por símbolo/día, calculada una sola vez al cerrar la ventana -- `AlreadyClosedError` protege contra recálculo -- con hora de detección, hora de entrada aproximada por el sellado, hora y valor del máximo, rendimiento final, duración de la ventana observada; ninguno de estos campos requiere decidir qué es "perder fuerza").
2. **NO guardado -- funciones puras bajo demanda**: `derive_movement_start`, `derive_weakness_point`, `derive_impulse_end`, `derive_movement_duration` -- reciben el umbral **como parámetro obligatorio, sin default**, cada vez que se llaman. Como la trayectoria cruda se conserva completa, se pueden recalcular en cualquier momento futuro con distintos umbrales sin haber "grabado mal" nada -- nunca se persistió una interpretación, solo el dato.

**Limitación de datos heredada, documentada igual que el resto del Memory Engine**: la única granularidad disponible es el ciclo de `scan_worker.py` (~5 minutos) -- no hay tick-data. El Exit Journal solo puede construir trayectoria completa para símbolos capturados en vivo de ahora en adelante, no retroactivamente sobre los 30 días históricos (que solo tienen snapshot inicial + cierre).

**Integración con `live_integration.py`** (aditiva, sin tocar Radar Explosivo ni `/atlas`):
- Sesión **regular**: por cada símbolo del ranking oficial ya sellado, registra un punto de trayectoria en cada ciclo (`_track_trajectory`).
- Sesión **afterhours/closed** (mismo momento que la calificación del Prediction Journal): cierra el resumen objetivo de cada símbolo recién calificado (`close_exit_summary`), usando la hora de sellado como "entrada" y la hora de calificación como cierre de la ventana.

**Probado con datos sintéticos** (`test_exit_journal.py`, 9 casos + 3 nuevos en `test_live_integration.py`): trayectoria guardada y recuperada en orden; resumen objetivo correcto (detección/entrada/pico/final/duración) sin ningún umbral; cierre único garantizado; sin muestras no inventa nada; las 4 funciones de derivación dan resultados distintos con umbrales distintos sobre la misma trayectoria (confirma que nada quedó fijo); sesión regular acumula trayectoria solo si hay ranking sellado ese día; el cierre del Exit Journal ocurre en el mismo ciclo que la calificación del Prediction Journal. Toda la batería completa del Memory Engine (Clasificador, tasas base, horarios, integración en vivo, demo de 2026-07-30) se re-corrió sin regresiones.

**Pendiente, explícitamente fuera de esta fase**: el Exit Pattern Engine que use esta memoria para proponer umbrales con evidencia -- no empieza hasta que haya trayectorias reales acumuladas.

---

**Fuera de este plan, explícitamente**: aplicar cualquier propuesta de recalibración a `explosive_config.json`/`explosive_factors.py` de forma permanente -- requiere su propia propuesta formal y aprobación, cada vez.

**Orden de ejecución**: estrictamente 1→8, sin saltos, cada uno aprobado antes de empezar el siguiente.
