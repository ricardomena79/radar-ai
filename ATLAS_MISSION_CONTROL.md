# ATLAS_MISSION_CONTROL.md

**Estado: diseño aprobado, pendiente de implementación.** Fase oficial del proyecto Atlas (ver [ATLAS_ROADMAP.md](ATLAS_ROADMAP.md), fase 10). Diseño técnico del **Centro de Operaciones de Atlas** ("Mission Control") -- no es solo un monitor de procesos: es el lugar único desde donde se ve, se entiende y se controla todo lo que Atlas ejecuta, hoy y a lo largo de los próximos años del proyecto. **Nada de esto está implementado todavía.** Sujeto a [ATLAS_CONSTITUTION.md](ATLAS_CONSTITUTION.md).

**Por qué existe, en una frase**: durante esta sesión, verificar si una validación de 30 días estaba viva, bloqueada o avanzando requirió revisar manualmente PIDs, timestamps de archivos y logs cada vez que se preguntó. Mission Control automatiza ese diagnóstico, lo deja siempre visible, lo registra en el tiempo, y avisa solo cuando algo requiere atención.

**Relación con la Constitución**: infraestructura, igual que Alertas en tiempo real (fase 7) y Dashboard profesional (fase 8) -- no es una mejora al Radar Explosivo. Principio 2 ("todo cambio debe poder medirse") y Principio 7 (simplicidad: mecanismos cooperativos sobre control de bajo nivel del sistema operativo, reglas simples sobre inteligencia artificial donde una regla simple alcanza).

**Restricción de esta sesión, respetada en todo el diseño**: la validación V2 en curso no está instrumentada y no se le agrega nada mientras corre. Todo lo que sigue contempla un **modo heredado** (inferencia externa) para procesos sin instrumentar, y un **modo instrumentado** (datos exactos, autoreportados) para los que la adopten.

---

## 1. Arquitectura general

Tres piezas, desacopladas entre sí:

**a) El "latido" (heartbeat)** -- librería liviana y opcional que cualquier proceso de Atlas puede usar para reportarse a sí mismo: PID, CPU, memoria (vía `psutil`, cross-platform), progreso, y eventos importantes. Vive en `atlas_live/mission_control/heartbeat.py`.

**b) El Timeline** -- registro histórico permanente de todo lo importante, across todos los procesos, pasados y presentes (sección 4).

**c) El panel Mission Control** -- backend (nuevas rutas en `atlas_live/server.py` o un blueprint separado) + frontend (5ª sección del menú lateral del dashboard existente). Lee el estado actual y el historial, aplica la Supervisión Inteligente (sección 5), y lo muestra.

**Limitación reconocida, no resuelta en v1**: si Mission Control se sirve desde el mismo servidor Flask de Atlas Live, y ese servidor se cae, Mission Control cae con él. No se justifica un proceso independiente solo para esto al tamaño actual del proyecto -- trade-off explícito.

---

## 2. Estándares transversales

Los tres ajustes de esta ronda de revisión. Se aplican a **todo** lo demás en este documento -- ningún módulo define sus propios estados, severidades o identificadores.

### 2.1 Estados estandarizados

Exactamente estos 7, iguales para cualquier tipo de proceso (Scanner, Validación, Paper Trading, IA, lo que venga):

| Estado | Significa |
|---|---|
| **Iniciando** | El proceso arrancó pero todavía no completó su primera unidad de trabajo (ej. todavía descargando el historial diario antes de empezar a evaluar días) |
| **Ejecutándose** | Trabajando activamente |
| **Pausado** | El usuario pidió una pausa (botón "Pausar") y el proceso la atendió en el próximo punto de control cooperativo |
| **Esperando** | Vivo, sin trabajo activo en este momento -- ej. esperando un disparador programado, o en pausa de reintento tras un rate-limit, o esperando una dependencia externa. Distinto de "Pausado": nadie lo pidió, es parte de su operación normal |
| **Finalizado** | Terminó todo su trabajo con éxito |
| **Error** | Terminó (o se detuvo) por un problema no manejado |
| **Cancelado** | El usuario pidió detenerlo (botón "Detener") antes de que terminara todo su trabajo, y el proceso atendió la señal de forma prolija |

