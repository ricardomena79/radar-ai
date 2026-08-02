# RADAR_EXPLOSIVO_V2.md

Documento de diseño técnico del Radar Explosivo v2, basado en evidencia. Sujeto a [ATLAS_CONSTITUTION.md](ATLAS_CONSTITUTION.md) -- ninguna propuesta de este documento se implementa sin pasar antes por el formato de la sección METODOLOGÍA DE PROPUESTAS, y **nada de lo que sigue está implementado todavía**: es un diseño para revisar y aprobar antes de tocar el motor.

**Estado de la evidencia**: **FINAL -- validación completa, 30/30 días** (2026-06-18 a 2026-07-31, ver [VALIDATION_RESULTS.md](VALIDATION_RESULTS.md)). Todas las cifras preliminares citadas en el resto de este documento (marcadas "preliminar", "7/30", "8/30", "11/30" días) quedan **reemplazadas** por la sección "RESULTADOS FINALES" inmediatamente abajo -- se conservan más adelante en el documento solo como registro histórico de cómo evolucionó la evidencia, no como cifras vigentes. Todo salió de correr los módulos de `atlas_live/backtest/` sobre los 30 días ya guardados -- no son estimaciones.

No se modificó `explosive_engine.py`, `explosive_config.json` ni ningún archivo de `/atlas` en ningún momento de esta auditoría, incluyendo el cierre final. Todo el análisis se hizo con módulos auxiliares de solo lectura (`whatif_simulator.py`, `filter_interaction.py`, `rvol_role_comparison.py`) que leen datos ya guardados.

---

# RESULTADOS FINALES DE LA VALIDACIÓN (30/30 días)

## Resumen ejecutivo

| | |
|---|---|
| Universo analizado | ~2.577 símbolos/día (Universo Racional completo) |
| Días analizados | 30 sesiones de mercado (2026-06-18 a 2026-07-31) |
| Reconstrucciones exitosas | 73.123 de 77.310 posibles (94.6%) |
| Errores de datos | 4.187 (5.4% -- símbolos delistados/sin datos, no cuentan como descarte del radar) |
| **Precision@10** | **4.67%** |
| **Precision@20** | **2.33%** |
| **Recall** | **2.33%** |
| Falsos positivos (elegibles que no eran ganadoras reales) | 15 en 30 días |
| Falsos negativos (ganadoras reales descartadas) | 586 de 600 posibles (97.7%) |
| Oportunidades elegibles totales | 29 en 30 días (menos de 1 por día en promedio) |

## Las 14 veces (de 600 posibles) que el radar detectó a una ganadora real

| Fecha | Símbolo | Rank | Subió | Puntaje | Por qué entró |
|---|---|---|---|---|---|
| 2026-06-22 | SAGT | 1 | +32.3% | 76.5 | RVOL 21.1x, alta volatilidad |
| 2026-06-23 | BLZE | 1 | +43.6% | 80.8 | RVOL 6.4x, alta volatilidad |
| 2026-06-23 | SOXS | 2 | +23.9% | 66.0 | RVOL 4.7x, alta volatilidad |
| 2026-06-23 | UVIX | 3 | +10.7% | 61.7 | RVOL 4.0x, alta volatilidad |
| 2026-06-24 | WEN | 1 | +25.6% | 75.1 | RVOL 5.7x, alta volatilidad |
| 2026-06-25 | SAGT | 1 | +19.0% | 65.2 | RVOL 3.5x, alta volatilidad |
| 2026-07-01 | SOXS | 1 | +19.1% | 56.7 | RVOL 2.5x, alta volatilidad |
| 2026-07-02 | YRD | 1 | +59.6% | 77.5 | RVOL 267.8x, alta volatilidad |
| 2026-07-02 | SOXS | 2 | +16.8% | 42.3 | RVOL 2.7x, alta volatilidad |
| 2026-07-07 | BJDX | 1 | +25.0% | 86.7 | RVOL 30.0x, alta volatilidad |
| 2026-07-09 | WRAP | 1 | +48.4% | 87.2 | RVOL 13.4x, alta volatilidad |
| 2026-07-13 | AGEN | 1 | +82.7% | 77.7 | RVOL 61.1x, alta volatilidad |
| 2026-07-30 | NUWE | 1 | +135.4% | 82.2 | RVOL 90.1x, alta volatilidad |
| 2026-07-30 | XRX | 3 | +32.2% | 59.0 | RVOL 3.2x, alta volatilidad |

**Patrón perfecto**: las 14 detecciones tienen RVOL entre 2.5x y 267.8x -- el radar solo atrapa los casos más extremos y obvios (RVOL muy por encima del umbral de 2.0x), nunca un caso "al límite".

## Las 15 ganadoras más grandes que el radar nunca detectó

| Fecha | Símbolo | Subió | Descartada en | RVOL real | Gap real |
|---|---|---|---|---|---|
| 2026-07-23 | FFAI | +9.543% ⚠️ | rvol | 0.06x | +10.212% ⚠️ |
| 2026-07-21 | CCG | +2.537% ⚠️ | liquidity | 0.03x | +3.231% ⚠️ |
| 2026-07-21 | PRPL | +2.141% ⚠️ | liquidity | 0.01x | +2.066% ⚠️ |
| 2026-07-06 | ENFY | +410.0% ⚠️ | price | -- | +410.0% ⚠️ |
| 2026-07-20 | ENFY | +258.8% ⚠️ | price | -- | +258.8% ⚠️ |
| 2026-07-27 | BINI | +75.0% | price | -- | +25.0% |
| 2026-07-29 | GSUN | +65.6% | price | 0.01x | +0.5% |
| 2026-07-10 | MSPR | +56.8% | price | 0.05x | +1.7% |
| 2026-06-18 | BFLY | +55.9% | rvol | 1.14x | +26.3% |
| 2026-07-23 | NVEC | +54.8% | rvol | 0.29x | +23.9% |
| 2026-07-02 | MSPR | +47.4% | price | 0.51x | -15.0% |
| 2026-07-29 | HURN | +40.4% | rvol | 0.15x | +10.5% |
| 2026-07-01 | BINI | +40.0% | price | -- | 0.0% |
| 2026-07-22 | SMCX | +39.4% | rvol | 1.03x | +27.4% |
| 2026-07-16 | CDNA | +35.6% | rvol | 0.90x | +22.4% |

**⚠️ Hallazgo crítico de calidad de datos, no ocultarlo**: los 5 primeros casos (marcados ⚠️) -- movimientos de +75% a +9.543% en un solo día -- son casi con certeza **artefactos de datos** (splits no ajustados correctamente, o tickers muy ilíquidos con precios erráticos en la fuente), no oportunidades de trading reales. Ningún trader minorista puede operar un movimiento de "9.543% en un día" de forma realista -- eso es una señal de dato corrupto, no de mercado. Esto significa que el Recall real (2.33%) probablemente está **subestimado**: estos artefactos ocupan lugares en el top-20 "real" de cada día, desplazando a ganadoras genuinas que si eran detectables. Se recomienda, para la próxima validación, filtrar la verdad de referencia por un techo razonable de variación diaria (ej. descartar >150-200% como probable error de datos) antes de calcular Recall -- no se implementó este filtro ahora para no alterar retroactivamente un resultado ya cerrado.

## Motivo de los 586 descartes (agregado)

| Etapa | Descartes | % del total |
|---|---|---|
| RVOL | 330 | 56.3% |
| Liquidez | 195 | 33.3% |
| Precio | 60 | 10.2% |
| Volatilidad | 1 | 0.2% |
| Movimiento / Tamaño | 0 | 0% |

## Comparación de escenarios sobre RVOL -- CONFIRMADA con datos completos

| Escenario | Precision@10 | Precision@20 | Recall | Falsos positivos |
|---|---|---|---|---|
| 1. Radar actual | 4.67% | 2.33% | 2.33% | 15 |
| 2. Sin RVOL (gate y score en 0) | 28.33% | 19.83% | 46.33% | 8.084 |
| 3. Mejor umbral probado (0.5x) | 14.67% | 7.33% | 7.33% | 90 |
| 4. RVOL con menos peso (mismo gate) | 4.67% | 2.33% | 2.33% (sin cambio, mismo motivo que antes: muy pocas elegibles para que el reordenamiento importe) | 15 |
| **5. RVOL solo como factor de puntuación** | **30.67%** | **20.17%** | **46.33%** | 8.084 |

**La conclusión preliminar se confirma en su totalidad, con una muestra 4x más grande**: quitar a RVOL del rol de filtro excluyente (escenarios 2 y 5) multiplica Precision@10 por ~6,5x, Precision@20 por ~8,6x y Recall por ~20x, simultáneamente. El escenario 5 (RVOL como factor, no como filtro) sigue siendo levemente mejor que eliminarlo del todo.

## Filtros: poder individual y contribución marginal -- CONFIRMADO con datos completos

| Filtro | Recall individual (solo) | Contribución marginal a Recall (dado el resto) |
|---|---|---|
| RVOL | 2.83% | **-44.0pp** (con los 6 gates activos) |
| Liquidez | 58.17% | **-24.8pp** (una vez que RVOL está fuera) |
| Movimiento | 84.83% | -4.7pp (sin RVOL) |
| Tamaño | 93.00% | -3.3pp (sin RVOL) |
| Volatilidad | 94.17% | -2.0pp (sin RVOL) / -0.17pp (con los 6) |
| Precio | 89.50% | -0.67pp (sin RVOL) / -0.33pp (con los 6) |

