# Análisis de precursores y propuesta de ALERTA TEMPRANA

**2026-08-17.** Análisis real sobre `GET /api/admin/precursor-report`
(commit `cec8baa`), sobre la Base Histórica completa: 162.105 observaciones,
5.374 símbolos. Responde la pregunta operativa real: no "qué días ya se
movieron" (eso lo cubrió el primer informe), sino "qué características
tenía el mismo símbolo en los días `T-1..T-5` ANTES de que empezara un
movimiento fuerte".

## Corrección honesta sobre mi propuesta anterior

Propuse mapear directamente `timing_deteccion` → PREPARACIÓN/ALERTA_TEMPRANA/
INICIO/CONFIRMACIÓN/TARDÍO. **La evidencia no sostiene ese mapeo tal cual.**
La distribución de `timing_deteccion` casi no cambia entre `T-5` y `T-1`
antes de un onset real (para +20%: `antes_del_movimiento` es 49.1% en T-5 y
46.5% en T-1 -- prácticamente igual). La etiqueta `timing_deteccion` se basa
en el cambio de precio DE ESE DÍA, que por definición todavía no se movió
mucho en los días previos al inicio -- por eso no transiciona limpiamente
día a día. **La señal real que sí transiciona con evidencia clara es el
volumen relativo (`relative_volume`)**, no la etiqueta de timing. Corrijo
la propuesta más abajo con esto.

## Hallazgo central: el volumen relativo sube ANTES del precio, y cuánto antes depende de la magnitud del movimiento

| Umbral | n episodios | `relative_volume` T-5 | T-4 | T-3 | T-2 | T-1 | Baseline mercado |
|---|---:|---:|---:|---:|---:|---:|---:|
| +20% | 6.378 | 1.51 | 3.36 | 3.88 | 2.38 | **7.26** | 1.73 |
| +50% | 1.557 | **6.61** | 6.10 | 2.46 | 3.54 | **8.52** | 1.73 |
| +100% | 390 | **6.14** | 5.27 | 4.37 | **16.52** | 15.88 | 1.73 |

- **Para movimientos de +20%**, el volumen relativo está esencialmente en
  el promedio del mercado hasta `T-2`, y recién se dispara (4x el
  promedio) en `T-1` -- la señal aparece con **1 día** de anticipación.
- **Para +50%**, el volumen ya está claramente elevado (3.8x) desde `T-5`
  -- la señal aparece con **hasta 5 días** de anticipación.
- **Para +100%**, el volumen ya está 3.5x el promedio en `T-5`, y se
  dispara a 9x el promedio entre `T-3` y `T-2` -- la señal más fuerte de
  todas, visible con **varios días** de anticipación.

**Conclusión real, no inventada: cuanto más grande termina siendo el
movimiento, más temprano y más fuerte aparece la anomalía de volumen
relativo antes de que el precio se mueva.** `volatility_14d_pct` y
`daily_range_pct` (usadas en el primer informe) son planas en toda la
ventana `T-1..T-5` -- describen un "régimen" del símbolo (ya viene volátil
hace tiempo), no el momento exacto de entrada. `gap_pct` y
`change_pct_delta` (aceleración día a día) también saltan recién en `T-1`
para +20% (`gap_pct` 0.48 vs baseline 0.05; `change_pct_delta` +1.15 vs
negativo en T-2..T-4) -- consistentes con "recién ahí empieza a notarse en
el precio".

## Qué pasa DESPUÉS de un onset de +20% (evidencia de falsos positivos)

De 6.378 episodios que arrancan con +20%:

| Resultado | n | % |
|---|---:|---:|
| También llega a +50% | 536 | 8.4% |
| También llega a +100% | 136 | 2.1% |
| **Se queda solo en +20-49%** | 5.842 | **91.6%** |

Los que se quedan cortos retroceden en promedio **-14.2%** desde el pico.
**El +20% inicial es común; que continúe es la excepción.** Esto es central
para la propuesta: una alerta temprana que solo mira "¿va a llegar a
+20%?" va a acertar seguido pero en la mayoría de los casos el movimiento
no vale la pena perseguir más allá de una entrada rápida y una salida
disciplinada.

## Racional disponible vs no disponible (T-1, umbral +20%)

| | n episodios T-1 | `relative_volume` T-1 | `volatility_14d_pct` T-1 | % de los onsets que llegan a +100% |
|---|---:|---:|---:|---:|
| Racional disponible | 1.131 | 1.23 (≈ baseline) | 5.92 (≈ baseline) | 0.9% |
| Racional NO disponible | 3.316 | **9.31** | 9.21 | **2.6%** |

