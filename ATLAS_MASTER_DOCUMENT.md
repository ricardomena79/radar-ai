# ATLAS_MASTER_DOCUMENT.md

> **⚠️ Nota de vigencia (Hito 5, Fase 5.4, 2026-09-04)**: este documento no se actualiza desde antes del 2026-08-17 -- no refleja Hito 3 (Fases 3.0-3.6, sistema de aprendizaje-seguro: elegibilidad, activación controlada, evaluación continua/revocación, cerrado y auditado) ni Hito 4 (Fases 4.1-4.4, observabilidad/paneles, cerrado y auditado -- localmente, sin commit/push/deploy todavía, a diferencia de Hito 3 que sí está commiteado y pusheado a esta rama). Para el estado real, ver el código y `.claude/plans/ethereal-mixing-anchor.md`.

**Documento oficial único del proyecto Atlas.** Contiene el conocimiento completo del proyecto: no es un resumen, es la fuente de verdad consolidada para que cualquier arquitecto o IA pueda continuar el trabajo sin leer una sola conversación anterior.

**Regla de vigencia**: si algo aquí contradice el estado real del código o de un documento fuente (listados en cada sección), el código y el documento fuente tienen prioridad — este documento debe corregirse, no el código. Es un documento vivo: se actualiza después de cada entregable, decisión o cambio de fase importante.

**Nada aquí fue inventado.** Todo número, decisión y estado proviene de un documento existente en el repositorio o del código real. Donde algo pedido no existe todavía (por ejemplo, un "Ranking Engine" como módulo independiente, o una lista predefinida de "83 puntos"), este documento lo dice explícitamente en vez de inventarlo — ver secciones 9 y 18.

Fecha de compilación: 2026-08-02.

---

## ÍNDICE

1. Visión y objetivo real de Atlas
2. Qué problema resuelve Atlas
3. Filosofía del proyecto
4. Constitución completa
5. Arquitectura completa (todas las capas)
6. Flujo completo de Atlas: desde que abre el mercado hasta el ranking
7. Explicación detallada de cada módulo
8. Memory Engine
9. Ranking Engine
10. Radar Explosivo
11. Mission Control
12. Pattern Store
13. Pattern Evolution
14. Knowledge Engine y el resto de las capas de conocimiento/aprendizaje
15. Decisiones técnicas aprobadas
16. Decisiones descartadas y por qué
17. Roadmap completo
18. Checklist maestro (todas las tareas, todos los módulos, todos los entregables)
19. Estado actual del proyecto
20. Próximos pasos

---

## 1. VISIÓN Y OBJETIVO REAL DE ATLAS

Fuente: [ATLAS_CONSTITUTION.md](ATLAS_CONSTITUTION.md).

Atlas existe para **detectar las mejores oportunidades de trading intradía de alto momentum antes que el mercado las descubra**. No fue creado para encontrar las mejores empresas ni para invertir a largo plazo — fue creado para detectar oportunidades explosivas.

**Objetivo operativo exacto**: detectar acciones con alta probabilidad de realizar un movimiento explosivo durante los próximos 5 a 10 minutos.

**La pregunta que filtra toda decisión futura del proyecto**: *"¿Este cambio mejora la capacidad de Atlas para detectar antes las acciones explosivas?"* — si la respuesta es NO, el cambio no se implementa. Esta pregunta, no el entusiasmo por una idea, es el criterio de admisión de cualquier propuesta.

---

## 2. QUÉ PROBLEMA RESUELVE ATLAS

Un trader intradía humano no puede vigilar ~2.577 símbolos (el Universo Racional completo) en tiempo real para detectar, en los primeros minutos, cuál de ellos está iniciando un movimiento explosivo. Atlas automatiza esa vigilancia: escanea el universo, aplica un modelo de elegibilidad y puntaje explícito (nunca una caja negra) y entrega un ranking accionable de candidatos, con la razón de cada inclusión.

Explícitamente, Atlas **no** resuelve: selección de acciones de calidad para invertir a largo plazo, dividendos, value investing, ni screening genérico de mercado (ver sección 4, "Lo que Atlas nunca hará").

Evidencia honesta sobre qué tan bien resuelve el problema hoy: la validación histórica de 30 días (sección 10) mostró que la versión actual del motor detecta apenas el 2.33% de las oportunidades reales — el problema que Atlas busca resolver todavía no está resuelto de forma efectiva; el proyecto tiene, con evidencia, un diagnóstico claro de por qué y un plan de mejora (Cambio Nº1 en curso).

---

## 3. FILOSOFÍA DEL PROYECTO

1. **Evolución por evidencia, no por acumulación.** Adoptada el 2026-08-01. Atlas no crece agregando funciones por intuición. Todo cambio sigue 8 pasos en orden: identificar problema real → proponer solución → justificar contra la Constitución → identificar qué métrica oficial mejora → implementar solo si es validable objetivamente → validar con datos históricos → validar con datos en tiempo real → recién entonces considerar el cambio permanente.
2. **Un cambio a la vez.** Nunca se mueven dos variables grandes en movimiento simultáneamente (ejemplo real: RVOL se congeló explícitamente para no analizarlo junto con Liquidez).
3. **Simplicidad sobre acumulación de indicadores** (Principio 7 de la Constitución) — un diseño de 3 etapas se mantiene mientras no haya evidencia en contra; no se rediseña desde cero sin motivo medido.
4. **Transparencia radical, incluida la que incomoda.** Ejemplos reales dentro de este mismo proyecto: se reportó que 5 de las 15 "mayores ganadoras" de la validación eran casi con certeza artefactos de datos, no movimientos reales — no se ocultó para no ensuciar el resultado. Se reportó que un candidato de mejora (techo de precio) que parecía prometedor con 8 días dejó de sostenerse con 30 días completos — se descartó en vez de mantenerse por inercia.
5. **Ningún dato inventado.** Cuando falta evidencia (por ejemplo, el score de Momentum nunca se persistió en la validación histórica), el documento correspondiente dice explícitamente "sin datos" en vez de estimar un número.
6. **Aprobación incremental explícita.** Ninguna fase, entregable o cambio avanza sin aprobación explícita del paso anterior. "No agreguemos más alcance" es una instrucción que se ha aplicado literalmente durante el proyecto (Entregable Nº1 de Mission Control).
7. **Separación estricta de capas** (ver sección 5): lo experimental nunca toca lo congelado sin pasar primero por evidencia.

---

## 4. CONSTITUCIÓN COMPLETA

Fuente primaria y autoridad máxima del proyecto: [ATLAS_CONSTITUTION.md](ATLAS_CONSTITUTION.md), adoptada el 2026-08-01. Reproducida aquí en su totalidad porque este documento debe permitir continuar sin leer archivos externos.

### Misión

Atlas existe para detectar las mejores oportunidades de trading intradía de alto momentum antes que el mercado las descubra. No fue creado para encontrar las mejores empresas. No fue creado para invertir a largo plazo. Fue creado para detectar oportunidades explosivas.

### Objetivo

Detectar acciones con alta probabilidad de realizar un movimiento explosivo durante los próximos 5 a 10 minutos. Toda modificación futura debe responder: *"¿Este cambio mejora la capacidad de Atlas para detectar antes las acciones explosivas?"* Si la respuesta es NO, el cambio no debe implementarse.

### Los 8 principios

1. Los datos tienen prioridad sobre las opiniones.
2. Todo cambio debe poder medirse.
3. Ningún algoritmo nuevo entra a Atlas Core sin haber sido validado previamente.
4. Atlas siempre debe explicar por qué recomienda una acción.
5. El proveedor de datos nunca podrá estar acoplado al motor.
6. Radar Explosivo es el módulo más importante del sistema.
7. La simplicidad vale más que agregar indicadores.
8. Ninguna propuesta importante podrá implementarse sin respetar esta Constitución.

### Lo que Atlas nunca hará

- No buscará dividendos.
- No buscará value investing.
- No priorizará las mejores empresas.
- No será un screener genérico.
- No optimizará para inversiones de largo plazo.

### Métricas oficiales

Toda mejora debe demostrar impacto medible sobre al menos una: **Precision@10, Precision@20, Recall, tiempo de detección, falsos positivos, falsos negativos.** Si una modificación no mejora ninguna, debe justificarse antes de aprobarse.

### Arquitectura (mandato constitucional)

Atlas Core debe permanecer independiente. Todo desarrollo experimental se realiza inicialmente dentro de `atlas_live`. Solo después de validarse con evidencia puede incorporarse al Core.

### Regla de oro

Antes de escribir cualquier código importante, comprobar que la propuesta respeta este documento. Si una propuesta contradice la Constitución, se detiene la implementación y se explica antes de realizar cambios. Toda propuesta debe indicar explícitamente cuál(es) de los 8 principios la respaldan.

### Metodología de propuestas (evolución por evidencia)

8 pasos en orden: 1) identificar un problema real, 2) proponer una solución, 3) explicar por qué respeta la Constitución, 4) explicar qué métricas oficiales mejorará, 5) implementar solo si existe forma objetiva de validar, 6) validar primero con datos históricos, 7) validar después con datos en tiempo real, 8) solo entonces considerar incorporación permanente.

Formato obligatorio de toda propuesta, que debe esperar aprobación antes de implementarse:

```
PROBLEMA:
HIPÓTESIS:
PRINCIPIOS DE LA CONSTITUCIÓN QUE RESPALDAN ESTA PROPUESTA:
IMPACTO ESPERADO:
RIESGOS:
CÓMO SE VALIDARÁ:
CRITERIOS DE ÉXITO:
```

Registro de cada propuesta evaluada: [DECISION_LOG.md](DECISION_LOG.md).

### Documentos previos de Atlas Core, distintos de esta Constitución (importante no confundirlos)

Antes de que existiera `ATLAS_CONSTITUTION.md` (adoptada 2026-08-01), Atlas Core ya tenía dos documentos internos propios en `atlas/docs/`, escritos durante la construcción del Core (2026-07-29/30). **Siguen vigentes, pero tienen un alcance distinto**: son la filosofía de *trading/riesgo* del Decision Engine de Atlas Core, no la gobernanza del proyecto completo. No fueron reemplazados ni contradichos por la Constitución — conviven con ella en capas distintas.

**`atlas/docs/ATLAS_PRINCIPLES.md`** (7 principios de riesgo/trading):
1. El capital se protege antes de buscar ganancias.
2. Atlas nunca opera por emociones. Solo por evidencia.
3. Si no existe ventaja estadística... NO HAY OPERACIÓN.
4. Es mejor perder una oportunidad que quedar atrapado en una mala operación.
5. Atlas busca consistencia. No busca hacerse rico en una sola operación.
6. La mejor operación no siempre es la acción que más sube. Es la que ofrece la mejor relación entre riesgo y probabilidad.
7. Cada operación sirve para aprender. Cada error mejora Atlas.