Orden de impacto real, confirmado: **RVOL >> Liquidez >> Movimiento ≈ Tamaño > Volatilidad ≈ Precio**.

## Explosive DNA -- confirmado con 600 observaciones (vs. 140 preliminares)

Separación explosivas vs. resto del universo: Cambio%=98.3%, Gap%=97.0%, RVOL=88.5%, Volatilidad=76.6%, Volumen$=65.8%, Market Cap=22.6%, Precio=17.7% (inverso -- las explosivas son más baratas). **El vacío de datos de market cap persiste a escala completa: 73.2% de las 600 observaciones explosivas reales siguen con `market_cap=None`** -- no se resolvió, sigue siendo la Propuesta 2 pendiente.

---

# INFORME EJECUTIVO

**1. Lo que aprendió el Radar Explosivo**: que la definición actual de RVOL (volumen acumulado en los primeros 10 minutos comparado contra el promedio de UN DÍA COMPLETO) es matemáticamente casi imposible de superar para cualquier acción, incluidas las que sí explotan de verdad -- ni las 14 detecciones reales superaron holgadamente el umbral (todas, sin excepción, tuvieron RVOL anómalamente alto, pero el filtro descartó a 330 ganadoras reales con RVOL insuficiente bajo esa misma definición). El radar aprendió, en efecto, "solo mira los casos más extremos posibles" -- no un rango razonable de oportunidades.

**2. Filtros realmente útiles**: Volatilidad y Movimiento -- casi nunca descartan a una ganadora real (1 y 0 descartes respectivamente en 600 casos) y sí tienen separación estadística real en Explosive DNA. El precio, tal como se usa hoy (piso mínimo), tiene bajo impacto marginal pero el *concepto* de precio (como techo, no piso) mostró valor real en la comparación de escenarios.

**3. Filtros que perjudicaron el rendimiento**: RVOL, de lejos (56.3% de los descartes, -44pp de Recall marginal). Liquidez en segundo lugar, mucho más atrás pero real (33.3% de los descartes, -24.8pp una vez que RVOL sale de la ecuación).

**4. Cambios recomendados para aumentar Precision@10/@20/Recall** (ver detalle completo con formato PROBLEMA/HIPÓTESIS/... en la Parte 1 y en las 12 mejoras ya documentadas más abajo): redefinir el rol de RVOL (de filtro a factor de puntuación, o redefinir su línea base ajustada al tiempo) es, con estos datos, la única palanca que por sí sola cambia el orden de magnitud del resultado. Todo lo demás (Liquidez, Gap separado, techo de precio) es de segundo orden en comparación.

**5. Orden por impacto esperado y dificultad**: sin cambios respecto al orden ya documentado en la Parte 1 -- la validación completa **confirmó**, no modificó, esa priorización. Ver tabla de 12 mejoras más abajo.

**No se implementó ningún cambio.** Esta sección cierra la evidencia; la decisión de qué implementar y en qué orden queda pendiente de tu aprobación, siguiendo la METODOLOGÍA DE PROPUESTAS.

---

# PARTE 1 -- DISEÑO TÉCNICO: RADAR EXPLOSIVO V2

## 1. Arquitectura propuesta

La arquitectura actual (Etapa A de elegibilidad → Etapa B de puntaje ponderado por factores enchufables → Etapa C de ajuste por tamaño, todo configurable vía `explosive_config.json`) **no tiene evidencia en contra** -- ningún hallazgo de la auditoría cuestiona el diseño en tres etapas ni el registro de factores. Por Principio 7 de la Constitución (la simplicidad vale más que agregar indicadores), v2 **mantiene esta arquitectura** y propone cambios puntuales dentro de ella, no un rediseño desde cero:

- **a) Separar el gate "movement" en dos gates independientes**: uno de Gap % y uno de Cambio %, en vez del `max(|gap|, |cambio|)` combinado de hoy. Evidencia: Gap % aislado (Precision@10=21.8%) supera ampliamente al gate combinado actual (11.8%) -- ver [Mapa completo, fila Gap](#mapa-completo-auditoría-de-los-7-filtros-principales).
- **b) Redefinir el rol de RVOL** -- de gate excluyente rígido a línea base ajustada al tiempo transcurrido, y/o a factor de puntuación puro sin poder de exclusión. **Decisión pendiente, bloqueada hasta cerrar los 30 días** (ver Parte 1.5 y la sección "RVOL: CONGELADO").
- **c) Agregar un factor/gate de techo de precio** -- hoy `price` solo tiene un piso (`min_price`), nunca un techo. Evidencia: un techo ~$30 mejoró Precision@20 en 56% cuando se probó sin RVOL activo.
- **d) Extender la infraestructura de validación** (no el motor en vivo): persistir `momentum_score` y `vwap_distance_score` por símbolo en `historical_scan.py`, y resolver el vacío de market cap (74.3% de las ganadoras reales quedaron con `market_cap=None`). Sin esto, v2 no puede auditarse ni calibrarse con confianza en los factores de momentum/VWAP/tamaño.
- **e) Liquidez, Volatilidad y el gate de Market Cap se mantienen sin cambios de diseño por ahora** -- Volatilidad tiene evidencia de estar bien calibrada; Liquidez tiene evidencia de ser un problema pero todavía no se investigó con el mismo rigor que RVOL (es la siguiente candidata, no una decisión tomada); Market Cap está subestimado por el vacío de datos, no se puede juzgar su diseño todavía.

## 2. Mejoras priorizadas según la evidencia obtenida

Consolida las 11 propuestas ya documentadas en la Parte 2 más los 2 hallazgos nuevos de esta ronda de auditoría (Gap aislado, Liquidez como siguiente cuello de botella). Orden por impacto medido, no por intuición:

| # | Mejora | Evidencia que la respalda | Estado |
|---|---|---|---|
| 1 | Redefinir el rol de RVOL | Recall +42pp al removerlo del gate (comparación de 5 escenarios) | **Congelada hasta 30 días** |
| 2 | Investigar Liquidez con el mismo rigor que RVOL | Contribución marginal a Recall de -26.8% sin RVOL, la mayor de los 5 restantes | No iniciada (deliberadamente, para no mover 2 variables a la vez) |
| 3 | Separar Gap de "movement" como gate propio | P@10=21.8% aislado vs. 11.8% del gate combinado | Diseñada, no implementada |
| 4 | Agregar techo de precio como factor/gate nuevo | +56% Precision@20 (sin RVOL, techo $30) | Diseñada, no implementada |
| 5 | Resolver vacío de datos de market cap en la validación | 74.3% de ganadoras reales con `market_cap=None` | Diseñada, no implementada |
| 6 | Extender persistencia (momentum, VWAP) para futuras corridas | Bloquea auditar Momentum y recalibrar pesos de Etapa B | Diseñada, no implementada |
| 7 | Recalibrar pesos de Etapa B con separación real de Explosive DNA | Cambio%/Gap% separan más que RVOL pese a pesar menos | Depende de la mejora 6 |
| 8 | Recalibrar curva de penalización por tamaño | Depende de resolver la mejora 5 primero | Bloqueada por la mejora 5 |
| 9 | Snapshot configurable (probar otros minutos post-apertura) | Nunca validado, supuesto de diseño inicial | Diseñada, requiere corridas históricas nuevas |
| 10 | Nuevo factor de ruptura intradía (distancia al máximo) | Señal de momentum no capturada hoy | Diseñada, requiere corrida nueva |
| 11 | Reconstrucción histórica de sector/money flow | Factor de 10% de peso nunca puesto a prueba | Diseñada, prioridad baja |
| 12 | Short interest / float histórico real | Predictor clásico de short squeeze, no capturado hoy | **Requiere Atlas Core** -- ver punto 6 |

## 3. Cambios de bajo riesgo

Ninguno de estos toca la elegibilidad ni el puntaje que ve el usuario final hoy -- son extensiones de infraestructura o mediciones, no cambios de comportamiento:

- Mejora 6 (extender persistencia de `historical_scan.py`) -- agrega campos, no modifica ningún cálculo existente.
- Mejora 5 (resolver vacío de market cap) -- mejora la calidad del dato, no cambia ninguna fórmula.
- Mejora 2 (investigar Liquidez) -- es medición, no modificación, igual que se hizo con RVOL.
- Herramientas ya construidas (`whatif_simulator.py`, `filter_interaction.py`, `rvol_role_comparison.py`) -- de solo lectura, ya en uso, sin riesgo adicional.

## 4. Cambios de alto impacto

- Mejora 1 (rol de RVOL) -- el de mayor impacto medido de todos (+42pp de Recall en la comparación preliminar), pero también el de mayor riesgo si se calibra mal (ver sección 7).
- Mejora 3 (separar Gap) -- segundo mayor impacto medido de forma aislada.
- Mejora 4 (techo de precio) -- impacto real medido (+56% P@20) aunque en un contexto sin RVOL, hay que reconfirmar con la config final.
- Mejora 2 (Liquidez), una vez investigada con el mismo rigor -- candidata a impacto comparable al de RVOL, todavía no confirmada.

## 5. Cambios que deben esperar a completar los 30 días

