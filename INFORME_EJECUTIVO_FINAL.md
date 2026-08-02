# INFORME EJECUTIVO — Cierre de la Fase de Validación Histórica

**Para**: el dueño de Atlas
**Propósito**: base de decisión para aprobar o rechazar la implementación del Cambio Nº1 (RVOL)
**Fecha**: 2026-08-02

Este documento reemplaza y amplía el informe ejecutivo anterior. Está escrito para tomar una decisión, no para entender el código. **No contiene ninguna propuesta de implementación** — solo diagnóstico y recomendación.

---

## 1. Estado actual de Atlas

**≈64% del proyecto está completado**, sobre 9 módulos planificados. No es un número de "qué tan bueno es Atlas hoy" — es "cuánto del plan de trabajo ya se hizo". El resultado de la validación (sección 2) muestra que buena parte de ese 64% construyó las herramientas correctas para *descubrir* que el motor todavía no funciona bien, que es exactamente para lo que se construyeron.

**Módulos completos**:
- **Arquitectura base** — el cerebro de Atlas (Atlas Core) está separado y protegido; nada de lo experimental puede romperlo.
- **Validación histórica** — se probó el sistema contra 30 días reales de mercado completos, sin atajos.
- **Explosive DNA** — se sabe, con datos, qué características tienen realmente las acciones que explotan.
- **Alertas en tiempo real** (alcance básico) — el sistema ya avisa activamente (navegador, sonido, resaltado visual) en vez de depender de que alguien mire la pantalla.

**Módulos en desarrollo**:
- **Radar Explosivo (el motor de detección)** — construido y en funcionamiento, pero la validación reveló que necesita una revisión importante antes de confiar en él.
- **Optimización del Radar** — el diagnóstico y el plan de mejora ya están listos; falta la decisión de implementarlo (es justamente lo que este informe busca destrabar).
- **Cambio de proveedor de datos** — se investigaron alternativas al proveedor gratuito actual (Yahoo Finance); todavía no se decidió ni se implementó nada.
- **Dashboard** — funciona y muestra datos en vivo, pero todavía no tiene el nivel de pulido de una herramienta profesional.

**Módulos que faltan**:
- **Asistencia con IA para interpretar las señales** — no se empezó. Tiene sentido: no vale la pena construir un asistente inteligente sobre un motor que todavía detecta muy poco.

---

## 2. Resultado de la validación histórica de 30 días

Se probó el sistema contra 30 sesiones reales de mercado (18 de junio a 31 de julio), sobre el universo completo de casi 2.600 acciones que Atlas puede analizar — no una muestra chica.

**Qué funcionó**: la validación en sí fue rigurosa y confiable. Se diseñó específicamente para no hacer trampa (el sistema nunca tuvo acceso a información del futuro al tomar sus decisiones simuladas). Cuando el radar SÍ marcó una acción como oportunidad, casi siempre acertó por razones sólidas — de 29 veces que el sistema dijo "esto es una oportunidad", solo 15 resultaron equivocadas.

**Qué no funcionó**: el radar encontró muy pocas oportunidades. De 600 acciones que realmente tuvieron un movimiento fuerte durante esos 30 días, el sistema solo detectó 14 — un 2,3%. En el resumen del día (las 10 mejores oportunidades que muestra Atlas), acertó apenas el 4,7% de las veces.

**Los descubrimientos más importantes**:
1. **La causa es un solo filtro, no el enfoque general.** Un filtro que mide "volumen inusual" descarta más de la mitad de todas las oportunidades reales perdidas, porque compara el volumen de los primeros 10 minutos contra el promedio de un día *completo* — una comparación que, matemáticamente, casi ninguna acción puede superar tan temprano, ni siquiera las que sí explotan.
2. **No es un problema de "ajustar un poco" — es un problema de diseño.** Se probó bajar ese umbral progresivamente y la mejora fue chica en cada paso. En cambio, cuando se dejó de usar ese filtro como una puerta que descarta y se usó solo como un punto a favor en el puntaje, las tres métricas de calidad mejoraron a la vez, de forma grande — algo que no suele pasar (normalmente mejorar una métrica empeora otra).
3. **Se encontraron datos sospechosos.** Algunas de las "mayores ganadoras" del período (subas de más de 2.000%, incluso 9.500% en un día) son casi con certeza errores de datos, no movimientos reales de mercado. Esto significa que el problema real podría ser todavía más grande de lo medido — esos casos probablemente ocuparon lugares que le hubieran correspondido a ganadoras genuinas.
4. **Falta un dato importante en 3 de cada 4 casos**: el tamaño de la empresa (cuánto vale en el mercado) no se pudo obtener para el 73% de las acciones que sí explotaron. Esto no afecta el resultado de hoy, pero impide ajustar con confianza cualquier parte del sistema relacionada con "empresas grandes vs. chicas".

---

## 3. Diagnóstico del sistema

**Cuellos de botella, de mayor a menor impacto**:

| Filtro | Responsable de qué % de las oportunidades perdidas |
|---|---|
| Volumen inusual (RVOL) | 56% |
| Volumen mínimo en dólares (liquidez) | 33% |
| Precio mínimo | 10% |
| Volatilidad mínima | menos del 1% (prácticamente no es un problema) |

**Causa raíz del bajo rendimiento**: no es que las señales que usa Atlas estén mal elegidas — se confirmó con datos que las señales correctas (el cambio de precio, el salto de apertura, el volumen inusual, la volatilidad) sí distinguen bien a una acción que va a explotar de una que no. El problema es puntual: una de esas señales (el volumen inusual) se usa como una puerta que se cierra casi siempre, en vez de como una de varias cosas que suman. Es un error de diseño concreto y corregible, no una falla del enfoque general de Atlas.

