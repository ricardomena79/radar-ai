# DECISION_LOG.md

Historial oficial de decisiones del proyecto. Toda decisión importante futura debe registrarse aquí antes de darse por adoptada, con Fecha, Problema, Alternativas evaluadas, Decisión tomada, Justificación e Impacto esperado -- ver [ATLAS_CONSTITUTION.md](ATLAS_CONSTITUTION.md).

Las entradas de esta sección quedan reconstruidas a partir de las decisiones reales tomadas durante el desarrollo de Atlas Live / Radar Explosivo (sesión iniciada 2026-08-01), no son hipotéticas.

---

## 2026-08-01 -- Congelamiento de la investigación de RVOL hasta cerrar la validación de 30 días

**Problema**: la hipótesis de RVOL (Propuesta 1 de `RADAR_EXPLOSIVO_V2.md`, y la comparación de 5 escenarios que le siguió) ya mostró evidencia fuerte y consistente con 7-8 días de datos. Seguir profundizando sobre RVOL específicamente con datos parciales arriesga sacar una conclusión prematura antes de tener el dataset completo.

**Alternativas evaluadas**: seguir refinando la hipótesis de RVOL con más experimentos vs. congelarla (no proponer más cambios sobre RVOL) y redirigir el esfuerzo de auditoría a los otros 5 filtros mientras termina la validación.

**Decisión tomada**: congelar RVOL. No se abre ninguna propuesta nueva relacionada con RVOL hasta recalcular la comparación de 5 escenarios con los 30 días completos. Mientras tanto, la auditoría continúa sobre el resto de los filtros.

**Justificación**: decisión directa del usuario -- "considero esta hipótesis suficientemente investigada por ahora." Evita analizar dos variables en movimiento a la vez y da tiempo a que la validación complete su recolección.

**Impacto esperado**: la próxima hipótesis a formalizar (liquidez, ver sección "SIGUIENTE CUELLO DE BOTELLA" de `RADAR_EXPLOSIVO_V2.md`) se investiga sin interferencia de RVOL en el análisis.

---

## 2026-08-01 -- Excepción controlada: avanzar en Alertas en tiempo real (fase 7) mientras la validación histórica sigue en curso

**Problema**: la metodología "un objetivo a la vez" dejaba sin trabajo posible mientras la validación de 30 días corría en segundo plano, porque casi todas las fases restantes de la hoja de ruta dependen de sus resultados o del Radar Explosivo validado.

**Alternativas evaluadas**: esperar sin avanzar nada hasta que cierre la validación vs. identificar una fase con una porción genuinamente independiente (técnica, no solo de calendario) y trabajarla como excepción explícita.

**Decisión tomada**: excepción controlada y aprobada explícitamente por el usuario. Se implementó la infraestructura de notificación de la fase 7 (Alertas en tiempo real) -- 3 canales (navegador, sonido, resaltado visual), arquitectura modular para agregar canales futuros -- separando el "mecanismo técnico" (independiente) del "juicio sobre si la señal es confiable para operar" (que sigue dependiendo de la validación, sin resolverse).

**Justificación**: la infraestructura de notificación reacciona al mismo campo `explosive.eligible` que ya expone el dashboard hoy -- no necesita ningún resultado de los 30 días para funcionar ni para tener valor, y no toca `explosive_engine.py`, `explosive_config.json` ni `/atlas`.

**Impacto esperado**: Atlas ya no depende de que alguien tenga el dashboard abierto para enterarse de una oportunidad nueva. Fase 7 del roadmap pasa de "Pendiente" a "~40% completado" -- ver `ATLAS_ROADMAP.md`.

---

## 2026-08-01 -- Reorganización del dashboard en tres secciones

**Problema**: la interfaz original de Atlas Live mostraba una sola pantalla dominada por la recomendación de Decision Engine ("¿Atlas compraría esto?"), sin distinguir entre oportunidades de alto momentum, el ranking general del mercado y acciones interesantes sin recomendación aún.

**Alternativas evaluadas**: mantener una sola vista vs. dividir en secciones especializadas navegables desde un menú lateral.

**Decisión tomada**: tres secciones (Radar Explosivo, Radar General, Watchlist) con Radar Explosivo como pantalla principal.

**Justificación**: cada sección responde una pregunta distinta; mezclarlas obligaba a Radar Explosivo a heredar la lógica de Decision Engine, que no es su propósito.

**Impacto esperado**: mejor experiencia de usuario, sin tocar Decision Engine ni Atlas Score.

---

## 2026-08-01 -- Radar Explosivo como motor propio, independiente de Decision Engine

**Problema**: la primera versión de Radar Explosivo filtraba sobre `display_decision.code == "SI_COMPRARIA"` (una traducción de la salida de Decision Engine), lo que mezclaba "es una buena inversión" con "se está moviendo rápido ahora".

**Alternativas evaluadas**: (a) seguir filtrando sobre la salida de Decision Engine; (b) construir un motor de puntaje propio dentro de `atlas_live`; (c) implementarlo como el Índice de Explosión (IE) ya reservado como stub vacío en `atlas/engine/explosion_index.py`.

**Decisión tomada**: (b) -- motor propio, 100% dentro de `atlas_live`, cero cambios en `/atlas`.

**Justificación**: Decision Engine responde una pregunta de calidad/confianza de inversión; Radar Explosivo responde una pregunta de velocidad. Usar la salida de Decision Engine como filtro heredaba sesgos que no aplican (ej. mega-caps de alta confianza pero movimiento lento).

**Impacto esperado**: Radar Explosivo deja de depender de Decision Engine; permite penalizar activamente a empresas grandes y lentas cuando existen alternativas de mayor momentum.

---

## 2026-08-01 -- Penalización continua por tamaño, con excepción por catalizador extremo

**Problema**: mega-caps como AMZN o AAPL aparecían como "mejor oportunidad" en pruebas reales, pese a ser estructuralmente incapaces de moverse explosivamente en 5-10 minutos la mayoría de los días.

**Alternativas evaluadas**: prohibir mega-caps por completo (corte binario) vs. penalización logarítmica continua sobre el puntaje final, con una excepción explícita para catalizadores extraordinarios (gap ≥ 5% y RVOL ≥ 5x simultáneos).

**Decisión tomada**: penalización continua + excepción explícita y justificada en el texto de razones mostrado al usuario.

**Justificación**: un corte binario ignoraría el caso real de una mega-cap con una sorpresa de earnings genuina; una penalización continua deja que la "velocidad pesa más que la calidad de la empresa" (instrucción explícita del usuario) sin volverse una regla absoluta sin excepciones.

**Impacto esperado**: verificado con datos reales -- una microcap con señales moderadas (score 89) supera a una mega-cap con catalizador extremo (score 35.8) en las pruebas sintéticas del motor.

---

## 2026-08-01 -- Configuración centralizada en JSON, separada del código del motor

**Problema**: se pidió que todos los umbrales del Radar Explosivo (precio, gap, RVOL, market cap, float, pesos de cada factor) fueran ajustables sin modificar la lógica del motor.

**Alternativas evaluadas**: constantes dentro de los módulos Python vs. un archivo de configuración externo cargado en tiempo de ejecución.

**Decisión tomada**: `atlas_live/explosive_config.json`, con un loader (`explosive_config.py`) que aplica valores por defecto si el archivo falta o está corrupto.

**Justificación**: cumple el pedido explícito de "completamente configurable... para ajustar sin tocar código", y evita que un JSON mal editado tumbe el motor en producción.

**Impacto esperado**: los umbrales pueden ajustarse (fase "Optimización del Radar" del roadmap) sin tocar ningún archivo `.py`.

---

## 2026-08-01 -- Registro de factores "enchufables" en vez de lógica embebida

**Problema**: se pidió que el diseño permitiera agregar nuevos factores (noticias, opciones, short interest, float) en el futuro sin reescribir el motor.

**Alternativas evaluadas**: if/else embebido en la función de puntaje vs. un registro (`FACTORS: List[Factor]`) de funciones puras e independientes.

**Decisión tomada**: patrón de registro (`atlas_live/explosive_factors.py`), donde cada factor es una función aislada que recibe los mismos `ExplosiveInputs` y devuelve un puntaje + una razón en lenguaje natural.

**Justificación**: aislar cada señal reduce el riesgo de que agregar un factor nuevo rompa el cálculo de los demás, y hace que cada factor sea auditable por separado.

**Impacto esperado**: agregar un factor nuevo debería requerir solo una función nueva + una entrada de peso en el JSON de configuración, sin tocar `explosive_engine.py`.

---

## 2026-08-01 -- Modo Diagnóstico instrumentando el motor real, no reconstruyéndolo aparte

**Problema**: se pidió poder auditar cuántas acciones caen en cada etapa del embudo de filtros y por qué, sin convertir el Radar Explosivo en una caja negra.

**Alternativas evaluadas**: reconstruir el embudo evaluando cada símbolo contra cada umbral por separado en un módulo de diagnóstico aparte, vs. instrumentar directamente `explosive_engine.evaluate()` para que deje un rastro (`stage_trace`, `failed_stage`, `metrics`) de su propia ejecución real.

**Decisión tomada**: instrumentar `evaluate()` in situ. Se verificó explícitamente (con casos sintéticos) que el refactor no cambia ningún resultado (mismo `score`, mismo `eligible` que antes de agregar la instrumentación).

**Justificación**: reconstruir el embudo aparte arriesgaba que el diagnóstico divergiera silenciosamente del comportamiento real del motor con el tiempo.

**Impacto esperado**: el modo Diagnóstico es, por construcción, siempre fiel al motor real -- confirmado comparando la API contra la interfaz, número por número, sin discrepancias.

---

## 2026-08-01 -- Validación histórica contra el Universo Racional completo, no la muestra de 200

**Problema**: el escaneo en vivo usa una muestra de 200 símbolos (150 acciones + 50 ETFs). Validar la pregunta "¿el radar detecta a la verdadera ganadora del día?" contra esa misma muestra sesga la prueba a favor del motor si la ganadora real no estaba en la muestra.

**Alternativas evaluadas**: validar contra la muestra de 200 (más rápido) vs. contra el Universo Racional completo (~2577 símbolos, mucho más lento).