- **Cualquier decisión sobre RVOL** -- explícitamente congelada por instrucción directa, ver [DECISION_LOG.md](DECISION_LOG.md).
- La comparación de 5 escenarios de RVOL debe recalcularse completa antes de decidir la mejora 1.
- Mejora 7 (recalibrar pesos de Etapa B) -- necesita el dataset completo, no solo la persistencia extendida (mejora 6), para tener suficiente volumen de observaciones.
- Confirmar que Liquidez sigue siendo el segundo cuello de botella con el dataset completo antes de abrir formalmente la mejora 2 (con 30 días en vez de 11, el orden relativo podría cambiar, aunque la magnitud observada hasta ahora es grande).
- Mejora 9 (snapshot alternativo) -- mejor esperar a tener resueltas las decisiones de RVOL/Liquidez antes de multiplicar variables en juego.

## 6. Cambios que requieren un nuevo proveedor de datos

**Solo la mejora 12** (short interest / float histórico real). El `DataProvider`/`DataCollector` actuales (`atlas/data/providers/`, `atlas/data/collectors/`) no exponen ese dato en absoluto -- `Quote` no tiene el campo. Implica trabajo de arquitectura en la fase "Cambio de proveedor de datos" del roadmap general (`ATLAS_ROADMAP.md`, fase 6), no solo un ajuste al Radar Explosivo. Es la única de las 12 mejoras que toca `/atlas`.

Todas las demás mejoras (1 a 11) son 100% implementables dentro de `atlas_live`, sin tocar Atlas Core.

## 7. Riesgos de cada modificación

| Mejora | Riesgo principal | Severidad |
|---|---|---|
| 1. Rol de RVOL | Un ajuste mal calibrado podría reemplazar "descarta casi todo" por "no descarta nada" -- de 4 falsos positivos a miles si no se agrega algún mecanismo de acotar el pool elegible | Alto |
| 2. Investigar Liquidez | Ninguno directo (es medición) -- riesgo indirecto de encontrar un segundo hallazgo tan grande como RVOL y no tener capacidad de atender ambos a la vez | Bajo |
| 3. Separar Gap | Podría solaparse con el factor "gap" que ya existe en la Etapa B -- hay que verificar que el gate nuevo no sea redundante con el factor existente | Medio |
| 4. Techo de precio | Podría excluir explosiones legítimas de precio más alto (menos común pero reales); solaparse parcialmente con Market Cap | Medio |
| 5. Resolver vacío de market cap | Ninguno directo -- es infraestructura de validación, no toca el motor en vivo | Bajo |
| 6. Extender persistencia | Ninguno -- aditivo, no cambia ningún cálculo existente | Bajo |
| 7. Recalibrar pesos Etapa B | Sobreajuste a los 30 días específicos si no se valida luego en tiempo real (paso 7 de la Metodología de Propuestas) | Medio |
| 8. Recalibrar curva de tamaño | Igual que 7, más el riesgo de estar calibrando sobre datos de market cap todavía incompletos si no se resolvió la mejora 5 antes | Medio |
| 9. Snapshot alternativo | Costo alto en tiempo/API por cada snapshot adicional a probar; bajo riesgo de calidad | Bajo (costo) / Bajo (calidad) |
| 10. Factor de ruptura | Requiere nueva corrida histórica; podría solaparse con VWAP distance | Medio |
| 11. Sector/money flow histórico | Complejidad de cómputo nueva (agregación por sector); impacto desconocido, podría no justificar el esfuerzo | Medio |
| 12. Short interest/float real | Toca Atlas Core -- el único con riesgo de romper otras partes del sistema que consumen `DataCollector` | **Alto** |

## 8. Orden recomendado de implementación

Ninguno de estos pasos se ejecuta sin aprobación explícita y sin pasar por el formato de propuesta de la Constitución. El orden respeta: no mover dos variables grandes a la vez, infraestructura antes que algoritmo, y validación histórica antes que validación en tiempo real (Metodología de Propuestas).

1. **Cerrar los 30 días de validación** (en curso, sin tocar).
2. **Mejora 6** (extender persistencia) y **Mejora 5** (resolver vacío de market cap) -- infraestructura, bajo riesgo, se pueden hacer en paralelo entre sí sin esperar nada más.
3. **Recalcular la comparación de 5 escenarios de RVOL con los 30 días completos** -- decisión formal sobre la Mejora 1.
4. **Investigar Liquidez con el mismo rigor que RVOL** (Mejora 2) -- recién después de cerrar RVOL, para no analizar dos variables en movimiento a la vez.
5. **Implementar y validar históricamente, en un solo lote**: Mejora 3 (separar Gap), Mejora 4 (techo de precio) -- agrupadas porque ambas ya tienen evidencia fuerte y no dependen de decisiones pendientes.
6. **Con la persistencia ya extendida (paso 2)**: Mejora 7 (recalibrar pesos de Etapa B) y, si se resolvió el vacío de market cap, Mejora 8 (recalibrar curva de tamaño).
7. **Validar en tiempo real** todo lo adoptado en los pasos 3 a 6, antes de considerarlo permanente (paso 7 de la Metodología de Propuestas) -- Objetivo Nº3, no cubierto todavía.
8. **Mejoras 9, 10, 11** -- prioridad menor, se abordan si los pasos anteriores no agotan el retorno esperado.
9. **Mejora 12** (short interest/float) -- solo si se prioriza la fase "Cambio de proveedor de datos" del roadmap general; es la única que sale del alcance de `atlas_live`.

---

# PARTE 2 -- AUDITORÍA Y EVIDENCIA COMPLETA

## AUDITORÍA TÉCNICA (8 puntos)

### 1. Qué variables contribuyen más a detectar acciones explosivas

Según la separación estadística de Explosive DNA (fracción del grupo de control que queda por debajo de la mediana del grupo explosivo -- 100% = separación perfecta):

| Variable | Separación | Mediana explosivas | Mediana control |
|---|---|---|---|
| Cambio % (snapshot) | 98.7% | +6.09% | +0.38% |
| Gap % | 97.7% | +3.26% | +0.04% |
| RVOL | 90.9% | 0.14x | 0.04x |
| Volatilidad (score ATR) | 74.3% | 100.0 | 61.9 |
| Volumen en $ | 69.5% | $6.3M | $1.7M |

Las 5 variables que ya usa el motor son, en efecto, discriminativas -- la elección original de factores no fue arbitraria. Cambio% y Gap% separan incluso más que RVOL, pese a que RVOL tiene el mayor peso (25%) en la Etapa B.

### 2. Qué variables aportan muy poco

**Market Cap**, tal como se usa hoy: separación 20.4%, y **con una limitación de datos severa** (ver punto 7) que hace la medición poco confiable todavía. **Precio** tiene una relación real pero débil e inversa (ver punto 4) que hoy no se traduce en ningún factor. Ninguna variable actual mostró separación negativa o nula -- ninguna es "ruido puro" -- pero Market Cap es la más débil de las medidas con datos suficientes.

### 3. Qué variables generan falsos positivos

De los símbolos que el radar aprobó pero que NO fueron ganadoras reales del día (7 días, muestra chica: 4 casos), **el 100% pasó el filtro de RVOL "raspando"** el umbral (dentro del 25% de margen sobre 2.0x). Es la única variable con evidencia de sobre-permisividad hasta ahora -- pero la muestra es de apenas 4 casos, así que esto necesita confirmarse con más días.

### 4. Qué variables provocan falsos negativos

**RVOL, por lejos**: de las oportunidades reales (top-20 ganadoras del día) que el radar no detectó, el filtro de RVOL fue el responsable del **59.8%** de los descartes (79 de 132 en 7 días), seguido de liquidez (34.1%, 45 casos) y precio (7.6%, 10 casos). **Ningún** descarte se atribuyó a movimiento, volatilidad o tamaño -- las ganadoras reales casi nunca llegan siquiera a esas etapas, quedan eliminadas antes.

Hallazgo adicional no capturado por ningún factor actual: **precio** -- las explosivas tienen mediana de $11.43 contra $48.42 del control (83% del control por encima de la mediana explosiva). Ningún factor usa el precio directamente hoy (solo Market Cap, que es un proxy indirecto y con datos incompletos).

### 5. Qué filtros son demasiado estrictos

Con la heurística de "cercanía al umbral" (ver `judge_exclusion()`), **ninguno** de los descartes de RVOL calificó como "cerca" del umbral -- estaban lejos (mediana explosiva 0.14x vs. umbral 2.0x: **93% de distancia**). Esto es una señal importante: **el problema no es que el umbral de RVOL esté "un poco alto", es que la definición misma de RVOL (volumen acumulado vs. promedio de UN DÍA COMPLETO) es estructuralmente incompatible con medir 10 minutos después de la apertura.** Ni siquiera las ganadoras reales alcanzan RVOL cercano a 2.0x tan temprano.

### 6. Qué filtros son demasiado permisivos

RVOL también aparece del lado permisivo (punto 3), aunque con una muestra todavía chica. Esto no es contradictorio: la separación relativa (90.9%) confirma que RVOL SÍ distingue win de no-win en términos relativos, incluso con valores absolutos minúsculos para ambos grupos -- el problema es de escala/definición, no de que la variable no sirva.

### 7. Qué información importante no se está usando

