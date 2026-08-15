# Experimentos A, B, C — informe metodológico (2026-08-16)

**Estado: evidencia histórica (backtest). Nada de esto se implementó en
`candidate_gates.py`, en el score ni en `DecisionEngine`.** Este documento
explica, en lenguaje normal, de dónde sale cada número de
`experiments_abc_resultado.json`, cómo reproducirlo, y qué se puede y no se
puede concluir todavía. Ver también `PROPUESTA_MADUREZ_APRENDIZAJE.md` para
la arquitectura de Madurez que separa este backtest del aprendizaje en vivo.

---

## 0. Qué problema intenta resolver cada experimento

Atlas detecta candidatas con 7 puertas (`candidate_gates.py`) y clasifica su
`timing_deteccion` en 6 categorías (`phase_classifier.py`). El estudio
histórico de ~3 meses encontró que algunas variables ya disponibles
diferencian, con evidencia real, cuáles detecciones tienden a llegar más
lejos (+20%/+50%/+100%) de cuáles no. Antes de tocar ninguna regla de
detección, se decidió probar esas hipótesis con la disciplina explícita:

```
HALLAZGO HISTÓRICO → HIPÓTESIS → REGLA EXPERIMENTAL → VALIDACIÓN FUERA DE
MUESTRA → COMPARACIÓN CONTRA EL ALGORITMO ACTUAL → SOLO ENTONCES POSIBLE
INTEGRACIÓN
```

Los tres experimentos están en el paso "validación fuera de muestra /
comparación contra el baseline" — todavía NO en "integración".

- **Experimento A** — ¿la volatilidad reciente del símbolo (14 días
  previos) ayuda a distinguir qué candidatas van a continuar más lejos?
- **Experimento B** — ¿detectar en fase "temprana genuina" (recién
  arrancando) realmente da mejor resultado que detectar "tarde", como el
  diseño de `phase_classifier.py` asume? (Esto NO propone una regla nueva
  -- es una validación continua de una clasificación que ya existe.)
