# Learning Engine (Atlas Live) -- notas de arquitectura previas a la propuesta formal

**Aclaración de nombre, para no confundir a una sesión futura**: este
documento **no** es el "Learning Engine" de 8 etapas que ya existe en
`/atlas` Core (calibración estadística/basada en reglas, congelado, ver
`ATLAS_ROADMAP.md` línea 38 y el historial de commits "Learning Engine,
Etapa 1" a "Etapa 8"). Es un componente nuevo y distinto, dentro de
`atlas_live/`, para el **aprendizaje continuo del Memory Engine** a
partir de la evidencia que se acumula en vivo (Prediction Journal, Exit
Journal, y eventualmente un Learning Store todavía no construido).

**Estado**: fase futura. **No tiene todavía una propuesta formal de
arquitectura ni validación aprobada** -- ver [DECISION_LOG.md](DECISION_LOG.md)
("El aprendizaje continuo... será una nueva fase, con su propia
propuesta de arquitectura y validación", 2026-08-02). Este documento
existe para no perder las reglas de funcionamiento que ya se definieron
por adelantado, antes de que exista esa propuesta.

**Sobre Atlas Alpha 1.0**: queda **construido y listo para su primera
validación en mercado real** (no "terminado" ni "cerrado" -- corrección
explícita del usuario, 2026-08-02). El Memory Engine sigue congelado tal
como se declaró en `MEMORY_ENGINE.md`; nada de lo que sigue en este
documento se implementó todavía.

---

## 1. Qué ya está construido (estructura, sin cálculo)

Dos indicadores permanentes en la barra superior de la Cabina del
Piloto, aprobados e incorporados el 2026-08-02:

- **🧠 Aprendizaje** -- progreso del ciclo de aprendizaje actual.
- **🎯 Confianza de Atlas** -- confianza global del sistema completo.

Implementación actual (solo estructura, sin cálculo real):
- `atlas_live/memory/learning_status.py` -- `get_learning_status()` y
  `get_atlas_confidence()`, ambas devuelven hoy un estado honestamente
  vacío/no disponible, documentado en el docstring del módulo.
- `atlas_live/server.py` -- endpoint `/api/learning-status`.
- `atlas_live/static/cabina/` (`index.html`, `cabina.js`, `cabina.css`) --
  renderizado en la barra superior, con polling cada 60s.

---

## 2. Reglas de funcionamiento aprobadas (2026-08-02) -- cálculo NO implementado todavía

Estas reglas gobiernan cómo deberá comportarse el cálculo real cuando se
construya (fase futura, con su propia propuesta). Se documentan ahora
para que ninguna implementación futura las tenga que redescubrir.

### 2.1 -- 🧠 Aprendizaje: máquina de estados del ciclo de aprendizaje

- El porcentaje representa el **progreso del ciclo actual de
  aprendizaje** -- no acumula entre ciclos, es específico del ciclo en
  curso.
- Debe **crecer únicamente con evidencia nueva acumulada** -- ningún
  otro factor (tiempo transcurrido, actividad del mercado, etc.) puede
  mover este porcentaje.
- Al llegar al umbral definido para la comparación (umbral todavía sin
  definir -- depende del diseño aprobado del Learning Engine), **el
  ciclo no se reinicia automáticamente**.
- En ese momento, el estado pasa a **"Comparación en curso"**.
- Un ciclo de aprendizaje nuevo **solo puede comenzar después de que**:
  1. la comparación (Learning Comparator, no implementado) termine, **y**
  2. se cree una nueva versión validada del conocimiento (una nueva
     versión del Memory Store / baseline -- implica que en algún momento
     va a hacer falta un concepto de **versionado** del Memory Store,
     hoy inexistente; queda anotado como necesidad futura, no se
     construye ahora).

Estados conocidos hasta ahora (pueden refinarse en la propuesta formal):
`Observando` → `Aprendiendo` → `Comparación en curso` → (nueva versión
validada) → nuevo ciclo (`Observando`/`Aprendiendo`).

### 2.2 -- 🎯 Confianza de Atlas: nunca un número arbitrario

- Representa el **estado actual del sistema completo** -- no el
  resultado de una predicción puntual.
- Debe poder **subir o bajar con el tiempo** (no es un contador
  monótono ni un promedio histórico fijo).
- **Nunca debe ser un número arbitrario.** Todo valor mostrado debe
  poder **explicarse indicando qué factores contribuyeron a ese valor**
  -- el cálculo futuro no puede devolver un porcentaje sin también poder
  justificar de dónde sale (mismo espíritu que ya rige el resto del
  proyecto: nunca reportar un número sin su evidencia, ver
  `base_rates.BaseRateResult`, que siempre viaja con `sample_size`,
  `wilson_lower_bound` y `reason`).
- Ya documentado antes (`learning_status.get_atlas_confidence()`): se
  calculará comparando Memory Store (memoria oficial), Learning Store
  (evidencia reciente, todavía no existe), Prediction Journal y Exit
  Journal.
- **Nunca debe depender de una sola operación ni de un solo día.**

---

## 3. Qué falta antes de poder implementar esto

No se resuelve nada de lo siguiente ahora -- quedan anotadas como
preguntas abiertas para la futura propuesta formal:

- Definir el **umbral** de evidencia nueva que dispara "Comparación en
  curso" (días, número de observaciones, o ambos).
- Diseñar el **Learning Store** (dónde y cómo se acumula la evidencia
  nueva -- hoy `live_integration.py` no escribe de vuelta en el Memory
  Store, ver `PRIMER_DIA_OPERACION_ATLAS_ALPHA.md`, sección 6).
- Diseñar el **Learning Comparator** (qué significa "comparar" la
  evidencia reciente contra la memoria oficial, y qué convierte esa
  comparación en una "nueva versión validada del conocimiento").
- Diseñar el **versionado del Memory Store** (cómo conviven la versión
  vigente y una nueva versión validada sin perder la trazabilidad de
  Atlas Alpha 1.0 como baseline).
- Definir la fórmula (o el conjunto de factores) detrás de "Confianza de
  Atlas", y cómo se expone la explicación de cada valor.

---

**Cierre de esta ronda de trabajo (2026-08-02)**: no se agrega más
funcionalidad después de este documento. La primera validación de Atlas
Alpha en condiciones reales de mercado comienza mañana, siguiendo
[PRIMER_DIA_OPERACION_ATLAS_ALPHA.md](PRIMER_DIA_OPERACION_ATLAS_ALPHA.md).