**`atlas/docs/ATLAS_RULES.md`** (5 reglas operativas de Decision Engine):
1. Si la acción NO está disponible en Racional → DESCARTAR, no se sigue analizando.
2. Atlas solo busca operaciones de 5 a 20 minutos, no inversiones de largo plazo.
3. No perseguir una acción después de una subida extrema sin una nueva señal.
4. La decisión siempre está basada en datos, no en emociones.
5. Atlas entrega solo tres oportunidades por sesión: 🥇 Principal, 🥈 Alternativa, 🥉 Condicional.

**Nota de honestidad importante**: la Regla 5 (3 oportunidades por sesión, de Decision Engine) y el `top_n=20` de Radar Explosivo (sección 10) son contratos de salida distintos, de dos motores distintos, que no se armonizaron entre sí en ningún documento — son dos productos con propósitos diferentes (Decision Engine responde "¿es buena inversión?"; Radar Explosivo responde "¿se está moviendo rápido ahora?", ver Decisión del 2026-08-01 en sección 15).

---

## 5. ARQUITECTURA COMPLETA (TODAS LAS CAPAS)

Fuentes: [ATLAS_BOOTSTRAP.md](ATLAS_BOOTSTRAP.md), `atlas/docs/ARCHITECTURE.md`, [ATLAS_MISSION_CONTROL.md](ATLAS_MISSION_CONTROL.md).

### Separación de primer nivel

```
/atlas            Atlas Core -- el "cerebro". Congelado en v1.0 desde 2026-07-30/31.
atlas_live/        Todo lo experimental, en vivo y de presentación. Nunca modifica /atlas directamente.
```

### Atlas Core (`/atlas`) — 8 capas, verificadas sin dependencias circulares

**Principios permanentes del Core** (`atlas/docs/ARCHITECTURE.md`): todo dato de mercado pasa por Data Collector (ningún motor consulta Yahoo Finance directamente); cada módulo tiene una única responsabilidad; sin IA — todo scoring es fórmulas y umbrales explícitos visibles en el código; los motores de análisis solo miden y proponen, nunca deciden ni aplican solos; conocimiento del mercado y del operador nunca se mezclan; nunca se borra conocimiento (los patrones cambian de estado, nunca se sobrescriben); trazabilidad completa (fuente, hora, estado del dato, versión del motor en cada evento/predicción).

| Capa | Módulos | Responsabilidad |
|---|---|---|
| **0 — Datos e indicadores** | `atlas/data/providers/` (YahooFinanceProvider), `atlas/data/collectors/data_collector.py` (única puerta de entrada, caché en memoria), `atlas/data/universe/` (Universo Racional, 2577 instrumentos), `atlas/data/models/quote.py` (`Quote` normalizado), `atlas/storage/memory_cache.py`, `atlas/indicators/` (10 indicadores puros: EMA, SMA, RSI, MACD, ATR, Volatility, RVOL, Dollar Volume, VWAP, Gap%) | Fundación de datos |
| **1 — Motores de puntuación** (`atlas/engine/`, **protegidos**) | `score_engine.py`+`atlas_score.py` (Atlas Score, 7 factores), `momentum_engine.py` (Momentum Score, 9 factores), `money_flow_engine.py` (por sector/industria), `market_context_engine.py` (SPY/QQQ/IWM/VIX/BTC, sector líder, calendario), `decision_engine.py` (COMPRAR/VIGILAR/DESCARTAR, confianza acumulativa, checklist explicable) | Puntaje y decisión de inversión |
| **2 — Scanners** | `atlas/scanners/premarket.py` (universo completo), `atlas/scanners/momentum_radar.py` (ranking especializado) — también existen `afterhours.py`, `etfs.py`, `microcaps.py`, `momentum.py` | Consumen Capa 0+1, no escriben en ningún repositorio |
| **3 — Persistencia** (dos dominios sin cruce) | `atlas/knowledge/` (`event_store`, `prediction_store`, `pattern_store`, `knowledge_engine`) → `atlas_knowledge.db`, dominio **mercado**; `atlas/decision_journal/` → `decision_journal.db`, dominio **operador** | Ver secciones 12-14 |
| **4 — Registro único** | `atlas/decision_recorder/` (`DecisionRecorder`) | Único punto de escritura autorizado hacia Knowledge Base y Decision Journal — `record_decision()`, `record_market_event()`, `record_trade()` |
| **5 — Aprendizaje** (dos motores independientes) | `atlas/learning/` (`LearningEngine`, fachada de `AccuracyTracker`+`PatternEvolution`+`CalibrationAdvisor`) aprende de Knowledge Base; `atlas/operator_learning/` (`OperatorLearningEngine`) aprende de Decision Journal | Ninguno escribe nada — solo devuelven reportes/propuestas |
| **6 — Gobernanza** | `atlas/calibration_manager/` (`CalibrationManager`) | Única puerta de entrada para modificar conocimiento permanente. Ciclo `Pendiente → Revisada → Aprobada/Rechazada → Implementada`. Sin imports de `knowledge`/`learning` (duck typing) |
| **7 — Investigación** (interfaces, sin lógica completa) | `atlas/research_lab/`, `atlas/strategy_lab/` | `NotImplementedError` declarado, listo para cuando haya historial suficiente |

**Componentes protegidos** (no se modifican salvo necesidad técnica explícita aprobada): los 5 motores de Capa 1, Decision Recorder, Calibration Manager, la separación Knowledge Base/Decision Journal.

**Extensibles dentro de la arquitectura existente**: nuevos indicadores, nuevos análisis en ResearchLab/StrategyLab/OperatorLearningEngine, nuevas dimensiones de reporte en AccuracyTracker, nuevos scanners.

**Pendiente conocido, documentado, no resuelto en v1.0**: no existe vínculo garantizado entre una predicción (`PredictionRecord.event_id`) y el evento que confirma su resultado — `AccuracyTracker` lo resuelve con correlación de solo lectura (mismo ticker + misma fecha + hora más cercana), funcional pero no garantizada.

### Atlas Live (`atlas_live/`)

```
atlas_live/
  explosive_engine.py       Radar Explosivo -- motor de 3 etapas (ver sección 10)
  explosive_factors.py      Registro de factores enchufables de Radar Explosivo
  explosive_config.py/.json Configuración externa del motor
  explosive_diagnostics.py  Modo Diagnóstico (embudo + auditoría por símbolo)
  scan_worker.py            Orquestador del escaneo en vivo (usa SOLO Atlas Core + Radar Explosivo)
  server.py                 Servidor Flask, cero lógica de negocio, delega en scan_worker
  static/                   Dashboard (HTML/CSS/JS sin framework): app.js, index.html, style.css, notifications.js
  backtest/                 Infraestructura de validación histórica (ver sección 10)
  mission_control/          Centro de Operaciones (ver sección 11)
```

**Regla de capas explícita**: todo desarrollo experimental nace aquí. Solo sube a `/atlas` con evidencia validada. `explosive_engine.py` es la única excepción documentada a "consume Atlas Core sin reimplementar lógica propia" (documentado en el docstring de `atlas_live/__init__.py`) — reutiliza fórmulas puras de `score_engine`/`momentum_engine` en modo lectura, sin duplicarlas ni modificarlas.

---

## 6. FLUJO COMPLETO DE ATLAS: DESDE QUE ABRE EL MERCADO HASTA EL RANKING

Reconstruido a partir de los docstrings reales de `scan_worker.py`, `explosive_engine.py`, `server.py` y del dashboard.

**Escaneo en vivo (`atlas_live/scan_worker.py`)**:
1. Recorre un watchlist tomado del Universo Racional (muestra de 200 símbolos: 150 acciones + 50 ETFs, en el escaneo en vivo — distinto de la validación histórica, que usa el Universo Racional completo de ~2.577).
2. Para cada símbolo, calcula el ranking completo usando **exclusivamente Atlas Core**: Data Collector obtiene el `Quote`, luego Atlas Score, Momentum Engine, Money Flow Engine, Market Context Engine y Decision Engine, en ese orden de dependencia (Capa 0 → Capa 1 de la sección 5).
3. **Adicionalmente** (sin repetir ninguna llamada de red ni recalcular ningún indicador), pasa el mismo `Quote`/`MomentumResult` ya calculado a `explosive_engine.evaluate()` — Radar Explosivo. Esta es la única llamada de este flujo que no es Atlas Core puro.
4. Registra cada decisión real de Decision Engine en la Knowledge Base vía `DecisionRecorder` (primera vez que ese componente se usa en un flujo real, no de prueba).
5. Cachea el resultado completo en memoria.

**Servidor (`atlas_live/server.py`)**: expone el estado ya cacheado como JSON vía endpoints (incluye `/api/explosive-diagnostics`) y sirve el dashboard estático. No calcula nada — cero lógica de negocio en esta capa, todo delega en `scan_worker`.

**Dashboard (`atlas_live/static/`)**: 4 secciones navegables — 🔥 Radar Explosivo (pantalla principal), 📈 Radar General, 📋 Watchlist, 🔬 Diagnóstico. Notificaciones activas (navegador, sonido, resaltado visual) cuando aparece una oportunidad nueva en Radar Explosivo (compara el set de elegibles entre polls, nunca repite el mismo símbolo).

**Dentro de Radar Explosivo (`explosive_engine.evaluate()`), por símbolo**:
- **Etapa A — Elegibilidad** (pasa/no pasa): 6 gates secuenciales (price → liquidity → rvol → movement → volatility → size), cada uno con su umbral en `explosive_config.json`. Deja un `stage_trace` de qué etapas superó y `failed_stage` si fue descartado.
- **Etapa B — Puntaje ponderado**: si es elegible, cada factor registrado en `explosive_factors.py` (relative_volume, volatility, momentum, gap, vwap_distance, sector_money_flow, float) devuelve un puntaje 0-100 + una razón en lenguaje natural; se pondera por los pesos de `explosive_config.json`, normalizando solo por los factores que sí pudieron calcularse.
- **Etapa C — Ajuste por tamaño**: penalización continua (no binaria) para mega-caps, con excepción explícita si hay catalizador extremo simultáneo (gap ≥5% y RVOL ≥5x).
- Resultado: `eligible`, `score`, `reasons` (lenguaje natural), `excluded_reason` si aplica, `metrics` crudas, `stage_trace`.

**Ranking final que ve el usuario**: los elegibles se ordenan por `score` descendente; el dashboard muestra el top configurado (`top_n=20` en `explosive_config.json`). Esto es lo más cercano a un "Ranking Engine" que existe hoy — ver sección 9 para la aclaración completa de por qué no es un módulo separado.

---

## 7. EXPLICACIÓN DETALLADA DE CADA MÓDULO