No existe un estado "Deteniéndose" aparte: mientras atiende una solicitud de cancelación, el proceso sigue mostrando "Ejecutándose" (todavía está terminando su unidad de trabajo actual) hasta que efectivamente sale y pasa a "Cancelado" -- siete estados, sin uno adicional para la transición.

### 2.2 Severidad de eventos

Todo evento del Timeline, sin excepción, tiene exactamente una de estas cuatro:

| Severidad | Uso |
|---|---|
| **INFO** | Ciclo de vida normal: inicio, fin exitoso, pausa/reanudación pedida por el usuario, cambios de estado esperados, hitos que un proceso reporta por su cuenta |
| **WARNING** | Algo que el sistema puede seguir tolerando pero vale la pena mostrar: rate-limiting activo, consumo de CPU/memoria por encima de lo esperado |
| **ERROR** | Un proceso individual reportó un error propio, o un detector determinó que ese proceso específico falló o se degradó de forma significativa -- impacto contenido a ese proceso |
| **CRITICAL** | Impacto más amplio que un solo proceso: caída de Internet (detector cross-proceso), un proceso sin heartbeat (posible cuelgue), o un proceso que desapareció sin reportar su propio cierre |

**Mapeo por defecto según el tipo de evento** (los detectores de la sección 5 pueden anular esto caso por caso si la situación concreta lo justifica, pero todo evento nuevo que se agregue en el futuro debe caer en alguna de estas cuatro, no inventar una quinta):

| `event_type` | Severidad por defecto |
|---|---|
| `process_started`, `process_completed`, `process_paused`, `process_resumed`, `state_changed`, `milestone` | INFO |
| `alert_resolved` | INFO (la resolución siempre es buena noticia, sin importar la severidad de la alerta original) |
| `process_stopped` (Cancelado, pedido por el usuario) | INFO |
| Alerta de rate-limiting, alerta de CPU/memoria alta | WARNING |
| `process_error` (el proceso reportó su propio error) | ERROR |
| Alerta de "sin heartbeat", "proceso terminó de forma inesperada", "caída de Internet" | CRITICAL |

El semáforo del panel principal (sección 3) se deriva directamente de esto: verde si no hay nada por encima de INFO activo, amarillo si hay WARNING, rojo si hay ERROR o CRITICAL.

### 2.3 Run ID

Cada **ejecución** de un proceso (no cada tipo -- dos corridas del mismo script son dos Run ID distintos) recibe un identificador único al arrancar, con este formato:

```
<ETIQUETA>_<YYYYMMDD>_<HHMM>
```

Ejemplo real de esta sesión: `VALIDATION_V2_20260802_1530`. `<ETIQUETA>` es un nombre corto en mayúsculas elegido al lanzar el proceso (no tiene que ser exactamente la clave interna del catálogo de tipos -- da lugar a distinguir, por ejemplo, `VALIDATION_V1` de `VALIDATION_V2`, ambas del mismo `process_type` `backtest_validation`). Si dos procesos del mismo tipo arrancan en el mismo minuto, se usa `HHMMSS` en su lugar para evitar colisión.

**El Run ID es la clave que conecta todo lo relacionado con una misma ejecución**: es el nombre del archivo de estado (`status/<RUN_ID>.json`), el valor de `process_id` en cada fila del Timeline que le corresponde, y la referencia dentro del `log_path` de esa ejecución. Para procesos nuevos que adopten esta convención, se recomienda que también nombren sus carpetas/archivos de resultados a partir del mismo Run ID, para poder reconstruir "todo lo que pasó en esta ejecución específica" desde cualquiera de los 4 lugares sin tener que adivinar la correspondencia por fecha o por cercanía de horario.

Esta convención se adopta **de acá en adelante** para procesos instrumentados -- no implica renombrar carpetas ya existentes de corridas pasadas (como `results_v1`/`results_v2` de esta sesión), eso sería un cambio fuera del alcance de este documento.

### 2.4 Esquema del archivo de latido (heartbeat) -- estándar oficial

