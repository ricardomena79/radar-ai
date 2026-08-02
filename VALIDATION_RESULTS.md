# VALIDATION_RESULTS.md

Registro oficial de todas las validaciones históricas del Radar Explosivo. Cada corrida de `atlas_live/backtest/run_validation.py` debe agregar una entrada nueva aquí -- nunca se sobrescriben entradas anteriores, para poder comparar la evolución del motor a lo largo del tiempo.

Ver metodología completa en los docstrings de `atlas_live/backtest/historical_scan.py` y `atlas_live/backtest/validation_report.py`. Resumen: sin lookahead (indicadores calculados solo con datos previos al día evaluado; snapshot reconstruido a los N minutos de la apertura con velas reales de 5 minutos, no con el cierre del día), sobre el Universo Racional completo.

## Plantilla de entrada

```
## Validación [N] -- YYYY-MM-DD

- **Fecha de la corrida**: 
- **Universo analizado**: (cantidad de símbolos reconstruidos exitosamente / total del Universo Racional)
- **Días analizados**: (cantidad de sesiones de mercado, rango de fechas)
- **Snapshot**: (minutos después de la apertura)
- **Config usada**: (hash o resumen de explosive_config.json vigente en esa corrida)

### Métricas

| Métrica | Valor |
|---|---|
| Precision@10 (promedio) | |
| Precision@20 (promedio) | |
| Recall (promedio) | |
| Falsos positivos | |
| Falsos negativos | |

### Observaciones

(hallazgos concretos: qué filtro perdió más oportunidades, qué patrones aparecen en Explosive DNA, cualquier limitación de datos de esa corrida)

### Conclusiones

(qué se decide a partir de esta validación -- si motiva un cambio, debe quedar registrado también en DECISION_LOG.md)
```

---

## Validaciones registradas

## Validación 1 -- 2026-08-01

- **Fecha de la corrida**: 2026-08-01 (iniciada ~14:45, finalizada ~19:30)
- **Universo analizado**: 73.123 reconstrucciones exitosas de 77.310 posibles (94.6%) -- ~2.577 símbolos/día, Universo Racional completo
- **Días analizados**: 30 sesiones de mercado, 2026-06-18 a 2026-07-31
- **Snapshot**: 10 minutos después de la apertura, reconstruido con velas reales de 5 minutos
- **Config usada**: `explosive_config.json` sin modificar en ningún momento de la validación (gates: min_price=1.0, min_dollar_volume=2M, min_rvol=2.0, min_abs_gap_or_change_pct=2.0, min_volatility_score=50.0, large_cap_ceiling=10B)

### Métricas

| Métrica | Valor |
|---|---|
| Precision@10 (promedio) | 4.67% |
| Precision@20 (promedio) | 2.33% |
| Recall (promedio) | 2.33% |
| Falsos positivos | 15 (en 30 días) |
| Falsos negativos | 586 de 600 posibles (97.7%) |

### Observaciones

- RVOL es responsable del 56.3% de los descartes de ganadoras reales (330/586); Liquidez del 33.3% (195/586); Precio del 10.2% (60/586); Volatilidad de apenas 0.2% (1/586).
- Las 14 detecciones reales (de 600 posibles) tuvieron todas RVOL entre 2.5x y 267.8x -- el radar solo atrapa casos extremos, nunca un caso límite.
- Comparación de 5 escenarios sobre el rol de RVOL: quitarlo como filtro excluyente (dejándolo como factor de puntuación) multiplica Precision@10 por ~6,5x, Precision@20 por ~8,6x y Recall por ~20x, simultáneamente -- sin trade-off entre las tres métricas.
- Explosive DNA (600 observaciones): separación fuerte en Cambio% (98.3%), Gap% (97.0%), RVOL (88.5%), Volatilidad (76.6%); débil en Market Cap (22.6%, pero 73.2% de las observaciones con dato faltante) y Precio (17.7%, relación inversa -- las explosivas son más baratas).
- **Limitación de datos detectada**: al menos 5 de las 15 mayores "ganadoras reales" (FFAI +9.543%, CCG +2.537%, PRPL +2.141%, ENFY +410%/+259%) son casi con certeza artefactos de datos (splits no ajustados o tickers muy ilíquidos), no movimientos reales -- probablemente desplazaron a ganadoras genuinas del top-20 diario, subestimando el Recall real. No se corrigió retroactivamente para no alterar un resultado ya cerrado -- queda como recomendación para la próxima corrida.
- Detalle completo en [RADAR_EXPLOSIVO_V2.md](RADAR_EXPLOSIVO_V2.md), sección "RESULTADOS FINALES DE LA VALIDACIÓN".

### Conclusiones

RVOL, tal como está definido hoy, no es un filtro afinable con un ajuste de umbral -- es estructuralmente incompatible con un snapshot temprano (compara volumen de 10 minutos contra el promedio de un día completo). La evidencia respalda con fuerza estadística (30 días, no una muestra chica) la Propuesta 1 de `RADAR_EXPLOSIVO_V2.md`. Ninguna decisión de cambio se tomó todavía -- pendiente de aprobación siguiendo la METODOLOGÍA DE PROPUESTAS. Registrar en [DECISION_LOG.md](DECISION_LOG.md) cuando se apruebe algo.
