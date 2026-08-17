# Propuesta final del algoritmo de Atlas — estudio de separación A/B/C

**2026-08-17.** Análisis real sobre `GET /api/admin/separation-report`
(commit `c500105`). Este documento corrige y reemplaza partes del análisis
anterior (`ALERTA_TEMPRANA_ANALISIS_Y_PROPUESTA.md`) a la luz de un hallazgo
metodológico importante que apareció al hacer la separación A/B/C.

## Corrección metodológica que hay que decir primero, sin maquillar

`categorize_onsets()` clasifica un onset de +20% como categoría C (100%+)
SOLO si el `max_advance_pct` de ESE MISMO día (su propia ventana de 10 días
hacia adelante) ya llega a 100%. Eso captura únicamente movimientos que
explotan muy rápido, todos dentro de una sola ventana de 10 días. **De los
136 onsets que calificaron como C bajo esta definición estricta, solo 4
tenían los 5 días previos disponibles en la base** (`n_episodios=4` en
`por_categoria.C_100_mas.T-1`). Es una muestra demasiado chica para sacar
ninguna conclusión -- lo digo explícito, no la uso como evidencia.

**Los movimientos de +100% que tardan más de una ventana de 10 días en
completarse** (la mayoría, probablemente) quedan mejor capturados por el
análisis anterior (`generate_precursor_report`, umbral +100% buscado de
forma independiente): ahí el onset se detecta símbolo por símbolo sin esa
restricción, dando **390 episodios, 258 con datos de T-1**. Esa sigue
siendo la fuente confiable para "+100%" -- la uso más abajo, no los datos
de C de este reporte nuevo.

**Las categorías A (20-49%, n=5.842) y B (50-99%, n=400) sí tienen muestra
real** -- 4.406 y 37 episodios respectivamente con datos de T-1. Ahí está
la comparación seria de este informe.

## El hallazgo que cambia el enfoque: la MEDIANA cuenta una historia distinta al promedio

En el informe anterior reporté que `relative_volume` promedio subía fuerte
antes de un onset. Con la separación A/B/C y la mediana calculada, aparece
lo siguiente (T-1, `relative_volume`):

| Categoría | n | Mediana | Promedio | p75 | p90 |
|---|---:|---:|---:|---:|---:|
| A (20-49%) | 4.406 | **0.765** | 6.289 | 1.133 | 1.841 |
| B (50-99%) | 37 | **1.849** | 121.191 | 6.386 | 112.837 |

**El promedio estaba inflado por outliers extremos** -- la mediana real de
`relative_volume` en T-1 para la categoría A es 0.765 (por debajo del
promedio normal de mercado), no elevada. El "volumen anormal antes del
movimiento" del informe anterior era, en gran parte, un puñado de casos
extremos arrastrando el promedio -- **no es cierto para la mayoría de los
movimientos de +20%.** `relative_volume`, usado solo, NO es un buen
separador confiable entre A y B a nivel de mediana (0.77 vs 1.85 -- hay
diferencia, pero ambos números son moderados, nada parecido a los "7x-16x"
que mostraba el promedio).

## Lo que SÍ separa A de B con evidencia sólida (mediana, en T-1..T-5)

| Feature | Categoría A (mediana) | Categoría B (mediana) | ¿Estable en T-1..T-5? |
|---|---:|---:|---|
| `volatility_14d_pct` | ~6.8-7.0 (flat) | ~10.1-11.4 (flat) | Sí, ambas -- B es ~50-65% más alta en TODOS los offsets |
| `daily_range_pct` | ~5.9-6.1 (flat) | ~7.0-21.6 (más ruidoso, siempre más alto que A) | Sí, ambas |
| `change_pct_delta` (T-1) | -0.002 (≈0) | +1.163 (real) | Solo relevante en T-1 |

**`volatility_14d_pct` es el separador más confiable encontrado en este
estudio**: la mediana de B es consistentemente 50-65% más alta que la de A
en los 5 offsets, sin ruido de outliers (son medianas, no promedios). Esto
es un régimen de fondo (recordá: `volatility_14d_pct` es un promedio móvil
de 14 días, cambia lento) -- **el símbolo que va a continuar más allá de
+20% típicamente YA viene de un régimen más volátil, días antes, no solo
el día del onset.**

## Persistencia del volumen anormal (la pregunta específica que pediste)

Días (de los 5 previos disponibles) con `relative_volume >= 2.0`:

| Categoría | Promedio de días elevados | % con 0 días elevados | % con 2+ días elevados |
|---|---:|---:|---:|
| A (n=4.406) | 0.34 | 75% (3.315/4.406) | ~7% |
| B (n=37) | 1.19 | 32% (12/37) | ~35% (13/37) |
| C (n=4, muestra insuficiente) | 2.25 | 25% | 50% |