Formato mínimo obligatorio que **todo** proceso instrumentado debe poder reportar, sin excepción -- Scanner, Validación, Paper Trading, IA, Backtest, APIs, o cualquier cosa que se agregue después. Este esquema es el contrato: mientras un proceso lo cumpla, se integra con Mission Control sin que Mission Control necesite saber nada específico de ese proceso.

Cada campo tiene un **origen** distinto -- no todos los reporta activamente el proceso, algunos los calcula la librería sola, y otros se calculan recién al leer el archivo (no se guardan). Esta distinción es la que hace que instrumentar un proceso sea agregar 3-4 llamadas, no tener que llevar la cuenta de todo a mano:

| Campo pedido | Nombre en el archivo | Origen | Notas |
|---|---|---|---|
| Run ID | `run_id` | Automático (la librería lo genera en `start()`, formato de la sección 2.3) | El proceso nunca lo arma a mano -- evita errores de formato |
| Nombre del proceso | `label` | Reportado por el proceso (parámetro de `start()`) | Ej. `"Validación histórica V2"` |
| — (necesario para el catálogo, no pedido explícitamente pero requerido para que el registro de tipos funcione) | `process_type` | Reportado por el proceso (parámetro de `start()`) | Una de las claves del catálogo, sección 7 |
| Estado | `state` | Reportado por el proceso | Uno de los 7 de la sección 2.1 |
| Inicio | `started_at` | Automático, capturado una vez en `start()` | |
| Último Heartbeat | `last_heartbeat` | Automático, actualizado en cada llamada | Es lo que alimenta el detector de "sin heartbeat" |
| Progreso (% y unidad) | `progress` | Reportado por el proceso | `{done, total, unit}` -- `total` puede ser `null` si el proceso no tiene un final definido (ej. el scanner en vivo); en ese caso no hay "%" que mostrar, y se dice explícitamente en vez de inventar un 100% falso |
| PID | `pid` | Automático (`os.getpid()`) | |
| CPU | `cpu_percent` | Automático (`psutil` sobre el propio proceso) | |
| Memoria | `memory_mb` | Automático (`psutil` sobre el propio proceso) | |
| Tiempo transcurrido | *(no se guarda -- derivado)* | **Derivado al leer**: `ahora - started_at` | No hace falta que el proceso lo calcule ni lo reporte |
| Tiempo estimado restante | *(no se guarda -- derivado)* | **Derivado al leer**, solo si `progress.total` no es `null` y `progress.done > 0`: `transcurrido / done × (total - done)` | Cumple el "si es posible" -- si no hay `total`, Mission Control muestra "no disponible", nunca un número inventado |
| Nivel de severidad actual | `severity` | Reportado por el proceso, default `INFO` | Es la **autoevaluación** del proceso sobre sí mismo (puede subirla si nota algo raro en su propia ejecución, ej. muchos reintentos). Es distinta de las alertas que la Supervisión Inteligente detecta desde afuera (sección 5) -- ambas existen a la vez y se combinan al mostrar, pero no son el mismo dato: una la dice el proceso, la otra la calcula Mission Control mirándolo desde afuera |
| Último mensaje de estado | `last_message` | Reportado por el proceso | Texto corto, ej. `"Procesando día 8/30: 2026-06-30"` -- para mostrar un resumen de una línea sin tener que abrir el log completo |
| Versión de Atlas | `atlas_version` | Automático, capturado una vez en `start()` | El hash corto del commit de git vigente (`git rev-parse --short HEAD`) más un flag `dirty: true/false` si había cambios sin confirmar al momento de arrancar. Se eligió el commit en vez de un número de versión mantenido a mano porque siempre es exacto -- no depende de que alguien se acuerde de actualizarlo, y permite saber, años después, con qué código exacto corrió cada ejecución del Timeline |
| Versión del formato de heartbeat | `heartbeat_schema` | Constante fija de la librería | Independiente del commit de Git: el commit versiona el *código*, `heartbeat_schema` versiona el *formato del archivo*. Permite evolucionar el esquema del heartbeat en el futuro sin romper la lectura de archivos viejos, y sin que un cambio de código no relacionado con el heartbeat lo haga parecer "otra versión" |

