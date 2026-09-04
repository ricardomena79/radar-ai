# ATLAS_BOOTSTRAP.md

> **⚠️ Nota de vigencia (Hito 5, Fase 5.4, 2026-09-04)**: este documento no se actualiza desde antes del 2026-08-17 -- no refleja Hito 3 (Fases 3.0-3.6, sistema de aprendizaje-seguro: elegibilidad, activación controlada, evaluación continua/revocación, cerrado y auditado) ni Hito 4 (Fases 4.1-4.4, observabilidad/paneles, cerrado y auditado -- localmente, sin commit/push/deploy todavía, a diferencia de Hito 3 que sí está commiteado y pusheado a esta rama). Para incorporarse al estado real del proyecto, leer el código y `.claude/plans/ethereal-mixing-anchor.md` antes que este documento.

**Documento de arranque oficial del proyecto Atlas.** Su propósito es que cualquier IA (u operador humano) pueda incorporarse a este proyecto y continuar el trabajo inmediatamente, sin necesidad de leer conversaciones anteriores.

Es un **documento vivo**: debe actualizarse después de cada entregable importante o decisión permanente. Si algo aquí contradice el estado real del código o de los documentos fuente listados en la sección 13, el código y esos documentos tienen prioridad -- corrígelo aquí.

No contiene historial de conversaciones ni código fuente. Para eso, ver los documentos referenciados en cada sección.

---

## 1. Visión del proyecto

Atlas existe para **detectar las mejores oportunidades de trading intradía de alto momentum antes que el mercado las descubra** -- específicamente, acciones con alta probabilidad de un movimiento explosivo en los próximos 5 a 10 minutos.

Atlas **no** es un screener genérico, no busca dividendos, no hace value investing, no prioriza "las mejores empresas" y no optimiza para largo plazo. Toda decisión de diseño debe poder responder que sí a una sola pregunta: *"¿Este cambio mejora la capacidad de Atlas para detectar antes las acciones explosivas?"*.

Fuente completa: [ATLAS_CONSTITUTION.md](ATLAS_CONSTITUTION.md).

---

## 2. Estado actual

- **Atlas Core** (`/atlas`): congelado en v1.0. No se modifica salvo que algo validado en `atlas_live` esté listo para incorporarse, con evidencia.
- **Cambio Nº1 (RVOL)**: implementado sobre Radar Explosivo -- `gates.min_rvol` pasó de `2.0` a `0.0` en `explosive_config.json`. Es el único cambio funcional autorizado en el ciclo actual de evolución incremental.
- **Validación histórica V2** (30 días, sin look-ahead, universo completo ~2577 símbolos) corriendo en segundo plano para medir el efecto del Cambio Nº1. **Progreso: 15/30 días** (verificar contando archivos `YYYY-MM-DD.json` en `atlas_live/backtest/results_v2/`). **Este proceso nunca debe reiniciarse, modificarse ni interrumpirse** -- ver sección 11.
- **Atlas Mission Control** (Centro de Operaciones, `atlas_live/mission_control/`): en construcción por entregables independientes, cada uno aprobado antes de empezar el siguiente. Entregables 1 y 2 implementados y probados; Entregable 2 pendiente de aprobación final del usuario; diseño del Entregable 3 (Modo Heredado) entregado y pendiente de aprobación antes de escribir código.
- **Memory Engine -- Atlas Alpha 1.0 congelado** (`atlas_live/memory/`, 2026-08-02): primera versión funcional completa -- genera rankings (Ranking Score), aprende (Memory Store + Clasificador + tasas base sobre 73.123 observaciones reales), registra y sella predicciones (Prediction Journal), las califica automáticamente al cierre con tiempo de anticipación, se recalibra diariamente, y se mantiene activo durante toda la sesión (Modo Interactivo Continuo, conectado a `scan_worker.py`). Todo probado con datos sintéticos/históricos -- **sin verificar todavía en condiciones de mercado reales**. A partir de esta baseline, toda mejora al Memory Engine debe demostrar una mejora medible respecto a Atlas Alpha 1.0 antes de incorporarse. Fuente completa: [MEMORY_ENGINE.md](MEMORY_ENGINE.md).
- Metodología vigente desde el 2026-08-01: Atlas evoluciona por **evidencia incremental**, un cambio/entregable a la vez, cada uno con aprobación explícita antes de avanzar al siguiente.