Ver también secciones 8 a 14 para los módulos que el usuario pidió en detalle propio (Memory Engine, Ranking Engine, Radar Explosivo, Mission Control, Pattern Store, Pattern Evolution, Knowledge Engine).

### Atlas Core

- **`atlas/data/providers/`**: interfaz `DataProvider` abstracta + `YahooFinanceProvider`, única implementación actual. Diseñada para intercambiarse sin tocar el motor (Principio 5).
- **`atlas/data/collectors/data_collector.py`**: única puerta de entrada a datos de mercado, con caché en memoria. Ningún motor llama al proveedor directamente.
- **`atlas/data/universe/`**: Universo Racional, 2577 instrumentos, cargado desde un PDF oficial estático.
- **`atlas/indicators/`**: 10 indicadores puros (EMA, SMA, RSI, MACD, ATR, Volatility, RVOL, Dollar Volume, VWAP, Gap%), sin estado, reutilizados tanto por Atlas Core como (en modo lectura) por `historical_scan.py`.
- **`atlas/engine/score_engine.py` + `atlas_score.py`**: Atlas Score, 7 factores ponderados.
- **`atlas/engine/momentum_engine.py`**: Momentum Score, 9 factores, reutiliza componentes de score_engine.
- **`atlas/engine/money_flow_engine.py`**: Money Flow Score por sector/industria.
- **`atlas/engine/market_context_engine.py`**: contexto de mercado (índices, VIX, BTC, sector líder, calendario).
- **`atlas/engine/decision_engine.py`**: produce COMPRAR/VIGILAR/DESCARTAR con confianza acumulativa y checklist explicable (Principio 4 de la Constitución, "Atlas siempre explica por qué").
- **`atlas/engine/explosion_index.py`**: **stub vacío**, nunca implementado — fue el plan original para un "Índice de Explosión" dentro del Core, reemplazado deliberadamente por Radar Explosivo dentro de `atlas_live` (ver Decisión, sección 15/16).
- **`atlas/scanners/`**: `premarket.py`, `momentum_radar.py` (implementados y en uso conceptual), más `afterhours.py`, `etfs.py`, `microcaps.py`, `momentum.py` (existen como archivos, alcance de uso no auditado en esta sesión).
- **`atlas/decision_journal/`**: repositorio puro de operaciones/decisiones del **operador** (dominio separado de conocimiento de mercado).
- **`atlas/decision_recorder/`**: único punto de escritura autorizado hacia Knowledge Base y Decision Journal.
- **`atlas/calibration_manager/`**: única puerta de entrada para aplicar cambios permanentes de conocimiento (patrones) o registrar calibraciones de motor (que siguen requiriendo edición humana).
- **`atlas/operator_learning/`**: aprende del Decision Journal (patrones del propio operador humano, no del mercado) — dominio completamente separado de `atlas/learning/`.
- **`atlas/research_lab/`, `atlas/strategy_lab/`**: interfaces declaradas, sin lógica implementada (`NotImplementedError`), preparadas para cuando haya historial real suficiente.
- **`atlas/alerts/`**: paquete vacío en Core, sin uso — toda la infraestructura de alertas real vive en `atlas_live` (ver Fase 7 del roadmap, sección 17).
- **`atlas/backtesting/runner.py`**: existe como paquete de Core, distinto del backtest de Radar Explosivo (`atlas_live/backtest/`) — no auditado en detalle en esta sesión.
- **`atlas/cache/`**: contiene las bases reales `atlas_knowledge.db` y `decision_journal.db`.

### Atlas Live — módulos de presentación y orquestación

- **`atlas_live/scan_worker.py`**: ver sección 6.
- **`atlas_live/server.py`**: ver sección 6.
- **`atlas_live/static/`**: dashboard; `notifications.js` implementa los 3 canales de alertas (navegador, sonido, resaltado) con arquitectura de registro (`NOTIFICATION_CHANNELS`, mismo patrón que `explosive_factors.py`) para agregar canales futuros (push, correo, webhook, Telegram, Discord) sin tocar la lógica de detección.

### Atlas Live — Radar Explosivo, Mission Control, Pattern Store/Evolution/Knowledge Engine

Ver secciones 10, 11, 12, 13, 14 respectivamente para el detalle completo pedido de estos módulos.

---

## 8. MEMORY ENGINE

**Estado: ATLAS ALPHA 1.0 -- BASELINE CONGELADA (2026-08-02).** Documento oficial completo: [MEMORY_ENGINE.md](MEMORY_ENGINE.md) — propuesta formal en el formato de la Constitución (PROBLEMA/HIPÓTESIS/PRINCIPIOS/ARQUITECTURA/FLUJO DE DATOS/QUÉ APRENDE/QUÉ GUARDA/QUÉ DESCARTA/CÓMO EVOLUCIONA/IMPACTO/RIESGOS/VALIDACIÓN/CRITERIOS DE ÉXITO), aprobada, implementada y congelada como primera versión funcional del Memory Engine. **Alcance ampliado respecto al diseño conceptual original de esta sección**: no es memoria solo de explosiones — estudia TODO el mercado escaneado a diario (explosivas, normales, débiles, perdedoras, falsas rupturas, microcaps, ETFs, sectores, horarios, volumen), para recalibrar el Ranking de Radar Explosivo con tasas base reales de la población completa.

**Componentes construidos y validados** (detalle exacto de cada uno, con sus pruebas, en la sección 18 bloque K):
- Memory Store, Clasificador de Resultado, Carga histórica retroactiva (73.123 observaciones reales de los 30 días validados), Motor de tasas base, Generador de Propuestas -- los 5 primeros entregables del plan original, completos.
- **Ranking Score de desempate** (`ranking_score.py`) -- 4 niveles de prioridad estricta (sin pesos inventados), validado con mejora medible: NUWE #25→#2, XRX #39→#1, Precision@10 20%→70% en el día de prueba.
- **Prediction Journal** (`prediction_journal.py`) -- registro dinámico + sellado único inmutable + calificación automática + tiempo de anticipación.
- **Integración en tiempo real** con `scan_worker.py` (`market_hours.py`, `live_integration.py`) -- detección de sesión, snapshot dinámico en premarket, sellado automático antes de la apertura, calificación automática al cierre.
- **Modo Interactivo Continuo** -- recalibración diaria automática de la evidencia, apagado prolijo sin pérdida de estado.

**Pendiente, no incluido en esta baseline**: validación agregada de Precision@10/@20/Recall sobre los 30 días completos (Entregable 6 completo); checkpoints intermedios (Entregable 8); verificación en condiciones de mercado reales (todo lo anterior se probó con datos sintéticos y/o históricos, nunca contra el premarket real todavía).

**Regla vigente a partir de esta baseline**: cualquier mejora futura al Memory Engine debe demostrar una mejora medible respecto a Atlas Alpha 1.0 antes de incorporarse -- mismo principio de evolución por evidencia que rige todo el proyecto, ahora aplicado explícitamente a esta baseline como punto de comparación.

### Lo que ya existe y es la base real para este diseño

Atlas Core (congelado) ya contiene, para el dominio de Decision Engine (no para Radar Explosivo), el equivalente funcional de una memoria: `event_store.py` + `pattern_store.py` + `prediction_store.py` + `knowledge_engine.py` (fachada) — ver secciones 12-14 para el detalle completo de estos módulos reales. **Radar Explosivo nunca escribió en esta base** — las 30 explosiones diarias de la validación histórica viven en archivos JSON planos (`atlas_live/backtest/results_v1/`, `results_v2/`), no en una base consultable con búsqueda de similitud.

### Las cuatro preguntas de diseño originales y su respuesta (mantenidas y ratificadas en el diseño formal aprobado)

1. **Cómo almacenar cada explosión histórica**: mismo patrón de `event_store.py` (SQLite, WAL, índices por símbolo/sector/fecha) pero un Event Store **nativo de Radar Explosivo** (no el de Core), porque las métricas son distintas (`price, gap_pct, change_pct, relative_volume, dollar_volume, volatility_score, market_cap` vs. `atlas_score, momentum_score, money_flow_score` de Core). Cada fila guardaría todo lo que el radar vio, elegible o no, no solo los aciertos.
2. **Cómo comparar una acción actual contra miles de explosiones anteriores**: mismo mecanismo de `pattern_store.py` — vector de features normalizadas, distancia euclidiana, k-vecinos-más-cercanos determinista. Explícitamente **sin caja negra, sin embeddings, sin ML opaco** (exigencia del Principio 4: Atlas siempre debe explicar su recomendación). Generalización propuesta: de "ADN por símbolo" (lo que ya hace `pattern_store.py`) a "ADN por evento" (comparar contra cualquier explosión pasada de cualquier símbolo).
3. **Cómo aprenderá Atlas por qué explotó una acción**: con honestidad explícita — hoy Atlas puede explicar el **cómo** (qué condiciones cuantitativas se cumplieron) pero no el **porqué causal** (noticia, catalizador, rotación sectorial); esa fuente de datos no existe en el proyecto. Lo alcanzable con lo que hay: mismo enfoque de `PatternEvolution` (sección 13) — agrupar por combinaciones de rango de features, medir `win_rate` con significancia estadística (intervalo de Wilson), aprendizaje por correlación validada, no por causalidad.
4. **Cómo convertir esa memoria en un algoritmo de probabilidad de explosión futura**: no un modelo de ML de caja negra. Composición de las dos piezas anteriores, aplicada hacia adelante: vector de features de la acción actual → K vecinos más cercanos en la memoria → probabilidad = `win_rate` histórico de ese grupo, siempre junto con el tamaño de muestra y el intervalo de confianza que lo sostienen. Sin muestra suficiente, la respuesta es "evidencia insuficiente", nunca un número inventado.

### Dónde debería vivir

No en `/atlas` (Principio 3: ningún algoritmo nuevo entra a Core sin validación previa). Vive en `atlas_live/memory/`, modelado sobre el patrón ya probado de Core, con el set de features real de Radar Explosivo. Solo después de validarse con evidencia se propondría, si corresponde, unificar con el Knowledge Engine de Core.

**Estado real de aprobación**: la propuesta formal completa (formato de la sección 4) fue presentada y **aprobada** el 2026-08-02, con el diseño explícitamente congelado (no se amplía la arquitectura sin una decisión nueva). El plan de implementación de 8 entregables, el Ranking Score, el Prediction Journal, la integración en tiempo real y el Modo Interactivo Continuo también están aprobados e implementados, y constituyen en conjunto **Atlas Alpha 1.0**, la baseline oficial congelada. Ver [MEMORY_ENGINE.md](MEMORY_ENGINE.md) para el texto completo de cada propuesta, y la sección 18 (bloque K) para el estado exacto de cada componente.

---

## 9. RANKING ENGINE

