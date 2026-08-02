# ATLAS_CONSTITUTION.md

Este documento es la máxima autoridad del proyecto Atlas. Toda decisión futura —de diseño, de implementación, de priorización— deberá respetarlo. Ninguna instrucción, conversación o propuesta puntual puede anular lo que está escrito aquí sin que primero se explique la contradicción y se decida modificar este documento explícitamente.

# MISIÓN

Atlas existe para detectar las mejores oportunidades de trading intradía de alto momentum antes que el mercado las descubra.

No fue creado para encontrar las mejores empresas.

No fue creado para invertir a largo plazo.

Fue creado para detectar oportunidades explosivas.

# OBJETIVO

Detectar acciones con alta probabilidad de realizar un movimiento explosivo durante los próximos 5 a 10 minutos.

Toda modificación futura deberá responder una sola pregunta:

**"¿Este cambio mejora la capacidad de Atlas para detectar antes las acciones explosivas?"**

Si la respuesta es NO, el cambio no debe implementarse.

# PRINCIPIOS

1. Los datos tienen prioridad sobre las opiniones.
2. Todo cambio debe poder medirse.
3. Ningún algoritmo nuevo entra a Atlas Core sin haber sido validado previamente.
4. Atlas siempre debe explicar por qué recomienda una acción.
5. El proveedor de datos nunca podrá estar acoplado al motor.
6. Radar Explosivo es el módulo más importante del sistema.
7. La simplicidad vale más que agregar indicadores.
8. Ninguna propuesta importante podrá implementarse sin respetar esta Constitución.

# LO QUE ATLAS NUNCA HARÁ

- No buscará dividendos.
- No buscará value investing.
- No priorizará las mejores empresas.
- No será un screener genérico.
- No optimizará para inversiones de largo plazo.

# MÉTRICAS OFICIALES

Toda mejora deberá demostrar un impacto medible sobre al menos una de estas métricas:

- Precision@10
- Precision@20
- Recall
- Tiempo de detección
- Falsos positivos
- Falsos negativos

Si una modificación no mejora ninguna de ellas, deberá justificarse antes de aprobarse.

# ARQUITECTURA

Atlas Core debe permanecer independiente.

Todo desarrollo experimental deberá realizarse inicialmente dentro de atlas_live.

Solo después de validarse con evidencia podrá incorporarse al Core.

# REGLA DE ORO

Antes de escribir cualquier código importante deberás comprobar que la propuesta respeta este documento.

Si alguna propuesta contradice esta Constitución, debes detener la implementación y explicarlo antes de realizar cambios.

A partir de la adopción de este documento, toda propuesta de mejora debe indicar explícitamente cuál(es) de los ocho principios de la sección PRINCIPIOS la respaldan. Una propuesta que no pueda anclarse a al menos un principio, o que contradiga la sección LO QUE ATLAS NUNCA HARÁ, no se implementa sin antes señalar el conflicto y resolverlo con quien pueda decidir un cambio a esta Constitución.

# METODOLOGÍA DE PROPUESTAS (evolución por evidencia, no por acumulación)

Adoptada el 2026-08-01. Atlas no crece agregando funciones por intuición: crece mediante evidencia, siguiendo estos ocho pasos, en orden:

1. Identificar un problema real.
2. Proponer una solución.
3. Explicar por qué esa solución respeta esta Constitución.
4. Explicar qué métricas oficiales (sección MÉTRICAS OFICIALES) mejorará.
5. Implementar únicamente si existe una forma objetiva de validar la mejora.
6. Validar primero con datos históricos.
7. Validar después con datos en tiempo real.
8. Solo entonces considerar incorporar el cambio de forma permanente.

Toda propuesta de mejora debe presentarse con este formato exacto, y esperar aprobación antes de implementarse:

```
PROBLEMA:
...

HIPÓTESIS:
...

PRINCIPIOS DE LA CONSTITUCIÓN QUE RESPALDAN ESTA PROPUESTA:
...

IMPACTO ESPERADO:
...

RIESGOS:
...

CÓMO SE VALIDARÁ:
...

CRITERIOS DE ÉXITO:
...
```

Ninguna propuesta salta directamente a "implementar": el diseño se aprueba primero, se valida con datos históricos, luego con datos en tiempo real, y solo entonces se considera permanente. Ver [DECISION_LOG.md](DECISION_LOG.md) para el registro de cada propuesta evaluada bajo este formato.
