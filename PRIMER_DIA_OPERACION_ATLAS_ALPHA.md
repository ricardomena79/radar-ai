# Primer Día de Operación de Atlas Alpha

Guía operativa para el usuario humano durante la primera prueba en tiempo
real de Atlas Alpha 1.0 (Cabina del Piloto conectada a datos reales,
Paneles 1-6 y 9-12; Alertas y Atlas Opina siguen en MOCK -- ver nota al
final). Este documento no cambia ni un archivo de código: es la
referencia de qué mirar, qué anotar y qué decidir mientras Atlas corre.

Todos los horarios son America/New_York (huso horario de mercado, mismo
que usa `market_hours.py`).

---

## 1. Checklist de inicio (antes del premarket)

Hacer esto **antes de las 04:00 ET** (inicio real del premarket, según
`market_hours.PREMARKET_START`):

- [ ] Confirmar que no hay otra instancia de `atlas_live/server.py` corriendo ya en el puerto 5000 (Atlas no es un servicio 24/7 -- si quedó un proceso viejo abierto, hay que cerrarlo primero).
- [ ] Arrancar el servidor real: `python -m atlas_live.server`.
- [ ] Confirmar en la consola que el hilo de fondo arrancó (`scan_worker.start_background_refresh()`) sin errores.
- [ ] Abrir la Cabina del Piloto en `http://localhost:5000` y confirmar que carga (no queda en blanco, no hay errores de consola del navegador).
- [ ] Confirmar en la barra superior que el reloj/sesión/cuenta regresiva muestran la hora de Nueva York correcta y "Premarket" como sesión activa.
- [ ] Abrir el panel **Memory Engine** y confirmar:
  - `observation_count` = 73.123 (o el número vigente si se corrió un backfill nuevo -- nunca debería ser 0).
  - `days_backed` = 30.
  - `last_recalibrated_on` = la fecha de mercado de hoy (se recalibra una vez por día nuevo, automáticamente).
- [ ] Verificar, de forma solo lectura, que la validación histórica V2 (`atlas_live/backtest/results_v2/`) sigue en su conteo esperado -- **no tocarla, no reiniciarla, no interrumpirla** bajo ninguna circunstancia durante la sesión de hoy.
- [ ] Confirmar que **Prediction Journal** muestra "todavía no se selló el ranking de hoy" (estado esperado antes de las 09:25 ET -- si ya muestra un sellado, algo corrió antes de tiempo y hay que investigar).
- [ ] Dejar el servidor corriendo sin cerrar la pestaña/terminal durante todo el horario que se quiera cubrir (Modo Interactivo Continuo: Atlas solo escanea, actualiza el ranking y recalibra mientras este proceso está abierto).

---

## 2. Qué paneles observar primero

En este orden, apenas arranca el premarket:

1. **Barra superior** -- confirma sesión (`Premarket`) y cuenta regresiva hasta la apertura (09:30 ET). Es el ancla de todo lo demás.
2. **Memory Engine** -- confirma que la evidencia de hoy es la vigente (ver checklist de inicio). Se revisa una vez al empezar, no hace falta seguir mirándolo durante el día (solo cambia una vez por día de mercado).
3. **Oportunidad del Día (Hero)** -- el candidato #1 del ranking dinámico en vivo. Antes de las 09:25 ET, este panel es **informativo y puede cambiar varias veces** a medida que llegan más datos de premarket -- no es todavía una predicción oficial.
4. **Radar Completo** -- para confirmar que el escaneo está trayendo un número razonable de símbolos (sanity check de que Yahoo Finance está respondiendo, no que haya 0 o un número sospechosamente bajo).
5. **Explosivas / Momentum / No tocar** -- pantallas rápidas de qué está eligible y en qué categoría, útil para tener contexto antes del momento clave.
6. **El momento más importante del premarket: la ventana 09:25-09:30 ET.** En algún ciclo de escaneo dentro de esa ventana (cada 5 minutos), Atlas sella el ranking oficial del día **una sola vez**. Confirmarlo en **Prediction Journal**: `sealed_today` debe pasar de `null` a tener `sealed_at`, `candidate_count` y `top_symbol`. A partir de ese momento, esa es LA predicción oficial de hoy -- inmutable, no se puede volver a sellar.

---

## 3. Qué métricas registrar durante el día

Anotar manualmente (no hay todavía un cuaderno de campo automático más allá de lo que el sistema ya persiste):