**Aclaración honesta antes de describirlo: no existe un módulo llamado "Ranking Engine" en el proyecto.** No inventar uno aquí sería contradecir el principio de este documento ("no inventar nada"). Lo que existe, repartido en dos lugares distintos, es:

1. **La función de ranking real y en uso**, dentro de Radar Explosivo — Etapa B de `explosive_engine.py` (sección 6/10): puntaje ponderado por factores, ordenado descendente, top 20 configurable. Esto es lo único que hoy "rankea" candidatos en producción.
2. **Un diseño de ranking distinto, para el informe comparativo V1 vs V2, no implementado**: "Ranking Top 20" — definido conceptualmente como los 20 símbolos que más veces aparecieron en el top-20 elegible diario (por score real) a lo largo de un período de 30 días, ordenados por frecuencia de aparición y, en empate, por posición promedio. Esta definición fue propuesta y sometida a aprobación del usuario como parte del diseño del generador automático del Informe Comparativo Oficial V1 vs V2 (sección 15/18) — **el código de este informe todavía no se escribió**, solo se explicó el diseño.

Si en el futuro se decide construir un "Ranking Engine" como componente propio y desacoplado (por ejemplo, para servir rankings de múltiples fuentes -- Radar Explosivo, Decision Engine, un futuro Memory Engine -- de forma unificada), eso es una propuesta nueva, no algo que ya exista, y debe pasar por la metodología de la sección 4.

---

## 10. RADAR EXPLOSIVO

Fuentes: [RADAR_EXPLOSIVO_V2.md](RADAR_EXPLOSIVO_V2.md), [VALIDATION_RESULTS.md](VALIDATION_RESULTS.md), [INFORME_EJECUTIVO_FINAL.md](INFORME_EJECUTIVO_FINAL.md), [DECISION_LOG.md](DECISION_LOG.md), código real (`atlas_live/explosive_engine.py`, `explosive_factors.py`, `explosive_config.py/json`).

### Qué es

Motor propio de `atlas_live`, **independiente de Decision Engine** (Decisión, sección 15). Responde: "¿qué tan probable es que este símbolo tenga un movimiento fuerte en los próximos 5-10 minutos?" — no "¿es una buena inversión?". Es, por mandato del Principio 6 de la Constitución, el módulo más importante del sistema.

### Diseño: 3 etapas (sin cambios de arquitectura, evidencia no encontró motivo para rediseñarlo)

- **Etapa A — Elegibilidad**: 6 gates en `explosive_config.json` → `gates`: `min_price` (1.0), `min_dollar_volume` (2,000,000), `min_rvol` (**0.0 desde el Cambio Nº1**, era 2.0), `min_abs_gap_or_change_pct` (2.0), `min_volatility_score` (50.0), `large_cap_ceiling` (10,000,000,000) con excepción `mega_cap_exception_gap_pct` (5.0) + `mega_cap_exception_rvol` (5.0).
- **Etapa B — Puntaje ponderado**: `weights` → `relative_volume` 0.25, `volatility` 0.15, `momentum` 0.15, `gap` 0.15, `vwap_distance` 0.10, `sector_money_flow` 0.10, `float` 0.10. Cada factor es una función pura registrada en `explosive_factors.py` (patrón "enchufable": agregar un factor nuevo = una función + una entrada de peso, sin tocar el motor).
- **Etapa C — Ajuste por tamaño**: `size_factor` → penalización logarítmica continua entre `small_cap_reference` ($300M) y `mega_cap_reference` ($200B), `min_factor` 0.5 a `max_factor` 1.0, con excepción por catalizador extremo (ver Etapa A).
- `top_n`: 20 (cuántos se muestran en el dashboard).

### Validación histórica — Validación 1, 2026-08-01 (config original, antes del Cambio Nº1)

30 sesiones (2026-06-18 a 2026-07-31), Universo Racional completo, 73.123/77.310 reconstrucciones exitosas (94.6%), snapshot a los 10 minutos con velas reales de 5 minutos (sin lookahead).

| Métrica | Valor |
|---|---|
| Precision@10 | 4.67% |
| Precision@20 | 2.33% |
| Recall | 2.33% |
| Falsos positivos | 15 (30 días) |
| Falsos negativos | 586 de 600 posibles (97.7%) |
| Oportunidades elegibles totales | 29 en 30 días |

Motivo de los 586 descartes: RVOL 56.3% (330), Liquidez 33.3% (195), Precio 10.2% (60), Volatilidad 0.2% (1), Movimiento 0%.

**Hallazgo de calidad de datos, reportado sin ocultar**: al menos 5 de las 15 "mayores ganadoras" (FFAI +9.543%, CCG +2.537%, PRPL +2.141%, ENFY +410%/+259%) son casi con certeza artefactos de datos (splits no ajustados, tickers ilíquidos), no movimientos reales — probablemente desplazaron a ganadoras genuinas del top-20 diario, por lo que el Recall real probablemente está subestimado. No se corrigió retroactivamente.

### Explosive DNA (600 observaciones explosivas reales, período completo)

Separación explosivas vs. resto del universo: Cambio% 98.3%, Gap% 97.0%, RVOL 88.5%, Volatilidad 76.6%, Volumen$ 65.8%, Market Cap 22.6% (73.2% de las observaciones con `market_cap=None` — vacío de datos sin resolver), Precio 17.7% (relación inversa — las explosivas son más baratas).

### Comparación de 5 escenarios sobre el rol de RVOL — CONFIRMADA con los 30 días completos

| Escenario | Precision@10 | Precision@20 | Recall | Falsos positivos |
|---|---|---|---|---|
| 1. Radar actual (RVOL como gate 2.0x) | 4.67% | 2.33% | 2.33% | 15 |
| 2. Sin RVOL (gate y score en 0) | 28.33% | 19.83% | 46.33% | 8.084 |
| 3. Mejor umbral probado (0.5x) | 14.67% | 7.33% | 7.33% | 90 |
| 4. RVOL con menos peso (mismo gate) | 4.67% | 2.33% | 2.33% (sin cambio) | 15 |
| 5. RVOL solo como factor de puntuación | 30.67% | 20.17% | 46.33% | 8.084 |

Conclusión confirmada: quitar a RVOL del rol de filtro excluyente multiplica Precision@10 por ~6.5x, Precision@20 por ~8.6x y Recall por ~20x, **simultáneamente, sin trade-off**.

### Las 12 mejoras priorizadas de Radar Explosivo v2 (diseñadas, ninguna implementada salvo la Mejora 1 vía Cambio Nº1)

| # | Mejora | Evidencia | Estado |
|---|---|---|---|
| 1 | Redefinir el rol de RVOL | Recall +42-44pp al removerlo del gate | **Implementado como Cambio Nº1** (gate a 0.0), en validación V2 |
| 2 | Investigar Liquidez con el mismo rigor que RVOL | Contribución marginal -26.8/-27pp a Recall sin RVOL, la mayor de los 5 restantes | Investigada preliminarmente (segundo cuello de botella confirmado), sin propuesta formal abierta todavía |
| 3 | Separar Gap de "movement" como gate propio | Gap aislado P@10=21.8% vs. 11.8% del gate combinado | Diseñada, no implementada |
| 4 | Agregar techo de precio como factor/gate nuevo | +56% Precision@20 (sin RVOL, techo $30) preliminar | Diseñada; **reevaluada con los 30 días completos y descartada** — dejó de sostenerse (ver sección 16) |
| 5 | Resolver vacío de datos de market cap en la validación | 73.2-74.3% de ganadoras reales con `market_cap=None` | Diseñada, no implementada |
| 6 | Extender persistencia (momentum, VWAP) para futuras corridas | Bloquea auditar Momentum y recalibrar pesos de Etapa B | Diseñada, no implementada |
| 7 | Recalibrar pesos de Etapa B con separación real de Explosive DNA | Cambio%/Gap% separan más que RVOL pese a pesar menos | Depende de la Mejora 6 |
| 8 | Recalibrar curva de penalización por tamaño | Depende de resolver la Mejora 5 primero | Bloqueada por la Mejora 5 |
| 9 | Snapshot configurable (otros minutos post-apertura) | Nunca validado, supuesto de diseño inicial | Diseñada, requiere corridas nuevas |
| 10 | Nuevo factor de ruptura intradía (distancia al máximo) | Señal de momentum no capturada hoy | Diseñada, requiere corrida nueva |
| 11 | Reconstrucción histórica de sector/money flow | Factor de 10% de peso nunca puesto a prueba | Diseñada, prioridad baja |
| 12 | Short interest / float histórico real | Predictor clásico de short squeeze, no capturado hoy | Diseñada — **única de las 12 que requiere tocar Atlas Core** |

Detalle completo de cada una (formato PROBLEMA/HIPÓTESIS/PRINCIPIOS/IMPACTO/RIESGOS/VALIDACIÓN/CRITERIOS) en [RADAR_EXPLOSIVO_V2.md](RADAR_EXPLOSIVO_V2.md), Parte 2.

### Cambio Nº1 (RVOL) — estado actual

Implementado: `gates.min_rvol` de 2.0 a 0.0 en `explosive_config.json`, único cambio funcional aprobado en este ciclo. Config V1 respaldada en `atlas_live/backtest/results_v1/` (incluye `explosive_config_v1.json`). Validación V2 sobre los mismos 30 días exactos (2026-06-18 a 2026-07-31) corriendo en segundo plano, **en 15/30 días al último chequeo, nunca reiniciada ni interrumpida**. `explosive_engine.py` y `explosive_factors.py` sin cambios — verificado. Pesos de score idénticos entre V1 y V2 (confirmado programáticamente).

### Herramientas de análisis construidas (todas de solo lectura, sin tocar el motor)

`atlas_live/backtest/whatif_simulator.py` (simula cambios de umbral de la Etapa A sin descargar datos nuevos), `filter_interaction.py` (malla de 64 combinaciones de los 6 gates, contribución marginal leave-one-out), `rvol_role_comparison.py` (los 5 escenarios de la tabla de arriba), `explosive_dna.py` (perfil estadístico), `historical_scan.py` (reconstrucción sin lookahead), `validation_report.py` (Precision@10/@20/Recall por día y consolidado), `run_validation.py` (CLI).

---

## 11. MISSION CONTROL

Fuente completa: [ATLAS_MISSION_CONTROL.md](ATLAS_MISSION_CONTROL.md). Diseño aprobado, implementación en curso por entregables.

### Qué es

Centro de Operaciones de Atlas: visibilidad y control unificados sobre cualquier proceso de larga duración (validaciones, escaneo en vivo, y a futuro Paper Trading, IA, escaneo de noticias). Vive enteramente en `atlas_live/mission_control/`.

### Estándares de diseño (los 3 ajustados y aprobados explícitamente)