- **Nivel de precio absoluto** -- señal real (punto 4), sin factor propio hoy.
- **Market cap real para el 74.3% de las ganadoras explosivas**: en la reconstrucción histórica, la mayoría de los símbolos realmente explosivos quedaron con `market_cap=None` (no se pudo obtener su capitalización vía `fast_info`, probablemente por rate-limiting o por ser nombres de baja cobertura en Yahoo Finance). Esto no es un defecto de diseño del Radar -- es un vacío en la infraestructura de datos de la validación -- pero significa que hoy el factor de tamaño y el gate de "size" están operando a ciegas para 3 de cada 4 ganadoras reales.
- **Distancia al máximo intradía (breakout)**: no capturado por ningún factor actual.
- **Sector / money flow histórico**: excluido deliberadamente de esta validación por simplicidad (ver [DECISION_LOG.md](DECISION_LOG.md)), nunca puesto a prueba.
- **Short interest, float histórico real, catalizadores de noticias, actividad de opciones**: no disponibles en la capa de datos actual (`Quote` no tiene estos campos).

### 8. Qué mejoras podrían aumentar Precision@10, Precision@20 y Recall

Ver sección siguiente -- cada una en el formato obligatorio de la Constitución.

---

## ANÁLISIS DE INTERACCIÓN ENTRE FILTROS

Ampliación pedida explícitamente: no basta con medir cada filtro por separado, hay que entender cómo interactúan. Metodología: se enumeraron las **64 combinaciones posibles** de los 6 gates de la Etapa A (activo/inactivo cada uno), reevaluando las métricas ya guardadas contra cada combinación (`atlas_live/backtest/filter_interaction.py`, construido sobre `whatif_simulator.py` -- cero descargas nuevas, cero cambios al motor). Evidencia sobre los mismos 7 días preliminares; se repetirá con los 30 días completos.

**Advertencia metodológica que hay que leer antes que los números**: con solo 7 días y apenas 4 falsos positivos / ~134 falsos negativos totales en la muestra, cada símbolo individual mueve los porcentajes en saltos grandes (1/70). Las conclusiones CUALITATIVAS (qué filtro domina, qué filtros no aportan nada dado el resto) son sólidas porque son consistentes y grandes en magnitud. Los valores numéricos EXACTOS de precisión/recall por combinación van a cambiar con los 30 días y no deben tomarse como definitivos todavía.

### Hallazgo central de esta sección

**RVOL es, hoy, el único filtro que realmente decide qué pasa y qué no.** Los otros 5 gates (price, liquidity, movement, volatility, size) tienen **contribución marginal de exactamente 0.0** sobre Precision@10/@20/Recall cuando se los remueve UNO A LA VEZ manteniendo los demás 5 activos (ablation "leave-one-out"). Esto no significa que esos 5 filtros sean malos -- significa que, dado que RVOL ya es tan estricto, casi ningún símbolo sobrevive a RVOL sin *también* cumplir los otros 5 gates de por sí. RVOL, tal como está definido hoy, ya hace casi todo el trabajo de filtrado -- y ese trabajo es, según la Propuesta 1, un trabajo mal calibrado (compara volumen de 10 minutos contra promedio de día completo). **Esto implica que todo el resto de este análisis de interacción debe volver a correrse después de corregir RVOL (Propuesta 1) -- hoy está confundido por la dominancia de un solo filtro.**

### 1. ¿Qué combinación de filtros aporta mayor Precision@10?

Top de la malla de 64: combinaciones que incluyen **`movement`** (gap/cambio %) SIN `rvol` ni `liquidity`: `['movement']` solo, P10=0.129, muy por encima de la configuración real de los 6 gates (P10=0.086). **Pero con una salvedad operativa importante**: esa combinación deja pasar 4.728 falsos positivos en 7 días (contra 4 de la config real) -- técnicamente "más precisa en el top 10" simplemente porque deja pasar tantos candidatos que el top 10 por puntaje tiene más chances de incluir a un ganador real por volumen de intentos. No es una recomendación de usar `movement` solo; es evidencia de que **`movement` (gap % / cambio %) es, individualmente, el filtro con mejor relación señal/ruido de los 6**, algo que ya sugería la separación de Explosive DNA (Gap%=97.7%, Cambio%=98.7%, las dos separaciones más altas de todas las variables).

### 2. ¿Qué combinación aporta mayor Recall?

El conjunto vacío (ningún filtro activo) da Recall=97.1% -- trivial y sin valor práctico (deja pasar prácticamente todo el universo). La combinación no trivial con mejor Recall es **`['volatility']` sola**: R=96.4%, con FP=10.072 (inviable en la práctica, pero confirma que el filtro de volatilidad, solo, casi nunca excluye a una ganadora real -- es el gate menos responsable de falsos negativos, consistente con que en el análisis anterior 0 de los descartes de ganadoras reales se atribuyeron a volatilidad).

### 3. ¿Qué filtros son redundantes (dado el resto)?

**Liquidity, movement, volatility y size** -- los 4 tienen contribución marginal de 0.0 sobre las 3 métricas cuando se remueven de a uno, dado que los otros 5 (incluyendo RVOL) están activos. Es una redundancia **condicional a que RVOL sea tan estricto como es hoy** -- no una redundancia estructural de esos filtros en sí.

### 4. ¿Qué filtros pierden importancia cuando otro ya está presente?

Del análisis par a par (interacción sobre Precision@10): el único par con interacción no nula en esta muestra es **price × RVOL** (overlap ±0.0143 en ambas direcciones) -- indica que parte de lo que aporta el filtro de precio ya lo captura RVOL, y viceversa, en la porción de casos donde ambos están activos. El resto de los 15 pares no mostró interacción medible con esta muestra (consistente con el hallazgo central: RVOL absorbe casi toda la señal, dejando poco margen para medir interacciones entre los demás).

### 5. ¿Qué filtros tienen mayor poder predictivo individual (solos, sin los demás)?

| Filtro solo | Precision@10 | Recall | Falsos positivos (7 días) |
|---|---|---|---|
| **movement** | **0.129** | 0.886 | 4.728 |
| liquidity | 0.100 | 0.614 | 8.070 |
| rvol | 0.100 | **0.050** | **17** |
| volatility | 0.100 | 0.964 | 10.072 |
| size | 0.100 | 0.921 | 15.055 |
| price | 0.086 | 0.914 | 16.079 |

`movement` es el único filtro con Precision@10 individual por encima del resto. `rvol` es, por lejos, el más restrictivo en solitario (FP=17, dos órdenes de magnitud menos que cualquier otro) -- coherente con ser también el que más falsos negativos genera.

### 6. ¿Qué combinaciones generan la mayor cantidad de falsos positivos?

Las combinaciones con pocos o ningún gate activo: conjunto vacío (FP=16.291), `['price']` solo (FP=16.079), `['size']` solo (FP=15.055). Ningún hallazgo sorprendente aquí -- confirma que, sin filtros, el universo completo pasa.

### 7. ¿Qué combinaciones generan la mayor cantidad de falsos negativos?

Todas las combinaciones que incluyen **`rvol` + `price`** (con o sin los demás) empatan en el peor Recall posible de la muestra (FN=134 -- prácticamente todas las ganadoras reales descartadas). Confirma, desde el ángulo de combinaciones en vez de filtros individuales, que RVOL es el cuello de botella dominante en cualquier combinación donde participa.

### 8. ¿Qué variables nuevas serían de mayor impacto?

Se probó empíricamente (no por intuición) un candidato concreto ya sugerido por Explosive DNA: **un techo de precio**. Resultado:

| Configuración | Precision@10 | Precision@20 | Recall |
|---|---|---|---|
| 5 gates activos sin RVOL, sin techo | 0.114 | 0.064 | 0.507 |
| + techo de precio $15 | 0.157 | 0.086 | 0.229 |
| + techo de precio $20 | 0.143 | 0.093 | 0.264 |
| + techo de precio $30 | 0.143 | **0.100** | 0.314 |
| + techo de precio $50 | 0.114 | 0.086 | 0.364 |

**Un techo de precio alrededor de $30 mejora Precision@20 en 56% (0.064 → 0.100) frente a los 5 gates sin RVOL**, a costa de Recall. Cuando se prueba el mismo techo de precio SOBRE la configuración real de 6 gates (con RVOL activo), el efecto es **nulo** -- porque RVOL ya deja pasar tan pocos símbolos que ninguno superaba el techo de precio de todas formas. Esta es la evidencia más concreta de esta sección: **cualquier variable nueva que se pruebe hoy, con RVOL en su forma actual, va a parecer inútil -- no porque lo sea, sino porque RVOL nunca deja que el resto del embudo procese suficientes candidatos como para que su efecto se note.** Corregir RVOL (Propuesta 1) es un prerrequisito para poder evaluar honestamente cualquier otra variable nueva, incluyendo el techo de precio.

---

## MATRIZ DE IMPACTO POR FILTRO

Basada en la malla de 64 combinaciones (7 días, preliminar). "Contribución" = delta marginal leave-one-out (dado que los otros 5 están activos, config real). "Riesgo de falsos positivos/negativos" = cuánto empeora esa métrica si se remueve el filtro (mayor valor absoluto = el filtro es más responsable de contenerla). "Dependencia" = del análisis par a par. "Prioridad de mejora" = juicio del Arquitecto, combinando todo lo anterior con la auditoría de la sección previa -- no es una fórmula automática, está justificada caso por caso.