---

## 4. Riesgos actuales

**Qué impide pasar a Paper Trading (operar en simulado)**: con la configuración actual, Atlas mostraría en promedio menos de una oportunidad por día, y la mayoría de las veces sin acertar. Un período de paper trading así no generaría información nueva — ya se hizo el equivalente con 30 días reales de mercado, y el resultado ya se conoce. Sería tiempo de calendario gastado sin aprender nada adicional.

**Qué impide operar con dinero real**: (1) el sistema ni siquiera pasó todavía por una prueba en tiempo real (paper trading) — es un paso obligatorio que no se hizo; (2) el motor actual detecta apenas el 2,3% de las oportunidades reales; (3) hay datos faltantes (tamaño de empresa) y sospecha de errores de datos sin filtrar; (4) ninguna de las mejoras identificadas está implementada ni probada todavía. Operar con capital real hoy sería arriesgar dinero sobre un sistema que, con evidencia, casi no encuentra lo que se supone que tiene que encontrar.

---

## 5. Recomendaciones estratégicas

**Los 5 cambios más importantes, en el orden recomendado**:

| # | Cambio | Impacto esperado (medido con los 30 días reales) | Riesgo principal |
|---|---|---|---|
| 1 | Dejar de usar el volumen inusual como filtro que descarta, y usarlo solo para puntuar | Detección: 2,3% → 46,3% (20 veces más). Acierto en Top 10: 4,7% → ~30% (6 veces más). Acierto en Top 20: 2,3% → ~20% (8,5 veces más) | Sin nada que lo acompañe, el sistema pasaría de mostrar casi nada a mostrar miles de candidatos por día — necesita mantenerse el filtro de liquidez como contención |
| 2 | Relajar el filtro de volumen mínimo en dólares (liquidez) | Detección sube otros 25 puntos, hasta 71% | Bajo — es un ajuste más chico y aditivo |
| 3 | Darle protagonismo propio al "salto de apertura" (gap), hoy mezclado con otra señal | Probado solo: acierto en Top 10 hasta 32%, detección hasta 63% (todavía no probado combinado con los cambios 1 y 2) | Solo, sin nada más, deja pasar ruido — necesita combinarse con al menos otro filtro |
| 4 | Resolver el dato faltante de tamaño de empresa | No mejora las métricas directamente — es la base para poder confiar en cualquier ajuste relacionado con tamaño de empresa | Bajo — es trabajo de infraestructura de análisis, no toca el sistema en producción |
| 5 | Reajustar cuánto pesa cada señal en el puntaje final, con los datos ya medidos de cuál señal distingue mejor | No medido todavía — depende de completar los cambios 1 a 4 primero | Riesgo de ajustar demasiado fino a este período específico si no se reconfirma después |

**Nota de honestidad**: un sexto candidato (un techo de precio máximo) parecía prometedor en un análisis preliminar con menos datos. Al confirmarlo con los 30 días completos, **dejó de sostenerse** — se descartó de esta lista en vez de mantenerlo por inercia.

---

## 6. Plan de trabajo recomendado

**Qué debe hacerse primero**: aprobar e implementar el Cambio Nº1 (volumen inusual), junto con el Cambio Nº2 (liquidez) como su contención necesaria — no se recomienda implementar el Nº1 solo. Después de implementarlos, se debe repetir la validación histórica para confirmar la mejora con datos nuevos, y recién entonces pasar a una prueba en tiempo real (paper trading) antes de considerar cualquier cambio permanente.

**Qué puede esperar**: el Cambio Nº3 (salto de apertura), el Cambio Nº5 (reajuste de pesos), la decisión sobre proveedor de datos, y el desarrollo del dashboard profesional. Ninguno de estos bloquea al Cambio Nº1, y el Cambio Nº1 no depende de ellos.

**Qué NO debería hacerse todavía**:
- Paper trading con la configuración actual (no aportaría información nueva).
- Cualquier operación con dinero real.
- Agregar indicadores o señales nuevas antes de terminar de corregir los dos problemas raíz ya identificados (el propio criterio de Atlas es que la simplicidad vale más que acumular indicadores).
- Cambiar de proveedor de datos ahora — el problema detectado no es de calidad de datos, es de cómo se usa una señal que ya se tiene.

---

## 7. Conclusión ejecutiva

**¿Autorizaría, como Director Técnico, pasar a la siguiente fase?**

**Sí, autorizo avanzar con la implementación del Cambio Nº1 (junto con el Nº2 como contención) — no autorizo paper trading ni dinero real todavía.**

Justificación: es inusual encontrar un cambio que mejore tres métricas de calidad a la vez, por un orden de magnitud, con evidencia medida sobre 30 días reales de mercado y no sobre una muestra chica ni una intuición. El riesgo de implementarlo es bajo y conocido (contenerlo con el filtro de liquidez), y el costo de no implementarlo es mantener un sistema que hoy detecta 1 de cada 43 oportunidades reales. La condición no negociable es seguir el proceso ya establecido: implementar, volver a validar con datos históricos, recién después probar en tiempo real, y solo entonces considerarlo definitivo — sin saltarse ningún paso.

---

*Ningún cambio mencionado en este informe está implementado. Detalle técnico completo: [RADAR_EXPLOSIVO_V2.md](RADAR_EXPLOSIVO_V2.md). Registro de la validación: [VALIDATION_RESULTS.md](VALIDATION_RESULTS.md). Historial de decisiones: [DECISION_LOG.md](DECISION_LOG.md).*