- **7 estados estandarizados**: Iniciando, Ejecutándose, Pausado, Esperando, Finalizado, Error, Cancelado.
- **4 niveles de severidad**: INFO, WARNING, ERROR, CRITICAL (autoevaluados por el proceso, distinto de una alerta detectada externamente).
- **Run ID único**: `<ETIQUETA>_<YYYYMMDD>_<HHMM>` (o `<HHMMSS>` en colisión), todo mayúsculas, siempre generado por `make_run_id()`, nunca a mano.

### Esquema del heartbeat (sección 2.4 del diseño)

`heartbeat_schema` (versión del formato, independiente del commit), `run_id`, `process_type`, `label`, `state`, `started_at`, `last_heartbeat`, `progress {done,total,unit}`, `pid`, `cpu_percent`, `memory_mb`, `severity`, `last_message`, `atlas_version {commit, dirty}` (hash corto de git). Derivados solo al leer, nunca persistidos: `elapsed_seconds`, `eta_seconds` (`None` si `total` es desconocido, nunca un número inventado).

### Supervisión Inteligente (diseño, sección 5, no implementada)

6 detectores vía registro pluggable: procesos detenidos, rate limiting, APIs lentas (no medible hoy — ningún proceso reporta latencia todavía), consumo excesivo de CPU/memoria, procesos sin heartbeat, caídas de Internet (el único detector cross-process).

### Los 9 entregables — estado real

| # | Entregable | Estado |
|---|---|---|
| 1 | Librería de latido (`heartbeat.py`) | **✅ Completado, aprobado de forma definitiva.** Revisión de calidad de 5 puntos realizada; una desviación real (Run ID sin validar contra formato) corregida y re-validada. |
| 2 | Timeline (SQLite) + integración con el latido (`timeline.py`) | **Implementado y probado. Pendiente de aprobación final del usuario.** Ver desviaciones menores abajo. |
| 3 | Modo heredado (`legacy_inspector.py`) | **Diseño técnico entregado, pendiente de aprobación** antes de escribir código. |
| 4 | API backend de Mission Control | No iniciado. Depende de 1, 2, 3. |
| 5 | Panel principal (frontend, solo lectura) | No iniciado. Depende de 4. |
| 6 | Vista de Timeline (frontend) | No iniciado. Depende de 5. |
| 7 | Supervisión Inteligente (6 detectores) | No iniciado. Depende de 4, 5. |
| 8 | Botones de control (Iniciar/Pausar/Reanudar/Detener) | No iniciado. Riesgo Alto (única pieza que actúa sobre procesos reales). Depende de 1, 4, 5. |
| 9 | Instrumentar un proceso real (nunca la V2 actual) | No iniciado. Depende de 1-8. |

Orden estrictamente secuencial 1→9, cada uno aprobado antes del siguiente.

### Entregable 1 — detalle de cierre

Cualquier script Python reporta estado, Run ID, progreso, PID, CPU/memoria (`psutil`, nueva dependencia agregada a `requirements.txt`), versión (hash de git + `dirty`) a un JSON con escritura atómica (archivo temporal + `os.replace()`). Run ID validado con regex `^[A-Z0-9_]+_\d{8}_\d{4}(\d{2})?$`.

### Entregable 2 — detalle de cierre (pendiente de aprobación)

`timeline.py`: SQLite append-only, `record_event`/`get_events_for_run`/`get_recent_events` (con filtro `min_severity`). Solo transiciones reales de estado generan evento (no cada `step()`) — validado con prueba de 6 eventos exactos sobre una secuencia con 3 `step()` repetidos en el mismo estado. `heartbeat.py` modificado: `_event_type_for_transition()`, método `milestone()` nuevo.

**Desviaciones menores reportadas, ninguna crítica**: (a) `heartbeat.py` ahora depende de `timeline.py` y `loguru` — cambia la respuesta "aislado" del cierre del Entregable 1, pero es la integración que este entregable estaba autorizado a hacer; (b) la severidad del evento de Timeline es la que el propio proceso reporta, no una recalculada contra una tabla de mapeo por defecto; (c) cada evento de ciclo de vida incluye `metadata={"done","total","unit"}` automático, no especificado explícitamente en el diseño original.

### Entregable 3 — diseño entregado, pendiente de aprobación

`legacy_inspector.py`: inspección de solo lectura de un proceso que no usa el latido (ej. la validación V2) — cuenta de archivos de progreso, timestamp del más reciente, PID/CPU/memoria si sigue vivo, última línea de log. **No escribe al Timeline** (inferir eventos de ciclo de vida sin cooperación del proceso es ambiguo). Snapshot con las mismas claves que `read_status()` más `source: "heartbeat"|"legacy"`. Total compatibilidad con el Core congelado — solo lee archivos y atributos de proceso, nunca escribe hacia el proceso observado.

---

## 12. PATTERN STORE

Fuente: `atlas/knowledge/pattern_store.py` (Atlas Core, congelado). Parte de la Capa 3 de persistencia (sección 5).

### Qué hace

Búsqueda de patrones similares sobre los eventos registrados en `EventStore` — **sin IA**: similitud = distancia euclidiana simple sobre features numéricas normalizadas a [0,1] (`FEATURE_RANGES`: `gap_percent` -20/20, `rvol` 0/5, `atlas_score` 0/100, `momentum_score` 0/100, `money_flow_score` 0/100). Determinista, transparente, barata de calcular — coherente con el Principio 4 de la Constitución (Atlas siempre explica por qué).

### Componentes (por nombre de clase, verificados en el código)

- **`SymbolDNA`**: el "ADN" de un símbolo — promedio de sus propias features a lo largo de todo su historial en la base, para comparar el comportamiento típico de dos acciones entre sí.
- **`PatternStore`**: motor de búsqueda de similitud descrito arriba.
- **`Pattern`** / **`PatternTransition`**: identidad persistente de un patrón con estado e historial de transiciones.
- **`PatternRegistry`**: da identidad persistente a los patrones — estados posibles: `En observación` / `Activo` / `En decadencia` / `Inactivo` / `Reactivado`, historial de transiciones **nunca borrado**, evidencia acumulativa. Es la única fuente que lee `PatternEvolution` (sección 13) para juzgar vigencia, y el único componente autorizado a llamar `transition_state()` es `CalibrationManager` (Capa 6), nunca `PatternEvolution` ni `PatternStore` directamente.

### Nota importante para el Memory Engine (sección 8)

Este es exactamente el patrón de diseño que se propuso reutilizar/generalizar para una memoria nativa de Radar Explosivo — pero **hoy `PatternStore` opera sobre el feature set de Decision Engine** (`atlas_score`, `momentum_score`, `money_flow_score`), no sobre el de Radar Explosivo (`relative_volume`, `gap_pct`, `volatility_score`, etc.). No son intercambiables sin adaptación.

---

## 13. PATTERN EVOLUTION

Fuente: `atlas/learning/pattern_evolution.py` (Atlas Core, congelado). Parte de la Capa 5 (Aprendizaje).

### Qué hace

Mide si los patrones conocidos (leídos de `PatternRegistry`) siguen siendo confiables o perdieron vigencia, y **propone** transiciones de estado — nunca las aplica. De solo lectura, siempre: nunca llama a `register_pattern()` ni a `transition_state()` — esa aplicación es responsabilidad exclusiva de `CalibrationManager`, tras aprobación humana.

### Contrato de evidencia que cada Pattern debe cumplir

Campo `evidence` con: `sample_size` (muestra histórica), `win_rate` (0-1, tasa de éxito histórica), opcionalmente `recent_sample_size`/`recent_win_rate` (ventana reciente) y `baseline_win_rate` (referencia; si falta, usa `DEFAULT_BASELINE_WIN_RATE`). Si estas claves no están, el patrón se reporta como **evidencia insuficiente** — nunca se inventa un valor (mismo principio de honestidad que rige todo el proyecto).

### Validación de confiabilidad estadística — 3 condiciones simultáneas

1. **Tamaño de muestra** suficiente.
2. **Significancia**: el límite inferior del intervalo de Wilson debe superar el baseline — no alcanza con "ser distinto", tiene que ser mejor incluso en el escenario pesimista.
3. **Consistencia temporal**: evidencia en más de una ventana (histórica y reciente), ambas con muestra suficiente.

Este es exactamente el mecanismo que se propuso reutilizar como base del "algoritmo de probabilidad de explosión futura" del Memory Engine (sección 8, pregunta 4) — aplicado hacia adelante (predicción) en vez de hacia atrás (auditoría de vigencia).

---

## 14. KNOWLEDGE ENGINE Y EL RESTO DE LAS CAPAS DE CONOCIMIENTO/APRENDIZAJE

Fuente: `atlas/knowledge/*.py`, `atlas/learning/*.py` (Atlas Core, congelado). Ver también sección 5 (Capas 3, 5, 6).

### `atlas/knowledge/event_store.py`

Registro persistente de eventos de mercado, SQLite 100% local, modo WAL (pensado para escalar a millones de filas). Guarda cada evento relevante: no solo explosiones, también colapsos, falsas rupturas y mercado normal (`EXPLOSION`, `COLLAPSE`, `FALSE_BREAKOUT`, `NORMAL`), con contexto completo (precio, gap, RVOL, scores, decisión, resultado). Indexado por ticker, tipo de evento, sector, industria y fecha.

### `atlas/knowledge/pattern_store.py`

Ver sección 12.

### `atlas/knowledge/prediction_store.py`

Guarda qué predijo Atlas (decisión, confianza, scores) para un símbolo en un momento dado, para comparar después contra el resultado real registrado en `event_store`. La comparación en sí (acierto/error) es trabajo de `AccuracyTracker`, no de este módulo — aquí solo se registra y consulta.

### `atlas/knowledge/knowledge_engine.py`

Fachada única sobre `event_store`, `prediction_store` y `pattern_store`. Punto de entrada del núcleo de conocimiento: registra eventos y predicciones, expone búsqueda de patrones similares, comparación de ADN entre símbolos y estadísticas agregadas (`KnowledgeStatistics`). No genera señales ni decide nada nuevo — solo persiste y consulta.

### `atlas/knowledge/engine_versions.py`

Registro centralizado de versiones de los motores de Atlas (`ATLAS_CORE`, `MOMENTUM_ENGINE`, `MONEY_FLOW_ENGINE`, `DECISION_ENGINE`, `KNOWLEDGE_ENGINE`), adjuntado como metadata a cada evento/predicción guardado — permite comparar resultados históricos cuando cambie la lógica de un motor. Sin automatismo: subir la versión es responsabilidad manual de quien modifica el motor.

### `atlas/learning/learning_engine.py`

Fachada única de aprendizaje de mercado: orquesta `AccuracyTracker` + `PatternEvolution` + `CalibrationAdvisor`. Nadie fuera de este paquete debería instanciar esos tres directamente. Aprende **únicamente** del mercado — no importa `decision_journal` ni `operator_learning` (esa es la otra mitad del conocimiento, deliberadamente separada) ni `calibration_manager` (solo propone, nunca aplica). 100% de solo lectura: ningún método escribe en ninguna base ni motor.