| Filtro | Poder predictivo individual (P10 solo) | Contrib. a P@10 | Contrib. a P@20 | Contrib. a Recall | Riesgo falsos (+) si se quita | Riesgo falsos (-) si se quita | Dependencia de otros filtros | Prioridad de mejora |
|---|---|---|---|---|---|---|---|---|
| **RVOL** | 0.100 (pero Recall solo=0.050, el más estricto) | -0.029 | -0.021 | **-0.464** (el mayor, por lejos) | Sube +2.178 FP si se quita (contiene muchísimo ruido) | Baja -65 FN si se quita (es el mayor responsable de excluir ganadoras reales) | Overlap medible con `price` (±0.0143) | **1 -- crítica.** Redefinir la línea base (Propuesta 1) antes que cualquier otro cambio. |
| **movement** (gap/cambio %) | **0.129 (el más alto)** | 0.000 (hoy tapado por RVOL) | 0.000 | 0.000 | Sin datos suficientes (tapado por RVOL) | Sin datos suficientes | Sin interacción medible con esta muestra | **2 -- alta, pendiente de re-medir sin RVOL dominando.** Es el filtro individualmente más fuerte; su verdadero valor está oculto hoy. |
| **price** (nivel absoluto, no existe como factor hoy) | No es un filtro hoy -- se probó como candidato nuevo | Mejora P@20 +56% cuando se prueba sin RVOL | +56% (con techo $30, sin RVOL) | Reduce recall (trade-off esperado) | N/A (no implementado) | Overlap con RVOL (±0.0143) | **3 -- alta como variable NUEVA**, condicionada a resolver RVOL primero para medirla limpiamente (Propuesta 4). |
| **liquidity** | 0.100 | 0.000 | 0.000 | 0.000 | Sin datos suficientes (tapado por RVOL) | Sin datos suficientes | Sin interacción medible | 4 -- baja por ahora; re-evaluar después de corregir RVOL. |
| **volatility** | 0.100 | 0.000 | 0.000 | 0.000 (Recall individual más alto: 0.964) | Sin datos suficientes | Bajo (0 descartes de ganadoras atribuidos a este gate en la auditoría anterior) | Sin interacción medible | 5 -- baja; es el filtro menos responsable de falsos negativos, probablemente ya bien calibrado. |
| **size** | 0.100 | 0.000 | 0.000 | 0.000 (Recall individual 0.921) | Sin datos suficientes | Bajo (0 descartes atribuidos en la auditoría anterior) | Sin interacción medible | 6 -- baja; revisar solo si el vacío de market cap (Propuesta 2) cambia el panorama. |

**Nota de honestidad**: las celdas "sin datos suficientes" no son un error -- son la consecuencia directa del hallazgo central de esta sección (RVOL tapa la señal de los demás filtros). No se rellenaron con estimaciones para que la tabla se viera completa; se dejan en blanco hasta que haya evidencia real que las respalde, tal como pide la Constitución (Principio 1).

---

## COMPARACIÓN DE ESCENARIOS SOBRE EL ROL DE RVOL

Pedido explícito: comparar objetivamente 5 escenarios para decidir la base del Radar Explosivo v2, **sin implementar ninguno todavía**. Evidencia preliminar sobre **8 de los 30 días** (se repetirá al cerrar la validación completa). Nuevo módulo de solo lectura: `atlas_live/backtest/rvol_role_comparison.py`, construido sobre `whatif_simulator.py` -- cero cambios en `explosive_engine.py` ni en `explosive_config.json`.

**Limitación que aplica a los escenarios 2, 4 y 5** (cualquiera que recalcule el puntaje de ranking): el puntaje real de la Etapa B combina 6-7 factores; solo 3 se pueden reconstruir desde los datos ya guardados (RVOL, gap, volatilidad -- ver docstring del módulo). Los puntajes de esos escenarios son **parciales**, útiles para comparar entre sí, no para predecir el desempeño exacto de una implementación real con los 6-7 factores completos.

| Escenario | Precision@10 | Precision@20 | Recall | Falsos positivos | Falsos negativos | Elegibles (8 días) |
|---|---|---|---|---|---|---|
| **1. Radar actual** (puntaje real) | 7.5% | 3.75% | 3.75% | 4 | 154 | 10 |
| **2. Sin RVOL** (gate y score en 0) | **33.75%** | **21.88%** | **48.13%** | 2.357 | 83 | 2.434 |
| **3. Umbral RVOL 0.5x** | 20.0% | 10.0% | 10.0% | 34 | 144 | -- |
| 3. Umbral RVOL 1.0x | 15.0% | 7.5% | 7.5% | 20 | 148 | -- |
| 3. Umbral RVOL 1.5x | 10.0% | 5.0% | 5.0% | 9 | 152 | -- |
| 3. Umbral RVOL 2.0x (actual) | 7.5% | 3.75% | 3.75% | 4 | 154 | -- |
| 3. Umbral RVOL 2.5x / 3.0x | 7.5% | 3.75% | 3.75% | 0 | 154 | -- |
| **4. RVOL con peso 0.10** (mismo gate, ranking parcial) | 7.5% | 3.75% | 3.75% | 4 | 154 | 10 |
| **5. RVOL solo como factor** (gate off, peso normal en score parcial) | **36.25%** | **21.25%** | **48.13%** | 2.357 | 83 | 2.434 |

### Lectura de los resultados

**Los escenarios 2 y 5 (quitar RVOL como filtro excluyente) mejoran Precision@10, Precision@20 Y Recall simultáneamente** frente al radar actual -- no es un trade-off donde se gana una métrica a costa de otra, las tres mejoran a la vez. Esto es una señal fuerte y consistente, no marginal: Recall pasa de 3.75% a 48.13% (13x), Precision@10 de 7.5% a >33%.

**El escenario 5 (RVOL como factor de puntuación, sin ser filtro) es marginalmente mejor que el escenario 2 (RVOL eliminado por completo)** en Precision@10 (36.25% vs 33.75%), con Recall idéntico. Esto sugiere que RVOL conserva valor real como señal de *ranking* -- coherente con la separación de 90.9% medida en Explosive DNA -- aunque no debería seguir siendo un filtro que descarta candidatos por completo.

**El escenario 3 (barrido de umbral) muestra una relación monótona y suave**: bajar el umbral mejora las tres métricas de forma gradual, pero ningún punto del barrido (ni siquiera 0.5x) se acerca a la mejora que se obtiene sacando a RVOL del rol de filtro directamente (escenarios 2/5). Esto es evidencia de que **el problema no es "el umbral es un poco alto" -- es que RVOL no debería excluir candidatos en absoluto, dado cómo está definido hoy** (comparación contra promedio de día completo a los 10 minutos de la apertura, ver Propuesta 1).

**El escenario 4 (mismo gate, solo cambia el peso de RVOL en el ranking) no mostró ningún cambio** frente al escenario 1. No es un resultado nulo por error -- con el gate actual solo hay 10 símbolos elegibles en 8 días (1.25/día), muy por debajo de K=10 -- reordenar un puñado de candidatos no puede cambiar cuántos entran al top 10 cuando casi todos ya entraban. Este escenario solo será informativo una vez que el gate deje de ser tan restrictivo (es decir, después de resolver la Propuesta 1).

**Sobre los falsos positivos "altos" (2.357) de los escenarios 2 y 5**: no deben leerse como que el radar "empeora" -- son consecuencia mecánica de que, sin el gate de RVOL, casi todo el universo pasa a ser "elegible" (2.434 de ~16.000+ observaciones en 8 días). Precision@10/@20 es la métrica diseñada exactamente para no depender del tamaño del conjunto elegible (mide calidad del top-K, no del pool completo) -- y esa métrica mejoró, no empeoró. Un radar de producción real necesitaría, de todas formas, ALGÚN mecanismo de acotar el pool elegible a un tamaño operable (aunque no sea RVOL con su definición actual) -- eso es tema de la Propuesta 1 (redefinir, no eliminar, la señal de volumen).

### Conclusión de esta comparación (para decidir la base de v2)

Con la evidencia de 8 días, el orden de preferencia para la base de v2 es: **(5) RVOL como factor de puntuación > (2) RVOL eliminado por completo > (3) ajustar el umbral > (1) mantener como está**. Ningún cambio se implementó -- esta tabla existe para decidir con evidencia cuando cierren los 30 días, tal como se pidió. Se recomienda que el equipo confirme esta conclusión con el dataset completo antes de mover cualquier cambio a `explosive_config.json`.

---

## RVOL: CONGELADO -- pendiente de reconfirmar con los 30 días completos

Decisión del 2026-08-01 (ver [DECISION_LOG.md](DECISION_LOG.md)): la hipótesis de RVOL (Propuesta 1, y la comparación de escenarios de la sección anterior) se considera suficientemente investigada por ahora. **No se abre ninguna propuesta nueva sobre RVOL hasta que termine la validación completa de 30 días**, momento en el que se recalculará exactamente la misma comparación de 5 escenarios con el dataset completo y, si es posible, con los factores de momentum/VWAP también reconstruidos (ver limitación de "puntaje parcial" arriba). Solo entonces se decide si RVOL sigue siendo filtro excluyente, pasa a ser factor de puntuación, o adopta otro rol.

---

## SIGUIENTE CUELLO DE BOTELLA: LIQUIDEZ (con RVOL congelado/desactivado)