**Ejemplo de archivo de latido** (conceptual, no código):
```json
{
  "heartbeat_schema": "1.0",
  "run_id": "VALIDATION_V2_20260802_1530",
  "process_type": "backtest_validation",
  "label": "Validación histórica V2 (RVOL como factor)",
  "state": "Ejecutándose",
  "started_at": "2026-08-02T15:30:00",
  "last_heartbeat": "2026-08-02T18:41:25",
  "progress": {"done": 10, "total": 30, "unit": "días"},
  "pid": 15564,
  "cpu_percent": 12.4,
  "memory_mb": 420.6,
  "severity": "INFO",
  "last_message": "Procesando día 10/30: 2026-07-01",
  "atlas_version": {"commit": "a1b2c3d", "dirty": true}
}
```

**Compatibilidad con procesos que no reportan todo**: si un proceso no tiene un `total` de progreso conocido, `total` va como `null`, no se omite el campo -- Mission Control siempre puede esperar la clave `progress`, solo que a veces con `total: null` en vez de inventar cómo llenarla.

---

## 3. Panel principal

Vista de "ahora mismo", generada a partir de los latidos de todos los procesos activos (instrumentados o heredados). Todo lo que sigue usa los estándares de la sección 2:

- **Estado general de Atlas**: semáforo verde/amarillo/rojo, derivado de la severidad más alta entre las alertas activas (sección 2.2).
- **Procesos activos**: una tarjeta por proceso, con Run ID, tipo (catálogo, sección 7), uno de los 7 estados estandarizados, PID, hora de inicio.
- **CPU y memoria**: autoreportado vía `psutil` sobre el propio PID (instrumentado), o inferido por línea de comando (heredado, marcado como aproximado).
- **Tiempo activo**: `ahora - started_at`.
- **Progreso**: barra `done/total` (ej. "7/30 días"). Tiempo estimado restante: proyección lineal simple (`transcurrido / done × restantes`), mostrada explícitamente como estimación.
- **Último heartbeat**: timestamp de la última actualización -- alimenta el detector de "sin heartbeat" (sección 5).
- **Último log recibido**: últimas N líneas del archivo en `log_path`, leídas bajo demanda.
- **Alertas activas**: lista consolidada de la Supervisión Inteligente, con severidad y proceso afectado (o "sistema" si es cross-proceso).

---

## 4. Timeline

Registro cronológico **permanente**, distinto del panel principal (que es del "ahora"). Cada fila es un evento, nunca se edita, solo se agrega.

**Forma de cada evento**:
- `run_id` (sección 2.3), `process_type`, `label` (guardados en el evento, no solo referenciados -- un evento de hace un año se sigue pudiendo leer aunque ese tipo de proceso ya no exista en el catálogo actual).
- `timestamp`.
- `event_type`: `process_started`, `process_completed`, `process_error`, `process_stopped`, `process_paused`, `process_resumed`, `state_changed`, `alert_raised`, `alert_resolved`, `milestone`.
- `severity`: una de las 4 de la sección 2.2, siempre presente.
- `message`: descripción legible.
- `metadata`: datos adicionales libres según el tipo de evento.

**Quién escribe**: los propios procesos instrumentados (vía la misma librería de latido -- `heartbeat.start()` ya genera `process_started`, etc.) y la Supervisión Inteligente cuando detecta o resuelve una anomalía. Para procesos heredados, Mission Control sintetiza eventos básicos por inferencia, marcados como tal.

**Cómo se guarda, pensando en años de historia**: se recomienda **una base SQLite** (`atlas_live/mission_control/timeline.db`) en vez de un archivo que crece para siempre -- permite filtrar por Run ID, tipo de evento o severidad sin cargar todo en memoria, y reutiliza un patrón que el proyecto ya usa (Decision Journal y la Knowledge Base ya son bases SQLite embebidas). La política de retención queda configurable, sin fijar un número en este documento por falta de evidencia de que haga falta todavía.

---

## 5. Supervisión Inteligente (diseño, no implementación)