### `atlas/learning/pattern_evolution.py`

Ver sección 13.

### `atlas/learning/accuracy_tracker.py`

Mide qué tan bien las decisiones de Decision Engine predijeron lo que realmente pasó. De solo lectura siempre. Empareja cada predicción con el evento de mercado del mismo símbolo y fecha más cercano en hora (correlación de solo lectura, no un vínculo garantizado — ver "pendiente conocido" de la sección 5). Reglas de acierto fijas y explícitas (sin IA): COMPRAR acierta si `close_result_percent > 0` o fue EXPLOSION; DESCARTAR acierta si cerró neutro/negativo o fue COLLAPSE/FALSE_BREAKOUT; VIGILAR no se clasifica como acierto/error. Ignora predicciones/eventos con `data_status` distinto de OK, y grupos bajo `MIN_SAMPLE_SIZE`.

### `atlas/learning/calibration_advisor.py`

Consolida evidencia de `AccuracyTracker` y `PatternEvolution` en `CalibrationProposal` concretas. De solo lectura siempre — no importa `calibration_manager`, no sabe que existe. No propone valores específicos de ajuste (ej. "subir el peso de RVOL a 0.25") porque eso requeriría análisis causal que este módulo no hace; señala **dónde** la evidencia sugiere revisar algo, dejando el ajuste a criterio humano.

### `atlas/calibration_manager/calibration_manager.py`

Ver sección 5, Capa 6. Única puerta de entrada para aplicar cambios permanentes de conocimiento.

### `atlas/decision_journal/decision_journal.py` y `atlas/decision_recorder/decision_recorder.py`

Ver sección 5, Capas 3 y 4.

### `atlas/operator_learning/operator_learning.py`

Aprende del Decision Journal (patrones del operador humano — qué opera, cuándo, cómo). Nunca importa `atlas.knowledge` (verificado por grep) — separación mercado/operador estructural, no solo documental. Devuelve `OperatorInsight`, nunca escribe nada.

---

## 15. DECISIONES TÉCNICAS APROBADAS

Fuente completa: [DECISION_LOG.md](DECISION_LOG.md) (formato: Problema/Alternativas evaluadas/Decisión/Justificación/Impacto, todas fechadas 2026-08-01 salvo donde se indica). Reproducidas con su decisión y justificación; ver el archivo fuente para el detalle completo de cada una.

1. **Congelamiento de RVOL** hasta cerrar la validación de 30 días — evita analizar dos variables en movimiento a la vez.
2. **Excepción controlada**: avanzar en Alertas en tiempo real (Fase 7) mientras la validación histórica corría — la infraestructura de notificación no depende de los resultados de los 30 días.
3. **Reorganización del dashboard en 3 (luego 4) secciones** — cada sección responde una pregunta distinta, mezclarlas obligaba a Radar Explosivo a heredar lógica de Decision Engine.
4. **Radar Explosivo como motor propio, independiente de Decision Engine** — Decision Engine responde calidad/confianza de inversión, Radar Explosivo responde velocidad; usar la salida de Decision Engine como filtro heredaba sesgos que no aplican.
5. **Penalización continua por tamaño, con excepción por catalizador extremo** — un corte binario ignoraría una mega-cap con sorpresa de earnings genuina.
6. **Configuración centralizada en JSON** (`explosive_config.json`), separada del código del motor, con valores por defecto si el archivo falta/está corrupto.
7. **Registro de factores "enchufables"** (`explosive_factors.py`) en vez de lógica embebida — aísla cada señal, hace cada factor auditable por separado.
8. **Modo Diagnóstico instrumentando el motor real** (`evaluate()` in situ), no reconstruyéndolo aparte — evita que el diagnóstico diverja silenciosamente del comportamiento real; verificado con casos sintéticos (mismo `score`/`eligible` antes y después).
9. **Validación histórica contra el Universo Racional completo** (~2577 símbolos), no la muestra de 200 del escaneo en vivo — decisión explícita del usuario priorizando corrección sobre velocidad.
10. **Reconstrucción con velas intradía reales, snapshot a los 10 minutos, sin usar el cierre del día como insumo** — único modo honesto de medir si el radar detecta antes, no después; acota la validación a ~60 días de historia intradía disponible en Yahoo Finance.
11. **Cero cambios en `/atlas`** durante todo el desarrollo de Radar Explosivo/Diagnóstico/Validación/Explosive DNA — confirmado con `git status` en cada iteración.
12. **Adopción de la metodología de evolución por evidencia** (formato PROBLEMA/HIPÓTESIS/...) — formalizada en la Constitución.
13. **Adopción de `ATLAS_CONSTITUTION.md`** como autoridad máxima del proyecto.
14. **Cambio Nº1 (RVOL)**: `gates.min_rvol` de 2.0 a 0.0 — único cambio funcional autorizado del ciclo actual, en validación V2.
15. **Timeline de Mission Control en SQLite** — reutiliza el mismo tipo de almacenamiento que ya usan Decision Journal y Knowledge Base, no introduce una herramienta nueva.
16. **7 estados estandarizados, 4 severidades, Run ID único** como estándares oficiales de cualquier proceso instrumentado de Atlas.
17. **`heartbeat_schema` separado del hash de git** — el commit versiona el código, el schema versiona el formato del heartbeat.
18. **Solo transiciones reales de estado escriben al Timeline** (no cada `step()`) — evita ruido en el registro histórico permanente.

---

## 16. DECISIONES DESCARTADAS Y POR QUÉ

Compiladas de [RADAR_EXPLOSIVO_V2.md](RADAR_EXPLOSIVO_V2.md), [DECISION_LOG.md](DECISION_LOG.md) y [INFORME_EJECUTIVO_FINAL.md](INFORME_EJECUTIVO_FINAL.md). Estas son alternativas evaluadas y rechazadas, no ideas nunca consideradas.

1. **Índice de Explosión (IE) dentro de Atlas Core** (`atlas/engine/explosion_index.py`) — alternativa considerada al construir Radar Explosivo. **Descartada** a favor de un motor propio 100% dentro de `atlas_live`, para no acoplar el detector de velocidad a la arquitectura protegida del Core. El stub sigue vacío en el Core, deliberadamente sin implementar.
2. **Filtrar Radar Explosivo sobre la salida de Decision Engine** (`display_decision.code == "SI_COMPRARIA"`) — descartada porque mezclaba "es buena inversión" con "se mueve rápido ahora", heredando sesgos que no aplican (ej. mega-caps de alta confianza pero movimiento lento).
3. **Prohibición binaria de mega-caps** — descartada a favor de penalización continua con excepción por catalizador extremo, para no ignorar el caso real de una sorpresa de earnings genuina en una empresa grande.
4. **Constantes embebidas en código para los umbrales del motor** — descartadas a favor de configuración externa en JSON, para permitir ajustar sin tocar código.
5. **Lógica de factores embebida (if/else)** — descartada a favor de un registro de factores enchufables, para aislar el riesgo de que un factor nuevo rompa el cálculo de los demás.
6. **Reconstruir el embudo de diagnóstico aparte del motor real** — descartada a favor de instrumentar `evaluate()` in situ, para que el diagnóstico nunca pueda divergir silenciosamente del comportamiento real.
7. **Validar contra la muestra de 200 símbolos del escaneo en vivo** — descartada a favor del Universo Racional completo (~2577), porque una validación que no puede ver a la verdadera ganadora del día no es honesta.
8. **Usar el cierre del día completo como insumo del radar simulado** — descartada (habría sido lookahead/trampa) a favor de reconstrucción con velas intradía reales cortadas en el minuto del snapshot; el cierre del día se usa **solo** como verdad de referencia, nunca como insumo.
9. **Ajustar solo el umbral numérico de RVOL (Propuesta 3), como sustituto de redefinir su rol (Propuesta 1)** — la evidencia mostró que los descartes de RVOL están *lejos* del umbral (93% de distancia), no cerca — un ajuste de umbral por sí solo no habría bastado. Se adoptó la Propuesta 1 (cambiar el rol, no solo el número) como Cambio Nº1.
10. **Techo de precio como factor/gate nuevo (Mejora 4 de las 12)** — parecía prometedor en el análisis preliminar con 8 días (+56% Precision@20 sin RVOL). **Al confirmarse con los 30 días completos, dejó de sostenerse** — descartada de la lista de recomendaciones del informe ejecutivo final, reportada explícitamente como "nota de honestidad" en vez de mantenerse por inercia.
11. **Implementar el Cambio Nº1 (RVOL) sin el Cambio Nº2 (Liquidez) como contención** — el informe ejecutivo final recomendó explícitamente no implementar el Nº1 solo, sino junto con el filtro de liquidez como contención del pool elegible; el proyecto implementó únicamente el Cambio Nº1 en este ciclo, dejando Liquidez para el ciclo siguiente (instrucción explícita del usuario de un cambio a la vez).
12. **Pasar a Paper Trading con la configuración de Radar Explosivo v1 (pre-Cambio Nº1)** — descartado explícitamente en el Informe Ejecutivo Final: con menos de una oportunidad detectada por día, un período de paper trading no generaría información nueva.

---

## 17. ROADMAP COMPLETO

Fuente vigente: [ATLAS_ROADMAP.md](ATLAS_ROADMAP.md) — **leer siempre el archivo directamente**, este documento puede quedar desactualizado si el roadmap cambia sin sincronizar aquí. Progreso general: **≈63%**, promedio simple de las 10 fases (no ponderado, estimación de juicio, no automática).

| Fase | % | Estado resumido |
|---|---|---|
| 1. Arquitectura | 100% | Completada — Core v1.0 congelado, `atlas_live` activo |
| 2. Radar Explosivo | 75% | Implementado, medido con evidencia completa de 30 días; Cambio Nº1 en validación |
| 3. Validación histórica | 100% | Completada — 30/30 días |
| 4. Explosive DNA | 95% | Completada — perfil estadístico sobre 600 observaciones |
| 5. Optimización del Radar | 45% | Diseño confirmado con evidencia final; Cambio Nº1 implementado y en validación V2, pendiente comparación formal V1 vs V2 |
| 6. Cambio de proveedor de datos | 30% | Evaluación comparativa completa (5 proveedores); sin decisión ni implementación |
| 7. Alertas en tiempo real | 100% | Completado (alcance aprobado: 3 canales); push/correo/Telegram/Discord planificados para fase posterior, no cuentan en contra |
| 8. Dashboard profesional | 35% | MVP funcional en uso; falta versión profesional definitiva |
| 9. IA de apoyo a decisiones | 0% | Sin trabajo iniciado — depende de Radar Explosivo validado |
| 10. Mission Control | 45% | Entregables 1-2 implementados y probados; Entregable 2 pendiente de aprobación; diseño del Entregable 3 en revisión |