Pedido explícito: seguir auditando el resto de los filtros sin tocar RVOL. Metodología: se repitió el mismo análisis de interacción de la sección anterior, pero fijando RVOL siempre desactivado (32 combinaciones de los 5 filtros restantes en vez de 64) -- así se ve qué filtro domina una vez que RVOL deja de tapar la señal de los demás. Evidencia sobre **10 de los 30 días** (creció de 8 a 10 mientras se corría este análisis). Nuevo: `atlas_live.backtest.filter_interaction.run_grid_excluding()`.

### Hallazgo: liquidez es, por lejos, el filtro más determinante entre los 5 restantes

| Filtro | Contribución marginal a Recall (leave-one-out, RVOL off) | Δ Falsos positivos si se quita | Δ Falsos negativos si se quita |
|---|---|---|---|
| **liquidity** | **-0.27** (el mayor, por lejos) | **-1.957** | **+54** |
| movement | -0.05 | -3.626 | +10 |
| size | -0.03 | -449 | +6 |
| price | -0.01 | -14 | +2 |
| volatility | -0.005 | -283 | +1 |

Quitar el gate de liquidez (manteniendo los otros 4 activos, sin RVOL) recuperaría 27 puntos porcentuales de Recall -- una magnitud muy por encima de los otros 4 filtros, que apenas se mueven. Esto es consistente con la auditoría original (con RVOL activo): ahí liquidez ya era la **segunda** causa de falsos negativos (45 de 132, 34.1%), justo detrás de RVOL. Con RVOL fuera de la ecuación, liquidez pasa a ser la primera causa, con nitidez.

Poder individual (solo, RVOL desactivado): liquidez tiene el Recall más bajo de los 5 en solitario (58.0%, contra 86-96% del resto) -- es, con diferencia, el filtro más restrictivo de los que quedan.

Interacción par a par: liquidez se solapa parcialmente con `movement` (+0.02) y `volatility` (+0.01) -- overlaps reales pero de magnitud mucho menor que el que tenía RVOL con el resto del sistema (donde tapaba casi toda la señal).

### Interpretación (sin proponer un cambio todavía)

El umbral actual de liquidez (`min_dollar_volume = $2,000,000`) puede estar descartando oportunidades reales que sí tienen suficiente volumen relativo/movimiento pero no llegan a ese piso de dólares -- exactamente el mismo patrón que se vio con RVOL, en una escala menor. **No se propone todavía un cambio de umbral ni de rol para liquidez** -- se deja registrado como el candidato más fuerte para la próxima hipótesis a investigar formalmente (formato PROBLEMA/HIPÓTESIS/... de la Constitución), después de resolver RVOL con los 30 días completos, para no analizar dos filtros en movimiento a la vez.

---

## MAPA COMPLETO: AUDITORÍA DE LOS 7 FILTROS PRINCIPALES

Cierre pedido de la auditoría, antes de elaborar el plan definitivo de v2. Evidencia sobre **11 de los 30 días**. No se propone ningún cambio en esta sección -- es el mapa de comportamiento completo, tal como se pidió.

Nota de alcance: el motor real tiene 6 gates en la Etapa A (price, liquidity, rvol, movement, volatility, size). De los 7 conceptos pedidos, **Gap no existe como filtro independiente hoy** -- está fusionado con "cambio %" dentro de "movement" -- así que se aisló con un experimento aparte (mismo método que el techo de precio de la comparación anterior). **Momentum tampoco existe como filtro** -- es un factor de la Etapa B (peso 15%, basado en RSI) que nunca se usó como gate, y su score **no se guardó** en la validación histórica (se confirmó revisando las claves de `metrics`: solo existen `price, gap_pct, change_pct, relative_volume, dollar_volume, volatility_score, market_cap` -- ni rastro de momentum/RSI). Se reporta esa ausencia explícitamente en vez de inventar un número.

| Filtro | Poder predictivo individual | Contrib. P@10 | Contrib. P@20 | Contrib. Recall | FP si se quita | FN si se quita | Interacciones | ¿Cuello de botella? | Prioridad |
|---|---|---|---|---|---|---|---|---|---|
| **RVOL** | P10=9.1%, Recall=4.5% (el más restrictivo de todos) | -2.7% | -2.7% | **-42.3%** (la mayor del sistema) | +3.115 | +93 (marginal; ~60% de los descartes históricos totales) | Overlap con price/movement/size (+0.9% c/u), con liquidity (-1.8%) | **SÍ -- el mayor de todo el sistema** | **CONGELADA** (ya investigada; sin nuevas propuestas hasta cerrar 30 días, ver sección dedicada arriba) |
| **Liquidez** | P10=9.1%, Recall=56.4% | 0% (tapado por RVOL) / -1.8% (sin RVOL) | 0% / 0% | 0% (tapado) / **-26.8%** (sin RVOL, la mayor de los 5 restantes) | +2 / +2.131 | 0 / +59 | Overlap con RVOL (-1.8%), con movement/volatility (menor, análisis previo) | **SÍ -- el segundo del sistema, el primero entre los no-RVOL** | **ALTA.** Candidato inmediato a la próxima hipótesis formal, una vez cerrado RVOL. |
| **Gap** (aislado, hoy fusionado en "movement") | A `|gap|≥5%` aislado: P10=21.8%, P20=17.7%, R=36.8% -- el filtro individual con mejor Precision@10 y @20 de los 7 | No aplica (no es gate independiente hoy) | No aplica | No aplica | 517 (a umbral 5%) | 139 de 211 (a umbral 5%) | No aislable de "movement" con el diseño actual | **NO es cuello de botella hoy** (no excluye nada por sí solo -- está mezclado con cambio %), pero es la variable individual más fuerte de las 7 | **MEDIA-ALTA.** Separar Gap de "movement" como filtro o factor propio es una propuesta concreta con evidencia real de respaldo. |
| **Momentum** (RSI, factor de la Etapa B, nunca fue gate) | **No auditable** -- el score nunca se persistió en la validación histórica | Sin datos | Sin datos | Sin datos | Sin datos | Sin datos | Sin datos | **Desconocido** | Requiere primero extender la persistencia de `historical_scan.py` (Propuesta 5) antes de poder auditarlo con evidencia real. |
| **Volatilidad** | P10=10.0%, Recall=95.9% (el más alto de los 6 -- casi nunca excluye a una ganadora real) | 0% | 0% | 0% (tapado) / -0.5% (sin RVOL, casi nula) | 0 / +301 | 0 / +1 | Sin interacción medible con esta muestra | **NO** -- el menos restrictivo de los 6 | **BAJA.** Parece ya razonablemente calibrado. |
| **Precio** (mínimo, filtro piso) | P10=9.1%, Recall=88.2% | -0.9% | -0.5% | -0.5% (segunda mayor del sistema completo, muy por debajo de RVOL) | +3 / +14 | +1 / +2 | Overlap con RVOL (+0.9%) | **NO** como gate piso -- contribución marginal pequeña | **BAJA como gate actual.** Pero el PRECIO como variable (un techo, no un piso) ya mostró mejora real de 56% en Precision@20 cuando se probó sin RVOL (ver comparación de escenarios) -- la oportunidad está en agregar la variable, no en tocar el gate existente (Propuesta 4). |
| **Market Cap** (gate "size") | P10=10.0%, Recall=92.3% | 0% | 0% | 0% (tapado) / -3.2% (sin RVOL) | 0 / +496 | 0 / +7 | Overlap con RVOL (+0.9%) | **NO**, contribución marginal baja | **BAJA por ahora, con advertencia de calidad de datos**: 74.3% de las ganadoras reales tienen `market_cap=None` (Explosive DNA) -- esta fila probablemente subestima el verdadero poder del filtro por el vacío de datos, no porque el filtro sea débil en sí. Resolver el vacío (Propuesta 2) antes de confiar en esta prioridad. |

### Lectura consolidada del mapa

Ordenando los 7 por impacto real medido (excluyendo Momentum, sin datos): **RVOL (congelado) > Liquidez > Gap (como variable nueva) > Precio (como variable nueva, no como gate actual) > Market Cap (subestimado por datos faltantes) > Volatilidad**. Los tres filtros con contribución marginal genuinamente baja y sin caveats de datos (Volatilidad, y en menor medida el gate de tamaño una vez resuelto el vacío de market cap) son los más cercanos a estar "bien calibrados tal como están" -- no se recomienda tocarlos primero.

Con esto se completa el mapa pedido. El plan definitivo de v2 se elabora a partir de aquí, una vez decidido el destino de RVOL con los 30 días completos.

---

## OPORTUNIDADES DE MEJORA (priorizadas por Impacto → Dificultad → Riesgo)

### 1. Redefinir la línea base de RVOL ajustada al tiempo transcurrido

**Impacto: Muy alto | Dificultad: Baja | Riesgo: Medio**