- **Al sellar (≈09:25-09:30 ET)**: símbolo top, `probability_pct`, `confidence`, `semaforo`, `evidence_condition`, `evidence_sample_size`, `evidence_wilson_lower_bound_pct` -- todo esto ya queda guardado por el sistema en `sealed_predictions`, pero conviene anotarlo aparte para tenerlo a mano sin depender de la base de datos.
- **Durante la sesión regular (09:30-16:00 ET)**: cómo se mueve el precio real del símbolo sellado, comparado con lo que uno esperaría dado el `semaforo` -- esto es observación humana, Atlas no lo resume todavía (ver limitación de Alertas/Atlas Opina, nota final).
- **Cualquier error o excepción visible en la consola del servidor.** El diseño de `live_integration.py` está pensado para que un error ahí nunca tumbe el escaneo principal (queda en el campo `error` del resumen del ciclo, no interrumpe nada) -- pero si aparece un error repetido, vale la pena anotarlo igual.
- **Duración real de cada escaneo.** En la primera prueba con datos reales de esta fase se midió ~260 segundos (~4.3 min) para 203 símbolos -- es la latencia esperada, no un error, mientras no crezca mucho más que eso.
- **Al cierre/afterhours**: el resultado real calificado (ver sección 5) y el `anticipation_minutes` que calcula el sistema automáticamente.
- **Exit Journal**, una vez cerrado: `peak_return_pct`, `final_return_pct`, `sample_count` del símbolo sellado -- da una idea de si el pico se sostuvo o fue momentáneo (sin que el sistema decida nada por eso, ver limitación explícita del Exit Journal más abajo).

---

## 4. Qué decisiones tomar si Atlas cambia la Oportunidad del Día

Punto crítico para no confundirse: el panel "Oportunidad del Día" (Hero) sigue actualizándose **todo el día**, incluso después del sellado -- porque el Radar Explosivo sigue escaneando cada 5 minutos durante toda la sesión (Modo Interactivo Continuo), no se detiene al sellar.

- **Antes de las 09:25 ET (premarket, sin sellar todavía)**: que el candidato top cambie es normal y esperado -- es información acumulándose, no una decisión que tomar. No hay nada que "hacer" todavía.
- **Después del sellado (09:25-09:30 ET en adelante)**: el ranking oficial de HOY ya quedó fijo en `Prediction Journal` -- **inmutable, no se vuelve a sellar**. Si el Hero muestra un símbolo distinto más tarde en el día, **eso NO es un cambio de la predicción oficial** -- es el Radar Explosivo señalando una oportunidad nueva y distinta que apareció después. Tratarla como candidata para la evidencia de mañana, no como una corrección de lo ya sellado.
- **Atlas nunca ejecuta ninguna orden ni da una instrucción de compra/venta.** Ni el Ranking Score ni el Exit Journal son un algoritmo de salida ni de entrada -- son memoria y evidencia. Cualquier decisión de operar es enteramente del usuario humano, fuera de este sistema.
- Si el símbolo sellado deja de aparecer en el Radar Completo durante el día (por ejemplo, se vuelve no elegible), **no se recalcula ni se re-sella nada** -- el Exit Journal simplemente sigue registrando su trayectoria (o deja de tener muestras nuevas si el símbolo ya no aparece en absoluto en `results`).

---

## 5. Cómo evaluar si una predicción fue correcta o incorrecta

La calificación real la hace el sistema automáticamente, pero recién **en afterhours o al cierre** (`_grade_pending()`, disparado cuando la sesión pasa a `afterhours`/`closed` y hay un sellado del día sin calificar) -- **antes de eso, no se puede evaluar como correcta o incorrecta, solo como "predicha".**

Criterio ya implementado (mismo Clasificador de todo el proyecto, sin umbrales nuevos):

| `result_category` calificada | Umbral objetivo |
|---|---|
| `EXPLOSION` | cambio real ≥ 10% |
| `FALSE_BREAKOUT` | elegible en el sellado y cambio real < 5% |
| `LOSER` | cambio real ≤ -5% |
| `WEAK` | \|cambio real\| < 2% |
| `NORMAL` | el resto |

Cómo leerlo junto con lo que Atlas predijo al sellar:

- **Semáforo 🟢 (lift ≥ 10x el baseline poblacional) + resultado `EXPLOSION`** → acierto fuerte, la evidencia histórica se confirmó.
- **Semáforo 🟡 (matchea una condición confiable, pero con menor lift) + resultado `EXPLOSION` o al menos `NORMAL`/`WEAK`** → acierto parcial o esperado -- 🟡 nunca prometió un lift tan alto como 🟢.
- **Semáforo 🔴 (ninguna condición confiable matcheó)** → Atlas explícitamente no tenía base para una probabilidad elevada -- cualquier resultado que ocurra es información nueva, no un "acierto" ni un "error" de una predicción que nunca se hizo con confianza.
- **`anticipation_minutes`** (tiempo entre la primera detección en un snapshot dinámico y el movimiento confirmado) es la métrica secundaria: una categoría correcta pero con anticipación muy corta es menos útil operativamente que una correcta y temprana -- vale la pena mirarla, no solo el acierto binario.