**Dependencias entre fases**: 2 depende de 1; 3 depende de 2; 4 depende de 3; 5 depende de 3 y 4; 6 es independiente (groundwork parcial ya existe); 7 es independiente de la validación (reacciona a un campo que ya existe); 8 depende de 2 y 7; 9 depende de 2 (validado) y 4; 10 es independiente (diseñado para modo heredado sin instrumentar nada).

**Documento histórico, no vigente pero no eliminado**: `ROADMAP.md` (raíz, fases 1-3 previas a la Constitución) — reemplazado por `ATLAS_ROADMAP.md`, se conserva como registro. Igualmente `DECISIONES.md` — reemplazado por `ATLAS_CONSTITUTION.md` + `DECISION_LOG.md`.

---

## 18. CHECKLIST MAESTRO

**Nota sobre el pedido original de "83 puntos"**: no existe en este proyecto una lista predefinida de 83 puntos — ningún checklist parcial construido durante el proyecto (9 entregables de Mission Control, 13 puntos del Plan de Cierre a Producción, 8 principios de la Constitución, 10 fases del Roadmap, 12 mejoras de Radar Explosivo v2) suma 83 por separado ni combinado. Esta sección es el checklist maestro **compilado de cero**, combinando todo lo accionable mencionado en el proyecto, ordenado por fase y dependencia. El número total de puntos es el que resulta de la cuenta real, no un número objetivo.

Convención de estado: ✅ Completado · 🟡 En curso/Parcial · ⛔ Bloqueado (depende de algo no resuelto) · ⬜ No iniciado · 🚫 Descartado.

### A. Arquitectura y fundación (Fase 1)

1. ✅ Separar Atlas Core de las capas experimentales.
2. ✅ Atlas Core construido: Data Collector, 5 motores de Capa 1, Knowledge Base, Learning Engine (8 etapas).
3. ✅ `atlas_live` consume Atlas Core sin modificarlo (verificado en cada iteración).

### B. Radar Explosivo (Fase 2)

4. ✅ Motor propio de 3 etapas (`explosive_engine.py`).
5. ✅ Config centralizada editable sin tocar código (`explosive_config.json`).
6. ✅ Registro de factores enchufables (`explosive_factors.py`).
7. ✅ Modo Diagnóstico (embudo + auditoría por símbolo).
8. ✅ Penalización continua por tamaño con excepción por catalizador extremo.
9. ⬜ Precision@10/@20/Recall por encima de un umbral mínimo aceptable (el umbral en sí no está definido todavía — ver punto 79).

### C. Validación histórica y Explosive DNA (Fases 3-4)

10. ✅ Validación 1 completa: 30/30 días, Universo Racional completo, sin lookahead.
11. ✅ Informe por día y consolidado (`validation_report.py`).
12. ✅ Explosive DNA sobre 600 observaciones explosivas reales.
13. ✅ Herramientas de solo lectura: `whatif_simulator.py`, `filter_interaction.py`, `rvol_role_comparison.py`.
14. ⬜ Resolver el vacío de datos de market cap (73.2% de ganadoras reales sin dato) — Mejora 5 de Radar Explosivo v2.
15. ⬜ Filtrar la verdad de referencia por un techo razonable (~150-200%) para excluir artefactos de datos antes de recalcular Recall en la próxima validación.

### D. Optimización del Radar / 12 mejoras de v2 (Fase 5)

16. ✅ **Mejora 1** — Redefinir el rol de RVOL → implementada como Cambio Nº1.
17. 🟡 **Mejora 2** — Investigar Liquidez con el mismo rigor que RVOL → investigada preliminarmente (segundo cuello de botella confirmado), sin propuesta formal abierta.
18. ⬜ **Mejora 3** — Separar Gap de "movement" como gate propio.
19. 🚫 **Mejora 4** — Techo de precio como factor/gate nuevo → descartada tras confirmarse con 30 días.
20. ⬜ **Mejora 5** — Resolver vacío de market cap (= punto 14).
21. ⬜ **Mejora 6** — Extender persistencia de `historical_scan.py` (momentum, VWAP).
22. ⛔ **Mejora 7** — Recalibrar pesos de Etapa B → bloqueada por Mejora 6.
23. ⛔ **Mejora 8** — Recalibrar curva de penalización por tamaño → bloqueada por Mejora 5.
24. ⬜ **Mejora 9** — Snapshot configurable (otros minutos post-apertura).
25. ⬜ **Mejora 10** — Factor de ruptura intradía (distancia al máximo).
26. ⬜ **Mejora 11** — Reconstrucción histórica de sector/money flow.
27. ⬜ **Mejora 12** — Short interest / float histórico real (única que toca Atlas Core, depende de Fase 6).

### E. Cambio Nº1 (RVOL) — cierre formal

28. ✅ Propuesta formal presentada y aprobada (formato Constitución).
29. ✅ Implementado: `gates.min_rvol` 2.0 → 0.0.
30. ✅ Verificado: `explosive_engine.py`, `explosive_factors.py`, `/atlas` sin cambios.
31. ✅ Config V1 respaldada (`results_v1/`, incluye `explosive_config_v1.json`).
32. 🟡 Validación histórica V2 sobre los mismos 30 días — en curso, 15/30 al último chequeo, nunca interrumpida.
33. 🟡 **Diseño del generador automático del Informe Comparativo Oficial V1 vs V2** — explicado (archivos, métricas, dependencias, garantías de reproducibilidad), **código no escrito todavía**.
34. ⬜ Ejecutar el informe comparativo una vez V2 llegue a 30/30 días.
35. ⬜ Decisión formal: Aprobar / Rechazar / Requiere nueva validación.
36. ⬜ Registrar la decisión en `DECISION_LOG.md`.
37. ⬜ Validar el cambio adoptado en tiempo real (paso 7 de la Metodología de Propuestas) antes de considerarlo permanente.

### F. Cambio de proveedor de datos (Fase 6)

38. ✅ Evaluación comparativa de 5 proveedores (Polygon/Massive, Databento, Alpaca, Finnhub, Intrinio) contra 16 requerimientos reales.
39. ✅ Recomendación preliminar: Alpaca Market Data (Algo Trader Plus, $99/mes) como proveedor primario.
40. ⬜ Decisión final sobre fuente secundaria para short interest/float (Opción A: Polygon/Massive Starter $29/mes; Opción B: Intrinio $150/mes) — sin decidir.
41. ⬜ Prueba con clave de API gratuita/trial contra ~50 microcaps ilíquidos del Universo Racional, para verificar cobertura real (no solo marketing) — recomendada, no ejecutada.
42. ⬜ Propuesta formal (formato Constitución) antes de tocar `atlas/data/providers/` o crear cualquier cuenta.
43. ⬜ Implementar un segundo `DataProvider` pasando las mismas pruebas que `YahooFinanceProvider`.

### G. Alertas en tiempo real (Fase 7)

44. ✅ Detección de oportunidades genuinamente nuevas (sin repetir el mismo símbolo).
45. ✅ 3 canales: notificación de navegador, sonido configurable, resaltado visual.
46. ✅ Arquitectura modular (`NOTIFICATION_CHANNELS`).
47. ✅ Preferencias persistidas en `localStorage`.
48. ⬜ Canales adicionales (push al celular, correo, webhooks, Telegram, Discord) — planificados, sin priorizar.

### H. Dashboard profesional (Fase 8)

49. ✅ MVP funcional: 4 secciones (Radar Explosivo, Radar General, Watchlist, Diagnóstico).
50. ⬜ Versión "profesional" definitiva — criterios de éxito a definir cuando se priorice.

### I. IA de apoyo a decisiones (Fase 9)

51. ⬜ Sin trabajo iniciado — depende de Radar Explosivo validado y Explosive DNA (deliberadamente pospuesta: una IA de apoyo sobre un motor sin evidencia amplificaría el ruido).

### J. Mission Control (Fase 10) — 9 entregables

52. ✅ Diseño técnico completo aprobado (`ATLAS_MISSION_CONTROL.md`): arquitectura de 3 piezas, 7 estados, 4 severidades, Run ID, Supervisión Inteligente (diseño), consideraciones de escalabilidad.
53. ✅ Entregable 1 — heartbeat.py. Completado y aprobado de forma definitiva.
54. 🟡 Entregable 2 — Timeline. Implementado y probado, pendiente de aprobación final.
55. 🟡 Entregable 3 — Modo heredado. Diseño entregado, pendiente de aprobación, código no escrito.
56. ⬜ Entregable 4 — API backend.
57. ⬜ Entregable 5 — Panel principal (frontend).
58. ⬜ Entregable 6 — Vista de Timeline (frontend).
59. ⬜ Entregable 7 — Supervisión Inteligente (6 detectores).
60. ⬜ Entregable 8 — Botones de control (riesgo alto).
61. ⬜ Entregable 9 — Instrumentar un proceso real (nunca la V2 actual).

### K. Memory Engine — ATLAS ALPHA 1.0 congelado (2026-08-02)