Capa que convierte los datos crudos del panel principal y el Timeline en observaciones, usando la severidad estandarizada de la sección 2.2 para que se puedan filtrar de inmediato.

**Arquitectura -- registro de detectores** (mismo patrón que los factores del Radar Explosivo y los canales de notificación): funciones independientes, cada una revisa el estado actual (y, si lo necesita, una ventana del Timeline) y devuelve cero o más observaciones. Agregar un detector nuevo es escribir una función y sumarla -- no toca los demás.

**Los 6 detectores pedidos**:

1. **Procesos detenidos** (CRITICAL): el PID del último latido ya no existe, pero el estado seguía en "Ejecutándose" -- nunca llegó a "Finalizado", "Error" ni "Cancelado" por su cuenta. Evidencia: chequeo de PID vía `psutil`.
2. **Rate limiting** (WARNING): el contador `recent_warnings` del latido, o coincidencias de `RateLimitError` en la ventana reciente del log, supera un umbral configurable. Misma evidencia que usé manualmente esta sesión.
3. **APIs lentas** (WARNING): requiere que el proceso mida y reporte cuánto tardó su última operación de red relevante (`heartbeat.step(..., last_fetch_duration_seconds=...)`, campo opcional). **Nota honesta**: ningún proceso mide esto hoy -- el detector queda diseñado pero solo se activa para procesos que reporten ese dato. No se puede inferir latencia de red desde afuera sin que el proceso la mida.
4. **Consumo excesivo de CPU o memoria** (WARNING): umbrales configurables **por tipo de proceso** -- un backtest usa más memoria que el scanner en vivo por diseño.
5. **Procesos sin heartbeat** (CRITICAL): `last_update` más viejo que un múltiplo configurable del intervalo esperado, con el PID todavía vivo. Distinto del detector 1 (ahí el proceso ya no existe; acá sigue vivo pero no informa).
6. **Caídas de Internet** (CRITICAL): único detector cross-proceso -- si varios procesos independientes reportan errores de red en la misma ventana de tiempo, es más confiable que un solo proceso fallando.

**Cierre de alertas**: automático para condiciones que se autorresuelven (ej. rate-limiting que ya paró) o manual para las que requieren revisión (ej. un proceso terminado en Error) -- configurable por detector.

**Por qué reglas simples y no aprendizaje automático, por ahora**: Principio 7 de la Constitución. La arquitectura de registro no impide reemplazar un detector por algo más sofisticado el día que haga falta -- sería cambiar una función por otra con la misma firma, sin rediseñar nada más.

---

## 6. Diseño pensando en los próximos años

- **Separación estado-actual vs. Timeline**: el estado actual no carga con la historia; solo el Timeline lo hace (SQLite, sección 4).
- **`psutil` en vez de comandos específicos de Windows**: portabilidad si Atlas migra de entorno.
- **`schema_version` en cada archivo de latido y cada evento del Timeline desde el día uno**: para que un cambio futuro al formato no rompa la lectura de datos históricos.
- **Run ID como clave de trazabilidad end-to-end** (sección 2.3): pensado para que, años de operación después, cualquier ejecución pasada se pueda reconstruir completa a partir de un solo identificador.
- **Catálogo de tipos de proceso, de solo agregar, nunca de quitar**: eventos viejos del Timeline siguen mostrando el nombre correcto aunque ese tipo ya no se use.
- **Soporta N procesos simultáneos del mismo tipo desde el diseño base** (un archivo de estado por Run ID, no por tipo).
- **Detectores de Supervisión Inteligente reemplazables sin tocar el resto**.

---

## 7. Catálogo de tipos de proceso

- `backtest_validation` -- `atlas_live.backtest.run_validation` (ya existe).
- `live_scanner` -- el ciclo de refresco cada 5 minutos dentro de `atlas_live.server` (ya existe como hilo interno).
- `paper_trading` -- no existe todavía (fase futura del roadmap).
- `ai_assistant` -- no existe todavía (fase 9 del roadmap).
- `news_scanner`, `api_health_check` -- reservados, no existen todavía.

---

## 8. Botones: Iniciar, Pausar, Reanudar, Detener