**Decisión tomada**: Universo Racional completo. Decisión explícita del usuario: "no me interesa la velocidad... prefiero que tarde más y que la respuesta sea correcta".

**Justificación**: una validación que no puede ver a la verdadera ganadora del día no es una validación honesta de la capacidad de detección del motor.

**Impacto esperado**: validación mucho más lenta (~30 sesiones × 2577 símbolos, con descargas de velas de 5 minutos por día) pero estadísticamente representativa.

---

## 2026-08-01 -- Reconstrucción histórica con velas intradía reales, snapshot a los 10 minutos, sin usar el cierre del día como insumo

**Problema**: para validar honestamente si el radar "detecta antes", los indicadores de entrada no pueden usar información que todavía no existía en el momento simulado (mirar el cierre del día completo sería trampa).

**Alternativas evaluadas**: aproximar con el cierre del día completo (simple, pero con lookahead) vs. reconstruir con velas reales de 5 minutos del propio día, cortadas en el minuto 10 después de la apertura.

**Decisión tomada**: velas de 5 minutos reales. Esto limita la validación a fechas dentro de la ventana de ~60 días que conserva Yahoo Finance para datos intradía (se usó el resultado del día completo únicamente como verdad de referencia -- "quién ganó realmente ese día" -- nunca como insumo del radar).

**Justificación**: es la única forma de medir si el radar detecta la oportunidad antes de que ocurra, no después.

**Impacto esperado**: la validación histórica queda acotada a los últimos ~60 días de mercado como máximo; se documenta esta limitación en vez de ocultarla.

---

## 2026-08-01 -- Cero cambios en `/atlas` durante todo el desarrollo de Radar Explosivo, Diagnóstico, Validación histórica y Explosive DNA

**Problema**: instrucción explícita y repetida del usuario de no modificar Atlas Core en ningún momento de este desarrollo.

**Decisión tomada**: todo el código nuevo (`explosive_engine.py`, `explosive_factors.py`, `explosive_config.py/json`, `explosive_diagnostics.py`, todo `atlas_live/backtest/`) vive exclusivamente dentro de `atlas_live/`. Cuando se necesitaron fórmulas de indicadores (RSI, EMA, ATR, VWAP) para la reconstrucción histórica, se reutilizaron en modo lectura las funciones puras de `atlas.engine.score_engine` y `atlas.engine.momentum_engine`, sin modificarlas.

**Justificación**: Atlas Core está documentado como "congelado en v1.0"; todo lo experimental debe validarse afuera antes de siquiera proponerse como cambio al Core (ver fase "Optimización del Radar" del roadmap, y Principio 3 de la Constitución).

**Impacto esperado**: confirmado con `git status` en cada iteración de esta sesión -- cero archivos dentro de `/atlas` modificados.

---

## 2026-08-01 -- Adopción de la metodología de evolución por evidencia (formato PROBLEMA/HIPÓTESIS/...)

**Problema**: Atlas venía creciendo por acumulación de funciones (Radar Explosivo, Diagnóstico, validación, Explosive DNA) sin un mecanismo formal que obligara a justificar cada adición antes de construirla.

**Alternativas evaluadas**: continuar con revisión caso por caso vs. adoptar un formato obligatorio de propuesta (PROBLEMA, HIPÓTESIS, principios que la respaldan, impacto esperado, riesgos, validación, criterios de éxito) que deba aprobarse antes de implementar.

**Decisión tomada**: formato obligatorio adoptado y formalizado en la sección "METODOLOGÍA DE PROPUESTAS" de `ATLAS_CONSTITUTION.md`. Ninguna mejora futura se implementa sin ese diseño aprobado, validación histórica y luego validación en tiempo real, en ese orden.

**Justificación**: decisión directa del usuario -- "no quiero más cambios por intuición... quiero que Atlas evolucione únicamente mediante evidencia."

**Impacto esperado**: cada propuesta futura queda trazable y auditable en este mismo archivo, con su justificación explícita contra la Constitución, en vez de perderse en el historial de una conversación.

---

## 2026-08-01 -- Adopción de ATLAS_CONSTITUTION.md como autoridad máxima del proyecto

**Problema**: el proyecto necesitaba un documento de gobernanza estable que sobreviva a decisiones puntuales de cualquier conversación futura.

**Decisión tomada**: creación de `ATLAS_CONSTITUTION.md` con misión, objetivo, 8 principios, límites explícitos ("lo que Atlas nunca hará"), métricas oficiales y regla de que toda propuesta futura debe citar qué principio la respalda.

**Justificación**: decisión directa del usuario, para evitar que el proyecto derive hacia convertirse en un screener genérico o una herramienta de inversión de largo plazo.

**Impacto esperado**: toda propuesta de mejora a partir de este punto debe evaluarse contra este documento antes de implementarse.

---

## 2026-08-02 -- Congelamiento de Atlas Alpha 1.0 como baseline oficial del Memory Engine

**Problema**: el Memory Engine se construyó por una secuencia de propuestas aprobadas por separado (diseño original, plan de 8 entregables, Ranking Score de desempate, integración en tiempo real, Modo Interactivo Continuo) sin un punto de referencia único contra el cual medir mejoras futuras -- sin una baseline explícita, cualquier cambio posterior corre el riesgo de evaluarse contra "lo que había antes" de forma ambigua, no contra un estado concreto y documentado.

**Decisión tomada**: congelar el estado actual del Memory Engine como **Atlas Alpha 1.0** (2026-08-02) -- primera versión funcional capaz de generar rankings, aprender, registrar y sellar predicciones, calificarlas automáticamente, recalibrarse diariamente, y mantenerse activa durante toda la sesión de trabajo (Modo Interactivo Continuo). Declarada explícitamente en [MEMORY_ENGINE.md](MEMORY_ENGINE.md) y en el checklist maestro de `ATLAS_MASTER_DOCUMENT.md` (sección 18, bloque K, punto 75). A partir de esta versión, **cualquier mejora al Memory Engine debe demostrar una mejora medible respecto a Atlas Alpha 1.0 antes de incorporarse**.

**Justificación**: decisión directa del usuario, tras aprobar la integración en tiempo real y el Modo Interactivo Continuo. Es la misma lógica de evolución por evidencia que ya rige el resto del proyecto (Principio 2 de la Constitución), aplicada ahora como una baseline formal y nombrada, no solo como un principio general.

**Impacto esperado**: toda propuesta futura sobre el Memory Engine (nuevas condiciones, otra fórmula de Ranking Score, checkpoints intermedios, etc.) debe incluir una comparación explícita contra Atlas Alpha 1.0 -- mismo tipo de comparación ya usada para el Ranking Score (posición y Precision@10/@20 antes/después) -- antes de considerarse para incorporarse.

---

## 2026-08-02 -- Atlas Alpha 1.0: "construido, listo para primera validación" (no "terminado"); reglas de funcionamiento del Learning Engine documentadas por adelantado

**Problema**: dos cosas para dejar sin ambigüedad antes de la primera validación en mercado real de mañana. (1) Tras conectar los últimos paneles de la Cabina del Piloto se describió a Atlas Alpha 1.0 como "formalmente cerrado" -- el usuario corrigió ese estado: está construido, pero todavía no validado en condiciones reales, por lo que "terminado" es prematuro. (2) Se agregaron dos indicadores permanentes en la Cabina (🧠 Aprendizaje, 🎯 Confianza de Atlas) antes de que exista una propuesta formal del Learning Engine (aprendizaje continuo) -- sin fijar sus reglas de funcionamiento ahora, se corría el riesgo de que una implementación futura las definiera de forma distinta a lo ya decidido en esta conversación.

**Alternativas evaluadas**: (a) no documentar nada todavía y esperar a la propuesta formal completa del Learning Engine; (b) documentar solo las reglas ya dadas por el usuario, sin implementar ningún cálculo, dejando explícitamente abiertas las preguntas de diseño que todavía faltan (umbral, Learning Store, Learning Comparator, versionado del Memory Store).

**Decisión tomada**: (b). Se creó [LEARNING_ENGINE.md](LEARNING_ENGINE.md) -- notas de arquitectura previas a la propuesta formal, explícitamente distinguido del "Learning Engine" de 8 etapas ya existente y congelado en `/atlas` Core (para no confundir ambos en una sesión futura). Documenta: la máquina de estados del ciclo de Aprendizaje (`Observando` → `Aprendiendo` → `Comparación en curso` al llegar al umbral, sin reinicio automático, hasta que termine la comparación y se cree una nueva versión validada del conocimiento -- recién ahí puede empezar un ciclo nuevo) y el requisito de explicabilidad de la Confianza de Atlas (nunca un número arbitrario, siempre debe poder señalar qué factores contribuyeron, sube y baja con el tiempo, nunca depende de un solo día). Ningún cálculo se implementó -- `atlas_live/memory/learning_status.py` sigue devolviendo el mismo estado honestamente vacío que antes, ahora con un puntero a este documento.

**Justificación**: decisión directa del usuario -- "No considero Atlas Alpha 1.0 terminado todavía. Lo considero construido y listo para su primera validación en mercado real" y "Solo dejar documentadas estas reglas como parte de la arquitectura del Learning Engine. Después de eso, no agregaremos más funcionalidades." Mismo principio ya aplicado en todo el proyecto: documentar decisiones de arquitectura antes de que el código las tenga que redescubrir, sin adelantar implementación sin propuesta aprobada.

**Impacto esperado**: cuando se formalice la propuesta del Learning Engine, debe partir de estas reglas (no puede contradecirlas sin una nueva decisión explícita del usuario). Mientras tanto, ningún comportamiento del sistema cambió -- Atlas Alpha 1.0 queda exactamente como estaba, listo para la primera validación en mercado real que comienza mañana (ver [PRIMER_DIA_OPERACION_ATLAS_ALPHA.md](PRIMER_DIA_OPERACION_ATLAS_ALPHA.md)).

---

## 2026-08-02 -- Data Fusion Engine: propuesta formal aprobada solo como diseño, implementación explícitamente diferida

