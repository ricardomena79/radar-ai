# Propuesta: Madurez del Aprendizaje (reemplazo del "Nivel de aprendizaje" antiguo)

**Estado: PROPUESTA PENDIENTE DE REVISIÓN. No implementada. No desplegada.**
2026-08-15, en respuesta directa al hallazgo real de que la Cabina mostraba
"Aprendizaje: 71.4% (10/14 condiciones)" después del reset del aprendizaje
anterior (ver investigación y fix de `_evidence_cache` en la misma sesión).

Relacionado: [LEARNING_ENGINE.md](LEARNING_ENGINE.md) (notas de 2026-08-02,
todavía vigentes como contexto histórico -- esta propuesta las reemplaza en
la parte de cálculo, que aquel documento dejaba explícitamente pendiente).

---

## 0. Qué reemplaza esto exactamente

El indicador visible "🧠 Aprendizaje" (barra superior + panel Evolución) hoy
calcula `nivel_aprendizaje_pct = condiciones_confiables / 14 * 100` --
fracción de una grilla fija de 14 condiciones univariadas
(`calibration_advisor.CONDITION_GRID`) que superan un umbral de Wilson,
mezclando en la misma cuenta observaciones históricas y en vivo.

**Decisión ya tomada (pedido explícito del usuario, esta ronda):**
- Ese cálculo **deja de presentarse como "Aprendizaje" de Atlas.**
- Puede seguir existiendo **internamente** como diagnóstico del motor de
  condiciones del Memory Engine v1 (es información real, solo que describe
  otra cosa) -- si se conserva, se renombra a algo como
  `memory_engine_condition_coverage` ("cobertura de condiciones del motor
  de calibración v1") y nunca vuelve a aparecer bajo la etiqueta
  "Aprendizaje" ni "Madurez".
- Esta propuesta define su reemplazo real.

---

## 1. Los tres bloques, completamente separados

### A) Base Histórica de Referencia
Lo que ya existe de la fase anterior de esta sesión
(`atlas_live/reference/`). Se muestra siempre etiquetado como **referencia,
no como aprendizaje**:

- Símbolos estudiados históricamente (ej. "2.470 de 2.575")
- Observaciones históricas evaluables (ej. "78.826 casos")
- Patrones encontrados (por timing/dirección, con n explícito)
- Resultados +20% / +50% / +100% del backtest histórico
- Rango de fechas realmente cubierto

Nunca contribuye a ningún número de las secciones B o C.

### B) Aprendizaje en Vivo
Arranca en cero el lunes. Solo cuenta observaciones con origen en vivo
(equivalente a `source_version="live"` en el Memory Store, o las filas de
`candidate_registry`/`daily_summary` de CAPA 2 que nacen después del
reset). Ejemplo de cómo se ve el día 1:

```
Observaciones nuevas: 0
Casos cerrados: 0
Aciertos: No disponible
Fallos: No disponible
Precisión: No disponible
Madurez del aprendizaje: Sin evidencia suficiente
```

Y más adelante, con evidencia real:

```
Precisión: 7 / 10 = 70%
Madurez del aprendizaje: Evidencia inicial
```

**Nunca** un porcentaje de precisión sin su numerador y denominador al
lado. Nunca la madurez inferida a partir de la precisión.

### C) Madurez / Confianza del Aprendizaje
El reemplazo del 71.4%. Es una **etiqueta de estado**, no un porcentaje --
ver sección 3. Se calcula exclusivamente sobre B, nunca sobre A.

---

## 2. Separación dura entre Precisión y Madurez

Son dos preguntas distintas y **nunca se combinan en un solo número**:

| Pregunta | Métrica | Ejemplo de presentación |
|---|---|---|
| "¿Qué fracción de los casos cerrados acertó?" | **Precisión** -- un cociente real, recalculado siempre, siempre con numerador/denominador | `7 / 10 = 70%` |
| "¿Cuánto puedo confiar en ese cociente?" | **Madurez** -- un estado (sección 3), nunca un porcentaje | `Sin evidencia suficiente` |

Reglas explícitas de esta separación:
1. La precisión se muestra SIEMPRE que haya al menos 1 caso cerrado -- incluso con muestra chica. No se oculta un 70%/10 solo porque la muestra sea pequeña; se oculta la CONFIANZA en ese 70%, mostrando la madurez como "Sin evidencia suficiente" al lado.
2. La precisión puede **bajar** cuando crece la muestra -- eso se muestra explícitamente, no se congela un "mejor resultado histórico". Propuesta de presentación: mostrar precisión de la ventana reciente junto a la precisión acumulada completa, lado a lado, sin promediarlas ("Reciente: 3/5 = 60% · Acumulada: 41/70 = 58.6%").
3. La madurez nunca se calcula a partir de la propia precisión (no existe una regla "si acierta mucho, sube la madurez") -- se calcula exclusivamente a partir de CUÁNTA Y QUÉ TAN DIVERSA es la evidencia detrás, sección 3.

---

## 3. Los 11 ejes -- evidencia, no promedio

Cada eje madura de forma **independiente**, con su propia evaluación de
"qué tan bien cubierto está":

1. **Volumen** -- casos totales evaluados (cerrados, con resultado real).
2. **Días distintos de mercado** -- no basta con muchos casos en pocos días.
3. **Símbolos distintos** -- no basta con muchos casos en pocos símbolos.
4. **Regímenes de mercado distintos** -- variedad real de condiciones (alta/baja volatilidad, tendencia general del mercado, etc.), no todo el mismo tipo de sesión.
5. **Cobertura por timing de detección** -- evidencia en cada uno de los 6 buckets (`antes_del_movimiento` ... `agotamiento`), no solo en el más común.
6. **Cobertura por dirección** -- ALCISTA y BAJISTA representados, no solo el que domina el mercado del momento.
7. **Cobertura de comportamiento post-apertura** -- cuando exista dato real (detecciones en premarket), continúa/colapsa representados.
8. **Evidencia por objetivo de resultado** -- +20%, +50% y +100% se tratan como tres sub-ejes separados, porque +100% siempre va a tener MUCHA menos muestra que +20% de forma natural -- no se puede exigirles la misma cantidad de evidencia, pero cada uno necesita la suya propia antes de "contar".
9. **Consistencia de resultados** -- la precisión no cambia erráticamente entre ventanas de tiempo comparables.
10. **Recencia y estabilidad** -- la precisión reciente se compara contra la histórica; un cambio de régimen de mercado que la mueve se muestra, no se esconde.
11. **Validación fuera de muestra** -- una porción de la evidencia proviene de casos que nunca se usaron para ajustar ningún parámetro/umbral del propio sistema.

**Importante -- el punto clave de esta arquitectura:** el eje NO se mide por
su total agregado, sino por su **sub-bucket peor cubierto**. Ejemplo: el
eje 5 (timing) no está "cubierto" solo porque haya 10.000 casos en total si
9.900 son `antes_del_movimiento` y apenas 3 son `demasiado_tarde` -- el
estado del eje 5 lo determina el bucket con menos evidencia (`demasiado_tarde`
con 3), no el promedio ni el total.

---

## 4. La escalera de 7 estados

Aplica dos veces: **por eje** (evalúa cada uno de los 11 de forma
independiente) y **global** (ver sección 5, el cuello de botella). Los
nombres y el orden ya los propusiste; acá defino QUÉ tiene que demostrarse
conceptualmente para pasar de uno al siguiente -- sin números todavía.

### 0 · SIN EVIDENCIA
No hay casos cerrados en vivo, o los que hay no alcanzan ni para calcular
nada. Todo se muestra como "No disponible". Es el estado del lunes a
primera hora.

### 1 · EVIDENCIA INICIAL
Ya existen los primeros casos cerrados en vivo. Se puede mostrar una
precisión real (num/denom), pero es una sola cifra sin distribución detrás
-- probablemente concentrada en pocos días/símbolos/buckets. Este estado
existe específicamente para que "10 casos, 7 aciertos" se pueda MOSTRAR
como precisión, sin que nadie lo lea como "70% de aprendizaje" -- la
etiqueta de madurez al lado deja explícito que es apenas el comienzo.

### 2 · APRENDIZAJE EMERGENTE
Los 11 ejes dejaron de estar en cero -- hay al menos una presencia mínima
en cada uno (los 6 buckets de timing tocados al menos una vez, ambas
direcciones representadas, más de un puñado de días y símbolos distintos).
Todavía ningún eje tiene profundidad suficiente para confiar
estadísticamente en su peor bucket. Es la primera vez que se puede decir
"Atlas está empezando a cubrir el espacio completo de situaciones", no solo
acumulando volumen en un rincón.

### 3 · APRENDIZAJE EN DESARROLLO
Cada eje, EN SU PEOR SUB-BUCKET, cruza un mínimo estadístico real
(muestra mínima + algún límite de confianza tipo Wilson, mismo criterio
de rigor que ya usa el resto del proyecto -- el número exacto queda para
la implementación, no para esta propuesta). Acá es donde el "cuello de
botella" empieza a ser literal: no alcanza con que la mayoría de los casos
estén bien cubiertos, tiene que estarlo el bucket más débil de cada eje.

### 4 · APRENDIZAJE CONSISTENTE
Se suma la dimensión temporal: los ejes 9 (consistencia) y 10
(recencia/estabilidad) empiezan a exigir de verdad, no solo a mostrarse.
La precisión calculada sobre ventanas de tiempo separadas y no solapadas
tiene que sostenerse dentro de un rango razonable -- y la precisión
reciente se compara siempre contra la histórica, mostrando ambas.

### 5 · APRENDIZAJE VALIDADO
Se activa el eje 11: una parte real de la evidencia tiene que venir de
casos que nunca se usaron para calibrar ningún umbral/regla del propio
sistema. Es el punto en el que las reglas de detección (percentiles,
límites de fase, etc.) dejan de ser "descriptivas de lo que ya se vio" y
pasan a estar honestamente verificadas contra casos nuevos.

### 6 · MADUREZ ALTA
Todos los ejes -- incluidos los más caros de conseguir (evidencia de
+100%, regímenes de mercado poco frecuentes, combinaciones raras de
dirección+timing) -- tienen profundidad real, sostenida en el tiempo
suficiente como para haber atravesado distintas condiciones de mercado de
verdad (no inferidas). La validación fuera de muestra se sostiene en
múltiples rondas separadas, no en una sola vez. Este estado es
deliberadamente difícil y lento de alcanzar -- ese es el punto: si algún
día Atlas lo muestra, tiene que ser porque hay evidencia real detrás, no
porque el cálculo lo permitió con poca muestra.

---

## 5. El cuello de botella global

```
Madurez global = MÍNIMO(estado de los 11 ejes)
```

No un promedio, no un puntaje ponderado. Literal: si 10 ejes están en
"Madurez alta" y uno solo (por ejemplo, evidencia de +100%, que siempre va
a ser la más escasa) sigue en "Aprendizaje emergente", la madurez GLOBAL
de Atlas es "Aprendizaje emergente" -- el eje más débil manda.

Consecuencia práctica: la forma más rápida de subir la madurez global
nunca es "acumular más de lo que ya se tiene mucho", es específicamente
cerrar la brecha del eje más atrasado. Esto es intencional y responde
directamente a tu pedido: "Atlas no puede llegar a una madurez alta
solamente porque tenga muchos casos totales."

La Cabina siempre puede explicar CUÁL es el eje que está limitando la
madurez global (ej. "Madurez: Aprendizaje en desarrollo -- limitado por:
evidencia de +100%, 4 casos") -- mismo principio ya aprobado en
`LEARNING_ENGINE.md` para "Confianza de Atlas": nunca un número sin poder
explicar de dónde sale.

---

## 6. Cómo se vería en la Cabina (boceto conceptual, sin implementar)

```
┌─────────────────────────────────────────────────────────┐
│ 📚 Base Histórica de Referencia          (solo contexto)  │
│ 2.470 símbolos · 78.826 casos históricos                  │
│ [ver patrones encontrados →]                               │
├─────────────────────────────────────────────────────────┤
│ 🧠 Aprendizaje en Vivo                                     │
│ Observaciones nuevas: 0   Casos cerrados: 0                │
│ Precisión: No disponible                                    │
│ Madurez: Sin evidencia suficiente                           │
│ [ver los 11 ejes →]                                         │
└─────────────────────────────────────────────────────────┘
```

Al abrir "los 11 ejes", cada uno con su propio estado (de la escalera de
7 niveles) y su evidencia real (n, buckets cubiertos/faltantes) -- igual
de auditable que todo lo demás del proyecto.

El badge superior "🧠 Aprendizaje" pasa a mostrar el ESTADO (una palabra:
"Emergente", "En desarrollo", etc.), nunca un porcentaje -- exactamente
como ya se documentó en `LEARNING_ENGINE.md` para "Confianza de Atlas".

---

## 8. Anexo (2026-08-15) — Propuesta de umbrales y reglas de transición

**Estado: PROPUESTA DE NÚMEROS PARA REVISAR, todavía no implementada.**
Responde al pedido explícito de bajar la arquitectura conceptual (secciones
1-7, ya aprobadas) a números concretos para poder discutirlos, marcando
en cada caso si el número tiene una base estadística real o es una
decisión de diseño.

### 8.0 Bloques estadísticos que se reutilizan en varios ejes (no inventados hoy)

Para no repetir la justificación 11 veces, estas son las herramientas
reales que se reutilizan abajo:

| Herramienta | Qué dice | Ya usada en el proyecto |
|---|---|---|
| Piso de Wilson (`MIN_SAMPLE_SIZE=10`, `WILSON_Z=1.96`) | Por debajo de ~10 muestras, un intervalo de confianza de proporción no es confiable | Sí -- `atlas_live/memory/base_rates.py`, mismo criterio que `atlas.learning.pattern_evolution` en Core |
| Escalamiento 1/√n del ancho del intervalo | Para reducir a la mitad la incertidumbre de una tasa estimada, hace falta ~4x la muestra, no 2x | Hecho matemático general, no específico de este proyecto |
| Regla de "pocos éxitos observados" para eventos raros | Con una tasa base baja (ej. +100%), lo que limita la confianza no es el total de casos sino CUÁNTOS positivos reales se observaron -- un evento con 0-2 ocurrencias no permite estimar su tasa aunque el denominador sea enorme | Principio estándar de estimación de eventos raros (ligado a la "regla de tres" para intervalos con cero éxitos) |
| Solapamiento de intervalos de confianza | Si los intervalos de dos ventanas de tiempo se solapan, la diferencia observada entre ellas podría ser ruido de muestra, no un cambio real | Heurística estadística reconocida (conservadora frente a un test formal de dos proporciones) |
| Validación *walk-forward* (holdout temporal, nunca mezclado al azar) | En series de tiempo no se puede separar entrenamiento/prueba al azar -- hay que reservar SIEMPRE el tramo más reciente | Mismo principio anti-fuga ya aplicado esta sesión en `daily_reference.py` (features solo con datos ≤ D, resultado solo con datos > D) |
| Índice de concentración (tipo Herfindahl) | Mide si pocos elementos dominan una muestra -- estándar en finanzas para medir diversificación | Método reconocido, no inventado para esta propuesta |

Calendario bursátil real usado como ancla en varios ejes: ~5 días
hábiles/semana, ~21/mes, ~63/trimestre -- hechos de calendario, no
números inventados.

### 8.1 Tabla completa -- los 11 ejes × 7 niveles

Convención: cada celda es el **mínimo** para considerar ese eje en ese
nivel. Recordar (sección 5): la madurez GLOBAL es el mínimo de los 11
ejes, no un promedio.

**Eje 1 -- Volumen (casos CERRADOS y evaluados, nunca candidatas/señales abiertas)**
| L1 Sin evidencia | L2 Inicial | L3 Emergente | L4 En desarrollo | L5 Consistente | L6 Validado | L7 Madurez alta |
|---|---|---|---|---|---|---|
| 0 | 1-9 | 10-29 | 30-74 | 75-149 | 150-299 | ≥300 |

*Base:* el corte L2→L3 (n=10) es el piso de Wilson real. La forma creciente (más que lineal) de la progresión sigue el 1/√n. *Diseño:* los valores exactos 30/75/150/300.

**Eje 2 -- Días distintos de mercado**
| L1 | L2 | L3 | L4 | L5 | L6 | L7 |
|---|---|---|---|---|---|---|
| 0 | 1-4 | 5-9 (≥1 sem) | 10-20 (≥2 sem) | 21-41 (≥1 mes) | 42-62 (≥1 trim) | ≥63 (≥1 trim completo) |

*Base:* los cortes de semana/mes/trimestre son calendario real. *Diseño:* exigir que sean DISTINTOS (no solo muchos casos en pocos días) y los tramos exactos.

**Eje 3 -- Símbolos distintos + concentración**
| L1 | L2 | L3 | L4 | L5 | L6 | L7 |
|---|---|---|---|---|---|---|
| 0 | 1-9 | 10-24 | 25-49 (top-3 ≤30%) | 50-99 (top-3 ≤30%) | 100-199 (top-3 ≤20%) | ≥200 (top-3 ≤15%) |

*Base:* el índice de concentración top-N es método real de diversificación. *Diseño:* tramos de símbolos y cortes de porcentaje.

**Eje 4 -- Regímenes de mercado distintos** (derivados de datos que Atlas ya mide: volatilidad del universo ese día × sesgo direccional del universo ese día, en terciles → hasta 9 combinaciones, sin depender de una fuente externa)
| L1 | L2 | L3 | L4 | L5 | L6 | L7 |
|---|---|---|---|---|---|---|
| 0 regímenes | 1 | 2-3 | 4-5 | 6-7 | 8 | 9 (los 9, incluidos extremos) |

*Base:* usar la dispersión/dirección del propio universo escaneado es auto-referencial y auditable, no una dependencia nueva. *Diseño:* terciles, número de regímenes, y el corte por nivel.

**Eje 5 -- Cobertura por timing de detección** (mínimo en el bucket MÁS DÉBIL de los 6: antes_del_movimiento / al_comienzo / expansion_temprana / recorrido_significativo_ya_hecho / demasiado_tarde / agotamiento)
| L1 | L2 | L3 | L4 | L5 | L6 | L7 |
|---|---|---|---|---|---|---|
| 0 en algún bucket | ≥1 en los 6 | ≥10 en el peor | ≥20 | ≥30 | ≥50 | ≥75 |

*Base:* piso de Wilson (10) real. *Diseño:* la progresión 20/30/50/75. Nota real (del estudio histórico de esta sesión): `demasiado_tarde` y `agotamiento` son ~1.7% y ~8.8% del total -- exigirles el mismo piso que a los buckets frecuentes fuerza, matemáticamente, mucho más volumen total.

**Eje 6 -- Cobertura por dirección** (mínimo en la más débil de ALCISTA/BAJISTA/NEUTRAL)
| L1 | L2 | L3 | L4 | L5 | L6 | L7 |
|---|---|---|---|---|---|---|
| 0 | ≥1 en las 3 | ≥10 en la peor | ≥20 | ≥30 | ≥50 | ≥75 |

*Base/Diseño:* misma mecánica que Eje 5.

**Eje 7 -- Comportamiento post-apertura** (solo detecciones premarket; "no aplicable todavía" ≠ "insuficiente" mientras el total premarket sea bajo)
| L1 | L2 | L3 | L4 | L5 | L6 | L7 |
|---|---|---|---|---|---|---|
| sin datos | sin datos | ≥5 en el peor de {continúa, colapsa} | ≥10 | ≥15 | ≥25 | ≥40 |

*Diseño (100%):* pisos más bajos que otros ejes a propósito, porque depende de CUÁNDO ocurren las oportunidades reales, no de la calidad del estudio.

**Eje 8 -- Evidencia por objetivo (+20% / +50% / +100%)** (positivos REALES observados, mismo piso de 10 para los tres -- la dificultad creciente es consecuencia de que el evento es más raro, no una escalera inventada)
| L1 | L2 | L3 | L4 | L5 | L6 | L7 |
|---|---|---|---|---|---|---|
| 0 en +20% | ≥1 en +20% | ≥10 en +20% | ≥10 en +20% y ≥5 en +50% | ≥10 en +20% y ≥10 en +50% | + ≥5 en +100% | ≥10 en cada uno de los 3 |

*Base:* que +100% exija naturalmente más volumen total que +20% es matemático (tasa base más baja), no inventado -- confirmado con el dato real de esta sesión (+20%≈17%, +100%≈0.4% en el histórico). *Diseño:* en qué nivel exacto se exige cada escalón.

**Eje 9 -- Consistencia** (ventanas de tiempo no solapadas, del Eje 2; "consistente" = los intervalos de Wilson de precisión de cada ventana se solapan)
| L1-L3 | L4 | L5 | L6 | L7 |
|---|---|---|---|---|
| no evaluable | ≥2 ventanas, se muestran sin exigir solape | ≥3 ventanas, últimas 2 solapan | ≥4 ventanas, ≥3 de 4 solapan | ≥6 ventanas, todas solapan (o la excepción está explicada por un régimen distinto del Eje 4) |

*Base:* solapamiento de intervalos es heurística estadística real (más simple de explicar que un test formal de dos proporciones, que sería más potente pero menos transparente en la UI). *Diseño:* número de ventanas por nivel.

**Eje 10 -- Recencia y estabilidad** (ventana más reciente ≈21 días hábiles vs. precisión acumulada, siempre mostradas juntas; alerta si la reciente cae fuera del intervalo histórico)
| L1-L2 | L3 | L4 | L5 | L6 | L7 |
|---|---|---|---|---|---|
| sin ventana propia | ventana definida, muestra chica (se muestra igual) | ≥10 casos propios | ≥20 | ≥30, sin alertas sin explicar en 2 ventanas | ≥30 sostenido, todo el historial de alertas explicado por régimen documentado |

*Base:* misma mecánica de Eje 1/9 reaplicada a la ventana reciente. *Diseño:* tamaño de ventana (21 días).

**Eje 11 -- Validación fuera de muestra** (*walk-forward*: la ventana más reciente, rotando, nunca se usa para calibrar ningún umbral/parámetro)
| L1-L4 | L5 | L6 | L7 |
|---|---|---|---|
| no aplica (no hay volumen para separar calibración/holdout sin vaciar ambos) | holdout con ≥10 casos propios, primera comparación posible | holdout con ≥30 casos, ≥3 de los 6 buckets de timing cubiertos | ≥2 rondas de holdout no solapadas, mayoría de buckets y ambas direcciones cubiertas |

*Base:* walk-forward validation es el método estándar real para series de tiempo (no se puede mezclar pasado/futuro al azar). *Diseño:* tamaño de la ventana de holdout (2-4 semanas) y que sea rotante en vez de fija.

### 8.2 Resumen -- qué tiene base estadística y qué es decisión de diseño

| Con base estadística real | Decisión de diseño (defendible, no la única posible) |
|---|---|
| Piso n≥10 para cualquier intervalo de Wilson | Los valores exactos de cada nivel (30/75/150/300, etc.) |
| Escalamiento 1/√n (progresión más que lineal) | El NÚMERO de niveles intermedios entre pisos estadísticos |
| Regla de "pocos éxitos" para eventos raros -- por qué +100% es intrínsecamente más difícil | En qué nivel exacto se activa cada exigencia de +50%/+100% |
| Calendario bursátil real (5/21/63 días) | El tamaño elegido de cada ventana (21 días para "reciente", 2-4 semanas para holdout) |
| Índice de concentración (tipo Herfindahl) para diversificación | Los cortes de porcentaje de concentración (30%/20%/15%) |
| Solapamiento de intervalos como señal de consistencia | Cuántas ventanas se exigen y si deben solapar TODAS o la mayoría |
| Walk-forward (holdout temporal, no aleatorio) como método correcto para series de tiempo | Que el holdout sea rotante (vs. fijo de una sola vez) |
| Volatilidad/dirección del propio universo como proxy de régimen (auto-referencial, sin dependencia externa) | Terciles, 9 combinaciones, y cuántas se exigen por nivel |

### 8.3 Todavía sin resolver, para la conversación

- ¿El corte de "consistente" (Eje 9) debería usar un test formal de dos proporciones en vez de solo solapamiento de intervalos? Es más potente pero más difícil de explicar en la Cabina.
- ¿El holdout de Eje 11 debería ser una ventana fija (ej. siempre "el último mes") o rotante (se recalibra el corte a medida que pasa el tiempo)?
- ¿Los 9 regímenes del Eje 4 deberían simplificarse a menos categorías (ej. 4: alta/baja volatilidad × alcista/bajista, sin el tercil "medio") para que sea más fácil alcanzar evidencia en todos?

## 9. Estado de esta propuesta -- qué falta después de esta ronda

La sección 7 (versión anterior de este documento) decía que los umbrales
todavía no estaban definidos -- ya lo están, en la sección 8, como
propuesta para revisar. Lo que sigue sin resolver:

- Las 3 preguntas abiertas de la sección 8.3.
- Cualquier cambio de código -- esto sigue siendo solo arquitectura +
  números propuestos para revisar juntos, antes de tocar
  `evolution_panel.py`, `live_integration.py` o crear los módulos nuevos
  que hagan falta (nuevo Learning Store para separar histórico/vivo,
  cálculo real de cada eje, endpoint nuevo para la Cabina).
- Push/deploy: no aplica todavía, no hay código nuevo que desplegar.