---

## 3. Arquitectura completa

```
/atlas            Atlas Core -- el "cerebro". Congelado en v1.0. Ver atlas/docs/ARCHITECTURE.md.
atlas_live/       Todo lo experimental y en vivo. Ningún desarrollo nuevo toca /atlas directamente.
  backtest/       Infraestructura de validación histórica (motor de backtest de Radar Explosivo).
  mission_control/  Centro de Operaciones (monitoreo de procesos de larga duración).
  memory/         Memory Engine -- Atlas Alpha 1.0 (ver sección 14).
  static/         Frontend del dashboard Atlas Live (HTML/CSS/JS, sin framework).
  server.py       Servidor Flask que expone el dashboard y las rutas de la API.
  scan_worker.py  Worker de escaneo en vivo.
  explosive_*.py  Motor de Radar Explosivo (ver sección 14).
```

**Regla de capas**: todo desarrollo experimental nace en `atlas_live/`. Solo se incorpora a `/atlas` después de validarse con evidencia histórica y en tiempo real, siguiendo la Metodología de Propuestas (sección 9).

**Atlas Core** (`/atlas`) contiene, entre otros: `engine/` (decision engine, momentum/money-flow/risk/market-context engines, score engine), `scanners/` (momentum, premarket, afterhours, microcaps, etfs), `knowledge/` + `learning/` (Knowledge Engine y Learning Engine, con sus propios sub-motores: accuracy tracker, pattern evolution, calibration advisor), `decision_journal/` y `decision_recorder/`, `calibration_manager/`, `data/providers` (proveedores de datos desacoplados del motor), y `tests/` (suite de pruebas de Atlas Core). Todo esto es v1.0, estable, y no forma parte del trabajo activo actual salvo que una propuesta validada busque incorporarse ahí.

Fuente completa: [ATLAS_MISSION_CONTROL.md](ATLAS_MISSION_CONTROL.md) (sección de arquitectura de Mission Control) y `atlas/docs/ARCHITECTURE.md` (arquitectura de Atlas Core).

---

## 4. Constitución resumida

Autoridad máxima del proyecto: [ATLAS_CONSTITUTION.md](ATLAS_CONSTITUTION.md). Ninguna propuesta se implementa sin poder anclarse a al menos uno de estos principios:

1. Los datos tienen prioridad sobre las opiniones.
2. Todo cambio debe poder medirse.
3. Ningún algoritmo nuevo entra a Atlas Core sin haber sido validado previamente.
4. Atlas siempre debe explicar por qué recomienda una acción.
5. El proveedor de datos nunca podrá estar acoplado al motor.
6. Radar Explosivo es el módulo más importante del sistema.
7. La simplicidad vale más que agregar indicadores.
8. Ninguna propuesta importante podrá implementarse sin respetar esta Constitución.

**Métricas oficiales** (toda mejora debe demostrar impacto medible en al menos una): Precision@10, Precision@20, Recall, tiempo de detección, falsos positivos, falsos negativos.

**Metodología obligatoria de propuestas** (formato exacto en la Constitución): PROBLEMA → HIPÓTESIS → PRINCIPIOS QUE LA RESPALDAN → IMPACTO ESPERADO → RIESGOS → CÓMO SE VALIDARÁ → CRITERIOS DE ÉXITO. Se aprueba el diseño primero, se valida con datos históricos, después con datos en tiempo real, y solo entonces se considera permanente.

---

## 5. Roadmap maestro

Fuente completa y siempre vigente: [ATLAS_ROADMAP.md](ATLAS_ROADMAP.md) (11 fases, con % de avance estimado por fase, actualizado a mano tras cada cambio de estado). No se duplica el detalle aquí porque cambia con frecuencia -- **leer siempre el archivo directamente**, no confiar en un porcentaje citado en otro documento.

Fases: 1) Arquitectura, 2) Radar Explosivo, 3) Validación histórica, 4) Explosive DNA, 5) Optimización del Radar (Cambio Nº1 en curso), 6) Cambio de proveedor de datos, 7) Alertas en tiempo real, 8) Dashboard profesional, 9) IA de apoyo a decisiones, 10) Mission Control, 11) Memory Engine (Atlas Alpha 1.0 congelado).

---

## 6. Estado de cada módulo