- **Iniciar**: lanza una instancia nueva (genera su Run ID en ese momento), con confirmación explícita.
- **Detener**: señal cooperativa por defecto -- el proceso la revisa entre unidades de trabajo, termina su unidad actual, pasa a "Cancelado". "Forzar terminación" es una acción separada, más claramente marcada como peligrosa, con confirmación adicional.
- **Pausar/Reanudar**: mismo mecanismo cooperativo -- transición a "Pausado" y de vuelta a "Ejecutándose", no suspensión de bajo nivel del sistema operativo.
- Estos 4 botones **solo funcionan en modo instrumentado**.

---

## Lo que NO incluye este diseño (deliberadamente)

- Aprendizaje automático real para la Supervisión Inteligente en esta primera versión.
- Suspensión/reanudación real a nivel de sistema operativo.
- Un proceso Mission Control independiente del servidor Atlas Live.
- Instrumentar retroactivamente procesos ya en curso, como la V2 actual.
- Notificaciones (push, correo, etc.) sobre eventos de Mission Control -- reutilizaría `NOTIFICATION_CHANNELS` de la fase 7, integración a diseñar aparte.

---

# PLAN DE IMPLEMENTACIÓN

Aprobado el diseño de arriba, sin agregar funcionalidades nuevas -- esto es únicamente cómo construirlo, en 9 entregables independientes. Cada uno deja algo **verificable de punta a punta** antes de empezar el siguiente; ninguno depende de tocar la validación V2 en curso, y el Entregable 9 (el único que se acerca a un proceso real) se prueba con una corrida corta de prueba, nunca con V2.

"Tiempo estimado" está en sesiones de trabajo (quien implementa soy yo, no un equipo humano) -- **corta** ≈ 30-60 min, **media** ≈ 1-2h, **larga** ≈ 2-4h, incluyendo pruebas.

## Entregable 1 -- Librería de latido (heartbeat)

- **Objetivo**: que cualquier script Python pueda reportar su propio estado (los 7 estados estandarizados), Run ID, progreso y CPU/memoria (vía `psutil`) a un archivo de estado.
- **Archivos**: `atlas_live/mission_control/__init__.py` (nuevo), `atlas_live/mission_control/heartbeat.py` (nuevo).
- **Tiempo estimado**: media.
- **Riesgo**: Bajo -- código nuevo, aislado, no toca nada existente.
- **Dependencias**: ninguna.
- **Criterio de terminado**: un script de prueba de pocas líneas, importando la librería, genera un archivo de estado JSON válido (con Run ID con el formato correcto) que se actualiza en cada paso -- verificable leyendo el archivo directamente, sin ninguna interfaz todavía.
- **Estado (2026-08-02)**: ✅ Completado y **aprobado de forma definitiva**. Revisión de calidad de 5 puntos realizada (API pública, esquema JSON, dependencias, escritura atómica, desviaciones); única desviación real (Run ID sin validar contra el formato oficial) corregida y re-validada antes del cierre.

## Entregable 2 -- Timeline (SQLite) + integración con el latido