**Hallazgo importante y honesto**: la señal de volumen relativo más fuerte
(la que mejor anticipa movimientos grandes) se concentra en su mayoría en
símbolos que **hoy no están disponibles en Racional** -- probablemente
small/micro-caps de baja liquidez, coherente con que Racional tiende a
ofrecer nombres más grandes/líquidos. Dentro del universo SÍ operable en
Racional, la señal de volumen antes del onset es mucho más débil (apenas
por encima del promedio del mercado) y la probabilidad de llegar a +100%
es casi 3 veces menor. Esto no invalida el patrón -- 1.616 onsets de +20%
sí ocurrieron dentro de Racional -- pero hay que ser honesto: **la mejor
versión de esta señal es menos útil operativamente de lo que parece a
primera vista**, porque una parte grande de su fuerza vive fuera de lo que
el usuario puede operar hoy.

## Límites de este análisis (declarados, no ocultados)

- Datos DIARIOS -- habla en días de trading antes del inicio (`T-1..T-5`),
  nunca en minutos. Un análisis a nivel de minutos necesitaría datos
  intradía, que Tradier solo cubre ~8-14 días hacia atrás (limitación ya
  documentada en el proyecto).
- `volume_change_pct` y `change_pct_delta` no tienen baseline de mercado
  calculado (`n=0` en `baseline_universo_completo`) -- son derivadas que
  solo se calcularon para las filas cercanas a un onset, no para las
  162.105 filas completas. Se pueden comparar entre `T-1..T-5` entre sí,
  pero no contra un promedio general todavía.
- No se comparó directamente "features de los que llegan a +100%" vs
  "features de los que se quedan en +20-49%" -- el hallazgo de Racional
  disponible/no disponible es la comparación más cercana que sí se hizo.
  Sería el siguiente paso lógico si se quiere afinar más la señal.
- `relative_volume` promedio puede estar afectado por valores extremos
  (colas largas) -- los promedios de 15-16x en T-1/T-2 para +100% probablemente
  reflejan algunos casos muy extremos, no que la mayoría de los símbolos
  tengan exactamente ese volumen. No se calculó la mediana en este pase.

## Propuesta revisada de ALERTA TEMPRANA (basada en lo que la evidencia sí muestra)

Reemplaza el mapeo 1:1 por `timing_deteccion` de la propuesta anterior por
una lectura basada en la **trayectoria de `relative_volume`**, que es la
que sí transiciona con evidencia real:

- **PREPARACIÓN**: `volatility_14d_pct`/`daily_range_pct` ya elevados
  respecto al propio historial del símbolo (régimen inestable), pero
  `relative_volume` todavía cerca del promedio de mercado (~1-2x). No hay
  evidencia de que esto solo, sin más, anticipe nada con precisión --
  es contexto, no gatillo.
- **ALERTA_TEMPRANA**: `relative_volume` empieza a subir claramente por
  encima del promedio (3-6x) sin que el precio todavía se haya movido
  mucho (`timing_deteccion` sigue en `antes_del_movimiento`/`expansion_temprana`).
  Esta es la ventana de mayor valor operativo -- evidencia real: para
  movimientos de +50%, esta condición ya está presente 5 días antes
  (n=894, `relative_volume` T-5=6.61); para +100%, incluso más marcada
  (n=215, T-5=6.14, subiendo a 16.5x hacia T-2).
- **INICIO**: `relative_volume` se dispara (7x+), `gap_pct`/`change_pct_delta`
  se vuelven positivos, `timing_deteccion` pasa a `al_comienzo` -- esto ya
  estaba validado en el primer informe (ALCISTA n=3.052, 47.1% a +20%;
  BAJISTA n=1.378, 47.7%).
- **CONFIRMACIÓN**: `timing_deteccion == "recorrido_significativo_ya_hecho"`
  -- movimiento ya en curso, sigue acelerando (primer informe).
- **TARDÍO**: `timing_deteccion in ("demasiado_tarde", "agotamiento")` --
  no perseguir (primer informe).

**Diferencia clave con la propuesta anterior**: PREPARACIÓN y
ALERTA_TEMPRANA ya NO dependen de `timing_deteccion` (que no discrimina
bien en esa ventana) -- dependen de la trayectoria de `relative_volume`
frente a su propio baseline. Esto es exactamente lo que el objetivo
operativo pedía: una señal que aparece ANTES de que `timing_deteccion`
detecte nada en el precio.

## Siguiente paso técnico (no hecho todavía, para que quede explícito)

Para que "ALERTA_TEMPRANA" sea una regla ejecutable (no solo una
observación), falta: (1) calcular una mediana además del promedio (por los
valores extremos ya señalados), (2) comparar directamente features de
onsets que SÍ continúan a +50/+100% contra los que se quedan en +20-49%,
para afinar el umbral exacto de `relative_volume` que separa una alerta
temprana útil de ruido. Nada de esto se conecta a
`candidate_gates.py`/score/`DecisionEngine` sin autorización aparte.