62. ✅ Diseño técnico completo aprobado ([MEMORY_ENGINE.md](MEMORY_ENGINE.md)): propuesta formal completa (PROBLEMA/HIPÓTESIS/PRINCIPIOS/ARQUITECTURA/FLUJO DE DATOS/QUÉ APRENDE/QUÉ GUARDA/QUÉ DESCARTA/CÓMO EVOLUCIONA/IMPACTO/RIESGOS/VALIDACIÓN/CRITERIOS DE ÉXITO), alcance ampliado a estudiar todo el mercado (no solo explosiones), diseño explícitamente congelado.
63. ✅ Entregable 1 — Memory Store (`atlas_live/memory/store.py`). Esquema SQLite/WAL append-only, con columna `market_context` reservada. Probado con datos sintéticos (5 categorías, filtros, append-only).
64. ✅ Entregable 2 — Clasificador de Resultado (`classifier.py`). Probado sobre los 30 días reales completos (73.123 filas): las 14 detecciones reales conocidas y los 5 artefactos de datos sospechosos clasifican como EXPLOSION (el segundo caso es un límite de alcance documentado, no un error). Prueba de regresión permanente agregada (`test_classifier_golden.py`, 41 casos congelados).
65. ✅ Entregable 3 — Carga histórica retroactiva (`backfill.py`). 73.123 filas guardadas, 0 descartadas, integridad y append-only verificados sobre datos reales.
66. ✅ Entregable 4 — Motor de tasas base (`base_rates.py`). Validado sobre las 73.123 observaciones reales: RVOL≥2.5x y gap≥5% confirman señal fuerte y confiable, consistente con la auditoría previa de Radar Explosivo v2.
67. ✅ Entregable 5 — Generador de Propuestas (`calibration_advisor.py`). 10 de 14 condiciones de una grilla evidencia-informada resultaron confiables (la más fuerte: `gap_pct>=10.0`, 75.3x el baseline).
68. 🟡 Entregable 6 — Validación retroactiva. **Parcial**: demo de un día ejecutada dos veces (con y sin Ranking Score), resultado positivo (Precision@10 20%→70% en 2026-07-30, confirmado en 2 días adicionales). La validación agregada de Precision@10/@20/Recall sobre los 30 días completos (`proposal_validator.py`) todavía no se construyó -- queda como trabajo pendiente, no se declara terminada.
69. ✅ Entregable 7 — Captura diaria en producción. **Logrado por una vía distinta a la planeada**: no se construyó `daily_capture.py` por separado -- se resolvió directamente vía la integración en tiempo real con `scan_worker.py` (`live_integration.py`), que alimenta la memoria hacia adelante en cada premarket real.
70. ⬜ Entregable 8 — Checkpoints intermedios (10/30/60 min). No iniciado, diferido explícitamente (refinamiento, no bloqueante).
71. ✅ **Ranking Score de desempate** (`ranking_score.py`, propuesta separada aprobada e implementada) -- 4 niveles de prioridad estricta, sin pesos inventados. Validado: NUWE #25→#2, XRX #39→#1 (2026-07-30); generaliza en 2 días adicionales (2026-06-23, 2026-07-13).
72. ✅ **Prediction Journal** (`prediction_journal.py`) -- dos flujos (snapshots dinámicos + sellado único garantizado por `AlreadySealedError`), explicación completa embebida, tiempo de anticipación calculado automáticamente (`AlreadyGradedError` protege contra recalificación). Probado con datos sintéticos.
73. ✅ **Integración en tiempo real** (`market_hours.py`, `live_integration.py`, modificación aditiva de 6 líneas en `scan_worker.py`) -- detección de sesión por huso horario de Nueva York, snapshot dinámico durante premarket, sellado automático en la ventana 09:25-09:30, calificación automática al cierre contra cotización real. Probado con datos sintéticos y un `DataCollector` falso (sin red real). Sin verificar todavía en condiciones de mercado reales.
74. ✅ **Modo Interactivo Continuo** (decisión de arquitectura) -- recalibración diaria automática de la evidencia (tasas base/propuestas), apagado prolijo (`request_stop`/`wait_until_stopped`) sin pérdida de estado en curso. Explícitamente no es un servicio 24/7 (versión futura).
75. ✅ **Atlas Alpha 1.0 congelado** (2026-08-02) -- baseline oficial de referencia (detalle completo en la sección "ATLAS ALPHA 1.0" más abajo). A partir de esta versión, toda mejora al Memory Engine debe demostrar una mejora medible respecto a esta baseline antes de incorporarse.

### L. Ranking Engine (aclaración, no un desarrollo pendiente por sí solo)

76. ✅ Ranking real en producción = Etapa B de Radar Explosivo (ya cubierto en B); en premarket, adicionalmente re-rankeado por el Ranking Score del Memory Engine (bloque K).
77. ⬜ "Ranking Top 20" (frecuencia de aparición en el top-20 diario a lo largo de 30 días) — diseñado como parte del Informe Comparativo V1 vs V2 (punto 33), no implementado.

### M. Plan de Cierre de Atlas V2 hacia producción con dinero real (13 puntos, compilados en una sesión anterior de este mismo proyecto)

78. 🟡 Cierre formal del Cambio Nº1 (= puntos 28-37).
79. ⬜ Definir el criterio de aceptación mínimo para operar ("go-live bar": Precision@10/Recall/FP aceptables).
80. ⬜ Validación en tiempo real (paper trading) de la versión congelada del Radar.
81. ⬜ Lógica de gestión de posición (entrada, stop-loss, take-profit, salida por tiempo) — no existe hoy.
82. ⬜ Validación de rentabilidad neta (P&L real con comisiones/slippage, no solo detección).
83. 🟡 Motor de riesgo conectado a capital real — existe `risk_engine.py` teórico en Atlas Core, sin confirmar conexión a capital real/sizing/límites de pérdida.
84. 🟡 Feed de datos en tiempo real apto para producción (= puntos 38-43).
85. ⬜ Integración de ejecución con broker (paper primero, real después) — no existe ningún código de conexión a broker.
86. ⬜ Manejo seguro de credenciales de broker y de datos.
87. 🟡 Mission Control — supervisión operativa mínima viable (= parte de J).
88. ⬜ Plan de contingencia ante fallos con posición abierta.
89. 🟡 Registro y auditoría de operaciones reales ejecutadas — existe Decision Journal, sin confirmar cobertura de órdenes reales/fills/comisiones.
90. ⬜ Cuenta de trading fondeada y marco legal/regulatorio resuelto — decisión y trámite del usuario, no de desarrollo.

### N. Documentación de gobernanza del proyecto

91. ✅ `ATLAS_CONSTITUTION.md` adoptada.
92. ✅ `DECISION_LOG.md` en uso activo.
93. ✅ `ATLAS_ROADMAP.md` vigente, sincronizado a mano.
94. ✅ `ATLAS_MISSION_CONTROL.md` — diseño + plan de implementación.
95. ✅ `MEMORY_ENGINE.md` — diseño + plan de implementación + baseline Atlas Alpha 1.0.
96. ✅ `ATLAS_BOOTSTRAP.md` — punto de entrada rápido (versión corta de este documento).
97. ✅ `ATLAS_MASTER_DOCUMENT.md` — este documento, fuente de verdad completa.

**Total real del checklist: 97 puntos** (no 83 — número real de la compilación; creció de 92 a 97 al reflejar el cierre real de los Entregables 2-7 del Memory Engine y sumar Ranking Score, Prediction Journal, Integración en tiempo real, Modo Interactivo Continuo y el congelamiento de Atlas Alpha 1.0). ✅ Completados: 46 · 🟡 Parcial/en curso: 11 · ⛔ Bloqueados: 2 · 🚫 Descartados: 1 · ⬜ No iniciados: 37.

---

## 19. ESTADO ACTUAL DEL PROYECTO

- **Progreso general del roadmap**: ≈63% (10 fases, promedio simple, ver sección 17).
- **Atlas Core**: v1.0, congelado, estable, sin cambios en toda esta sesión de trabajo (verificado repetidamente con `git status`).
- **Radar Explosivo**: implementado y medido con evidencia completa. Diagnóstico honesto: la versión original detecta solo 2.33% de las oportunidades reales (Recall). Causa raíz identificada con evidencia: el filtro de RVOL, mal calibrado para un snapshot temprano, no el enfoque general del motor.
- **Cambio Nº1 (RVOL)**: implementado, en validación V2 (15/30 días al último chequeo, sin interrupciones). El Informe Comparativo Oficial V1 vs V2 está diseñado (archivos, métricas, garantías de reproducibilidad) pero no implementado — se generará automáticamente cuando V2 llegue a 30/30.
- **Mission Control**: 2 de 9 entregables completados/probados (heartbeat, Timeline), el Entregable 2 pendiente de aprobación final, diseño del Entregable 3 (modo heredado) entregado y pendiente de aprobación.
- **Memory Engine**: **Atlas Alpha 1.0 congelado** (2026-08-02) ([MEMORY_ENGINE.md](MEMORY_ENGINE.md)) — primera versión funcional completa: genera rankings (Ranking Score), aprende (Memory Store + Clasificador + tasas base sobre 73.123 observaciones reales), registra y sella predicciones (Prediction Journal), las califica automáticamente al cierre con tiempo de anticipación, se recalibra diariamente, y se mantiene activo durante toda la sesión (Modo Interactivo Continuo). Pendiente: validación agregada de 30 días, checkpoints intermedios, y verificación en condiciones de mercado reales. A partir de esta baseline, toda mejora debe demostrar mejora medible antes de incorporarse.
- **Camino a producción con dinero real**: evaluado explícitamente — **no, hoy Atlas no puede operar dinero real.** Faltan, sin excepción: ejecución con broker, lógica de salida de posiciones, validación de rentabilidad neta, validación en tiempo real, feed de datos apto para producción, y varios puntos más del checklist maestro (sección M).
- **Documentación de gobernanza**: completa y consistente — Constitución, Decision Log, Roadmap, Bootstrap, Mission Control, Memory Engine y este Master Document, todos sincronizados a la fecha de la última actualización.
- **Trabajo no comprometido a git**: existen cambios reales en el repositorio (código y documentación de esta sesión) todavía no confirmados con `git commit`, según el propio `CHANGELOG.md`.

---

## 20. PRÓXIMOS PASOS

En orden, respetando dependencias y la metodología de un cambio a la vez:

1. **Inmediato**: obtener aprobación del usuario sobre el Entregable 2 de Mission Control (Timeline) y sobre el diseño del Entregable 3 (Modo Heredado) — ambos ya entregados, pendientes de decisión.
2. **En paralelo, sin bloquear lo anterior**: seguir monitoreando en modo solo lectura la validación V2 hasta 30/30 días — **nunca reiniciarla, modificarla ni interrumpirla**.
3. **Al llegar a 30/30**: implementar y ejecutar el generador automático del Informe Comparativo Oficial V1 vs V2 (ya diseñado, sección 18 punto 33) y producir la decisión formal (Aprobar/Rechazar/Requiere nueva validación) para cerrar el Cambio Nº1.
4. **Tras cerrar el Cambio Nº1**: recién entonces evaluar el Cambio Nº2 (Liquidez, Mejora 2 de las 12) — nunca antes, para no mover dos variables a la vez.
5. **Si se retoma Mission Control**: continuar estrictamente en orden 3→4→5→6→7→8→9, cada uno con su propia aprobación.
6. **Si se retoma el camino a producción real**: seguir el orden del Plan de Cierre (sección 18, bloque M) — empezar por definir el criterio de aceptación mínimo (punto 79) y la validación en tiempo real (punto 80), antes de tocar ejecución de broker o dinero real.
7. **Memory Engine**: Atlas Alpha 1.0 está congelado como baseline (sección 18, bloque K, punto 75). Cualquier mejora futura al Memory Engine debe demostrar una mejora medible respecto a esta versión antes de incorporarse -- no se amplía el alcance por acumulación. Próximo paso concreto: la primera prueba completa en condiciones de mercado reales (todo lo construido se validó con datos sintéticos e históricos, nunca contra un premarket real). Trabajo pendiente adicional, no bloqueante: la validación agregada de 30 días del Entregable 6 (punto 68) y el Entregable 8 (checkpoints intermedios, punto 70).
8. **Mantenimiento de este documento**: actualizar esta sección y las secciones 15, 17, 18 y 19 cada vez que se cierre un entregable, se apruebe una decisión, o cambie el estado de una fase — no dejar que este documento quede desactualizado respecto al código real.