**Problema**: inmediatamente después de cerrar la etapa de construcción y fijar el backlog diferido (Learning Store, Learning Comparator, Confianza real, Data Fusion Engine, nuevos proveedores -- "ninguno se implementará antes de obtener varios días de evidencia en tiempo real"), se pidió implementar directamente la arquitectura de múltiples proveedores de datos -- contradiciendo esa misma regla recién fijada.

**Alternativas evaluadas**: se presentaron tres opciones explícitamente vía pregunta al usuario -- (a) mantener el plan original, no tocar nada todavía; (b) override consciente, implementar ya; (c) solo propuesta formal de arquitectura, sin código. El usuario eligió (c), y además confirmó explícitamente que sigue sin querer implementación antes de la validación en mercado real.

**Decisión tomada**: se redactó [DATA_FUSION_ENGINE_PROPUESTA.md](DATA_FUSION_ENGINE_PROPUESTA.md) -- propuesta formal completa (PROBLEMA/HIPÓTESIS/PRINCIPIOS/ARQUITECTURA/INTERFACES/FAILOVER/VALIDACIÓN ENTRE FUENTES/IMPACTO/PLAN DE MIGRACIÓN/RIESGOS/CRITERIOS DE ÉXITO), sin escribir ni una línea de código. Hallazgo clave del diseño: `atlas/data/providers/base.py` (`DataProvider`) y `atlas/data/collectors/data_collector.py` (`DataCollector`) ya existen en el Core congelado, ya anticipan múltiples proveedores (docstring de `DataProvider` los menciona explícitamente), y **todo** `atlas_live/` y `/atlas` Core ya pasan exclusivamente por `DataCollector` -- confirmado revisando el código, no supuesto. Esto reduce el "Data Fusion Engine" a una nueva implementación de `DataProvider` (`FusionProvider`, con failover) construida en `atlas_live/data_fusion/` (nunca dentro de `/atlas`, por la regla de arquitectura de la Constitución), que migra únicamente los 3 call sites de `atlas_live/` que hoy instancian `YahooFinanceProvider()` a mano. Se recomienda Alpaca como segundo proveedor (ya evaluado y recomendado en `DATA_PROVIDER_EVALUATION.md`), y se recomienda explícitamente **no** usar TradingView (sin API oficial, contradice el pedido de "fuente oficial compatible").

**Justificación**: decisión directa del usuario, tras marcarle la contradicción con la regla que él mismo había fijado minutos antes y ofrecerle las tres alternativas explícitamente -- "Opción 3... No quiero implementarlo antes de obtener varios días de evidencia en mercado real. Sin embargo, esta propuesta debe contemplar que Atlas, en su versión madura, nunca dependerá de una sola fuente de datos."

**Impacto esperado**: cuando llegue el momento (varios días de evidencia real de Atlas Alpha 1.0), la implementación puede empezar directamente desde este diseño sin tener que rediscutirlo. Ningún comportamiento del sistema cambió hoy -- cero código nuevo, cero archivos de `/atlas` o `atlas_live/data_fusion/` creados.

---

## 2026-08-02 -- Addendum: trazabilidad de precio (fuente, tipo de sesión, hora) en el Data Fusion Engine

**Problema**: comparación real entre Atlas y TradingView, mismo símbolo y momento: Atlas mostraba el precio de sesión regular (Yahoo), TradingView el de after-hours -- ambos correctos, pero mostrados sin contexto parecían datos contradictorios. El usuario pidió una solución de arquitectura, no un parche, antes del primer día de validación en mercado real.

**Alternativas evaluadas**: se investigó la causa raíz en vivo (no se asumió) -- se consultó `yf.Ticker("AAPL").info` en este entorno y se confirmó que Yahoo Finance sí expone `postMarketPrice`/`preMarketPrice`/`marketState`, pero `YahooFinanceProvider._quote_from_info()` nunca los lee, solo `regularMarketPrice`. Con eso confirmado, las alternativas eran: (a) arreglo puntual en `YahooFinanceProvider` para leer también los campos de after-hours; (b) extender la propuesta ya aprobada del Data Fusion Engine para que la trazabilidad de precio (fuente/tipo de sesión/hora) sea un requisito de arquitectura desde la Etapa 0, no un parche aislado.

**Decisión tomada**: (b). Se agregó un ADDENDUM a [DATA_FUSION_ENGINE_PROPUESTA.md](DATA_FUSION_ENGINE_PROPUESTA.md) (mismo documento, no uno nuevo, para no fragmentar la fuente de verdad): `Quote` gana dos campos aditivos (`price_type`, `source`; `timestamp` ya existía) sin romper nada existente; regla de UI permanente en la Cabina (todo precio se muestra con fuente + tipo de sesión + hora, nunca aislado); cuando dos fuentes reportan el mismo `price_type` se calcula discrepancia, cuando reportan `price_type` distintos (el caso real de hoy) se muestran ambos como información complementaria, no como una discrepancia; el failover se registra como evento nuevo (`provider_failover`) en el Timeline de Mission Control ya existente, no un mecanismo aparte. Sigue sin implementarse nada -- solo diseño.

**Justificación**: decisión directa del usuario -- "No quiero un parche. Quiero una solución de arquitectura" -- y su pedido explícito de que esto "quede documentada como parte permanente del proyecto, porque será la base del Learning Engine y del futuro Data Fusion Engine". Mismo principio de causa raíz sobre síntoma ya aplicado en el resto del proyecto (ej. el hallazgo de que `live_integration.py` no realimenta el Memory Store, documentado en vez de parcheado).

**Impacto esperado**: cuando se implemente la Etapa 0 del Data Fusion Engine (todavía sin fecha, gated a varios días de evidencia real), la trazabilidad de precio queda incluida desde el inicio, no como una mejora posterior -- elimina la confusión Atlas-vs-TradingView usando exclusivamente Yahoo Finance, sin necesitar un segundo proveedor real todavía.

---

## 2026-08-02 -- Implementada la Etapa 0 del addendum de trazabilidad de precio (override explícito, antes de la validación en mercado real)

**Problema**: el addendum de trazabilidad de precio quedó como diseño aprobado, gated a varios días de evidencia real. El usuario decidió adelantar específicamente la Etapa 0 (la única que no depende de un segundo proveedor) antes del primer día de validación, con instrucción explícita ("Mañana no existe") y pidiendo verificación de causa raíz antes de tocar código.

**Alternativas evaluadas**: se verificó en vivo (no se asumió) con `yf.Ticker(...).info` sobre KC, SOXL, PRPL y AAPL que Yahoo expone `preMarketPrice`/`postMarketPrice`/`marketState`, confirmando que la causa raíz es exactamente la ya documentada. Sobre dónde vive el cambio: (a) modificar `YahooFinanceProvider.get_quote()` directamente en `/atlas` Core -- cambiaría el comportamiento ya validado de los motores de Core (Decision Engine, Money Flow Engine, etc.) sin pasar por el proceso de validación que exige la Constitución; (b) crear una subclase nueva en `atlas_live/data_fusion/` que sobrescribe solo `_quote_from_info()`, reutilizando el resto del cálculo de la clase base sin tocar `/atlas`. Se eligió (b) y se explicó antes de implementar, según lo pedido ("si detectas una solución mejor, detente y explícamela").

**Decisión tomada**: `Quote` (`atlas/data/models/quote.py`) gana 6 campos aditivos con default (`source`, `price_type`, `market_state`, `price_regular`, `price_premarket`, `price_afterhours`) -- cero cambio de comportamiento para consumidores existentes. Nueva clase `YahooFinanceLiveProvider` (`atlas_live/data_fusion/yahoo_finance_live_provider.py`) selecciona el precio según `marketState` (REGULAR/PRE/POST, con fallback documentado a Regular si falta el dato esperado o el estado no mapea a un tipo conocido -- incluye CLOSED). Migrados los 2 call sites de `scan_worker.py` (no se tocó `live_integration.py::_grade_pending()`, según lo pedido explícitamente de no modificar Prediction Journal). Propagado el contexto de precio de punta a punta: `explosive_engine.py` (metrics) → `demo_ranking.py` (`RankedCandidate`) → `live_integration.py` (`serialize_ranked_candidate`) → Cabina (`cabina.js`/`cabina.css`), mostrado en Hero, Plan B, Explosivas, Momentum, No tocar y Radar Completo. Mission Control registra cada cambio de `marketState` en el Timeline (`event_type="state_changed"`, reutilizando el catálogo ya existente, sin inventar uno nuevo). No se tocó `ranking_score.py`, `base_rates.py`, `calibration_advisor.py`, `classifier.py`, `prediction_journal.py` ni `exit_journal.py`, según lo pedido.

**Justificación**: decisión directa del usuario -- verificación previa confirmada en vivo, luego autorización explícita ("entonces quiero implementar inmediatamente la solución"). Cero funcionalidades nuevas más allá de lo pedido; alcance idéntico al ya aprobado en el addendum de `DATA_FUSION_ENGINE_PROPUESTA.md`.