| Módulo | Estado |
|---|---|
| Atlas Core (`/atlas`) | Congelado v1.0, estable, con suite de tests propia. |
| Radar Explosivo (`explosive_engine.py`, `explosive_factors.py`, `explosive_config.json`) | Implementado y validado con 30 días de evidencia (config original). Cambio Nº1 (RVOL) implementado sobre esta base, en validación. |
| Infraestructura de validación histórica (`atlas_live/backtest/`) | Completa y en uso activo (motor de backtest, reportes, Explosive DNA, simulador what-if, comparación de rol de RVOL). |
| Validación V2 (resultado del Cambio Nº1) | En curso, 15/30 días. No tocar. |
| Dashboard Atlas Live (`atlas_live/static/`, `server.py`) | MVP funcional en uso (diagnóstico, alertas, notificaciones). Falta versión "profesional" definitiva (fase 8 del roadmap). |
| Mission Control (`atlas_live/mission_control/`) | En construcción por entregables (ver secciones 7 y 8). |
| Memory Engine (`atlas_live/memory/`) | **Atlas Alpha 1.0 congelado** -- primera versión funcional completa (ver sección 2 y [MEMORY_ENGINE.md](MEMORY_ENGINE.md)). Sin verificar en mercado real todavía. |
| Cambio de proveedor de datos | Evaluación comparativa completa ([DATA_PROVIDER_EVALUATION.md](DATA_PROVIDER_EVALUATION.md)), sin decisión ni implementación. |
| IA de apoyo a decisiones (fase 9) | Sin trabajo iniciado. |

---

## 7. Entregables completados

- **Radar Explosivo v1**: motor de 3 etapas (gates → score ponderado → penalización por tamaño), configuración externa en JSON, registro de factores enchufable.
- **Infraestructura de validación histórica completa**: `historical_scan.py`, `validation_report.py`, `explosive_dna.py`, `run_validation.py`, `whatif_simulator.py`, `filter_interaction.py`, `rvol_role_comparison.py`.
- **Cambio Nº1 (RVOL)**: `gates.min_rvol` de `2.0` a `0.0`. V1 respaldado en `atlas_live/backtest/results_v1/` (incluye `explosive_config_v1.json`).
- **Mission Control -- Entregable 1 (heartbeat)**: `atlas_live/mission_control/heartbeat.py`. Cualquier proceso puede reportar estado (7 estados estandarizados), progreso, PID, CPU/memoria (vía `psutil`), versión (hash de git + flag `dirty`), a un archivo JSON con escritura atómica. Run ID con formato validado (`<ETIQUETA>_<YYYYMMDD>_<HHMM[SS]>`). **Aprobado de forma definitiva.**
- **Mission Control -- Entregable 2 (Timeline)**: `atlas_live/mission_control/timeline.py`, integrado con `heartbeat.py`. Registro histórico permanente en SQLite, append-only, 10 tipos de evento, 4 niveles de severidad; solo las transiciones reales de estado generan evento (no cada `step()`). Implementado y probado. **Pendiente de aprobación final del usuario.**

Detalle de pruebas y desviaciones de cada entregable: [ATLAS_MISSION_CONTROL.md](ATLAS_MISSION_CONTROL.md), bajo cada bloque de entregable.

**Memory Engine -- Atlas Alpha 1.0 congelado** (`atlas_live/memory/`, 2026-08-02) -- primera versión funcional completa:
- **Memory Store** (`store.py`): esquema SQLite/WAL append-only, con `market_context` reservado para contexto de mercado futuro.
- **Clasificador de Resultado** (`classifier.py`): 5 categorías (EXPLOSION/FALSE_BREAKOUT/LOSER/WEAK/NORMAL), reglas explícitas con prioridad estricta; validado sobre 73.123 observaciones reales; prueba de regresión permanente (`test_classifier_golden.py`, 41 casos congelados).
- **Backfill** (`backfill.py`): 73.123 filas reales cargadas (30 días), 0 descartadas, integridad y append-only verificados.
- **Motor de tasas base** (`base_rates.py`): validación estadística de 3 condiciones (muestra, significancia de Wilson, consistencia temporal) -- misma metodología que `PatternEvolution` de Atlas Core, reimplementada localmente sin acoplarse a Core.
- **Generador de Propuestas** (`calibration_advisor.py`): 10 de 14 condiciones evidencia-informadas resultaron confiables.
- **Ranking Score de desempate** (`ranking_score.py`): 4 niveles de prioridad estricta (sin pesos inventados). Validado con mejora medible: NUWE #25→#2, XRX #39→#1, Precision@10 20%→70% (2026-07-30).
- **Prediction Journal** (`prediction_journal.py`): snapshots dinámicos + sellado único inmutable (`AlreadySealedError`) + calificación única (`AlreadyGradedError`) + tiempo de anticipación automático.
- **Integración en tiempo real** (`market_hours.py`, `live_integration.py` + 6 líneas aditivas en `scan_worker.py`): detección de sesión, snapshot en premarket, sellado automático (09:25-09:30), calificación automática al cierre.
- **Modo Interactivo Continuo**: recalibración diaria automática de evidencia; apagado prolijo (`request_stop`/`wait_until_stopped`) sin pérdida de estado. No es un servicio 24/7 (fuera de alcance, versión futura).