- **Objetivo**: cada evento de ciclo de vida (inicio, fin, error, pausa, cancelación) se registra permanentemente; el latido del Entregable 1 empieza a escribir automáticamente al Timeline.
- **Archivos**: `atlas_live/mission_control/timeline.py` (nuevo -- creación de la base y funciones de lectura/escritura), modificación de `heartbeat.py`.
- **Tiempo estimado**: media.
- **Riesgo**: Bajo.
- **Dependencias**: Entregable 1.
- **Criterio de terminado**: correr el mismo script de prueba y poder consultar, con una función simple (todavía sin interfaz), el historial completo de esa ejecución -- cada evento con su severidad correcta según la tabla de mapeo ya definida.
- **Estado (2026-08-02)**: Implementado y probado. **Pendiente de aprobación final del usuario.**
  - Archivos: `timeline.py` (nuevo -- base SQLite append-only, `record_event`, `get_events_for_run`, `get_recent_events` con filtro por severidad mínima), `heartbeat.py` (modificado -- `_event_type_for_transition()` decide si una escritura genera evento; se agregó `milestone()`).
  - Pruebas: secuencia completa de transiciones (6 eventos, sin duplicados en los `step()` repetidos dentro del mismo estado), camino de error, camino de cancelación, aislamiento de eventos por `run_id`, filtro `min_severity`, inspección directa de `timeline.db`. Todos los artefactos de prueba (`status/*.json`, filas de `timeline.db`) fueron eliminados tras validar.
  - Desviaciones menores detectadas, ninguna crítica: (a) `heartbeat.py` ahora depende de `timeline.py` y `loguru` -- cambia la respuesta "aislado, solo stdlib+psutil" dada en el cierre del Entregable 1, pero es exactamente la integración que este entregable estaba autorizado a hacer; (b) la severidad del evento de Timeline es la que el propio proceso reporta en la llamada, no una recalculada contra la tabla de mapeo evento→severidad por defecto del diseño; (c) cada evento de ciclo de vida generado automáticamente incluye `metadata={"done","total","unit"}`, no especificado explícitamente en el diseño original.

## Entregable 3 -- Modo heredado (inferencia externa)

- **Objetivo**: poder "ver" el estado aproximado de un proceso que no usa el latido -- como la validación V2 -- sin tocarlo ni instrumentarlo.
- **Archivos**: `atlas_live/mission_control/legacy_inspector.py` (nuevo).
- **Tiempo estimado**: media.
- **Riesgo**: Bajo -- es solo lectura externa (PID, timestamps de archivos, tail de log), el mismo tipo de comandos que ya usé manualmente esta sesión, ahora automatizados.
- **Dependencias**: ninguna (independiente del latido).
- **Criterio de terminado**: apuntar esta lógica a la validación V2 real (o a lo que quede de ella si ya terminó) en modo **solo lectura**, y obtener el mismo diagnóstico que hice a mano (días completados, PID, CPU, última línea de log) como datos estructurados, sin interfaz todavía.

## Entregable 4 -- API backend de Mission Control

- **Objetivo**: exponer instrumentados + heredados + Timeline como endpoints JSON del servidor Atlas Live existente.
- **Archivos**: `atlas_live/mission_control/registry.py` (nuevo -- catálogo de tipos de proceso), nuevas rutas en `atlas_live/server.py` (aditivo).
- **Tiempo estimado**: media.
- **Riesgo**: Bajo-Medio -- toca `server.py`, un archivo en uso, aunque de forma puramente aditiva (nuevas rutas, cero cambios a las existentes).
- **Dependencias**: Entregables 1, 2, 3.
- **Criterio de terminado**: pedir `/api/mission-control/processes` (o la ruta que se defina) desde el navegador o con `curl` y recibir un JSON válido con el estado real de la validación V2 (heredado) y de cualquier script de prueba instrumentado, al mismo tiempo.

## Entregable 5 -- Panel principal (frontend, solo lectura)

- **Objetivo**: nueva sección "Mission Control" en el dashboard existente, mostrando lo que ya expone el Entregable 4 -- sin botones de control todavía.
- **Archivos**: `atlas_live/static/index.html` (nueva sección + ítem de menú), `atlas_live/static/mission_control.js` (nuevo), `atlas_live/static/style.css` (estilos nuevos).
- **Tiempo estimado**: media.
- **Riesgo**: Bajo -- mismo patrón ya usado para Diagnóstico y Alertas.
- **Dependencias**: Entregable 4.
- **Criterio de terminado**: abrir el dashboard, ir a Mission Control, y ver ahí la validación V2 real (progreso, tiempo estimado, CPU/memoria aproximados) -- utilizable de punta a punta, sin necesitar la terminal.

## Entregable 6 -- Vista de Timeline (frontend)

- **Objetivo**: pantalla con el historial cronológico, filtrable por severidad.
- **Archivos**: extensión de `mission_control.js`, nueva sub-sección en `index.html`.
- **Tiempo estimado**: corta.
- **Riesgo**: Bajo.
- **Dependencias**: Entregable 5 (reutiliza la infraestructura de esa sección).
- **Criterio de terminado**: ver el historial completo de una ejecución de prueba, y poder filtrar para mostrar solo WARNING/ERROR/CRITICAL.

