# ATLAS CORE — Arquitectura v1.0 (congelada)

Este documento reemplaza el borrador inicial. Describe la arquitectura
real, construida y verificada con datos reales, tal como quedó al cerrar
Atlas Core. A partir de v1.0, esta arquitectura está **congelada**: no se
crean nuevos motores ni nuevos módulos de núcleo salvo una decisión
explícita de rediseño. Cualquier mejora futura reutiliza lo que ya existe.

## Principios permanentes

1. **Todo dato de mercado pasa por Data Collector.** Ningún motor consulta
   Yahoo Finance directamente.
2. **Cada módulo tiene una única responsabilidad.** Ningún módulo hace el
   trabajo de otro.
3. **Sin IA.** Todo el scoring y las decisiones son fórmulas y umbrales
   explícitos, visibles en el código.
4. **Solo mide y propone; nunca decide ni aplica solo.** Los motores de
   análisis y aprendizaje nunca modifican otro motor ni un repositorio
   automáticamente. La aplicación de cualquier cambio permanente pasa
   siempre por Calibration Manager, y detrás de eso, por revisión humana.
5. **Conocimiento del mercado y conocimiento del operador nunca se
   mezclan.** Son dos dominios completamente separados, con
   almacenamiento propio y sin dependencias cruzadas.
6. **Nunca se borra conocimiento.** Los patrones cambian de estado; los
   eventos, predicciones y recomendaciones se acumulan. Nada se sobrescribe.
7. **Trazabilidad completa.** Todo evento y predicción registra su fuente,
   hora de captura, estado del dato y versión de los motores que
   participaron.

## Capas y módulos

### Capa 0 — Datos e indicadores (fundación)

| Módulo | Responsabilidad | Escribe en | Lee de |
|---|---|---|---|
| `atlas/data/providers/` | Habla con Yahoo Finance (`YahooFinanceProvider`) | — | Yahoo Finance (externo) |
| `atlas/data/collectors/data_collector.py` | Única puerta de entrada a datos de mercado; cachea en memoria | — (caché interna) | `providers/` |
| `atlas/data/universe/` | Universo Racional (2577 instrumentos, desde el PDF oficial) | — | `racional_universe.json` (estático) |
| `atlas/data/models/quote.py` | Modelo `Quote`, normalizado, independiente del proveedor | — | — |
| `atlas/storage/memory_cache.py` | Caché genérica en RAM (TTL) | — | — |
| `atlas/indicators/` | Biblioteca de 10 indicadores puros (EMA, SMA, RSI, MACD, ATR, Volatility, RVOL, Dollar Volume, VWAP, Gap%) | — | — |

### Capa 1 — Motores de puntuación (`atlas/engine/`)

| Módulo | Responsabilidad | Depende de |
|---|---|---|
| `score_engine.py` + `atlas_score.py` | Atlas Score (7 factores ponderados) | Data Collector, indicadores |
| `momentum_engine.py` | Momentum Score (9 factores, reutiliza componentes de score_engine) | Data Collector, indicadores, score_engine |
| `money_flow_engine.py` | Money Flow Score por sector/industria | Data Collector, momentum_engine, universe |
| `market_context_engine.py` | Contexto de mercado (SPY/QQQ/IWM/VIX/BTC, sector líder, calendario) | Data Collector, money_flow_engine (opcional) |
| `decision_engine.py` | COMPRAR/VIGILAR/DESCARTAR, confianza acumulativa, checklist explicable | atlas_score, momentum_engine, money_flow_engine |

**Protegidos** (no se modifican salvo necesidad técnica explícita y aprobada): los cinco motores de esta capa.

### Capa 2 — Scanners (`atlas/scanners/`)

`premarket.py` (universo completo) y `momentum_radar.py` (ranking especializado). Ambos consumen Data Collector + Atlas Score; no escriben en ningún repositorio.

### Capa 3 — Persistencia (dos dominios, sin cruce)

| Módulo | Dominio | Base de datos | Quién escribe | Quién lee |
|---|---|---|---|---|
| `atlas/knowledge/` (`event_store`, `prediction_store`, `pattern_store`, `knowledge_engine`) | **Mercado** | `atlas_knowledge.db` | Decision Recorder (eventos/predicciones); Calibration Manager (estado de patrones, solo tras aprobación) | Learning Engine, Research Lab, Strategy Lab |
| `atlas/decision_journal/` | **Operador** | `decision_journal.db` | Decision Recorder | Operator Learning Engine |

`PatternRegistry` (dentro de `pattern_store.py`) da identidad persistente a los patrones: estado (`En observación` / `Activo` / `En decadencia` / `Inactivo` / `Reactivado`), historial de transiciones nunca borrado, evidencia acumulativa.