Detalle completo de cada componente, sus pruebas y su estado: [MEMORY_ENGINE.md](MEMORY_ENGINE.md).

---

## 8. Entregables pendientes

Plan completo de 9 entregables en [ATLAS_MISSION_CONTROL.md](ATLAS_MISSION_CONTROL.md), sección "PLAN DE IMPLEMENTACIÓN". Orden estrictamente secuencial, cada uno aprobado antes del siguiente:

- **Entregable 3 -- Modo heredado** (`legacy_inspector.py`): inferencia externa de solo lectura del estado de un proceso que no usa el latido (ej. la validación V2). Diseño técnico entregado, **pendiente de aprobación** antes de escribir código.
- **Entregable 4 -- API backend**: expone procesos instrumentados + heredados + Timeline como endpoints JSON en `server.py`.
- **Entregable 5 -- Panel principal (frontend, solo lectura)**: nueva sección "Mission Control" en el dashboard.
- **Entregable 6 -- Vista de Timeline (frontend)**: historial filtrable por severidad.
- **Entregable 7 -- Supervisión Inteligente**: 6 detectores automáticos de anomalías (procesos detenidos, rate limiting, APIs lentas, consumo excesivo de CPU/memoria, procesos sin heartbeat, caídas de Internet).
- **Entregable 8 -- Botones de control**: Iniciar/Pausar/Reanudar/Detener, protocolo cooperativo (nunca kill a nivel de sistema operativo por defecto). Riesgo alto -- única pieza que actúa sobre procesos reales.
- **Entregable 9 -- Instrumentar un proceso real**: agregar el latido a `run_validation.py`/`historical_scan.py` para la **próxima** corrida (nunca la V2 actual).

Después de Mission Control, o en paralelo si V2 termina antes: **informe comparativo V1 vs V2** (Precision@10/@20, Recall, FP/FN, oportunidades/día, ranking top-20, símbolos nuevos/perdidos, costo del cambio, conclusión objetiva) para cerrar formalmente el Cambio Nº1 antes de considerar el Cambio Nº2.

**Memory Engine, pendiente tras Atlas Alpha 1.0** (no bloqueante entre sí):
- **Primera prueba en condiciones de mercado reales** -- todo lo construido se validó con datos sintéticos e históricos, nunca contra un premarket real.
- **Validación agregada de 30 días** (Precision@10/@20/Recall completo sobre el período, `proposal_validator.py`) -- hoy solo existe la demo de días individuales.
- **Entregable 8 -- Checkpoints intermedios** (10/30/60 min): refinamiento, no bloqueante.
- Cualquier mejora debe demostrar mejora medible respecto a Atlas Alpha 1.0 antes de incorporarse.

---

## 9. Decisiones técnicas permanentes

