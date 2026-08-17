# Hallazgos de la Base Histórica (universo de mercado completo) y propuesta del algoritmo de Atlas

**2026-08-17.** Análisis real sobre `GET /api/admin/historical-scoring-report`
(commit `31cd792`), ejecutado sobre la Base Histórica reconstruida desde
cero (Fase 2) sobre el universo EQUITY completo (5.524 símbolos, no solo
Racional).

## Alcance de los datos

- **162.105 observaciones evaluables** (features + outcome), **5.374 símbolos** distintos.
- Features analizadas: `volatility_14d_pct`, `daily_range_pct` (mismas dos que ya se habían validado en los Experimentos A/B/C sobre Racional -- acá se re-verifican sobre el mercado completo).
- Agrupación: `(direction, timing_deteccion)` -- 13 grupos con muestra suficiente (`n >= 30`) de los 15 combinatoriamente posibles. No hay datos suficientes para `ALCISTA/demasiado_tarde`, `NEUTRAL/al_comienzo`, `NEUTRAL/expansion_temprana`, `NEUTRAL/demasiado_tarde`, `NEUTRAL/recorrido_significativo_ya_hecho`.
- **Límite explícito de este análisis**: no incluye el cruce con `racional_available` -- `historical_scoring.py` hoy solo agrupa por dirección/timing, no por operabilidad Racional. Si se quiere esa comparación, hace falta extender el módulo (no se hizo en este pase para no fabricar un cruce que el reporte real no trae).

## Tabla completa -- condición "ALTO" (volatilidad + rango diario en el tercil alto del propio grupo)

"Alto" = ambas features (`volatility_14d_pct` Y `daily_range_pct`) en el tercil superior calculado dentro de ese `(dirección, timing)` -- ver `_bucket_of_row` en `experiments.py`. `Δ vs bajo` = diferencia en puntos porcentuales de la tasa de +20% entre el bucket alto y el bucket bajo del mismo grupo -- mide cuánto discrimina realmente la condición.

### ALCISTA

| Timing | n | Aciertos +20% | Fallos +20% | % éxito +20% | % +50% | % +100% | % fallo | Drawdown prom. en fallos | Δ vs bajo (pp) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| agotamiento | 295 | 144 | 151 | 48.8% | 18.6% | 8.8% | 51.2% | -25.9% | +35.1 |
| al_comienzo | 3.052 | 1.437 | 1.615 | 47.1% | 19.5% | 8.5% | 52.9% | -36.4% | +20.8 |
| antes_del_movimiento | 6.628 | 2.772 | 3.856 | 41.8% | 11.3% | 3.1% | 58.2% | -25.3% | +37.4 |
| expansion_temprana | 130 | 60 | 70 | 46.2% | 19.2% | 7.7% | 53.8% | **-49.6%** | **+2.2** |
| recorrido_significativo_ya_hecho | 2.145 | 948 | 1.197 | 44.2% | 16.4% | 6.1% | 55.8% | -34.9% | +33.2 |

### BAJISTA

| Timing | n | Aciertos +20% | Fallos +20% | % éxito +20% | % +50% | % +100% | % fallo | Drawdown prom. en fallos | Δ vs bajo (pp) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| agotamiento | 2.787 | 1.445 | 1.342 | 51.8% | 21.4% | 9.8% | 48.2% | -36.4% | +34.6 |
| al_comienzo | 1.378 | 657 | 721 | 47.7% | 21.6% | 8.8% | 52.3% | -41.0% | +16.7 |
| antes_del_movimiento | 5.848 | 2.558 | 3.290 | 43.7% | 12.8% | 3.7% | 56.3% | -26.3% | +37.0 |
| demasiado_tarde | 741 | 425 | 316 | **57.4%** | 25.9% | 11.1% | **42.6%** | -37.9% | +38.6 |
| expansion_temprana | 1.501 | 838 | 663 | 55.8% | **26.3%** | **13.5%** | 44.2% | -40.8% | +19.4 |
| recorrido_significativo_ya_hecho | 350 | 176 | 174 | 50.3% | 22.0% | 9.4% | 49.7% | -35.3% | +38.5 |

### NEUTRAL

| Timing | n | Aciertos +20% | Fallos +20% | % éxito +20% | % +50% | % +100% | % fallo | Drawdown prom. en fallos | Δ vs bajo (pp) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| agotamiento | 1.174 | 489 | 685 | 41.7% | 12.4% | 4.6% | 58.3% | -27.6% | +32.7 |
| antes_del_movimiento | 13.010 | 5.058 | 7.952 | 38.9% | 9.5% | 2.3% | **61.1%** | -21.0% | +38.2 (pero bajo=0.7%, casi sin base) |

## Hallazgos más fuertes (evidencia real, con `n` explícito)

1. **La condición "alto" (volatilidad+rango en su propio tercil superior) discrimina de verdad, en las 3 direcciones**: en 11 de los 13 grupos, el bucket alto duplica o triplica la tasa de +20% del bucket bajo, con brechas de +16.7 a +38.6 puntos porcentuales. Esto ya se sabía sobre Racional (Experimentos A/B/C, 2026-08-16) -- **se confirma ahora sobre el mercado completo (5.524 símbolos, no 2.575)**.