### Capa 4 — Registro único (`atlas/decision_recorder/`)

`DecisionRecorder` es el **único** punto de escritura autorizado hacia Knowledge Base y Decision Journal. Tres métodos: `record_decision()`, `record_market_event()`, `record_trade()`. Ningún otro módulo de producción llama a `record_event`/`record_prediction`/`record_trade` directamente (verificado por auditoría de código).

### Capa 5 — Aprendizaje (dos motores independientes, sin dependencia entre sí)

| Módulo | Aprende de | Nunca toca | Escribe en |
|---|---|---|---|
| `atlas/learning/` (`LearningEngine`, fachada de `AccuracyTracker` + `PatternEvolution` + `CalibrationAdvisor`) | Knowledge Base (mercado) | Decision Journal, Pattern Store (nunca aplica cambios), ningún motor | Nada. Solo devuelve reportes y `CalibrationProposal` |
| `atlas/operator_learning/` (`OperatorLearningEngine`) | Decision Journal (operador), vía `get_trades()` | Knowledge Base, Pattern Store, cualquier motor | Nada. Solo devuelve `OperatorInsight` |

**API estable**: `LearningEngine` y `OperatorLearningEngine` son los únicos puntos de entrada a cada dominio de aprendizaje. Nadie más debería instanciar `AccuracyTracker`, `PatternEvolution` o `CalibrationAdvisor` directamente (son de uso interno de `LearningEngine`).

### Capa 6 — Gobernanza (`atlas/calibration_manager/`)

`CalibrationManager` es la **única puerta de entrada** para modificar conocimiento permanente:
- Recibe `CalibrationRecommendation`, las versiona (nunca sobrescribe), y aplica el ciclo `Pendiente → Revisada → Aprobada/Rechazada → Implementada`.
- Al implementar una recomendación de tipo `PATTERN_STATE_CHANGE`, es el único componente autorizado para llamar `PatternRegistry.transition_state()`.
- Las recomendaciones de tipo `ENGINE_CALIBRATION` nunca se aplican solas: siguen requiriendo que un humano edite el motor a mano; Calibration Manager solo registra el hecho y el resultado obtenido.
- Base de datos propia (`calibration_manager.db`), sin import de `atlas.knowledge` ni `atlas.learning` (recibe `pattern_registry` por duck typing, sin acoplarse a su tipo).

### Capa 7 — Investigación (interfaz, sin lógica completa)

`atlas/research_lab/` y `atlas/strategy_lab/`: interfaces declaradas (`NotImplementedError`), listas para implementarse cuando haya suficiente historial real acumulado. Nunca modificarán ningún motor automáticamente.

## Verificación de dependencias (auditado)

- **Cero dependencias circulares.** `engine/` no importa nada de `learning/`, `calibration_manager/`, `decision_recorder/` ni `operator_learning/`. `knowledge/` tampoco.
- **`calibration_manager.py` no importa ningún otro paquete de Atlas** — recibe `pattern_registry` por duck typing.
- **`atlas/learning/calibration_advisor.py` no importa `atlas.calibration_manager`** — produce `CalibrationProposal` (datos), no invoca escrituras.
- **`atlas/operator_learning/operator_learning.py` no importa `atlas.knowledge`** (verificado por grep) — la separación mercado/operador es estructural, no solo documental.

## Componentes protegidos vs. extensibles

- **Protegidos** (no modificar salvo necesidad técnica explícita, aprobada antes de tocarlos): los 5 motores de Capa 1, `Decision Recorder`, `Calibration Manager`, la separación Knowledge Base / Decision Journal.
- **Extensibles dentro de la arquitectura existente**: nuevos indicadores en `atlas/indicators/`; nuevos análisis dentro de `ResearchLab`/`StrategyLab`/`OperatorLearningEngine` (implementar los métodos ya declarados); nuevas dimensiones de reporte en `AccuracyTracker`; nuevos scanners que reutilicen Data Collector + Atlas Score.
- **Explícitamente pendientes, con esquema ya preparado**: Market Replay Engine (columnas `rank_in_scan`/`scan_size` ya existen en Knowledge Base), Dashboard, Alerts.

## Pendiente conocido (no resuelto en v1.0)

No existe todavía un vínculo explícito entre una predicción y el evento que
confirma su resultado (`PredictionRecord.event_id` no se completa en la
práctica). `AccuracyTracker` lo resuelve hoy con una correlación de
solo lectura (mismo ticker + misma fecha + hora más cercana). Es una
solución honesta y funcional, pero no un enlace garantizado. Si en el
futuro se vuelve un problema real (falsos emparejamientos), requerirá una
decisión explícita de diseño -- no se resuelve dentro de esta congelación.