---

## 6. Qué información guardar para mejorar Atlas al cierre

Lo que el sistema **ya guarda solo**, de forma durable (SQLite, sin intervención manual):

- `Prediction Journal`: todos los snapshots dinámicos del día, el sellado oficial, y (después de la calificación) el resultado real + tiempo de anticipación.
- `Exit Journal`: la trayectoria cruda completa del símbolo sellado durante la sesión regular, y el resumen objetivo al cierre (pico, rendimiento final, duración).

**Limitación real a tener en cuenta, verificada en el código de esta sesión (no es una suposición):** `live_integration.py` alimenta el Prediction Journal y el Exit Journal, pero **no** escribe de vuelta en el Memory Store (`store.record_observation`). Es decir, la evidencia que usan `base_rates.py`/`calibration_advisor.py` para calcular tasas base y condiciones confiables **sigue fija en los mismos 30 días históricos (2026-06-18 a 2026-07-31)** -- el resultado de hoy no se suma automáticamente a esa población, por más días reales que pasen. `MEMORY_ENGINE.md` (Entregable 7) describe la intención de que "la memoria crece hacia adelante en cada premarket real"; en la práctica esa frase aplica al Prediction Journal y al Exit Journal, no todavía a la base de tasas base del Memory Engine. Vale la pena registrar esto como un hallazgo a resolver en una futura propuesta formal (no implementar nada esta noche, según lo ya acordado).

Qué conviene guardar manualmente al cierre, hoy por hoy:

- Captura o anotación del estado final de Prediction Journal y Exit Journal del día (por si se quiere comparar entre varios días antes de que exista un reporte agregado).
- Cualquier discrepancia notada entre lo que decía el `semaforo`/`probability_pct` y lo que realmente pasó -- son justamente los casos que más valor tendrían si algún día se decide incorporar el día de hoy al Memory Store (vía un backfill nuevo, manual, sobre un archivo de día generado por `historical_scan.py`, el mismo mecanismo ya usado para los 30 días existentes).
- Observaciones sobre Alertas/Atlas Opina -- siguen en MOCK; cualquier necesidad real que se note hoy ("me hubiera servido una alerta cuando pasó X") es información directa para diseñar el Event Engine y el Insight Engine, que están planeados para después de este primer período de pruebas.

---

## 7. Checklist de cierre del mercado

Después de las 16:00 ET (fin de sesión regular) y, sobre todo, tras la ventana de afterhours (hasta las 20:00 ET) donde corre la calificación automática:

- [ ] Confirmar en **Prediction Journal** que el sellado de hoy aparece con `graded_at` no nulo (ya calificado) -- si sigue sin calificar, revisar la consola del servidor por errores de cotización (`_grade_pending` se salta símbolos sin cotización disponible, sin inventar un resultado).
- [ ] Anotar el resultado real (sección 5) contra lo predicho al sellar.
- [ ] Confirmar en **Exit Journal** que el resumen del símbolo sellado quedó cerrado (`window_closed_at` con valor) -- se cierra en el mismo momento que se califica la predicción.
- [ ] Revisar la consola del servidor por cualquier error acumulado durante el día (no debería haber ninguno que haya interrumpido el escaneo, por diseño -- pero vale la pena confirmarlo).
- [ ] Verificar, de nuevo de forma solo lectura, que la validación histórica V2 sigue intacta y sin tocar.
- [ ] Cerrar el servidor (Ctrl+C) y confirmar en consola que el cierre prolijo terminó ("Modo Interactivo Continuo" -- el hilo de fondo recibe la señal de parada y termina su ciclo actual antes de salir; el estado ya escrito en SQLite queda a salvo de cualquier forma).
- [ ] Guardar las anotaciones manuales de las secciones 3, 5 y 6 en algún lugar persistente (fuera de este documento, que es una guía reutilizable, no un cuaderno de bitácora de un día en particular).

---

## Nota sobre alcance: qué NO está conectado todavía

- **Alertas** y **Atlas Opina** siguen en modo MOCK (dato simulado, no real) -- decisión explícita para esta primera prueba. Los motores reales (Event Engine para Alertas, Insight Engine para Atlas Opina) se diseñarán recién después de este primer período de pruebas en tiempo real, no antes.
- **Mission Control**: el panel muestra únicamente procesos que hayan sido instrumentados con el latido (`heartbeat.py`) y tengan un archivo de estado activo o reciente en `atlas_live/mission_control/status/`. Hoy, ningún proceso de producción (ni `scan_worker`, ni la validación V2) está instrumentado todavía -- es esperable que el panel aparezca vacío ("sin procesos activos"), eso no es un error, es el estado real y honesto del sistema.