## Entregable 7 -- Supervisión Inteligente (los 6 detectores)

- **Objetivo**: detección automática de anomalías, alimentando "Alertas activas" del panel y el Timeline, sin intervención manual.
- **Archivos**: `atlas_live/mission_control/detectors.py` (nuevo -- registro de detectores), integración en la ruta del Entregable 4.
- **Tiempo estimado**: larga -- son 6 detectores distintos, cada uno con su propia lógica y su propio caso de prueba.
- **Riesgo**: Medio -- umbrales mal calibrados generan falsos positivos; se prueba primero con condiciones simuladas (ej. un script de prueba al que se le corta el latido a propósito) antes de confiar en él sobre un proceso real.
- **Dependencias**: Entregables 4, 5.
- **Criterio de terminado**: provocar deliberadamente cada una de las 6 condiciones (con procesos de prueba, no reales) y ver la alerta correspondiente aparecer en el panel y quedar registrada en el Timeline, sin intervención manual. El detector de "APIs lentas" se da por completo cuando queda **diseñado y con su prueba de que no dispara falsos positivos** -- no hay ningún proceso real que reporte latencia todavía, así que no puede probarse con un caso real hasta el Entregable 9 o después.

## Entregable 8 -- Botones de control (Iniciar / Pausar / Reanudar / Detener)

- **Objetivo**: controlar procesos instrumentados desde la interfaz, con el protocolo cooperativo ya diseñado (nunca matar/suspender a nivel de sistema operativo por defecto).
- **Archivos**: extensión de `heartbeat.py` (lectura de señales de control), nuevas rutas de control en el backend, botones en el frontend.
- **Tiempo estimado**: larga.
- **Riesgo**: **Alto** -- es la única pieza que ejecuta acciones sobre procesos reales. Se prueba extensivamente con scripts de prueba desechables antes de siquiera considerar usarla sobre un proceso real de Atlas.
- **Dependencias**: Entregables 1, 4, 5.
- **Criterio de terminado**: lanzar un script de prueba instrumentado desde la interfaz, pausarlo, reanudarlo y detenerlo de forma prolija (termina su unidad de trabajo actual antes de salir, pasa a "Cancelado", no deja ningún archivo a medio escribir) -- todo verificado con procesos de prueba, nunca con la validación V2 real.

## Entregable 9 -- Instrumentar un proceso real de Atlas (sin tocar V2)

- **Objetivo**: probar el sistema completo con un proceso real, no un script de prueba -- agregar el latido a `run_validation.py`/`historical_scan.py` para que la **próxima** corrida (no la V2 actual, que sigue sin tocarse) lo use.
- **Archivos**: modificación de `atlas_live/backtest/run_validation.py` y `atlas_live/backtest/historical_scan.py` (agregar llamadas al latido en los puntos ya identificados en esos scripts: inicio, cada día procesado, fin).
- **Tiempo estimado**: media.
- **Riesgo**: Medio -- modifica scripts reales usados en producción, aunque de forma aditiva; se prueba primero con una corrida corta (2-3 días) antes de confiar en la instrumentación para una corrida de 30 días.
- **Dependencias**: Entregables 1 a 8.
- **Criterio de terminado**: una validación de prueba corta corre con instrumentación completa, visible en tiempo real en Mission Control con progreso exacto (no inferido), controlable con los botones del Entregable 8, y con su historial completo en el Timeline al terminar.

---

**Orden de ejecución**: estrictamente 1→9, sin saltos, cada uno aprobado antes de empezar el siguiente -- mismo criterio "un objetivo a la vez" del resto del proyecto. Los Entregables 1, 2 y 3 no dependen entre sí y podrían reordenarse sin problema; se mantienen en ese orden porque 1→2 es la cadena más natural (el latido primero, después lo que ya escribe automáticamente al Timeline).

---

*Diseño y plan de implementación aprobados. Nada de esto está implementado todavía. La validación V2 sigue corriendo sin ninguna modificación ni instrumentación.*