```
PROBLEMA:
El gate de RVOL compara volumen acumulado desde la apertura contra el
promedio de UN DÍA COMPLETO (390 minutos). A los 10 minutos, ni siquiera
las ganadoras reales alcanzan RVOL cercano al umbral de 2.0x (mediana real
0.14x). Es la causa del 59.8% de los falsos negativos medidos (79/132 en
7 días).

HIPÓTESIS:
Si el umbral se compara contra una línea base ajustada al tiempo
transcurrido (average_volume × minutos_transcurridos/390) en vez del
promedio de día completo, el gate distinguirá interés anómalo real sin
penalizar sistemáticamente a cualquier snapshot temprano.

PRINCIPIOS DE LA CONSTITUCIÓN QUE RESPALDAN ESTA PROPUESTA:
Principio 1 (los datos tienen prioridad sobre las opiniones -- esto es
exactamente lo que reveló el dato) y Principio 2 (todo cambio debe poder
medirse).

IMPACTO ESPERADO:
Aumento sustancial de Recall (es la causa dominante de falsos negativos).
Riesgo de reducir Precision si el ajuste es demasiado permisivo en los
primeros minutos.

RIESGOS:
El volumen de apertura suele ser desproporcionadamente alto incluso en
días normales ("efecto apertura") -- un ajuste puramente lineal podría
sobrecorregir. Necesita calibrarse contra los mismos 30 días, no solo
linealizarse a ciegas.

CÓMO SE VALIDARÁ:
Requiere un cambio de FÓRMULA (no solo de umbral), por lo que
`whatif_simulator.py` no puede probarlo tal cual (solo simula valores de
umbral, no redefiniciones de cómo se calcula la métrica). Se validará con
una corrida histórica dedicada, comparando Precision@10/@20/Recall contra
la definición actual, sobre los mismos 30 días.

CRITERIOS DE ÉXITO:
Reducir el 59.8% de falsos negativos atribuidos a RVOL sin degradar
Precision@10/@20 en más de un margen a acordar antes de la corrida.
```

**Cambios en Atlas Core**: Ninguno.

---

### 2. Resolver el vacío de datos de market cap en la validación histórica

**Impacto: Alto (habilita medir correctamente el punto 6 y 9) | Dificultad: Media | Riesgo: Bajo**

```
PROBLEMA:
El 74.3% de las observaciones explosivas reales de los 7 días analizados
tienen market_cap=None en la reconstrucción histórica -- no se pudo
obtener su capitalización vía fast_info. Esto compromete la fiabilidad de
cualquier análisis sobre el factor de tamaño para la mayoría de los casos
reales.

HIPÓTESIS:
La causa más probable es rate-limiting de Yahoo Finance durante el fetch
threaded de fast_info para ~2500 símbolos (se observaron
YFRateLimitError en los logs de la corrida), o cobertura pobre de
fast_info para nombres de baja capitalización/liquidez -- que son
justamente los que más aparecen entre las ganadoras reales. Con reintentos
más espaciados o un proveedor de respaldo, se podría recuperar la mayoría
de estos datos.

PRINCIPIOS DE LA CONSTITUCIÓN QUE RESPALDAN ESTA PROPUESTA:
Principio 1 (los datos tienen prioridad sobre las opiniones -- no se puede
opinar sobre el factor de tamaño sin el dato) y Principio 5 (el proveedor
de datos nunca debe estar acoplado al motor -- un respaldo a fast_info
encaja con este principio).

IMPACTO ESPERADO:
No mejora ninguna métrica directamente -- mejora la CONFIABILIDAD de medir
el impacto de otras propuestas (4, 5, 9) que dependen de market cap.

RIESGOS:
Bajo -- es una mejora de infraestructura de validación (atlas_live/backtest),
no toca el motor.

CÓMO SE VALIDARÁ:
Comparar el % de símbolos con market_cap conocido antes/después del
cambio, sobre los mismos 30 días.

CRITERIOS DE ÉXITO:
Reducir el 74.3% de "tamaño desconocido" a menos del 20%.
```

**Cambios en Atlas Core**: Ninguno.

---

### 3. Recalibrar el valor absoluto del umbral de RVOL (independiente de la propuesta 1)

**Impacto: Alto | Dificultad: Baja (ya ejecutable hoy) | Riesgo: Medio**

```
PROBLEMA:
Incluso sin cambiar la fórmula de RVOL, el umbral fijo de 2.0x podría no
ser el punto óptimo para un snapshot a los 10 minutos.

HIPÓTESIS:
Existe un umbral distinto a 2.0x que mejora Recall sin destruir Precision,
aunque la evidencia del punto 5 de la auditoría (los descartes están LEJOS
del umbral, no cerca) sugiere que un simple ajuste de umbral probablemente
NO sea suficiente por sí solo -- esta propuesta debe evaluarse en conjunto
con la propuesta 1, no como sustituto.

PRINCIPIOS DE LA CONSTITUCIÓN QUE RESPALDAN ESTA PROPUESTA:
Principio 1, Principio 2.

IMPACTO ESPERADO:
Menor que la propuesta 1 en solitario, pero es la única de las dos que se
puede probar HOY con datos ya recolectados.

RIESGOS:
Bajo -- es completamente reversible y no requiere nueva descarga de datos.

CÓMO SE VALIDARÁ:
Con `atlas_live/backtest/whatif_simulator.py` sobre los 7 (y luego 30)
días ya guardados -- resultado en segundos, sin nueva descarga.

CRITERIOS DE ÉXITO:
Mejora medible de Recall; documentar hasta qué punto un ajuste de umbral
por sí solo (sin cambiar la fórmula) puede compensar el problema
identificado en la propuesta 1.
```

**Cambios en Atlas Core**: Ninguno.

---

### 4. Agregar un factor de nivel de precio

**Impacto: Medio | Dificultad: Baja | Riesgo: Bajo**

```
PROBLEMA:
Las acciones explosivas reales tienen precio significativamente más bajo
que el resto del universo (mediana $11.43 vs $48.42; 83% del universo por
encima de la mediana explosiva) y ningún factor actual usa el precio
directamente.

HIPÓTESIS:
Agregar un factor de nivel de precio (favoreciendo precios bajos, con
algún piso para evitar penny stocks no operables) mejoraría el ranking
entre elegibles.

PRINCIPIOS DE LA CONSTITUCIÓN QUE RESPALDAN ESTA PROPUESTA:
Principio 1, Principio 6 (Radar Explosivo es el módulo más importante --
justifica esta inversión), Principio 7 (un factor aislado en el registro
ya existente no rompe la simplicidad).

IMPACTO ESPERADO:
Mejora de Precision@10/@20 (mejor ranking, no cambia elegibilidad).

RIESGOS:
Podría solaparse parcialmente con Market Cap (ambos capturan "empresa
chica") -- debe verificarse que aporte señal incremental, no redundante.

CÓMO SE VALIDARÁ:
Requiere una nueva corrida histórica (el factor no está en los datos ya
guardados) comparando Precision@10/@20 con y sin el factor.

CRITERIOS DE ÉXITO:
Mejora de Precision@10 sin reducir Recall; separación incremental sobre
Market Cap (no solo redundante).
```

**Cambios en Atlas Core**: Ninguno (usa `quote.last_price`, ya existente).

---

### 5. Recalibrar los pesos de la Etapa B según separación real de Explosive DNA

**Impacto: Medio-alto | Dificultad: Media | Riesgo: Bajo**

```
PROBLEMA:
Los pesos actuales (RVOL 25%, volatilidad 15%, momentum 15%, gap 15%, VWAP
10%, sector 10%, float 10%) fueron un diseño inicial razonado, no medido.
La separación real observada (Cambio% 98.7%, Gap% 97.7%, RVOL 90.9%,
Volatilidad 74.3%, Volumen$ 69.5%) muestra que Cambio% y Gap% separan más
que RVOL, pese a pesar menos.

HIPÓTESIS:
Re-ponderar los factores proporcionalmente a su separación medida mejorará
el ranking entre elegibles.

PRINCIPIOS DE LA CONSTITUCIÓN QUE RESPALDAN ESTA PROPUESTA:
Principio 1, Principio 2.

IMPACTO ESPERADO:
Mejora de Precision@10/@20.

RIESGOS:
Requiere los scores de momentum/VWAP por símbolo, que HOY NO se guardan en
las corridas históricas -- no se puede validar retroactivamente sobre la
corrida de 30 días en curso.

CÓMO SE VALIDARÁ:
Requiere extender el formato de persistencia de `historical_scan.py`
(agregar momentum_score y vwap_distance_score a los metrics guardados) en
una corrida futura dedicada.

CRITERIOS DE ÉXITO:
Mejora de Precision@10/@20 manteniendo Recall.
```

**Cambios en Atlas Core**: Ninguno, pero requiere una corrida histórica nueva con persistencia extendida.

---

### 6. Extender el análisis de falsos positivos/negativos a los 6 gates (ya implementado)

**Impacto: Medio | Dificultad: Baja (completado) | Riesgo: Ninguno**

```
PROBLEMA:
El análisis de "demasiado permisivo" solo cubría RVOL; no había cobertura
de los otros 5 gates.

HIPÓTESIS:
Extender la misma lógica a los 6 gates revelaría más patrones de
sobre-permisividad.

PRINCIPIOS DE LA CONSTITUCIÓN QUE RESPALDAN ESTA PROPUESTA:
Principio 1, Principio 2, Principio 7 (extiende una función existente).

IMPACTO ESPERADO:
Ya implementado (ver `validation_report.py::consolidate_reports`). Con 7
días, solo RVOL mostró señal (muestra aún chica: 4 falsos positivos
totales). Se espera más señal con los 30 días completos.

RIESGOS:
Ninguno -- es una extensión de análisis de solo lectura.

CÓMO SE VALIDARÁ:
Ya corre sobre los datos guardados; se revisará de nuevo con los 30 días.

CRITERIOS DE ÉXITO:
Cumplido (herramienta funcionando); resultado sustantivo pendiente de más
días.
```