**Impacto esperado**: verificado con datos reales (KC, PRPL, servidor real, escaneo completo de 192 símbolos) -- el precio, tipo de sesión, fuente y hora aparecen siempre juntos en la Cabina, sin regresión en ningún test existente ni en la demo de Ranking Score (XRX #1/NUWE #2 sin cambios). Mercado real estaba en `CLOSED` durante la verificación (no `POST`), así que el caso premarket/after-hours en vivo se validó con datos sintéticos con la forma exacta de Yahoo -- los 3 estados (REGULAR/PRE/POST) y ambos fallbacks documentados se comportaron como se diseñó.

---

## 2026-08-02 -- Mejora de UX sobre la trazabilidad de precio: indicador visual de sesión, "Precio utilizado" destacado, historial en Mission Control

**Problema**: con `price_type`/`source`/`market_state` ya disponibles en cada `Quote`, la Cabina seguía mostrándolos como texto plano -- el usuario pidió que el estado de mercado fuera "mucho más visual" (no depender de leer texto), que "Precio utilizado" se destacara claramente, y que Mission Control mostrara la hora exacta de cada cambio de sesión para el futuro Learning Engine. También pidió una revisión final de toda la Cabina buscando cualquier precio sin contexto.

**Alternativas evaluadas**: el pedido incluía un punto 4 ("el gráfico mostrado por Atlas debe ser coherente con la sesión") -- se verificó por grep que la Cabina no tiene ningún gráfico/canvas/SVG en ningún panel. Construirlo habría sido una funcionalidad nueva, contradiciendo la instrucción explícita de cierre del propio usuario ("No quiero agregar funcionalidades nuevas"). Se preguntó y el usuario confirmó omitirlo, dejándolo para cuando se diseñe el gráfico interactivo de Atlas como tarea independiente.

**Decisión tomada**: se implementaron los puntos 1, 2, 3, 5 y 6. (1) Badge visual de `market_state` (🟢 REGULAR / 🟡 PREMARKET / 🟣 AFTER-HOURS / ⚫ CLOSED, distinto de `price_type` a propósito -- CLOSED no debe disfrazarse de "Regular" solo porque ese es el precio correcto a mostrar) en `cabina.js`/`cabina.css`, en todos los lugares donde ya aparecía un precio. (2) "Precio utilizado" separado visualmente del desglose Regular/Premarket/After-hours, con una etiqueta "EN USO -- Ranking Score". (3) Confirmado que el mecanismo ya existente (poll cada 30-60s) actualiza el indicador solo, sin reiniciar el servidor -- no hizo falta código nuevo. (5) `/api/mission-control` (`server.py`) extendido con `market_state_history`, leyendo `timeline.get_recent_events()` ya existente (sin tabla nueva); nuevo bloque en la Cabina mostrando hora exacta de cada cambio. (6) Revisión completa: se encontraron y corrigieron dos paneles con precio sin contexto -- `renderOportunidad()` (detalle completo, le faltaba el desglose) y `renderEtf()` (sigue en MOCK, se le agregó la etiqueta honesta "Dato simulado -- sin fuente real todavía" en vez de inventar contexto).

**Justificación**: decisión directa del usuario, aprovechando los campos ya construidos en la corrección de precio anterior -- "Ahora quiero dar un paso más aprovechando que Atlas ya conoce... marketState, regularMarketPrice, preMarketPrice, postMarketPrice, source, price_type."

**Impacto esperado**: mejora de UX pura, cero cambios de datos o cálculo. Nota operativa: durante la verificación se encontraron procesos Python huérfanos de sesiones anteriores todavía sirviendo en el puerto 5000 con código desactualizado -- limpiados (`Stop-Process`); a tener en cuenta para futuras verificaciones (confirmar `Get-Process python` antes de asumir que un servidor nuevo está sirviendo las respuestas).

---

## 2026-08-02 -- Auditoría del flujo de precio + regla de arquitectura: `YahooFinanceLiveProvider` como único punto autorizado para construir un `Quote` en `atlas_live`

**Problema**: tras implementar la trazabilidad de precio, hacía falta confirmar con evidencia de código (no supuestos) que existe un único punto de verdad para el precio de punta a punta, y declarar formalmente una regla que proteja esa propiedad hacia el futuro.

**Alternativas evaluadas**: la auditoría (grep exhaustivo sobre `regularMarketPrice`/`preMarketPrice`/`postMarketPrice`/`marketState`, sobre instanciaciones de `YahooFinanceProvider`/`YahooFinanceLiveProvider`, y sobre `import yfinance`) confirmó que solo 2 archivos leen esos 4 campos de Yahoo (`yahoo_finance.py` base y `yahoo_finance_live_provider.py`), y encontró 4 hallazgos de caminos alternativos: (A) `live_integration.py::_grade_pending()` usa el proveedor base sin sesión, decisión ya explícita del usuario; (B) los 5 motores de `/atlas` Core tienen un fallback dormido a `YahooFinanceProvider()`; (C) `investigator.py`, código huérfano nunca importado; (D) `atlas_live/backtest/`, pipeline histórico separado, no en vivo. Ninguno de los 4 está activo en el flujo en vivo actual (confirmado, no supuesto).

**Decisión tomada**: se declaró oficialmente, en `DATA_FUSION_ENGINE_PROPUESTA.md` (nueva sección "REGLA DE ARQUITECTURA") y en el docstring de `yahoo_finance_live_provider.py`, que `YahooFinanceLiveProvider` es el único punto autorizado para construir un `Quote` en vivo dentro de `atlas_live` -- ningún módulo nuevo puede llamar a `yfinance` directamente ni instanciar `YahooFinanceProvider` (base) por su cuenta; toda funcionalidad nueva debe pasar por `DataCollector`. Los Hallazgos A y B quedan documentados como excepciones anteriores a la regla, sin resolver -- la regla rige hacia adelante, no los reescribe retroactivamente.

**Justificación**: decisión directa del usuario -- "Quiero declarar oficialmente que YahooFinanceLiveProvider es el único punto autorizado para construir un Quote en Atlas Live... Eso deja la arquitectura protegida para el futuro." Mismo corolario del Principio 5 de la Constitución ("el proveedor de datos nunca podrá estar acoplado al motor") ya citado en la propuesta original del Data Fusion Engine.

**Impacto esperado**: ningún cambio de comportamiento -- es una regla de gobernanza, no código nuevo. Cualquier propuesta futura que agregue un módulo con acceso a datos de mercado debe poder señalar que pasa por `DataCollector`/`YahooFinanceLiveProvider`, o justificar explícitamente por qué es una excepción (mismo estándar que ya exige la Constitución para citar principios que respaldan una propuesta).

---

## 2026-08-02 -- Auditoría de exactitud del dato (SOXL/KC/PRPL) + descubrimiento de la sesión "Overnight" (Blue Ocean ATS)

**Problema**: la auditoría de arquitectura (entrada anterior) demostró un único flujo de datos, pero no demostró que el valor mostrado fuera correcto. El usuario pidió una auditoría de exactitud: comparar, símbolo por símbolo, el dato crudo de Yahoo, lo que Atlas selecciona, y el precio real de TradingView.

**Alternativas evaluadas**: se descartó explicar la primera discrepancia observada ("Atlas: 8.10 vs. TradingView: 8.38") con un párrafo más largo -- el usuario lo rechazó explícitamente ("No quiero resolver esto con una explicación más larga. Quiero resolverlo con datos"). Se evaluó entre (a) agregar una nota contextual más específica (propuesta intermedia, rechazada) y (b) modelar la cuarta sesión como un campo de datos real, siempre `None` hasta que exista un proveedor, mostrado como una fila más de la interfaz.

**Decisión tomada**: (b). Verificado con evidencia real (yfinance en vivo + navegador real contra tradingview.com, no simulado) que el precio principal de Atlas coincide exacto con Yahoo Finance y con el precio principal de TradingView en los 3 símbolos (SOXL 114.72, KC 11.15, PRPL 8.10 -- sesión Regular, mercado `CLOSED`). La única diferencia real es un indicador secundario de TradingView, "Overnight via BOATS" (Blue Ocean ATS, venue de trading nocturno ~20:00-04:00 ET), que ningún proveedor de Atlas entrega. Se agregó `Quote.price_overnight: Optional[float] = None` (aditivo), propagado de punta a punta por el mismo camino que `price_regular`/`price_premarket`/`price_afterhours`, y una fila nueva en la Cabina ("Overnight (Blue Ocean ATS): No disponible con el proveedor actual") -- sin párrafo explicativo, el dato mismo es la respuesta. Documentado como requerimiento futuro en `DATA_FUSION_ENGINE_PROPUESTA.md`, sección "SESIÓN OVERNIGHT": cuando un proveedor futuro del Data Fusion Engine lo entregue, solo necesita poblar ese campo -- ningún otro archivo debería requerir cambios.

**Justificación**: decisión directa del usuario -- "Quiero que Atlas sea completamente transparente... No quiero que esta limitación quede escondida." No se implementó ningún proveedor real para Overnight (Blue Ocean ATS no tiene cobertura confirmada en ningún proveedor evaluado hasta ahora) -- solo la estructura de datos, como se pidió explícitamente ("No implementes todavía la cuarta sesión").

**Impacto esperado**: la Cabina ahora es honesta sobre una categoría de información que no posee, en vez de omitirla o exigirle al usuario que confíe en un texto. Cuando se evalúe un proveedor con cobertura de Blue Ocean ATS (fuera de alcance hoy), la incorporación es aditiva, sin tocar `explosive_engine.py`, `demo_ranking.py`, `live_integration.py` ni la Cabina -- verificable revisando que todos ya propagan `price_overnight` desde ahora.

---

## 2026-08-03 -- Auditoría de la Cabina (real vs. simulado) + Calidad del Mercado con datos reales, sin veredicto compuesto

**Problema**: el usuario detectó que el banner global `⚠ DATOS SIMULADOS -- estructura en revisión...` contradecía el estado real del sistema (la mayoría de los paneles ya consumían datos reales). Pidió una auditoría completa panel por panel (tabla: Panel/Endpoint/Datos reales/Datos simulados/Estado) y la eliminación de cualquier mensaje de simulación en paneles ya reales.

**Alternativas evaluadas**: (a) actualizar el texto del banner global; (b) eliminarlo y reemplazarlo por etiquetas individuales por panel. Se eligió (b) -- un mensaje único no puede describir con precisión 22 paneles con estados mixtos.

**Decisión tomada**: auditoría completa (22 paneles/widgets: 15 reales vía 7 endpoints, 7 simulados). Eliminado `.sim-banner` de `index.html`/`cabina.css`. Agregada una etiqueta `MOCK` individual, con `title` explicando el motivo, en los 6 paneles que siguen simulados: barra de actividad, Atlas Opina, Alertas, ¿Por qué NO?, ETF, Configuración (Calidad del Mercado se excluye de esta lista porque se conectó a datos reales en el mismo turno, ver abajo). Se encontró y corrigió un hallazgo operativo real durante la verificación: un servidor huérfano de otro worktree (`setup-atlas-live-env-2e5642`) seguía respondiendo en el puerto 5000 con código desactualizado, enmascarando los cambios -- identificado comparando el HTML servido contra el HTML en disco, resuelto matando el proceso y arrancando uno nuevo desde el worktree correcto.

Como siguiente prioridad, el usuario pidió reemplazar el panel "Calidad del Mercado" (mock) por una versión real -- explícitamente **sin que Claude inventara una fórmula compuesta**. Se propuso el mapeo de datos reales para 5 factores candidatos (VIX, % de candidatos que superan el Ranking, oportunidades de alta confianza, % de falsas rupturas, amplitud de mercado por sector) -- 3 disponibles de inmediato, 2 requieren trabajo adicional (falsas rupturas solo se conoce al calificar; amplitud requiere exponer `MoneyFlowEngine.top(N)` completo, hoy solo se sirve el sector líder). El usuario confirmó: mostrar los factores reales por separado, sin ningún veredicto compuesto ("BUENA/REGULAR/MALA"), hasta que exista una fórmula validada. Implementado: `renderMarketQuality()` (`cabina.js`) ahora muestra VIX real (`context.vix_price`, umbral reutilizado de `RISK_VIX_HIGH=25`/`RISK_VIX_LOW=18` ya existentes en `scan_worker.py`, no inventado), % que supera el Ranking, conteo de alta confianza, microcaps con evidencia, y símbolos "No tocar" -- sin agregar ningún endpoint nuevo (todo ya viajaba por `/api/ranking` y `/api/memory-ranking`). `MOCK.marketQuality` retirado de `mock_data.js`.

**Justificación**: decisión directa del usuario -- "A partir de ahora quiero que la Cabina sea completamente honesta... sin mensajes globales que induzcan a error" y, sobre la fórmula, "No inventes una fórmula... prefiero mostrar los factores por separado antes que un juicio compuesto que pueda inducir a error." Mismo principio ya aplicado en todo el proyecto (Principio 3 de la Constitución: ningún algoritmo nuevo sin validar).

**Impacto esperado**: verificado con navegador real contra el servidor correcto -- VIX 16.1 (Baja), 167/192 candidatos superan el Ranking (87%), 19 oportunidades de alta confianza, todo consistente con el escaneo real de 192 símbolos. La fórmula compuesta de Calidad del Mercado queda como decisión pendiente, a definir junto con el usuario una vez exista evidencia suficiente para ponderar los factores.

---

## 2026-08-03 -- Regla de consenso Radar Explosivo + Memory Engine (permanente) -- auditoría en tiempo real detecta un candidato inelegible recomendado

**Problema**: el usuario detectó en la Cabina real que "Explosivas" recomendaba PRPL, un símbolo sin actividad real de premarket. Auditoría con datos reales (04:46 ET, premarket genuino) confirmó la causa: `relative_volume` (4.57x) y `gap_pct` se calculan con datos de la última sesión regular completada (Yahoo no expone volumen de premarket -- confirmado sobre el `.info` completo de yfinance, ningún campo `preMarketVolume` existe), y ese valor cayó en una banda históricamente confiable del Memory Engine (semáforo verde) -- **a pesar de que Radar Explosivo ya había rechazado el símbolo** (`eligible_radar=false`, `failed_stage="liquidity"`). La Cabina nunca revisaba ese veto.

**Alternativas evaluadas**: (a) parchear solo el filtro de "Explosivas" en `cabina.js`; (b) convertir el consenso Radar Explosivo + Memory Engine en una regla estructural, aplicada en el servidor (orden del ranking) y reforzada en cada consumidor (Cabina, Prediction Journal). El usuario pidió explícitamente (b) -- "quiero convertirlo en una regla permanente de Atlas."

**Decisión tomada**: (b), implementada en una sola fuente de verdad. `demo_ranking.build_ranking()`/`live_integration.build_live_ranking()` ordenan por `(eligible_radar, ranking_score)` -- un candidato rechazado por Radar Explosivo nunca puede quedar por encima de uno aceptado. `live_integration.run_live_cycle()` filtra a elegibles antes de armar el snapshot dinámico y el sellado del Prediction Journal -- un símbolo rechazado nunca queda registrado ahí, ni en una posición baja. Nuevo campo `RankedCandidate.radar_excluded_reason` propagado hasta la Cabina. `cabina.js`: `_explosivasReal()`/`_momentumReal()` exigen `eligible_radar` explícitamente; `_noTocarReal()` incluye a cualquier inelegible; `renderHero()`/`renderPlanB()`/`renderOportunidad()` verifican elegibilidad antes de recomendar; `renderRadarCompleto()` muestra el motivo exacto del rechazo, no solo "Sí/No". Auditoría de seguimiento (pedida explícitamente por el usuario antes de aprobar) encontró y corrigió 2 lugares adicionales que también se habían saltado la regla: "Calidad del Mercado" (4 de 5 factores no filtraban por `eligible_radar`) y el snapshot descargable "Guardar Estado del Día" (tomaba `candidates[0]`/`[1]` crudos en vez de replicar el guardia de Hero/Plan B).

**Justificación**: decisión directa del usuario -- "El Radar Explosivo es el filtro de operabilidad. El Memory Engine evalúa la evidencia histórica. La recomendación final solo puede existir cuando ambos están de acuerdo. Si cualquiera de los dos rechaza un símbolo, Atlas debe explicarlo, no recomendarlo." Y, en la auditoría de seguimiento: "Si existe aunque sea un lugar donde todavía pueda aparecer como candidato positivo, corrígelo antes de cerrar esta tarea."

**Impacto esperado**: `test_live_integration.py` actualizado (no solo parchado) para verificar explícitamente que los candidatos inelegibles sintéticos (`DEBIL1`/`DEBIL2`) nunca quedan sellados ni en el snapshot dinámico -- antes de esta regla, se sellaban igual y solo quedaban sin calificar por falta de cotización. Sin regresión en el Ranking Score (XRX #1/NUWE #2, demo 2026-07-30) ni en el resto de la batería. Documentado como regla permanente en `MEMORY_ENGINE.md`, sección "REGLA DE CONSENSO".

---

## 2026-08-06 -- Investigación 3: gate de liquidez/RVOL bloqueaba la elegibilidad en premarket (Volume=0 de Yahoo)

**Problema**: el Motor Predictivo (Fase 1.1, Sprint 3), validado contra 30 días reales reconstruidos, daba una mediana de **-330 minutos** para la condición más fuerte (`gap_pct >= 10%`) -- el sistema aprendía que el movimiento ya había pasado 5.5 horas *antes* de que Atlas detectara elegibilidad, en vez de anticiparse. Causa raíz confirmada con datos reales: `explosive_engine.py` calcula `dollar_volume = last_price * quote.volume` (gate de liquidez) y `rvol = quote.volume / average_volume` (gate de RVOL); Yahoo Finance reporta `Volume = 0` en el 100% de las velas de premarket (el precio sí se mueve vela a vela, prueba de operaciones reales -- confirmado sobre BFLY/2026-06-18, 20+ velas consecutivas con Volume=0 y Close cambiando), bloqueando ambos gates estructuralmente. De 117 casos reales con gap>=10%, 67 recién quedaban elegibles a las 09:30 ET (apertura regular) y 41 nunca llegaban a serlo.

**Alternativas evaluadas** (con evidencia real antes de implementar): (a) rango de precio High-Low en dólares como sustituto de liquidez -- probada sobre 14 casos reales de EXPLOSION, dio valores de $0-$110 en 13/14 casos, unidad sin sentido (dólares² por acción, no mide liquidez), descartada; (b) contador de cambios de precio entre velas -- requiere cambiar la interfaz de `explosive_engine.evaluate()` para recibir la serie cruda de velas, mucho más invasivo que lo mínimo necesario, descartada; (c) volumen promedio diario real del símbolo (`average_volume * price`) como piso de liquidez -- probada sobre los mismos 14 casos, 14/14 superan el umbral existente ($2M) sin necesidad de un umbral nuevo, mediana $34.7M; (d) omitir el gate directamente durante premarket -- elimina el piso de liquidez en vez de sustituirlo por evidencia, descartada.

**Decisión tomada**: (c). Cambio quirúrgico en `explosive_engine.py`, activado únicamente cuando `quote.market_state` es uno de los dos estados reales de premarket que Yahoo reporta (`PRE` o `PREPRE` -- ambos confirmados en producción real vía `/api/mission-control`; la primera versión del fix solo cubría `PRE`, corregida tras validar en Atlas Live, no solo en backtest) y el volumen no está reportado. Liquidez: sustituye `dollar_volume` por `average_volume * last_price` (mismo umbral, sin inventar uno nuevo). RVOL: se omite el gate en ese caso específico -- sin sustituto honesto posible (no existe línea base real de "premarket típico" por símbolo), `rvol` queda `None`, no fabricado. Sesión regular/after-hours/closed: comportamiento sin ningún cambio (verificado byte-idéntico). `historical_scan.py::reconstruct_symbol()` completa `market_state` real (antes quedaba `None`) reutilizando `market_hours.get_session()`, necesario para que el gate se active también en la reconstrucción histórica.

**Justificación**: decisión directa del usuario, actuando como Arquitecto Principal de Atlas -- "Quiero que diseñes una solución para que Atlas pueda evaluar correctamente el premarket sin degradar el comportamiento del mercado regular... No acepto: 'es una limitación de Yahoo' / 'evaluemos otro proveedor' / 'aceptemos el comportamiento actual'." Metodología exigida: identificar la dependencia exacta, diseñar alternativas, compararlas con evidencia real, implementar, validar. `PREPRE` no puede ejercitarse vía backtest histórico -- Yahoo no guarda velas de 5 min de antes de las 04:00 ET (verificado: la vela más temprana disponible con `prepost=True` es exactamente esa hora), validado en su lugar con prueba directa sobre los 6 estados reales usando datos de momentum reales, no sintéticos.

---

## 2026-08-06 -- Investigación 4: fuente oficial de la verdad y sincronización del conocimiento de Atlas

**Problema**: la auditoría posterior al cierre de la Investigación 3 encontró que la base histórica real del Exit Journal (30 días, 638 trayectorias, 89.786 muestras, reconstruida y validada localmente) nunca llegó a producción -- `/api/predictive-engine` en la URL pública seguía reportando `total_predictions: 0`. Todo el trabajo de los Sprints 1-5 y la Investigación 3 estaba desplegado en código, pero el objetivo original de la Fase 1.1 (una recomendación real, no "evidencia insuficiente", visible para un usuario real) seguía sin cumplirse en producción. Diagnóstico previo (mismo día): el mecanismo de persistencia (`ATLAS_DATA_DIR` + Volume, `db_path()`, construido en B1) resuelve *dónde* vive la base, pero no existe ningún camino, automático ni manual, para llevar una reconstrucción local hacia la base oficial, ni una reconstrucción automática si esa base se pierde.

**Decisión de arquitectura (previa al diseño de sincronización)**: la fuente oficial de la verdad del conocimiento de Atlas es **la base SQLite persistente del servidor de Railway** -- no la base local del desarrollador (sin continuidad, no ve el escaneo real de mercado) ni la reconstrucción automática desde datos de mercado (Yahoo Finance solo conserva velas de 5 minutos de los últimos ~60 días -- no puede sostener la verdad indefinidamente). Es la única que acumula conocimiento irrepetible (muestras reales del Exit Journal en vivo) y la única que ve un usuario real.

**Diseño de la sincronización, con una restricción explícita del usuario**: el repositorio nunca almacena bases SQLite (regla permanente) -- la sincronización debe usar un formato de intercambio independiente del motor de base de datos. Alternativas evaluadas para ese formato: CSV (descartada -- ambigüedad de tipos, posicional, se rompe en silencio si cambia el esquema), Parquet (descartada -- binario, pierde la auditabilidad vía `git diff` que sostiene el resto del diseño, y agrega una dependencia nueva para un volumen de datos que no la justifica), **JSONL** (elegida -- cada fila es una línea JSON independiente, autodescriptiva, diffable en git, sin dependencias nuevas -- `json` es de la librería estándar).

**Mecanismo completo**: `export_seed_delta.py` (local, solo lectura) compara el inventario local contra el inventario real de producción (nuevo endpoint de solo lectura `/api/exit-journal/inventory`, reutilizando `exit_journal.get_all_symbol_dates()` del Sprint 3) y exporta a un archivo `.jsonl` versionado **solo el delta** -- nunca la base completa. `seed_import.py` (servidor) importa esos seeds comiteados al arrancar el proceso -- mismo rol que ya cumple, en `origin/main`, el auto-import de `memory_store.db` como seed, aplicado acá al Exit Journal con un formato distinto. Estrictamente aditivo: antes de insertar una fila se verifica que su `(symbol, date, sampled_at)` no exista ya en destino -- nunca `UPDATE` ni `DELETE`, un dato real capturado en vivo nunca se sobrescribe. Es también el mecanismo de recuperación automática: si el Volume se pierde por completo, arrancar el proceso de nuevo reconstruye la base sola, de forma acumulativa e idempotente, a partir de todos los seeds comiteados hasta la fecha.

**Justificación**: decisión directa del usuario -- "El objetivo es preservar conocimiento, no una implementación específica" y "Quiero mantener como regla permanente del proyecto que el repositorio nunca almacena bases SQLite." El diseño completo (qué formato, cómo evitar sobrescribir, cómo detectar diferencias, cómo sincronizar solo lo necesario, cómo hacerlo repetible y seguro, cómo recuperar automáticamente, qué validar antes/después, cómo garantizar que nunca se pierdan datos reales) se aprobó por etapas explícitas antes de escribir código, sin excepciones a la regla de no comitear `.db`.

**Impacto esperado**: `atlas_live/server.py` gana un endpoint de solo lectura (`/api/exit-journal/inventory`) y un paso de importación al arrancar (`seed_import.import_all_seeds()`, antes de `scan_worker.start_background_refresh()`). Nuevos módulos `atlas_live/backtest/export_seed_delta.py` y `atlas_live/backtest/seed_import.py`, con pruebas propias (`test_seed_sync.py`, 6 casos: delta correcto, sin nada que sincronizar, aditivo e idempotente, nunca sobrescribe una clave existente, directorio ausente no rompe el arranque, fila corrupta no bloquea al resto). Validado end-to-end con datos sintéticos superpuestos sobre la base real local (89.786 muestras reales de por medio, sin tocarlas) -- exportación, importación y reintento idempotente correctos. Suite completa del proyecto: 86/86 verificaciones en verde. **Pendiente**: el primer seed real (el delta completo de 89.786 muestras hacia producción, hoy vacía) todavía no se generó ni se comiteó -- requiere que el endpoint nuevo esté desplegado primero; el commit de esta investigación se preparó sin push, a pedido explícito del usuario.

**Impacto esperado**: sesión regular/after-hours/closed sin regresión (verificado byte-idéntico sobre datos reales). Premarket: BFLY/2026-06-18 pasó de "elegible recién a los 330 min" a "elegible desde el minuto 5". Reconstrucción completa de 30 días con el fix: 638 trayectorias, 89.786 muestras, 21.705 con `eligible=1` durante horario premarket (antes, prácticamente ninguna). Motor Predictivo (Sprint 3) re-validado: mediana pasó de -330 min a **0 min**, recomendación `comprar_ahora`, n=78 casos reales. Suite completa de tests: 80/80 en verde. Durante la investigación se identificó y corrigió, por separado, un riesgo real de pérdida de datos (`test_exit_journal.py`/`test_live_integration.py` borraban `exit_journal.db` compartido al correrse directamente sin `ATLAS_DATA_DIR` aislado) -- ver `INVESTIGACIONES.md` para el detalle completo de ambos hallazgos.

---

## 2026-08-07 -- Panel de Desempeño de Atlas: dos niveles, acierto separado de rentabilidad, Atlas Score con pesos configurables

**Problema**: el usuario pidió un tablero profesional que mida objetivamente el desempeño de Atlas (18 métricas mínimas: recomendaciones, Win Rate diario/semanal/mensual, Profit Factor, ganancia/pérdida promedio, expectativa, drawdown, mejor/peor operación, abiertas/cerradas, detectadas vs. acertadas, evolución en el tiempo, Atlas Score). Investigación previa (sin implementar) encontró que Atlas tiene **tres poblaciones de "recomendación" distintas y no intercambiables** (Prediction Journal: 1/día; Exit Journal: hasta 20/día sellados; Prediction Log: decenas/día, mide acierto de *timing*, no rentabilidad) -- ninguna métrica podía calcularse sin elegir primero sobre cuál.

**Alternativas evaluadas**: calcular todo sobre la Oportunidad del Día (muestra mínima, una sola operación puede distorsionar cualquier estadística) vs. calcular sobre todo el top-20 sellado del Exit Journal (muestra representativa, ya es la fuente oficial de trayectorias históricas).

**Decisión tomada**:
1. **Población**: Nivel 1 (Oportunidad Oficial del Día, panel independiente, Prediction Journal) + Nivel 2 (Rendimiento histórico, todo el top-20 sellado, Exit Journal) -- nunca mezclados.
2. **Acierto del modelo ≠ rentabilidad** (corrección exigida por el usuario tras la primera versión del diseño): "acierto" = `category == "EXPLOSION"` reclasificando `final_return_pct` con el mismo Clasificador que ya usa todo el proyecto; "rentabilidad" = el retorno real, tratado en una sección financiera aparte (Win Rate financiero, Profit Factor, ganancia/pérdida promedio, expectativa, drawdown). Una operación puede ganar plata sin que Atlas haya "acertado" su predicción de movimiento explosivo, y viceversa -- el panel muestra ambas, nunca fusionadas en un solo número.
3. **Drawdown**: no representa capital real (Atlas no gestiona una cuenta) -- curva hipotética (capital inicial 100, una unidad fija por operación, sin interés compuesto), etiquetada como tal en toda la interfaz.
4. **Atlas Score**: combinación configurable (`atlas_live/performance_config.json`), no pesos fijos en código -- no hay evidencia histórica que justifique una ponderación específica sobre otra (mismo criterio ya usado para los pesos de Radar Explosivo en `explosive_config.json`).
5. **Detectadas vs. acertadas**: Memory Store (73.123 observaciones, universo completo escaneado), no el top-20 sellado -- es una pregunta de cobertura de detección, distinta de la rentabilidad de lo seleccionado.

**Regla de gobernanza aprobada**: toda modificación futura de `atlas_score_weights` en `performance_config.json` debe quedar registrada en este archivo, con fecha, pesos anteriores, pesos nuevos y justificación del cambio.

**Justificación**: decisión directa del usuario -- "No quiero mezclar 'rentabilidad positiva' con 'acierto de Atlas'... quiero dos conceptos separados" y "El Atlas Score no debe depender de pesos fijos elegidos arbitrariamente. Justifica técnicamente esos pesos o diseña el sistema para que sean configurables." Mismo principio ya establecido en el proyecto para "Calidad del Mercado" (2026-08-03): ningún juicio compuesto se fabrica sin evidencia o sin poder ajustarse.

**Impacto esperado**: `atlas_live/memory/exit_journal.py` gana `get_summaries_between()` (solo lectura, sin cambios al esquema). Nuevo módulo `atlas_live/performance_panel.py` (100% de lectura sobre Prediction Journal, Exit Journal y Memory Store), `atlas_live/performance_config.json`, endpoint `/api/performance`, vista nueva "📊 Desempeño" en la Cabina. `test_performance_panel.py` (6 casos, incluyendo una prueba dedicada a que acierto y rentabilidad nunca se mezclen, y otra a que sin datos no se inventa ningún número). Suite completa del proyecto: 92/92 en verde, verificado a mano el cálculo de Win Rate/Profit Factor/expectativa/drawdown/Atlas Score sobre datos sintéticos. Corrección de interfaz en el mismo commit: `fmtMoney()` (único punto de formato de precios de toda la Cabina) pasa de "$" genérico a "US$" -- solo presentación, sin cambios de lógica.
---

## 2026-08-06 -- Investigación 3 (Cabina: mensaje honesto cuando Yahoo no entrega premarket) -- CERRADA

**Problema**: la Cabina mostraba "Premarket: --" sin ninguna explicación cuando `preMarketPrice` de Yahoo venía `None`, dejando al usuario sin forma de distinguir una falla de Atlas de una ausencia real del dato en el proveedor.

**Causa raíz, confirmada con evidencia real** (no supuesta): `YahooFinanceLiveProvider` copia `price_premarket=info.get("preMarketPrice")` de forma incondicional -- verificado campo por campo contra un `.info` real de GDX en premarket genuino, sin ninguna pérdida en el camino. Cuando el campo llega vacío, es porque Yahoo no lo reportó en esa consulta puntual, no porque Atlas lo descarte.

**Alternativas evaluadas para resolverlo** (formato completo en la conversación de esta sesión): (A) reintentar Yahoo -- descartada, bajo valor, ya cubierto por el ciclo de refresco existente; (B) otro endpoint de Yahoo -- descartada, ya fusionado dentro de `.info`; (C) failover a Finnhub para premarket -- **validada y descartada con evidencia real**: muestra de 8 símbolos reales, Finnhub devolvió en el 100% de los casos el precio de cierre regular (mismo `t`/epoch que `regularMarketTime`), nunca el premarket -- confirmado además contra TradingView para GDX (82.65 real vs. 83.68 de Finnhub); (D) mostrar la ausencia honestamente, con el motivo explícito -- la única opción que sobrevivió a la validación.

**Decisión tomada**: (D). `cabina.js`/`cabina.css`: cuando `price_premarket` es `null`, la fila Premarket del desglose de precio muestra "No disponible" + "Proveedor: Yahoo Finance" + "Motivo: no reportó precio de premarket en esta consulta." en vez de un `--` desnudo. Cambio exclusivamente de presentación -- cero archivos Python modificados (confirmado con `git diff --stat`), por lo tanto no puede haber afectado a Regular, After-hours, Radar Explosivo, Motor Predictivo ni al Ranking.

**Validación**: función real (`priceBreakdownHtml()`) ejecutada en la Cabina real, contra datos reales de Yahoo capturados en vivo -- confirmado tanto el caso con premarket presente (no se rompe, sigue mostrando el precio y "EN USO") como el caso ausente (muestra el mensaje completo, exacto). Sin errores de consola.

**Alcance explícitamente fuera de esta investigación** (aclarado por el usuario tras una validación previa que los mezclaba por error): verificar el caso "Yahoo sí entrega premarket" en vivo y la transición automática cuando el dato aparece después dependen de (1) que exista una ventana real de premarket (hoy el mercado ya estaba en sesión `REGULAR`, sin ningún caso real disponible) y (2) de que el ciclo del scanner no se caiga por el defecto de failover no relacionado, ya registrado aparte como **Investigación 6 (Radar/Failover)**. Ninguno de los dos es una dependencia de esta implementación.

**Justificación**: decisión directa del usuario -- "Los puntos 2, 3 y 6 no pertenecen a esta investigación... No voy a mantener abierta una investigación por dependencias que pertenecen a otra." Aprobación explícita: "Apruebo la implementación de la Investigación 3. La Investigación 3 queda oficialmente CERRADA."

**Observación pendiente registrada** (no bloquea el cierre): validación de la transición automática durante premarket real, pendiente de que la Investigación 6 restablezca el funcionamiento normal del scanner y exista una ventana real de premarket. Ver [INVESTIGACIONES.md](INVESTIGACIONES.md) para el estado vigente de todas las investigaciones abiertas.

**Impacto esperado**: ninguno fuera de la Cabina -- mejora de honestidad/UX pura, sin cambio de datos ni de cálculo en ningún motor.

---

## 2026-08-06 -- Investigación 6 (Radar / Failover: `YFRateLimitError` no activaba Finnhub) -- CERRADA

**Problema**: cuando Yahoo Finance rechazaba una consulta por exceso de solicitudes, el ciclo completo de escaneo se caía (`symbols_scanned=0`) en vez de continuar con Finnhub, el proveedor de respaldo ya configurado en `MultiProvider`.

**Causa raíz, confirmada con evidencia de ejecución real** (instrumentando `FinnhubProvider` para contar invocaciones, no por lectura de código): `yfinance.exceptions.YFRateLimitError` no hereda de `ProviderError`. `YahooFinanceProvider.get_quotes()` (la ruta por lote, la que usa `scan_worker.py` en producción) solo atrapaba `(ProviderError, QuoteNotFoundError)` -- la excepción real escapaba sin conversión, cruzaba `MultiProvider.get_quotes()` de largo (su `except ProviderError` nunca la reconocía) y tumbaba el ciclo. Primera corrida de control: **Finnhub invocado 0 veces**. La ruta singular (`get_quote()`/`_fetch_info()`) no tenía este defecto -- ya envolvía cualquier excepción en `ProviderError` correctamente; el bug estaba aislado a la ruta por lote.

**Alternativas evaluadas para la corrección** (formato completo en la conversación de esta sesión): captura amplia (`except Exception`) -- descartada explícitamente por el usuario ("no quiero ocultar errores reales de programación... el código debe ser explícito y trazable"); captura específica de `YFRateLimitError` -- aprobada.

**Decisión tomada**: nueva clase `RateLimitError(ProviderError)` en `atlas/data/providers/base.py` (aditiva, subclase de `ProviderError`, `MultiProvider` la reconoce sin cambios propios). `YahooFinanceProvider.get_quotes()` captura únicamente `YFRateLimitError`, la envuelve en `RateLimitError` y la relanza de inmediato (corta el lote, no sigue símbolo por símbolo) -- un rate-limit es del proveedor completo, no de un símbolo puntual. Cualquier otro tipo de excepción sigue con el mismo comportamiento de siempre (`continue`, ahora con `logger.warning` para no quedar mudo). `multi_provider.py` no se tocó -- no apareció evidencia de defecto propio en él.

**Validación** (7 pruebas, con evidencia real, no teórica):
1. Prueba forzada y determinística (Yahoo real no estaba rate-limitado en el momento de probar): `YFRateLimitError` inyectado en el punto exacto de `yfinance.Ticker.info`, sin tocar ninguna línea de `yahoo_finance.py` -- MultiProvider logueó el failover, Finnhub fue invocado y devolvió 3/3 quotes reales (`source=finnhub`).
2. Prueba natural, sin forzar nada, mismo *call site* exacto que `scan_worker.py:370` (200 símbolos reales): Yahoo rate-limitó de forma natural, MultiProvider logueó el failover con el mensaje nuevo exacto, Finnhub respondió **61/200 quotes reales, 100% `source=finnhub`**.
3. Ciclo real completo contra el servidor en vivo (mercado abierto): `run_scan_once()` terminó limpio (`symbols_scanned=208, symbols_ok=178, errors=30, last_error=None`), ranking regenerado (`generated_at` avanzó, composición del top 20 cambió), precio real cambiado entre dos ciclos consecutivos (PAYC 219.02 → 215.0275), Radar Explosivo con 7/20 candidatos elegibles reales.
4. Regresión: símbolo inválido (`ZZZINVALIDOXYZ`) mezclado con símbolos válidos -- se omite solo (`QuoteNotFoundError`, comportamiento sin cambios), sin activar failover, confirma que ningún otro tipo de excepción cambió de comportamiento.

**Justificación**: decisión directa del usuario en cada etapa -- diagnóstico exigido con evidencia de ejecución real ("No acepto una explicación teórica"), diseño aprobado con la condición explícita de captura específica (no genérica), y aprobación final tras las 7 pruebas: "Las 7 pruebas quedaron demostradas con evidencia real... Queda oficialmente cerrada."

**Impacto esperado**: el escaneo en vivo ya no queda en cero cuando Yahoo rate-limita -- Finnhub sostiene el ciclo con lo que puede cubrir (precio, sin fundamentales, ver limitación ya documentada en `finnhub_provider.py`). Sin cambio de comportamiento para ningún otro tipo de fallo. Ver [INVESTIGACIONES.md](INVESTIGACIONES.md) para el estado vigente de todas las investigaciones.

---

## 2026-08-07 -- Panel de Evolución de Atlas (feature, no investigación)

**Problema/necesidad**: el usuario pidió un panel que demuestre con evidencia real cómo evolucionan la precisión y el aprendizaje de Atlas con el paso de los días, separando explícitamente tres conceptos que nunca se mezclan: (1) precisión del modelo, (2) rendimiento financiero, (3) evolución del aprendizaje. Restricciones: solo datos reales ya existentes, sin datos simulados ni números hardcodeados, sin tocar la lógica del Radar / Memory Engine / Motor Predictivo, sin romper endpoints existentes, y si un indicador no se puede calcular con evidencia real debe decir "No disponible" (nunca fabricar un valor).

**Decisión tomada**: nuevo módulo `atlas_live/evolution_panel.py` (100% lectura y reutilización) + endpoint aditivo `/api/evolution` + vista nueva "📈 Evolución" en la Cabina. No se reimplementa ningún cálculo:
- **Precisión** y **rendimiento financiero** salen de `performance_panel.get_global_performance()` (única fuente de verdad; "acierto" = `category == "EXPLOSION"` vía `classifier.py`, la misma definición del proyecto). Se agregan, sobre esos mismos datos, los conteos de aciertos por período (hoy/semana ISO/mes/histórico) y el mejor/peor global (performance_panel solo traía los de hoy).
- **Evolución del aprendizaje** se ensambla con contadores reales existentes: `exit_journal.get_all_symbol_dates()` (trayectorias), `exit_journal.count_trajectory_samples()` (muestras), `store.count_observations()` (casos), y `live_integration.get_memory_engine_summary()` (condiciones con evidencia + última recalibración).
- **"Nivel de aprendizaje (%)"**: definición real, no compuesto arbitrario = `condiciones_confiables / condiciones_totales_evaluadas * 100`, donde el total es `len(calibration_advisor.CONDITION_GRID)` y las confiables son las que superan el límite inferior de Wilson sobre el baseline. Si todavía no hay observaciones, es `None` → "No disponible".

**Justificación**: decisión directa del usuario -- "utilizando exclusivamente datos reales que ya existen en el sistema... No inventes una definición nueva [de acierto]... Si algún indicador no puede calcularse con evidencia real, mostrar 'No disponible'. No fabricar valores."

**Impacto esperado**: aditivo y de solo lectura. No modifica `/api/performance` ni la vista `desempeno`, ni ningún motor. Frontend reutiliza clases CSS ya existentes (sin CSS nuevo). Pruebas nuevas `atlas_live/test_evolution_panel.py` (5 casos: aciertos por período, precisión ≠ rentabilidad, mejor/peor global, conteos de aprendizaje reales, y "sin datos → todo None/'No disponible', nunca un número inventado"). Suite offline completa en verde (71/71). Verificado contra las bases reales locales: la sección de aprendizaje se puebla con datos reales (483 trayectorias, 66.330 muestras, 73.123 casos, 10/14 condiciones confiables, nivel 71.4%); precisión/financiero muestran "No disponible" cuando aún no hay trayectorias cerradas -- comportamiento honesto, sin fabricar.

---

## 2026-08-07 -- Optimización de latencia: canal rápido (Plan A + Plan B) + indicadores de frescura del dato

**Problema/necesidad**: el usuario pidió que el dato usado por Atlas nunca tenga más de 3s de antigüedad *cuando exista información nueva disponible*, y que la Cabina **advierta con evidencia** cuándo un precio deja de ser fresco.

**Auditoría del flujo del dato (medición real, esta sesión)**: el scanner del universo (`run_scan_once`, ~244 símbolos) tarda **72-650s por ciclo** y duerme `REFRESH_INTERVAL_SECONDS=300s` entre ciclos; `/api/ranking` es lectura cacheada barata (~0.73s); una cotización limpia de un símbolo es ~0.5-1.7s. Los proveedores están limitados: **Finnhub 60 req/min**, Yahoo con throttling agresivo, y Yahoo entrega `sourceInterval`≈15s (el DATO en sí solo cambia cada ~15s). Recién terminado un ciclo de 72s, el precio del top símbolo ya tenía 65s de antigüedad y seguía envejeciendo hasta el próximo ciclo.

**Conclusión con evidencia (documentada, no un fracaso)**: 3s de antigüedad del DATO para el **universo completo** es **físicamente imposible** con REST + estos rate-limits -- el ciclo (minutos) domina, y el único camino a sub-segundo para las 244 sería WebSocket, ya evaluado y descartado (Investigación 5). Sí es alcanzable para un puñado de símbolos.

**Alternativas evaluadas**: (a) acelerar el escaneo del universo -- rechazada, viola los límites del proveedor y no cambia que el ciclo dura minutos; (b) WebSocket -- ya descartado; (c) **canal rápido dedicado solo para los 2 símbolos visibles** (Oportunidad del Día = Hero, y Plan B) + transparencia de frescura -- elegida.

**Decisión tomada** (alcance exacto aprobado por el usuario, condición literal: *"Mantén el escaneo completo del universo con su frecuencia actual... implementa un canal de actualización rápida exclusivamente para la Oportunidad del Día (Plan A) y el Plan B... Solo los 2 símbolos visibles se refrescan... No quiero aumentar el consumo de API sobre las ~244 acciones."*):
- **El scanner del universo NO cambia** -- misma lógica, misma frecuencia, mismos límites. `scan_worker.run_scan_once` intacto.
- **Endpoint nuevo `/api/hot-quote?symbols=A,B`** (máximo 2 símbolos; el exceso se ignora): construye un `DataCollector` fresco por request (caché vacío -> dato nuevo, nunca sirve un valor viejo) sobre el `MultiProvider` (failover Yahoo→Finnhub ya existente) y devuelve la cotización cruda con su `price_as_of`. NO corre Radar, Memory, Motor Predictivo ni el scanner. Toda la lógica vive en `atlas_live/hot_quote.py` (testeable sin arrancar el servidor -- mismo criterio "cero lógica de negocio en la capa de servidor").
- **Frontend**: canal rápido cada `HOT_POLL_MS=3000` solo para Plan A + Plan B; los 5 indicadores de frescura pedidos (hora exacta del dato, "hace X s" recalculado cada segundo, proveedor usado, semáforo 🟢 0-3s / 🟡 3-10s / 🔴 >10s vía `freshnessStatus()` pura, y aviso de dato no fresco). Antigüedad calculada sin skew de reloj: `(server_time − price_as_of)` [ambos del servidor] `+ (ahora − recibido)` [ambos del cliente].
- **"Último recibido, nunca ocultar la antigüedad"**: si el proveedor no entrega un dato nuevo (mismo `price_as_of`, error o rate-limit) NO se reancla el reloj -- se conserva el último precio bueno y su antigüedad sigue creciendo (🟢→🟡→🔴). El timestamp solo avanza cuando llega un `price_as_of` genuinamente nuevo.
- **Manejo de errores explícito** (misma regla de la Investigación 6): un fallo por símbolo se captura solo con `except (ProviderError, QuoteNotFoundError)` -- nunca un `except Exception` genérico -- y no tumba el canal ni el otro símbolo.

**Presupuesto de API (respeta "no aumentar consumo sobre las 244")**: exactamente 2 símbolos cada 3s ≈ **40 req/min**, dentro de Finnhub (60/min) e **independiente** del escaneo del universo -- no le suma carga.

**Justificación**: es la única forma de alcanzar ≤3s donde es técnicamente posible (2 símbolos por REST) sin tocar el escaneo del universo ni violar los rate-limits, y de ser honestos donde no lo es (el universo, y los tramos en que Yahoo no ha entregado un tick nuevo) mostrando la antigüedad real en vez de ocultarla.

**Impacto esperado / verificación**: aditivo. Módulo nuevo `atlas_live/hot_quote.py`, endpoint `/api/hot-quote`, y presentación en el frontend (widget de frescura + fast poll + hot refresh); reutiliza las variables de color existentes (`--green/--amber/--red`). Pruebas nuevas `atlas_live/test_latency_freshness.py` (9 casos: tope de 2 símbolos, dedupe/uppercase, cotización OK, símbolo inexistente y rate-limit → "unavailable" sin tumbar el canal, lista vacía, y garantía de que el módulo no referencia scanner/Radar/Memory/Predictivo). Suite offline en verde (atlas_live 66/66 + atlas/tests 25/25). Latencia real medida contra el proveedor: round-trip **1.2-1.7s** por 1-2 símbolos, `price_as_of` a la par del `server_time` (antigüedad efectiva ≤3s con mercado abierto). Verificado en navegador: Hero 🟢 "En vivo · hace 2s", Plan B 🟡 "Con retraso · hace 4s", etiqueta "último recibido" cuando Yahoo devuelve el mismo timestamp (su `sourceInterval`≈15s hace que el semáforo cicle 🟢→🟡 honestamente entre ticks del proveedor).

---

## 2026-08-07 -- Regla permanente: cero MOCK en producción + limpieza completa de la Cabina

**Problema/necesidad**: en producción no puede existir ningún componente MOCK -- ningún dato, alerta, texto, panel o gráfico simulado. Cada elemento visible debe provenir de un motor real; si un panel no tiene información real, debe mostrar un estado honesto, nunca un ejemplo. **Regla permanente declarada por el usuario: preferir un panel vacío antes que un dato inventado.**

**Auditoría (solo lectura, durante la sesión de observación)**: `mock_data.js` (objeto `MOCK`) tenía la mayoría de campos ya muertos (reemplazados por endpoints reales) y **6 secciones activas** aún leyendo MOCK: "¿Por qué NO?", "Atlas Opina", "Alertas", barra de actividad, "ETF" y "Configuración", más una etiqueta visible `MOCK` en cada una de esas 6.

**Decisión tomada (definitiva, pre-autorizada por el usuario; ejecutada tras el cierre de mercado para no reiniciar el escaneo en vivo)**:
- **"¿Por qué NO?"** → descartes REALES de `/api/explosive-diagnostics` (tabla real: symbol, `failed_stage`, `reason`), priorizando los de mayor gap/RVOL. Estado honesto si no hay descartes.
- **"Atlas Opina"** → **Resumen Factual** determinista construido SOLO con datos reales (nº candidatos/elegibles, condición del top con Wilson/n, VIX real). Sin evidencia → "Sin evidencia suficiente para emitir un resumen." (No se inventa un motor de opinión.)
- **"Alertas"** → SOLO eventos reales de Mission Control (`market_state_history`, `provider_failover_history`) con su hora real. Sin eventos → "Sin alertas registradas en esta sesión."
- **Barra de actividad** → estado REAL del último ciclo (`/api/ranking`: `scanning`, `generated_at`, `symbols_ok/scanned`). Sin ciclo → "Esperando el primer escaneo del día."
- **"ETF"** → estado honesto ("sin fuente de datos conectada todavía"); no hay feed dedicado de ETF apalancados, así que se elimina toda fila simulada en vez de inventar datos.
- **"Configuración"** → nuevo endpoint `/api/config` que lee valores REALES del backend (`scan_worker.REFRESH_INTERVAL_SECONDS`, `classifier` umbrales, `explosive_config` techo microcap/gates, `market_hours` horario y ventana de sellado), no constantes hardcodeadas en la interfaz.
- Se **eliminó `mock_data.js`** y su `<script>`, y las 6 etiquetas `MOCK` del markup.

**Justificación**: decisión directa y definitiva del usuario -- "En producción no puede existir ningún componente MOCK. Si un panel no tiene información real, debe mostrar un estado honesto. Prefiero un panel vacío antes que un dato inventado." Un ejemplo simulado en pantalla puede confundirse con un dato real; el estado honesto no.

**Impacto esperado / verificación**: nuevo `/api/config`; frontend reconectado (6 secciones) a datos reales o estados honestos; `mock_data.js` borrado; cero referencias de código a `MOCK.` y cero texto "MOCK" visible. Pruebas nuevas `atlas_live/test_config_endpoint.py` (5 casos: intervalo, umbrales, techo microcap, horario/sellado, y sanidad de que la respuesta no trae marcas de ejemplo). Suite offline en verde (atlas_live 71/71). Verificado en navegador (servidor local): sin errores de consola; los 6 paneles muestran datos reales o el estado honesto correcto ("Sin evidencia suficiente", "Sin alertas registradas", "Esperando el primer escaneo", config real, ETF honesto); "MOCK" visible = 0.