- **Experimento C** — ¿el rango de precio del propio día (today's high-low)
  aporta información adicional a la de A, o mide básicamente lo mismo?

---

## 1. De dónde salen los datos

Todo sale de `historical_reference.db` (`atlas_live/reference/`), la Base
Histórica de Referencia construida en la fase anterior de este proyecto:

- **Universo**: 2.575 símbolos del universo Racional, **2.575/2.575
  procesados** (batch completo, sin muestreo parcial).
- **Período real**: ~3 meses de barras diarias por símbolo, vía Tradier.
- **Filas usadas en los experimentos**: `daily_features` (lo que se sabía
  HASTA ese día -- change%, gap%, RVOL, volatilidad de 14 días, rango del
  día, dirección) UNIDAS con `daily_outcome` (lo que pasó DESPUÉS de ese
  día -- avance máximo, si llegó a +20/+50/+100%) por símbolo+fecha.
  **78.826 filas** cumplen ambas condiciones a la vez -- este es el
  denominador real de todo el análisis, no 73.123 (esa era una cifra del
  Memory Store viejo, ya reiniciado y sin relación con este estudio) ni
  128.211 (el conteo crudo de `daily_outcome` sin exigir que también exista
  `daily_features` para esa fecha -- ver nota más abajo).
- **Fechas distintas**: 32 (el rango real donde existen tanto
  features como resultado; los extremos de cada símbolo se recortan por
  diseño -- ver sección 4).
- **Símbolos distintos representados**: 2.467 de 2.575 (108 quedaron sin
  suficiente historial o dieron error real de Tradier, documentado en su
  checkpoint).

### Cómo reproducirlo
```
python scripts/run_experiments_abc.py
```
Lee `historical_reference.db` (solo lectura), corre los 3 experimentos y
la Hipótesis B, y escribe `experiments_abc_resultado.json` (no se
commitea -- es un output regenerable, no código fuente).

---

## 2. Qué significan las columnas/números que vas a ver

| Campo | Qué es |
|---|---|
| `direction` | Dirección del símbolo EN EL DÍA de la detección (ALCISTA/BAJISTA/NEUTRAL) -- `daily_reference.classify_direction()`, banda neutral ±1%. |
| `max_advance_pct` | El avance máximo que tuvo el precio DESPUÉS de ese día (mínimo 10 días de mercado reales posteriores, nunca menos). |
| `+20% / +50% / +100%` | Si `max_advance_pct` llegó a esos umbrales. |
| `n` | Cuántas filas reales entran en ese grupo -- siempre mostrado, nunca un % sin su muestra. |
| `poblacion_total` | Todas las filas de esa dirección, SIN segmentar por la señal del experimento -- es el baseline (sección 3). |
| `alto` / `medio` / `bajo` | Terciles de la señal del experimento (ver sección 5) -- calculados walk-forward, nunca con datos futuros. |

**Nota sobre el denominador real vs. uno inflado**: `daily_outcome` tiene
128.211 filas en total, pero `MIN_BASELINE_DAYS` (20, para calcular
features) y `MIN_FORWARD_DAYS` (10, para calcular el resultado) son pisos
distintos -- los primeros ~20 días y los últimos ~10 días del rango de cada
símbolo tienen UNO de los dos datos pero no el otro. Un caso solo es
"evaluable" de verdad si tiene AMBOS -- por eso 78.826, no 128.211. Este
mismo error (usar el conteo crudo en vez del cruzado) fue el que se corrigió
esta ronda en `/api/historical-reference-summary` (ver informe de esa
investigación).

---

## 3. Qué es el baseline y contra qué se compara

El baseline es la fila `poblacion_total` de cada dirección: la tasa de
+20%/+50%/+100% de TODAS las detecciones de esa dirección, sin usar
ninguna señal experimental para elegir cuáles mirar primero. Representa
"lo que ya tiene Atlas hoy" -- ninguna de las 7 puertas actuales usa
volatilidad de 14 días ni rango del propio día para decidir nada, así que
el conjunto de candidatas de cada experimento es exactamente el mismo; lo
único que cambia es si SEGMENTAR ese mismo conjunto por la señal separa
mejor los casos que continúan de los que no.

**La comparación es siempre**: tasa del tercil "alto" de la señal **vs.**
tasa de la población total **vs.** tasa del tercil "bajo". Si "alto" no
supera claramente a la población total, la señal no aporta nada -- y eso
también se reporta, no se esconde.

---

## 4. Cómo funciona el walk-forward (y por qué no hay fuga de información)

De las 32 fechas distintas, las **primeras 10 quedan reservadas SOLO para
calibración** -- nunca se miden resultados en esas fechas, solo sirven para
tener datos previos con qué calcular los primeros cortes.

Para cada fecha posterior (22 fechas evaluadas, del 2026-07-01 al
2026-07-31), el corte de "alto/medio/bajo tercil" de ESE día se calcula
usando **exclusivamente** las filas ALCISTA de fechas **estrictamente
anteriores** a esa fecha (ventana expansiva: cada día que pasa, el
historial disponible para calcular el próximo corte crece). Nunca se usa
la fecha que se está evaluando, ni ninguna fecha futura, para decidir el
corte.

**Verificación real, no solo declarada**: `atlas_live/learning/
test_experiments.py::test_cuts_for_date_no_usan_datos_de_esa_fecha_ni_posteriores`
altera a propósito los valores de la fecha evaluada y de todas las
posteriores a un número absurdo (9999.0) y confirma que el corte calculado
para la fecha objetivo **no cambia** -- si hubiera fuga, el test fallaría.
Hay un segundo test (`test_cuts_for_date_si_cambian_si_se_altera_el_pasado`)
que confirma lo contrario: alterar el PASADO sí cambia el corte -- prueba
de que el mecanismo realmente usa los datos que debe, no que simplemente
los ignora todos.

---

## 5. Resultado de cada experimento, en lenguaje normal

### Experimento A -- Volatilidad de 14 días

**Qué mide la señal**: cuánto se movió el símbolo, en promedio, en los 14
días de mercado ANTES de la fecha evaluada (un ATR simplificado, ya
calculado por `daily_reference.py`).

**Resultado real (ALCISTA)**: entre las candidatas del tercil de
volatilidad más alta, **37.8% llegó a +20%** (n=4.172) contra **18.3%** de
la población completa (n=14.632) -- más del doble. El tercil más bajo cayó
a **2.3%** (n=5.364). La brecha se sostiene en +50% (7.4% vs 2.4%) y +100%
(1.2% vs 0.3%).

**En palabras simples**: un símbolo que YA venía moviéndose bastante en las
últimas dos semanas tiene mucha más chance de que un movimiento nuevo hoy
llegue lejos, que uno que venía tranquilo. No dice nada sobre CUÁNDO
detectarlo (eso es el Experimento B) ni sobre si va a subir o bajar (eso ya
lo resuelve `direction`, siempre por separado).

### Experimento B -- Timing genuino temprano vs. tarde

**Qué compara**: agrupa las 6 categorías de `phase_classifier.py` en 3
grupos -- `early_genuino` (al_comienzo + expansion_temprana), `late`
(recorrido_significativo_ya_hecho + demasiado_tarde + agotamiento), y
`antes_del_movimiento` aparte (es el 74.6% del dataset y, medido, es
mayormente "día sin nada relevante", no "a punto de explotar" -- por eso
NUNCA se cuenta junto con `early_genuino`).

**Resultado real (ALCISTA)**: `early_genuino` llegó a +20% en **33.8%** de
los casos (n=4.143); `late` en **24.9%** (n=3.834); `antes_del_movimiento`
en solo **15.8%** (n=13.158).

**En palabras simples**: detectar cuando el movimiento recién está
arrancando de verdad SÍ es mejor que detectarlo cuando ya recorrió una
parte importante -- confirma, con datos reales y completos (los 2.575
símbolos, no una muestra parcial), que el diseño de `phase_classifier.py`
va en la dirección correcta. No es una regla nueva -- es evidencia de que
la regla que ya existe tiene sentido.

### Experimento C -- Rango del propio día

**Qué mide la señal**: el rango alto-bajo del día de la detección, como %
del precio de cierre (`daily_range_pct`, calculado directamente del Quote
en vivo, sin llamada de red extra).

**Resultado real (ALCISTA)**: tercil alto **34.3%** (n=4.296) vs.
población **18.3%**; tercil bajo **4.9%** (n=5.208). Mejora real, pero
consistentemente **más débil** que la de volatilidad de 14 días en los 3
umbrales.

### ¿A y C miden lo mismo?

**Correlación real entre ambas señales** (todo el dataset ALCISTA,
n=21.135): Pearson = 0.676, Spearman = 0.827. Relacionadas (las dos
capturan "cuánto se mueve" el símbolo) pero no la misma variable.

**Prueba directa -- combinarlas exigiendo que ambas estén en su propio
tercil alto a la vez**: **40.0%** de +20% (n=3.041, el segmento más chico
de los tres por ser más exigente) -- supera a volatilidad_14d sola (37.8%)
y a daily_range sola (34.3%). Si fueran redundantes, combinar no debería
mejorar nada sobre la mejor de las dos por separado -- mejoró, así que
aportan algo de información independiente, aunque relacionada.

---

## 6. Qué mejoró realmente y cuánto (resumen)

| | Población (baseline) | Tercil alto | Mejora relativa |
|---|---|---|---|
| VOLATILIDAD_14D | 18.3% (n=14.632) | 37.8% (n=4.172) | ~2.1x |
| DAILY_RANGE | 18.3% (n=14.632) | 34.3% (n=4.296) | ~1.9x |
| COMBINADA | 18.3% (n=14.632) | 40.0% (n=3.041) | ~2.2x, segmento más chico |

Precisión reciente (últimas 5 fechas evaluadas del walk-forward,
27→31-jul) vs. acumulada (las 22 fechas evaluadas completas) para la
población ALCISTA: **17.3%** (n=3.820) vs. **18.3%** (n=14.632) -- se
sostiene, no colapsa entre el tramo reciente y el acumulado completo.

**Punto de gobernanza -- nunca ignorarlo**: BAJISTA y NEUTRAL muestran el
mismo patrón (volatilidad alta = más movimiento) porque `max_advance_pct`
mide un avance posterior en CUALQUIER dirección -- en BAJISTA eso es un
rebote, no continuación de la caída. Los números de la tabla de arriba son
exclusivamente de la columna ALCISTA; BAJISTA/NEUTRAL nunca se mezclaron
en ningún cálculo (separados por columna en el propio motor,
`atlas_live/learning/experiments.py`).

---

## 7. Qué NO podemos concluir todavía

- **Esto es UN backtest sobre UNA sola ventana de calendario** (~3 meses,
  mayo-agosto 2026). "Se sostiene entre reciente y acumulado" dentro de
  esa ventana NO es lo mismo que "se sostiene entre regímenes de mercado
  distintos" -- eso es exactamente lo que mide el Eje 4/9/11 de la
  arquitectura de Madurez, y hoy están en "Sin evidencia" porque el
  Aprendizaje en Vivo arranca en cero.
- **No sabemos si esta relación se mantiene con datos intradía reales**
  (velocidad/aceleración de 1 minuto) -- el estudio es 100% diario.
- **No hay validación fuera de muestra en el sentido fuerte** (Eje 11 de
  Madurez: un holdout que nunca se usó para nada, ni para calibrar ESTOS
  mismos terciles). El walk-forward evita fuga de información futura
  dentro del backtest, pero las 22 fechas evaluadas siguen siendo parte
  del mismo estudio que generó la hipótesis en primer lugar -- no es un
  conjunto de prueba totalmente independiente todavía.
- **La muestra de +100% es chica en términos absolutos** (91 casos ALCISTA
  en todo el dataset) -- la mejora ahí es real y en la dirección esperada,
  pero es la conclusión menos sólida de las tres.

## 8. Qué significa que falta validación en vivo

Todo lo de arriba usa datos DIARIOS de un período que ya pasó. El
Aprendizaje en Vivo (candidate_registry, radar CAPA 2) recién empieza a
acumular evidencia real cuando el mercado abra. Los campos diagnósticos
(`volatility_14d_pct_at_detection`, `daily_range_pct_at_detection`,
`early_vs_late_summary()`) ya están conectados y listos -- guardan estos
mismos datos por cada candidata real, sin afectar qué se detecta, para que
en algún momento futuro se pueda repetir este mismo análisis con datos
100% en vivo y ver si la relación se sostiene fuera del backtest. Hasta
que eso pase con evidencia suficiente (Eje 8/9/11 de Madurez), esto sigue
siendo "resultado prometedor de un backtest", no "validado en producción".

---

## 9. Conclusión final

**Demostrado con evidencia histórica real** (78.826 casos, walk-forward,
sin fuga de información, separado por dirección):
- Volatilidad de 14 días separa mejor los casos que continúan de los que
  no (Experimento A).
- El diseño de `timing_deteccion` de `phase_classifier.py` tiene sentido
  real -- temprano genuino > tarde > antes_del_movimiento (Experimento B).
- Rango del propio día aporta señal real, más débil que A, con información
  parcialmente independiente (Experimento C).

**Todavía solo hipótesis, no hechos establecidos**:
- Que esta relación se sostenga en otro régimen de mercado.
- Que se sostenga con datos intradía reales, no solo diarios.
- El tamaño exacto de la mejora en vivo (podría ser menor, igual o mayor
  que en el backtest -- no lo sabemos todavía).

**Qué NO se tocó y no debe tocarse todavía**: `candidate_gates.py` (las 7
puertas siguen exactamente igual), el score de ranking, y `DecisionEngine`.
Los campos de volatilidad/rango quedan guardados como diagnóstico en
`candidate_detection`, nunca leídos por ninguno de esos tres. Cualquier
cambio real a la lógica de detección requiere, primero, repetir este mismo
análisis con datos en vivo y mostrar que la mejora se sostiene fuera de
esta única ventana histórica.