- **Separación de capas**: `/atlas` congelado; todo lo nuevo nace en `atlas_live/` y solo sube al Core con evidencia. No negociable.
- **Un cambio/entregable a la vez**, con aprobación explícita antes de avanzar. No se amplía el alcance de un entregable ya aprobado sin proponerlo como mejora futura.
- **Evolución por evidencia**: histórico → tiempo real → permanente, nunca se salta directo a "implementar".
- **Mission Control -- 7 estados estandarizados**: Iniciando, Ejecutándose, Pausado, Esperando, Finalizado, Error, Cancelado. Ningún módulo define estados propios.
- **Mission Control -- 4 niveles de severidad**: INFO, WARNING, ERROR, CRITICAL. Autoevaluados por el propio proceso (distinto de una alerta detectada externamente).
- **Run ID único**: `<ETIQUETA>_<YYYYMMDD>_<HHMM>` (o `<HHMMSS>` en colisión), siempre generado por `make_run_id()`, nunca armado a mano.
- **`heartbeat_schema` separado del hash de git**: el schema versiona el formato del heartbeat; el commit versiona el código.
- **Timeline en SQLite**: reutiliza el mismo tipo de almacenamiento que ya usan Decision Journal y Knowledge Base de Atlas Core -- no se introduce una herramienta nueva al proyecto.
- **Timeline append-only**: ningún evento se edita ni se borra una vez escrito.
- **Escritura atómica** de archivos de estado: archivo temporal + `os.replace()`, para que ningún lector vea un archivo a medio escribir.
- **ETA solo cuando es calculable**: si no hay `total` conocido, `eta_seconds` es `None`, nunca un número inventado.
- **`psutil`** como dependencia estándar para CPU/memoria multiplataforma (decisión pensada a largo plazo, no solo para el entorno actual).
- **Memory Engine estudia TODO el mercado, no solo explosiones**: 5 categorías de resultado (EXPLOSION/FALSE_BREAKOUT/LOSER/WEAK/NORMAL), no solo un registro de ganadoras.
- **Ranking Score sin pesos inventados**: orden de prioridad estricto de 4 niveles (comparación lexicográfica), no una suma ponderada con coeficientes sin evidencia -- mismo error que ya se corrigió con los pesos originales de RVOL, no se repite.
- **Ranking Score NO reemplaza a Radar Explosivo ni modifica la detección** -- solo desempata candidatos que ya tienen la misma probabilidad histórica.
- **Prediction Journal, no solo Log**: cada predicción sellada guarda la explicación completa que la generó, no solo el símbolo y su posición.
- **Sellado y calificación verificablemente inmutables**: `AlreadySealedError`/`AlreadyGradedError` -- ninguna predicción ni resultado ya registrado se puede sobrescribir.
- **Atlas Alpha 1.0 como baseline formal**: toda mejora futura al Memory Engine debe demostrar una mejora medible respecto a esta versión antes de incorporarse.
- **Modo Interactivo Continuo, no un servicio 24/7**: Atlas trabaja mientras la app está abierta; el 24/7 queda explícitamente para una versión futura, sin modificar la arquitectura actual.

---

## 10. Reglas de desarrollo

1. Antes de escribir código importante, comprobar que la propuesta respeta [ATLAS_CONSTITUTION.md](ATLAS_CONSTITUTION.md). Si contradice algo, detenerse y explicarlo antes de continuar.
2. Toda mejora no trivial se presenta primero con el formato PROBLEMA/HIPÓTESIS/PRINCIPIOS/IMPACTO/RIESGOS/VALIDACIÓN/CRITERIOS DE ÉXITO, y espera aprobación explícita antes de implementarse.
3. No se agrega alcance no solicitado a un entregable ya aprobado. Mejoras no críticas se proponen para una versión futura.
4. No se avanza al siguiente entregable/cambio sin aprobación explícita del anterior.
5. Cualquier desviación entre lo implementado y lo diseñado se reporta explícitamente, aunque sea menor -- nunca se oculta ni se minimiza.
6. Decisiones importantes (con alternativas evaluadas y justificación) se registran en [DECISION_LOG.md](DECISION_LOG.md).
7. Antes y después de cualquier acción significativa, verificar en modo solo lectura que la validación V2 sigue intacta (ver sección 11).

---

## 11. Qué nunca debe modificarse