**Esta es la evidencia más clara del estudio**: en la categoría A, el 75%
de los casos NUNCA tuvo un día con volumen realmente elevado en toda la
ventana previa -- el movimiento de +20% ocurrió sin ninguna anomalía de
volumen detectable de antemano. En la categoría B, la mayoría SÍ tuvo al
menos un día elevado, y un tercio tuvo 2 o más. **No es "hubo volumen
alto", es "el volumen elevado se repitió más de un día" lo que separa a
los que continúan de los que no.**

Aceleración `relative_volume` T-1 menos T-5 (mediana): A=0.015 (≈0, sin
cambio real), B=0.514 (aumento real y sostenido). Confirma lo mismo.

## Racional disponible vs no disponible, DENTRO de cada categoría

| Categoría | Racional=true (n, mediana volatilidad) | Racional=false (n, mediana volatilidad) |
|---|---|---|
| A | n=1.128, volatilidad=5.16, rango=4.75 | n=3.278, volatilidad=7.46, rango=6.90 |
| B | **n=3** (insuficiente) | n=34, volatilidad=10.43, rango=19.48 |
| C | **n=0** (sin datos) | n=4 |

**Límite honesto que hay que decir**: no hay muestra suficiente para
confirmar si el patrón de separación A/B se sostiene DENTRO de Racional --
solo 3 casos de categoría B y 0 de categoría C tienen datos de precursores
dentro de lo operable en Racional. Lo único que sí se puede afirmar con
la categoría A (n=1.128, muestra real): dentro de Racional, la volatilidad
previa típica es más baja (5.16 vs 7.46 fuera de Racional) -- consistente
con el hallazgo anterior de que Racional tiende a nombres más calmos.

## Propuesta final revisada: 6 ventanas, con evidencia real en cada una

- **PREPARACIÓN**: `volatility_14d_pct` del símbolo ya por encima de su
  propio promedio histórico reciente -- contexto de régimen, presente por
  igual en A y B, NO discrimina continuidad por sí solo. Es la condición
  necesaria, no suficiente.
- **ALERTA_TEMPRANA**: un primer día con `relative_volume >= 2.0` sin que
  el precio se haya movido todavía (`timing_deteccion` sigue en
  `antes_del_movimiento`/`expansion_temprana`). El 75% de los movimientos
  que se quedan en +20-49% (A) nunca pasan de acá -- por eso esta ventana
  sola NO debe tratarse como señal fuerte.
- **ALERTA_FUERTE** (la pieza nueva y mejor respaldada de este estudio):
  volumen elevado que **persiste 2 o más de los últimos 5 días** (no un
  solo pico) **combinado con** `volatility_14d_pct` ya 50%+ por encima del
  régimen típico del símbolo. Evidencia real: en B, 35% de los casos
  cumplen "2+ días elevados" contra 7% en A; la mediana de volatilidad de
  B es 50-65% más alta que la de A en toda la ventana T-1..T-5.
- **INICIO**: `timing_deteccion == "al_comienzo"` en el día del onset --
  ya validado (informe 1): ALCISTA n=3.052 (47.1% a +20%), BAJISTA n=1.378
  (47.7%).
- **CONFIRMACIÓN**: `timing_deteccion == "recorrido_significativo_ya_hecho"`
  (informe 1).
- **NO_PERSEGUIR / SALIR**: `timing_deteccion in ("demasiado_tarde",
  "agotamiento")` (informe 1) -- y aunque se entre bien, recordar que
  **91.6% de los onsets de +20% se quedan en 20-49% y retroceden -14.2%
  en promedio** (informe 1): la salida disciplinada importa tanto como la
  entrada.

## Qué falta para que esto sea una regla ejecutable (no se hizo, declarado)

1. La categoría C (+100%) de este estudio necesita rehacerse con la
   definición del informe 1 (onset independiente, n=390) para tener
   muestra real -- lo de acá (n=4) no alcanza.
2. La comparación con Racional en B/C necesita más datos -- hoy no hay
   suficiente para confirmar si "ALERTA_FUERTE" funciona igual dentro de
   lo operable en Racional.
3. Nada de esto se probó como regla predictiva real (walk-forward, como sí
   se hizo con Experimentos A/B/C) -- es evidencia descriptiva de qué
   diferencia a A de B, no una validación de que "ALERTA_FUERTE" prediga
   bien hacia adelante en datos nuevos.

No se tocó `candidate_gates.py`, el score en vivo ni `DecisionEngine`.