**Cambios en Atlas Core**: Ninguno.

---

### 7. Snapshot de detección configurable (probar distintos minutos post-apertura)

**Impacto: Potencialmente alto, desconocido | Dificultad: Alta | Riesgo: Medio**

```
PROBLEMA:
El motor asume 10 minutos como punto óptimo de detección -- nunca se
validó, es un supuesto de diseño inicial.

HIPÓTESIS:
Otro snapshot (5/15/20 minutos) podría mejorar Precision/Recall
simultáneamente.

PRINCIPIOS DE LA CONSTITUCIÓN QUE RESPALDAN ESTA PROPUESTA:
Principio 1, Principio 2.

IMPACTO ESPERADO:
Desconocido hasta medirse -- podría ser alto si 10 minutos no es el punto
óptimo real.

RIESGOS:
Cada snapshot alternativo requiere su propia corrida histórica completa
(incluida la descarga intradía) -- mismo orden de costo en tiempo que la
validación actual, multiplicado por cada snapshot a probar.

CÓMO SE VALIDARÁ:
Corridas históricas adicionales con `snapshot_minutes_after_open` distinto.

CRITERIOS DE ÉXITO:
Identificar el snapshot con mejor combinación de Precision@10/@20/Recall.
```

**Cambios en Atlas Core**: Ninguno.

---

### 8. Nuevo factor de ruptura intradía (distancia al máximo del día)

**Impacto: Medio | Dificultad: Media | Riesgo: Bajo**

```
PROBLEMA:
Ningún factor actual mide si el precio está rompiendo el máximo reciente
de la sesión -- señal clásica de momentum, deliberadamente no copiada del
breakout score de Decision Engine para mantener independencia.

HIPÓTESIS:
Un factor propio de distancia al máximo intradía distinguiría rupturas
reales de gaps que luego retroceden.

PRINCIPIOS DE LA CONSTITUCIÓN QUE RESPALDAN ESTA PROPUESTA:
Principio 6, Principio 7.

IMPACTO ESPERADO:
Mejora de Precision@10/@20.

RIESGOS:
Requiere nueva corrida histórica (el dato no está guardado hoy).

CÓMO SE VALIDARÁ:
Corrida histórica dedicada, comparando con y sin el factor.

CRITERIOS DE ÉXITO:
Mejora medible de Precision@10 sin reducir Recall.
```

**Cambios en Atlas Core**: Ninguno (usa `quote.high`, ya existente).

---

### 9. Recalibrar la curva de penalización por tamaño con Explosive DNA

**Impacto: Medio (bloqueado por la propuesta 2) | Dificultad: Baja | Riesgo: Bajo**

```
PROBLEMA:
Los valores actuales (small_cap_reference=$300M, mega_cap_reference=$200B,
min_factor=0.5) fueron un diseño inicial razonado, no medido.

HIPÓTESIS:
La distribución real de market cap de las ganadoras reales mostraría un
punto de corte natural distinto -- pero hoy la muestra confiable es
pequeña (n=36 de 140, por el vacío de datos de la propuesta 2).

PRINCIPIOS DE LA CONSTITUCIÓN QUE RESPALDAN ESTA PROPUESTA:
Principio 1, Principio 2, Principio 3.

IMPACTO ESPERADO:
Mejora de Precision@10/@20, pero la confianza en el resultado depende de
resolver primero la propuesta 2.

RIESGOS:
Sobreajuste a los días específicos analizados si la muestra no es
representativa de todos los regímenes de mercado.

CÓMO SE VALIDARÁ:
`whatif_simulator.py` sobre alternativas de la curva, una vez resuelto el
vacío de datos.

CRITERIOS DE ÉXITO:
Mejora medible sin perder el mecanismo de excepción para catalizadores
extraordinarios en mega-caps.
```

**Cambios en Atlas Core**: Ninguno.

---

### 10. Reconstrucción histórica del factor de sector/money flow

**Impacto: Desconocido | Dificultad: Media-alta | Riesgo: Bajo**

```
PROBLEMA:
El factor de sector (10% del peso) nunca se puso a prueba -- la validación
actual pasa sector_money_flow_score=None para todos los símbolos, por
simplicidad.

HIPÓTESIS:
El factor podría estar aportando o restando valor real, pero hoy es un
peso "a ciegas".

PRINCIPIOS DE LA CONSTITUCIÓN QUE RESPALDAN ESTA PROPUESTA:
Principio 1, Principio 2.

IMPACTO ESPERADO:
Desconocido hasta medirse.

RIESGOS:
Reconstruir el money flow sectorial histórico agrega una etapa de cómputo
nueva y requiere sector/industry por símbolo, dato que tampoco se descarga
hoy en el backtest.

CÓMO SE VALIDARÁ:
Nueva corrida histórica que sí reconstruya el factor sectorial (de forma
independiente en atlas_live, sin reutilizar atlas.engine.money_flow_engine,
que está diseñado para "ahora", no para una fecha pasada).

CRITERIOS DE ÉXITO:
Determinar si el peso de 10% está justificado, debe subir, bajar o
eliminarse.
```

**Cambios en Atlas Core**: Ninguno.

---

### 11. Nueva fuente de datos: short interest / float histórico real

**Impacto: Potencialmente alto mas acotado a un patrón específico (short squeeze) | Dificultad: Alta | Riesgo: Alto**

```
PROBLEMA:
El factor "float" hoy usa una aproximación (acciones actuales, no
históricas) y ningún gate considera short interest -- un predictor clásico
de movimientos explosivos por short squeeze.

HIPÓTESIS:
Incorporar short interest y float histórico real mejoraría la detección
de ese patrón específico, que hoy el motor no distingue de un simple gap
por noticias.

PRINCIPIOS DE LA CONSTITUCIÓN QUE RESPALDAN ESTA PROPUESTA:
Principio 5 (cualquier fuente nueva debe integrarse detrás de una interfaz,
no acoplada al motor), Principio 6.

IMPACTO ESPERADO:
Alto para el subconjunto de casos de short squeeze, no genérico a todas
las explosiones -- impacto agregado sobre Precision/Recall incierto.

RIESGOS:
Es la ÚNICA de estas 11 propuestas que requiere tocar Atlas Core: el
DataProvider/DataCollector actuales no exponen short interest en absoluto
(`Quote` no tiene ese campo). Toca la fase "Cambio de proveedor de datos"
del roadmap, no solo el Radar Explosivo -- alcance mayor, más riesgo de
romper otras partes que consumen DataCollector.

CÓMO SE VALIDARÁ:
No se puede validar sin antes construir la capacidad de obtener el dato --
depende de trabajo de arquitectura previo (fase 6 de ATLAS_ROADMAP.md).

CRITERIOS DE ÉXITO:
A definir cuando se aborde esa fase.
```

**Cambios en Atlas Core**: **Sí -- la única de las 11 propuestas que lo requiere.**

---

## HOJA DE RUTA HACIA RADAR EXPLOSIVO V2 (basada únicamente en evidencia)

**Regla de esta hoja de ruta**: ningún paso avanza sin que el paso anterior produzca evidencia que lo justifique. Nada se implementa de forma permanente hasta validarse con datos históricos y luego en tiempo real, en ese orden (METODOLOGÍA DE PROPUESTAS de la Constitución).

**Paso 0 (en curso)**: terminar la validación histórica de 30 días. Sin eso, todo lo de aquí abajo es preliminar.

**Paso 1**: resolver el vacío de datos de market cap (propuesta 2) -- es infraestructura, no algoritmo, y todo lo demás que involucra tamaño depende de tener este dato confiable.

**Paso 2**: usar `whatif_simulator.py` para probar recalibraciones de umbral que NO requieren nueva descarga (propuestas 3, 6, y 9 una vez resuelto el paso 1) contra los 30 días completos. Esto es barato y rápido -- se hace antes de cualquier corrida nueva.

**Paso 3**: diseñar y ejecutar UNA corrida histórica nueva que incorpore de una vez varios cambios evaluables juntos: fórmula de RVOL ajustada al tiempo (propuesta 1), factor de nivel de precio (propuesta 4), factor de ruptura intradía (propuesta 8), y persistencia extendida de momentum/VWAP para poder recalibrar pesos (propuesta 5) -- agruparlas evita correr 4 validaciones completas por separado.

**Paso 4**: con los resultados del paso 3, decidir -- con evidencia, siguiendo el formato de propuesta -- cuáles cambios pasan a `explosive_config.json`/`explosive_factors.py` de forma permanente, y registrar cada decisión en [DECISION_LOG.md](DECISION_LOG.md).

**Paso 5**: validar en tiempo real (no solo históricamente) los cambios adoptados en el paso 4, antes de considerarlos definitivos -- paso 7 de la metodología de la Constitución.

**Paso 6 (fuera de esta hoja de ruta, prioridad menor)**: sector/money flow histórico (propuesta 10) y, solo si se prioriza la fase "Cambio de proveedor de datos" del roadmap general, short interest/float real (propuesta 11) -- la única que toca Atlas Core.

Este documento se actualizará cuando termine la validación de 30 días con las cifras finales, y cada vez que el `whatif_simulator.py` o una corrida nueva agregue evidencia.