- **La validación V2 en curso** (`atlas_live/backtest/results_v2/` y el proceso que la genera): nunca reiniciar, modificar ni interrumpir. Solo diagnóstico de solo lectura hasta que llegue a 30/30 días.
- **`/atlas` (Atlas Core)**: no se edita directamente. Solo se incorpora algo ahí después de validación completa con evidencia, siguiendo la metodología de propuestas.
- **`explosive_engine.py` y `explosive_factors.py`**: no deben cambiar como efecto colateral de ningún cambio de configuración -- el Cambio Nº1 fue exclusivamente en `explosive_config.json`.
- **`atlas_live/backtest/results_v1/`**: backup de referencia del Cambio Nº1; no editar ni borrar.
- **Eventos ya escritos en el Timeline de Mission Control**: append-only por diseño, ningún código debe editarlos ni borrarlos retroactivamente.
- **Observaciones ya escritas en el Memory Store, y predicciones ya selladas/calificadas en el Prediction Journal**: append-only por diseño (`AlreadySealedError`/`AlreadyGradedError` lo garantizan en código, no solo en documentación).
- **Radar Explosivo (`explosive_engine.py`, `explosive_factors.py`, `explosive_config.json`) desde el Memory Engine**: el Ranking Score y cualquier recalibración del Memory Engine solo proponen -- ninguna se aplica a estos archivos sin su propia propuesta formal aprobada, cada vez.
- **Archivos históricos superseded** (`ROADMAP.md`, `DECISIONES.md`): se conservan como registro histórico, no se editan; la fuente de verdad vigente es `ATLAS_ROADMAP.md` y `DECISION_LOG.md` / `ATLAS_CONSTITUTION.md` respectivamente.

---

## 12. Próximo paso exacto

Dos frentes independientes, ninguno bloquea al otro:

1. **Mission Control**: esperar aprobación del usuario sobre el **diseño técnico del Entregable Nº3 (Modo Heredado)** (documento de diseño ya entregado). Una vez aprobado: implementar `atlas_live/mission_control/legacy_inspector.py`, validarlo en modo solo lectura contra la validación V2 real, reportar y esperar aprobación antes del Entregable Nº4.
2. **Memory Engine (Atlas Alpha 1.0)**: la **primera prueba en condiciones de mercado reales** -- todo lo construido se validó con datos sintéticos e históricos, nunca contra un premarket real todavía. No implica más código por sí sola, sino correr `atlas_live/server.py` durante un premarket real y observar el resultado.

En paralelo, sin bloquear ninguno de los dos: seguir monitoreando (solo lectura) `atlas_live/backtest/results_v2/` hasta 30/30 días, y entonces generar el informe comparativo V1 vs V2 para cerrar formalmente el Cambio Nº1 antes de considerar el Cambio Nº2.

---

## 13. Estructura del repositorio

```
/atlas                          Atlas Core, congelado v1.0 (engine, scanners, knowledge, learning, tests, docs propios).
/atlas_live                     Todo lo experimental y en vivo.
  /backtest                     Validación histórica (motor, reportes, resultados v1/v2).
  /mission_control               Centro de Operaciones (heartbeat, timeline, futuros entregables).
  /memory                        Memory Engine -- Atlas Alpha 1.0 (store, classifier, base_rates, calibration_advisor, ranking_score, prediction_journal, market_hours, live_integration).
  /static                       Frontend del dashboard.
  server.py, scan_worker.py     Backend Flask y worker de escaneo en vivo (conectado al Memory Engine).
  explosive_*.py                Motor de Radar Explosivo.

ATLAS_CONSTITUTION.md           Autoridad máxima del proyecto (misión, principios, métricas, metodología).
ATLAS_ROADMAP.md                Plan maestro vigente, 11 fases con % de avance.
ATLAS_MISSION_CONTROL.md        Diseño técnico completo + plan de implementación de Mission Control.
MEMORY_ENGINE.md                Diseño + plan de implementación + baseline Atlas Alpha 1.0 del Memory Engine.
ATLAS_MASTER_DOCUMENT.md        Fuente de verdad completa del proyecto (20 secciones + checklist maestro).
ATLAS_BOOTSTRAP.md              Este documento -- punto de entrada único para retomar el proyecto.
DECISION_LOG.md                 Historial de decisiones importantes (formato: problema/alternativas/decisión/justificación/impacto).
DATA_PROVIDER_EVALUATION.md     Evaluación comparativa de proveedores de datos (fase 6 del roadmap).
RADAR_EXPLOSIVO_V2.md           Hipótesis y evidencia detrás del Cambio Nº1 (RVOL) y próximos candidatos.
VALIDATION_RESULTS.md           Resultados de la validación histórica de 30 días (config original).
INFORME_EJECUTIVO_FINAL.md      Informe ejecutivo de cierre de Radar Explosivo v1 / auditoría de Atlas Core.
ROADMAP.md, DECISIONES.md       Históricos, reemplazados -- no editar, ver notas al inicio de cada uno.
requirements.txt                Dependencias (incluye psutil, agregado para Mission Control).
```