2. **La señal más fuerte y más confiable en conjunto: `BAJISTA / expansion_temprana`** -- `n=1.501`, +20%=55.8%, +50%=26.3% (la más alta de todo el dataset), +100%=13.5% (la más alta de todo el dataset), con una tasa de fallo relativamente contenida (44.2%). Es el mejor punto combinado de magnitud + confiabilidad + muestra suficiente.

3. **La señal más "limpia" (menor tasa de fallo): `BAJISTA / demasiado_tarde`** -- 42.6% de fallo (la más baja de todos los grupos "alto"), +20%=57.4% (la más alta de todas). Contraintuitivo por el nombre ("demasiado tarde"), pero real: una aceleración bajista detectada tarde, con volatilidad/rango ya altos, sigue cayendo con más frecuencia que cualquier otro patrón medido.

4. **`antes_del_movimiento` es, en las 3 direcciones, la categoría de timing menos confiable** -- tasa de fallo 56.3%-61.1%, la más alta de todas las categorías, y además es la que domina el volumen del dataset (101.495 de las 162.105 observaciones evaluables, ~63%). Esto extiende y confirma, sobre el universo completo, el hallazgo de la Hipótesis B de los Experimentos A/B/C (Racional): "antes_del_movimiento" es mayoría del dataset y en su mayoría NO es una señal de explosión inminente.

5. **`ALCISTA / expansion_temprana` no muestra señal real** -- Δ=+2.2pp (essencialmente nulo: alto=46.2% vs bajo=44.0%), muestra chica (n=130+134), y el peor drawdown promedio en fallos de todo el dataset (-49.6%). Es la peor combinación posible: sin ventaja predictiva y mayor riesgo cuando falla. No debería tratarse como señal confiable.

6. **Los "falsos positivos" no son inocuos**: incluso en los mejores grupos, cuando la condición "alto" falla (no llega a +20%), el retroceso promedio real es severo -- entre -21.0% y -49.6% según el grupo. Ningún grupo tiene un fallo "benigno". Esto es evidencia real de que la probabilidad de acierto sola no alcanza: el tamaño del riesgo cuando falla varía mucho por grupo y debería pesar en cualquier decisión operativa futura.

## Propuesta concreta del algoritmo de Atlas

**Objetivo**: convertir esta evidencia en una clasificación de confianza histórica, reutilizando `atlas_live/learning/historical_scoring.py` (ya construido, testeado, standalone) -- **sin conectarlo todavía a `candidate_gates.py`, el score en vivo, ni `DecisionEngine`.**

### Nivel de Evidencia Histórica (propuesto, 4 niveles)

Para un candidato con `(direction, timing_deteccion, volatility_14d_pct, daily_range_pct)`, `historical_scoring.score_candidate()` ya devuelve el bucket + `n`/aciertos/`%`. Se propone una capa de clasificación ENCIMA de eso, derivada directamente de esta tabla (no de un umbral arbitrario):

- **ALTA**: bucket="alto" AND timing != "antes_del_movimiento" AND n>=500 AND Δ vs bajo >= 15pp.
  Grupos que califican hoy: `ALCISTA/al_comienzo`, `ALCISTA/recorrido_significativo_ya_hecho`, `BAJISTA/agotamiento`, `BAJISTA/al_comienzo`, `BAJISTA/demasiado_tarde`, `BAJISTA/expansion_temprana`, `NEUTRAL/agotamiento`.
- **MODERADA**: bucket="alto" AND timing != "antes_del_movimiento" AND (n entre 30 y 500, o Δ entre 5 y 15pp).
  Ej.: `ALCISTA/agotamiento` (n=295), `BAJISTA/recorrido_significativo_ya_hecho` (n=350).
- **BAJA (estructural)**: timing == "antes_del_movimiento", en cualquier dirección o bucket -- tasa de fallo 56-61% ya demostrada, sin importar cuán "alto" sea el bucket.
- **INSUFICIENTE**: n < 30 (piso ya usado en todo el proyecto), o grupo con Δ vs bajo < 5pp (ej. `ALCISTA/expansion_temprana`, Δ=2.2pp) -- se marca explícitamente como "sin evidencia de que esta condición discrimine", nunca se oculta.

### Cómo se reportaría (nunca un número aislado)

Cada evaluación expone siempre: `{direction, timing_deteccion, bucket, nivel_evidencia, n, aciertos_20/50/100, pct_20/50/100, pct_fallo, drawdown_promedio_en_fallos}` -- exactamente lo que ya devuelve `score_candidate()` + `false_positive_report()`, con el nivel de evidencia como capa de interpretación encima, no un reemplazo.

### Lo que este algoritmo NO hace (y no debe hacer sin aprobación aparte)

- No decide "comprar/vigilar/descartar" -- eso es `DecisionEngine`, sin tocar.
- No reemplaza ni pesa `candidate_gates.py` -- las puertas en vivo siguen siendo las que ya existen.
- No se ejecuta automáticamente sobre el radar en vivo -- es una consulta bajo demanda (`historical_scoring.score_candidate()`), standalone.

## Próximo paso (pendiente de tu autorización)

Conectar `historical_scoring.score_candidate()` como una lectura adicional -- no como reemplazo -- en algún punto del pipeline en vivo (candidato para `candidate_tracker.py`, ya que ese módulo ya calcula `direction`/`timing_deteccion`/`volatility_14d_pct` para cada detección). Esto queda explícitamente sin hacer hasta que lo autorices.