---

## 14. Glosario de componentes

- **Atlas Core**: el motor de decisión original de Atlas (`/atlas`), congelado en v1.0.
- **Atlas Live**: la capa experimental y de presentación (`atlas_live/`), donde ocurre todo el desarrollo activo.
- **Radar Explosivo**: motor de detección de momentum explosivo de Atlas Live (`explosive_engine.py` + `explosive_factors.py` + `explosive_config.json`). El módulo más importante del sistema (Principio 6).
- **Gates**: filtros excluyentes del Radar Explosivo (ej. RVOL mínimo, antes de que un candidato entre al cálculo de score).
- **Cambio Nº1**: primer cambio incremental del ciclo de evolución por evidencia -- `gates.min_rvol` de `2.0` a `0.0`.
- **Universo Racional**: conjunto de ~2577 símbolos usado como base de la validación histórica (no una muestra reducida).
- **Validación V1 / V2**: corridas de 30 días de la infraestructura de backtest, con la configuración original (V1) y con el Cambio Nº1 aplicado (V2), sobre los mismos días exactos, para comparación objetiva.
- **Mission Control (Centro de Operaciones)**: sistema de monitoreo unificado para cualquier proceso de larga duración de Atlas.
- **Heartbeat**: latido -- reporte periódico de estado de un proceso instrumentado (`heartbeat.py`), escrito como archivo JSON.
- **Run ID**: identificador único de una ejecución, formato `<ETIQUETA>_<YYYYMMDD>_<HHMM[SS]>`.
- **Timeline**: registro histórico permanente y append-only de eventos importantes de cualquier proceso (`timeline.py`, SQLite).
- **Modo heredado**: capacidad de Mission Control de inferir el estado de un proceso externo que no usa el heartbeat (Entregable 3).
- **Supervisión Inteligente**: detección automática de anomalías sobre procesos monitoreados (Entregable 7, diseño aprobado, no implementado).
- **Metodología de Propuestas**: formato obligatorio (PROBLEMA/HIPÓTESIS/PRINCIPIOS/IMPACTO/RIESGOS/VALIDACIÓN/CRITERIOS DE ÉXITO) para toda mejora no trivial, definido en la Constitución.
- **Entregable**: unidad mínima de trabajo aprobable de forma independiente, con objetivo, archivos, riesgo, dependencias y criterio de terminado propios.
- **Memory Engine**: sistema de Atlas Live que estudia todo el mercado escaneado a diario (no solo explosiones) para recalibrar el Ranking de Radar Explosivo con evidencia real. Vive en `atlas_live/memory/`.
- **Memory Store**: almacenamiento SQLite append-only de observaciones clasificadas (`store.py`).
- **Clasificador de Resultado**: asigna una de 5 categorías (EXPLOSION/FALSE_BREAKOUT/LOSER/WEAK/NORMAL) a cada observación, con reglas explícitas de prioridad (`classifier.py`).
- **Ranking Score**: mecanismo de desempate de 4 niveles entre candidatos con la misma probabilidad histórica -- no reemplaza a Radar Explosivo, no es un peso inventado (`ranking_score.py`).
- **Prediction Journal**: registro de predicciones con dos flujos (dinámico durante el premarket, sellado único antes de la apertura) más su resultado real y tiempo de anticipación (`prediction_journal.py`).
- **Sellado (sealing)**: acto de fijar el ranking oficial del día de forma verificablemente inmutable, antes de conocer el resultado real.
- **Tiempo de anticipación**: minutos entre la primera detección de un símbolo en un ranking dinámico y la confirmación de su movimiento real.
- **Modo Interactivo Continuo**: Atlas escanea, rankea, aprende y registra predicciones mientras la aplicación está abierta -- no un servicio 24/7 (explícitamente diferido a una versión futura).
- **Atlas Alpha 1.0**: baseline oficial congelada del Memory Engine (2026-08-02) -- primera versión funcional completa; toda mejora futura debe demostrar una mejora medible respecto a ella antes de incorporarse.

---

*Última actualización de este documento: 2026-08-02, tras congelar Atlas Alpha 1.0 (Memory Engine) como baseline oficial. Actualizar tras cada entregable aprobado, cambio de fase del roadmap, o decisión técnica permanente nueva.*
