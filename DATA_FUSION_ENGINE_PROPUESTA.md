# Propuesta formal: arquitectura de múltiples proveedores de datos (Data Provider + Data Fusion Engine)

**Estado: propuesta de diseño. No implementada.** Sigue el formato de
`ATLAS_CONSTITUTION.md` ("METODOLOGÍA DE PROPUESTAS"). Aprobada
explícitamente para **solo diseño, sin código** (2026-08-02) -- el
usuario confirmó que esto no se implementa antes de obtener varios días
de evidencia en mercado real de Atlas Alpha 1.0 (ver `DECISION_LOG.md`,
entrada "Atlas Alpha 1.0: construido, listo para primera validación").
Este documento existe para que el diseño no se pierda ni se tenga que
rehacer cuando llegue el momento de retomarlo.

---

## REGLA DE ARQUITECTURA (declarada oficialmente, 2026-08-02)

> **`atlas_live/data_fusion/yahoo_finance_live_provider.py::YahooFinanceLiveProvider`
> es el único punto autorizado para construir un `Quote` en vivo dentro de
> `atlas_live`.** Ningún módulo nuevo puede llamar a `yfinance` directamente
> ni instanciar `YahooFinanceProvider` (la clase base) por su cuenta para
> obtener datos de mercado en vivo. Toda funcionalidad nueva debe obtener
> su `Quote` exclusivamente a través de `DataCollector` (construido con
> `YahooFinanceLiveProvider`, o el proveedor que el registro del Data
> Fusion Engine determine cuando exista -- Etapas 1-3, todavía sin
> implementar).

Corolario directo del Principio 5 de `ATLAS_CONSTITUTION.md` ("el
proveedor de datos nunca podrá estar acoplado al motor"), aplicado de
forma concreta a `atlas_live`: si toda funcionalidad nueva pasa
exclusivamente por `DataCollector` + `YahooFinanceLiveProvider`, agregar
un proveedor nuevo en el futuro (Alpaca, Polygon, Finnhub) sigue
requiriendo tocar un solo lugar (`atlas_live/data_fusion/`), nunca cada
módulo consumidor.

**Precisión técnica, para que no se malinterprete la regla**:
`YahooFinanceLiveProvider` reutiliza internamente el parseo de
`YahooFinanceProvider` (clase base, vía `super()._quote_from_info()`) --
eso es composición interna válida, no una excepción a la regla. Lo que
la regla prohíbe es que un módulo *consumidor* (Radar, Memory Engine,
Prediction Journal, Exit Journal, Mission Control, Cabina, o cualquier
funcionalidad futura) construya su propio `DataCollector(YahooFinanceProvider())`
o llame a `yfinance` por fuera de `atlas_live/data_fusion/`.

**Excepciones ya existentes, anteriores a esta regla, documentadas y sin
resolver todavía** (la auditoría del 2026-08-02 las encontró; la regla
aplica hacia adelante, no las reescribe retroactivamente sin una
decisión aparte):
- `atlas_live/memory/live_integration.py::_grade_pending()` -- construye
  su propio `DataCollector(YahooFinanceProvider())` para calificar
  predicciones al cierre, decisión explícita del usuario de no tocar
  Prediction Journal durante la corrección de precio.
- Los 5 motores de `/atlas` Core (`decision_engine.py`,
  `market_context_engine.py`, `money_flow_engine.py`,
  `momentum_radar.py`, `premarket.py`) tienen un fallback
  `collector or DataCollector(YahooFinanceProvider())` -- dormido en el
  flujo en vivo actual (siempre se les inyecta el collector correcto),
  pero técnicamente fuera del alcance de esta regla porque viven en el
  Core congelado, no en `atlas_live`.

Ambas quedan fuera del alcance de esta regla (que rige `atlas_live` y
"funcionalidad nueva"), pero registradas para que una futura limpieza no
las redescubra de cero.

---

## PROBLEMA

Hoy, todo punto de `atlas_live/` que necesita datos de mercado
construye su propio `DataCollector(YahooFinanceProvider())` de forma
directa e independiente -- son exactamente **3 lugares**:
`atlas_live/scan_worker.py` (`run_scan_once()`, `get_symbol_detail()`) y
`atlas_live/memory/live_integration.py` (`_grade_pending()`). Si Yahoo
Finance falla, se degrada, o cambia su comportamiento (rate limiting,
campos faltantes en `info`), **Atlas completo deja de funcionar** -- no
hay ningún mecanismo de respaldo. Además, no existe ninguna forma de
detectar si Yahoo Finance está devolviendo datos incorrectos o
desactualizados, porque nunca se compara contra una segunda fuente.

Esto no es un problema hipotético: ya está documentado como riesgo
conocido (`ATLAS_ROADMAP.md`, backlog de la sesión 2026-08-02) y fue
investigado en profundidad en `DATA_PROVIDER_EVALUATION.md`
(comparación de Polygon, Databento, Alpaca, Finnhub, Intrinio contra los
requerimientos reales de Atlas) -- investigación que **nunca se
implementó**, quedó como recomendación pendiente de aprobación.

## HIPÓTESIS

Si Atlas consume datos a través de una capa de abstracción que (a)
nunca expone directamente un proveedor específico al resto del sistema,
y (b) puede usar más de un proveedor con conmutación automática ante
fallas, entonces Atlas gana resiliencia operativa (sigue funcionando
si Yahoo Finance falla) sin modificar ningún resultado del Radar
Explosivo, el Memory Engine, ni la Cabina del Piloto -- porque todos
ellos ya dependen exclusivamente de `DataCollector`, nunca de un
proveedor concreto (ver sección ARQUITECTURA).

## PRINCIPIOS DE LA CONSTITUCIÓN QUE RESPALDAN ESTA PROPUESTA

- **Principio 5**: "El proveedor de datos nunca podrá estar acoplado al
  motor." -- es literalmente el principio que esta propuesta implementa
  de punta a punta.
- **Principio 1**: "Los datos tienen prioridad sobre las opiniones." --
  el mecanismo de validación entre fuentes (sección correspondiente)
  registra discrepancias como datos objetivos, sin que el sistema
  "opine" cuál proveedor tiene razón.
- **Sección ARQUITECTURA de la Constitución**: "Todo desarrollo
  experimental deberá realizarse inicialmente dentro de `atlas_live`.
  Solo después de validarse con evidencia podrá incorporarse al Core."
  -- ver más abajo por qué esto fija dónde debe vivir el código nuevo.

---

## ARQUITECTURA

### Lo que ya existe hoy (no se inventa desde cero)

`/atlas` Core **ya tiene** la interfaz que el Principio 5 exige,
construida y congelada:

- `atlas/data/providers/base.py` -- `DataProvider` (ABC): contrato
  `get_quote(symbol)`, `get_quotes(symbols)`, `get_history(symbol, ...)`.
  El docstring ya dice explícitamente: *"Contrato que todo proveedor de
  datos (Yahoo Finance, Polygon, Finnhub, Alpaca, ...) debe cumplir"*.
- `atlas/data/models/quote.py` -- `Quote`: modelo normalizado,
  independiente del proveedor (ningún campo específico de Yahoo).
- `atlas/data/collectors/data_collector.py` -- `DataCollector`: envuelve
  UN `DataProvider`, agrega caché en memoria. **Es ya, hoy, el único
  punto de entrada real** -- confirmado revisando todo el repo: ningún
  motor de `/atlas` Core ni módulo de `atlas_live/` llama a
  `YahooFinanceProvider.get_quote()` directamente, todos pasan por
  `DataCollector`.
- `atlas/data/providers/__init__.py` -- ya tiene un registro
  (`_PROVIDERS: Dict[str, Type[DataProvider]]`) y una función
  `get_provider(name)`, pensado exactamente para esto, pero **sin usar
  todavía** -- ningún call site lo invoca, todos instancian
  `YahooFinanceProvider()` a mano.

**Conclusión clave**: el "Data Fusion Engine" no necesita ser una capa
nueva y paralela. La forma más simple, más segura y más alineada con lo
que ya existe es construirlo como **una implementación más de
`DataProvider`** -- una clase que por fuera se ve exactamente igual que
`YahooFinanceProvider` (mismo contrato: `get_quote`, `get_quotes`,
`get_history`), pero que por dentro consulta a una lista ordenada de
proveedores reales con failover. `DataCollector` no se entera de la
diferencia -- sigue recibiendo "un DataProvider", como siempre.

### Dónde vive el código nuevo (respeta la Constitución)

`atlas/data/providers/` es parte del Core, **congelado**. La sección
ARQUITECTURA de la Constitución es explícita: todo desarrollo
experimental va primero a `atlas_live/`. Por lo tanto:

- El Data Fusion Engine, el segundo proveedor nuevo (Alpaca -- ver
  PLAN DE MIGRACIÓN), y el registro de discrepancias se construyen en
  un módulo nuevo **`atlas_live/data_fusion/`** -- no se toca ni un
  archivo de `atlas/data/providers/`.
- El nuevo código sí puede **importar** `atlas.data.providers.base.DataProvider`
  y `atlas.data.models.quote.Quote` (leer una interfaz congelada no la
  modifica -- mismo patrón ya usado en todo `atlas_live/`, ej.
  `base_rates.py` reimplementando la fórmula de Wilson en vez de
  importar un símbolo privado del Core).
- Alcance explícito: esta propuesta migra los **3 call sites de
  `atlas_live/`** que hoy instancian `YahooFinanceProvider()` a mano
  (`scan_worker.py` x2, `live_integration.py` x1). **No toca** los
  motores propios de `/atlas` Core (Decision Engine, Money Flow Engine,
  Market Context Engine, Momentum Radar, Premarket Scanner) -- esos
  siguen usando `YahooFinanceProvider` directo, congelados, como
  siempre. Si algún día se decide migrarlos también, es una propuesta
  aparte (cambiaría código dentro de `/atlas`, lo que requiere su propio
  proceso de validación antes de incorporarse).

```
atlas_live/data_fusion/
  __init__.py
  fusion_provider.py     # FusionProvider(DataProvider) -- failover, orden de prioridad
  alpaca_provider.py      # AlpacaProvider(DataProvider) -- segundo proveedor real
  discrepancy_log.py      # registro de diferencias entre fuentes (solo lectura/registro)
  registry.py             # qué proveedores están activos y en qué orden -- config, no código hardcodeado
```

---

## INTERFACES DE DATA PROVIDERS

No se define una interfaz nueva -- se reutiliza `DataProvider` (ABC) tal
cual existe hoy. Cualquier proveedor nuevo (Alpaca, Polygon, Finnhub,
"cualquier otra fuente oficial compatible") debe:

1. Heredar de `atlas.data.providers.base.DataProvider`.
2. Implementar `get_quote(symbol) -> Quote` y
   `get_history(symbol, period, interval) -> pd.DataFrame`, devolviendo
   el mismo `Quote`/DataFrame normalizado que ya usa `YahooFinanceProvider`
   -- ningún consumidor debe poder distinguir de qué proveedor vino el
   dato por la forma del objeto.
3. Traducir cualquier error propio (timeout, símbolo no encontrado,
   error de autenticación, rate limit) a `ProviderError` o
   `QuoteNotFoundError` (ya definidos en `base.py`) -- el mecanismo de
   failover (siguiente sección) solo sabe reaccionar a esas dos
   excepciones, no a excepciones específicas de cada SDK.

**Nota sobre TradingView**: el pedido original mencionaba TradingView
como segundo proveedor candidato "si es técnicamente viable". Investigado
someramente: TradingView **no publica una API de datos oficial** para
este uso -- las librerías de Python que existen (`tvdatafeed`,
`tradingview-ta`, etc.) son no oficiales, dependen de acceso no
documentado a la plataforma web, y violan potencialmente sus términos de
servicio. Esto contradice explícitamente el pedido más reciente ("fuente
**oficial** compatible"). **Recomendación**: no usar TradingView.
`DATA_PROVIDER_EVALUATION.md` ya investigó y comparó 5 fuentes oficiales
con SDK propio (Polygon/Massive, Databento, **Alpaca**, Finnhub,
Intrinio) y recomendó Alpaca como proveedor primario -- ver PLAN DE
MIGRACIÓN. Si en algún momento se quiere reconsiderar TradingView de
todas formas, debería ser una decisión explícita y separada, señalando
que se acepta el riesgo de depender de una integración no oficial.

---

## DATA FUSION ENGINE

`FusionProvider(DataProvider)` -- vive en `atlas_live/data_fusion/fusion_provider.py`.

- Recibe una lista ordenada de proveedores en el constructor:
  `FusionProvider([primary, secondary, ...])`. El orden es la
  **prioridad** -- el primero de la lista es quien responde en
  condiciones normales.
- Implementa `get_quote`, `get_quotes`, `get_history` con la misma firma
  que cualquier otro `DataProvider` -- `DataCollector` no cambia ni una
  línea.
- No decide "cuál proveedor es mejor" ni pondera nada -- solo aplica la
  máquina de failover (siguiente sección) y, cuando corresponde, invoca
  el registro de discrepancias (sección correspondiente). Cero lógica de
  negocio de Atlas acá -- mismo principio que ya sigue `YahooFinanceProvider`
  ("responsabilidad única: obtener y normalizar, no calcular ni filtrar").

`registry.py` reemplaza la construcción hardcodeada
(`DataCollector(YahooFinanceProvider())`) por una función única, ej.
`get_default_collector()`, que arma el `FusionProvider` con la lista de
proveedores activos -- **un solo lugar** para agregar/quitar/reordenar
proveedores en el futuro (Polygon, Finnhub, etc.), en vez de tocar los 3
call sites cada vez.

---

## MECANISMO DE FAILOVER

Algoritmo explícito, sin ambigüedad (para que la implementación futura
no tenga que inventar el comportamiento):

1. `FusionProvider.get_quote(symbol)` intenta con el proveedor de mayor
   prioridad de la lista.
2. Si ese proveedor lanza `ProviderError` (falla de red, timeout, error
   de autenticación) -- **no** si lanza `QuoteNotFoundError` (el símbolo
   específico no existe para ese proveedor, no es una falla del
   proveedor en sí) -- se registra el fallo (log, no excepción visible
   hacia arriba) y se reintenta con el siguiente proveedor de la lista,
   en orden.
3. Si **todos** los proveedores fallan para ese símbolo, recién ahí se
   propaga `ProviderError` hacia el llamador -- Atlas debe saber
   explícitamente que no hay ningún dato disponible, nunca debe
   devolver un valor inventado o el último dato cacheado silenciosamente
   sin avisar (`DataCollector` ya tiene su propio caché con TTL, eso es
   independiente y no cambia).
4. `get_quotes(symbols)` (lote) aplica el mismo criterio símbolo por
   símbolo -- un símbolo que falla en el proveedor primario pero
   funciona en el secundario no debe hacer fallar a todo el lote, ni
   viceversa (un símbolo que ningún proveedor tiene simplemente no
   aparece en el resultado, mismo comportamiento que ya tiene
   `YahooFinanceProvider.get_quotes()` hoy con símbolos inválidos).
5. **No hay recuperación automática de vuelta al proveedor primario**
   dentro de una misma llamada -- cada llamada nueva a `get_quote`/`get_quotes`
   vuelve a intentar desde el proveedor de mayor prioridad primero. Esto
   es deliberadamente simple: no se implementa un "circuit breaker" con
   ventanas de tiempo todavía -- si se necesita en el futuro (ej. evitar
   golpear un proveedor caído en cada ciclo de 5 minutos), es una mejora
   posterior, justificada con evidencia real de cuánto tarda Yahoo en
   recuperarse cuando falla.
6. Cada fallo/failover se registra (nivel WARNING, mismo patrón de
   logging que ya usa el resto de `atlas_live/`) -- visible en
   Mission Control en el futuro (hoy no hay ningún proceso instrumentado
   con heartbeat todavía, ver `LEARNING_ENGINE.md`/`ATLAS_MISSION_CONTROL.md`
   para ese estado).

---

## VALIDACIÓN ENTRE MÚLTIPLES FUENTES (registro de discrepancias)

Pedido explícito: "si dos proveedores entregan datos para el mismo
símbolo, registrar las diferencias sin modificar todavía el algoritmo
de decisión." Mismo espíritu ya aplicado en el Exit Journal (guardar el
dato objetivo, no decidir nada con él todavía):

- `discrepancy_log.py` expone `record_discrepancy(symbol, field, provider_a, value_a, provider_b, value_b, observed_at)`
  -- **solo cuando efectivamente se consultan dos proveedores para el
  mismo símbolo** (no agrega una llamada extra al proveedor secundario
  solo para comparar; se registra cuando el failover ya obligó a
  consultar más de uno, o cuando explícitamente se corre un chequeo de
  validación aparte -- a decidir en la fase de implementación, no ahora).
- Persistencia: SQLite append-only en `atlas_live/data_fusion/discrepancy_log.db`,
  mismo patrón WAL ya usado en Memory Store / Prediction Journal / Exit
  Journal.
- **No se fija ningún umbral de "diferencia significativa" en esta
  propuesta** -- mismo principio ya aplicado en el Exit Journal (no
  fijar umbrales sin evidencia). Se registra la diferencia cruda
  (`value_a`, `value_b`, y la diferencia calculada) para todos los casos
  donde hay dos fuentes disponibles; cualquier clasificación de "esto es
  una discrepancia grave" queda como función pura bajo demanda, con
  parámetro explícito, para cuando haya evidencia real de qué campos
  divergen y cuánto.
- **No modifica el algoritmo de decisión** -- Radar Explosivo, Memory
  Engine y Ranking Score siguen usando el `Quote` que devuelve el
  proveedor de mayor prioridad (o el que haya respondido tras el
  failover), sin ningún ajuste ni promedio ni votación entre fuentes.
  Eso sería un cambio al algoritmo de detección, fuera del alcance
  explícito de esta etapa.

---

## IMPACTO SOBRE RADAR, MEMORY ENGINE Y CABINA

Gracias a que los tres ya dependen exclusivamente de `DataCollector`
(nunca de un proveedor directo -- confirmado revisando el código, no
supuesto), el impacto es mínimo y ya está acotado:

- **Radar Explosivo** (`atlas_live/explosive_engine.py`): no cambia ni
  una línea -- recibe `Quote`/métricas ya calculadas, nunca instancia un
  proveedor.
- **Memory Engine** (`atlas_live/memory/`): no cambia ninguna lógica de
  cálculo. El único punto que toca un proveedor es
  `live_integration.py::_grade_pending()` -- se migra igual que
  `scan_worker.py`.
- **Cabina del Piloto**: no cambia nada -- consume `atlas_live/server.py`,
  que a su vez consume `scan_worker`, nunca un proveedor.
- **Cambia únicamente**: los 3 call sites que hoy escriben
  `DataCollector(YahooFinanceProvider())` pasan a escribir
  `DataCollector(data_fusion.registry.get_default_collector())` (o
  equivalente) -- mismo tipo de objeto (`DataCollector`), mismo
  contrato, cero cambios en el resto de cada función.

---

## PLAN DE MIGRACIÓN DESDE YAHOO FINANCE (por etapas, cada una validable antes de la siguiente)

1. **Etapa 0 -- refactor puro, sin cambio de comportamiento**:
   `FusionProvider` envolviendo **solo** `YahooFinanceProvider` (lista de
   un elemento). Migrar los 3 call sites. Criterio de éxito: mismos
   resultados exactos que hoy, símbolo por símbolo, en un escaneo real
   -- si algo cambia, hay un bug en el refactor, no una mejora real.
2. **Etapa 1 -- segundo proveedor, solo como respaldo**: agregar
   `AlpacaProvider` (recomendado en `DATA_PROVIDER_EVALUATION.md`) como
   **segundo** en la lista de prioridad, no primero -- Yahoo sigue
   respondiendo en condiciones normales, Alpaca solo entra si Yahoo
   falla. Validar con una falla simulada (ej. símbolo inválido a
   propósito, o mockear una excepción) antes de exponerlo a fallas
   reales.
3. **Etapa 2 -- registro de discrepancias activo**: con los dos
   proveedores ya funcionando, activar `discrepancy_log.py` y dejarlo
   correr varios días reales para acumular evidencia de cuánto y en qué
   campos difieren Yahoo y Alpaca -- **esta es la evidencia real que la
   Constitución exige antes de considerar cualquier cambio al algoritmo
   de decisión** (Principio 3: "ningún algoritmo nuevo entra a Atlas
   Core sin haber sido validado previamente").
4. **Etapa 3 (futura, no parte de esta propuesta)**: con evidencia de
   varios días, decidir si Alpaca pasa a ser primario, si se agrega un
   tercer proveedor (Polygon/Finnhub), o si el orden de prioridad debe
   depender de la sesión de mercado (ej. Yahoo para regular, Alpaca para
   premarket/afterhours si tiene mejor cobertura ahí) -- **todo esto
   requiere su propia evidencia, no se decide en esta propuesta**.

Ninguna etapa de este plan se ejecuta todavía -- queda documentada para
retomarse después de la primera validación en mercado real de Atlas
Alpha 1.0.

---

## IMPACTO ESPERADO

- **Resiliencia**: Atlas sigue funcionando (con el mismo comportamiento
  observable) si Yahoo Finance falla, en vez de detener todo el sistema.
- **Preparación**: agregar un tercer proveedor futuro (Polygon, Finnhub)
  se reduce a implementar una clase nueva + agregarla al registro -- cero
  cambios en Radar, Memory Engine o Cabina.
- **Métrica oficial que mejora** (Constitución, sección MÉTRICAS
  OFICIALES): principalmente **tiempo de detección** indirectamente (un
  escaneo que hoy se cae completo por una falla de Yahoo pasa a
  completarse vía el proveedor de respaldo) -- no se espera ningún
  cambio en Precision@10/@20/Recall/falsos positivos/falsos negativos,
  porque el algoritmo de decisión no cambia (ver VALIDACIÓN ENTRE
  MÚLTIPLES FUENTES).

## RIESGOS

- **Alpaca requiere cuenta y API key** (nivel gratis con cobertura IEX
  ~2% del volumen, o plan pago $99/mes para cobertura SIP completa --
  ver `DATA_PROVIDER_EVALUATION.md`). No se crea ninguna cuenta ni se
  contrata ningún plan como parte de esta propuesta de diseño.
- **Etapa 0 (refactor puro) es el único paso con riesgo real de romper
  algo hoy funcional** -- mitigado por el criterio de éxito explícito
  (mismos resultados exactos antes/después) y por hacerse aislado de
  cualquier proveedor nuevo.
- **El registro de discrepancias puede generar mucho volumen de datos**
  si Yahoo y Alpaca difieren seguido en campos ruidosos (ej. volumen en
  el mismo segundo exacto) -- sin umbral definido todavía (a propósito,
  ver sección correspondiente), esto se revisa con la evidencia real de
  la Etapa 2, no se resuelve por adelantado.
- **TradingView, si se insiste en usarlo pese a la recomendación en
  contra**, agregaría una dependencia no oficial y un riesgo legal/de
  estabilidad distinto a cualquier otro proveedor de esta lista -- fuera
  de alcance de esta propuesta tal como está planteada.

## CÓMO SE VALIDARÁ

1. Etapa 0: comparación exacta de resultados (mismo escaneo, antes vs.
   después del refactor) sobre datos reales.
2. Etapa 1: prueba de failover controlada (falla simulada de Yahoo) más
   una corrida real donde, si Yahoo falla espontáneamente, Atlas siga
   produciendo un escaneo completo.
3. Etapa 2: revisión manual del `discrepancy_log` tras varios días
   reales, antes de decidir cualquier cosa sobre umbrales o cambios de
   prioridad.

## CRITERIOS DE ÉXITO

- Cero cambios en los resultados de Radar Explosivo / Memory Engine /
  Ranking Score atribuibles al refactor (Etapa 0).
- Un escaneo completo se sigue produciendo aunque el proveedor primario
  falle (Etapa 1), sin intervención manual.
- El registro de discrepancias existe y acumula datos reales sin haber
  tocado el algoritmo de decisión (Etapa 2).
- Ningún archivo de `/atlas` Core (`atlas/data/providers/`, motores de
  Capa 1) se modifica en ninguna etapa de esta propuesta.

---

**Cierre**: esta propuesta queda aprobada solo como diseño. No se
implementa nada de lo anterior hasta que Atlas Alpha 1.0 acumule varios
días de evidencia real en mercado (ver
[DECISION_LOG.md](DECISION_LOG.md) y
[PRIMER_DIA_OPERACION_ATLAS_ALPHA.md](PRIMER_DIA_OPERACION_ATLAS_ALPHA.md)).

---

# ADDENDUM (2026-08-02) -- Trazabilidad de precio: fuente, tipo de sesión y hora

**Sigue siendo diseño, no código.** Esta actualización extiende la
propuesta original con un requisito nuevo, disparado por un hallazgo
real, y queda documentada como parte permanente de esta arquitectura
(será la base del Learning Engine y de la implementación futura del
Data Fusion Engine, tal como se pidió).

## Disparador

Comparación real Atlas vs. TradingView, mismo símbolo, mismo momento:
Atlas mostraba el precio de sesión regular (Yahoo Finance), TradingView
mostraba el precio de after-hours. Ambos precios son correctos -- son
mediciones de sesiones distintas -- pero mostrados sin ese contexto
generan la apariencia de datos contradictorios.

## Causa raíz -- confirmada con una consulta real, no una hipótesis

Se verificó en vivo, en este entorno, con `yfinance` (`yf.Ticker("AAPL").info`):

```
regularMarketPrice -> 308.91
postMarketPrice     -> 307.34
postMarketChange    -> -1.57
marketState         -> CLOSED
postMarketTime      -> 1785542399
preMarketPrice      -> <ausente ahora porque el mercado no está en premarket>
```

**Confirmado**: Yahoo Finance sí expone el precio de after-hours
(`postMarketPrice`) y de premarket (`preMarketPrice`, cuando corresponde
a la sesión actual), además de `marketState`. El problema **no es que
a Yahoo le falte el dato** -- es que
`atlas/data/providers/yahoo_finance.py::YahooFinanceProvider._quote_from_info()`
**nunca lee esos campos**, solo `regularMarketPrice`/`currentPrice`.
Atlas muestra siempre el precio de sesión regular sin importar en qué
sesión de mercado esté ahora mismo. Esto explica el síntoma observado de
punta a punta.

## Qué exige esto del modelo de datos (`Quote`)

`atlas/data/models/quote.py::Quote` no tiene ningún campo para expresar
de qué proveedor vino el precio ni a qué sesión corresponde -- solo
`timestamp`. Se agregan tres campos, **aditivos, con default, sin romper
ningún consumidor existente** (mismo patrón ya usado para
`market_context` en el Memory Store):

```python
price_type: Literal["regular", "premarket", "afterhours", "unknown"] = "unknown"
source: str = "yahoo_finance"
# `timestamp` ya existe -- se documenta formalmente como el "as of" oficial del precio.
```

Nota de arquitectura: `Quote` vive en `/atlas` Core (congelado).
Agregar estos campos requiere pasar por el proceso de validación de la
Constitución antes de tocar ese archivo -- se incorpora a la Etapa 0 del
PLAN DE MIGRACIÓN ya definido en este documento (el refactor puro), no
se hace todavía.

## Regla de presentación obligatoria (Cabina del Piloto)

Requisito explícito del usuario: *"Atlas nunca debe mostrar un precio
sin indicar claramente fuente, tipo de precio y hora de la última
actualización."* Esto se convierte en una regla de UI permanente: todo
lugar de la Cabina que muestre un precio (Hero, Plan B, Explosivas,
Momentum, Radar Completo, detalle de símbolo) debe mostrar, de forma
visible (no solo en un tooltip), los tres datos juntos. Formato mínimo
de referencia: `$4.82 · Yahoo · Regular · 15:42 ET`.

Esto depende de que `price_type`/`source`/`timestamp` viajen desde
`Quote` hasta `serialize_ranked_candidate()`
(`atlas_live/memory/live_integration.py`) y de ahí al JSON que consume
`cabina.js` -- por eso el orden de implementación futura es: modelo de
datos → Fusion Engine → serialización → UI, nunca al revés.

## Cuando dos fuentes reportan valores distintos

Requisito explícito: *"Atlas no debe ocultarlo."* El Fusion Engine, al
consultar más de una fuente para el mismo símbolo (mismo mecanismo ya
diseñado en VALIDACIÓN ENTRE MÚLTIPLES FUENTES), expone -- no solo
registra en un log -- una estructura completa y visible:

```json
{
  "symbol": "...",
  "quotes_by_source": [
    {"source": "yahoo_finance", "price_type": "regular",    "price": 308.91, "as_of": "..."},
    {"source": "tradingview",   "price_type": "afterhours", "price": 307.34, "as_of": "..."}
  ],
  "used_for_ranking": {"source": "yahoo_finance", "reason": "proveedor de mayor prioridad, sin fallo"},
  "discrepancy_pct": null
}
```

**Distinción que evita una falsa alarma, central a este addendum**:
comparar el precio Regular de una fuente contra el After-hours de otra
**no es una discrepancia** -- son dos mediciones legítimas de dos
momentos distintos, exactamente el problema que disparó este addendum.
El cálculo de `discrepancy_pct` (registro de discrepancias ya diseñado)
solo tiene sentido **dentro del mismo `price_type`** (Regular vs.
Regular, Premarket vs. Premarket). Cuando los `price_type` difieren, no
se calcula ninguna discrepancia -- se muestran ambos valores como
información complementaria, con su tipo, sin comparar peras con manzanas.

`used_for_ranking` responde el cuarto punto pedido explícitamente
("fuente utilizada para el cálculo del Ranking") -- el Ranking Score
sigue usando exactamente un `Quote` (el del proveedor de mayor
prioridad, o el de respaldo si hubo failover), nunca un promedio ni una
fusión de valores; esta propuesta no cambia esa regla, solo la hace
visible y trazable.

## Failover visible en Mission Control

Requisito explícito: *"registrando el cambio en Mission Control."* El
mecanismo de failover (ya diseñado en MECANISMO DE FAILOVER) agrega, en
cada conmutación real de proveedor -- no en cada consulta individual,
solo cuando el proveedor de mayor prioridad efectivamente falla y se usa
el siguiente -- un evento en el Timeline ya existente de Mission Control
(`atlas_live/mission_control/timeline.py::record_event`), con un
`event_type` nuevo (`"provider_failover"`, se agrega al catálogo de
`ATLAS_MISSION_CONTROL.md` sección 4), severidad `WARNING`, y mensaje
explícito de qué proveedor falló y a cuál se conmutó. No se inventa un
mecanismo de registro nuevo -- se reutiliza el Timeline que ya
construyó Mission Control (Entregable 2).

## Alcance de este addendum sobre el plan ya existente

No cambia nada de lo ya definido (arquitectura, `FusionProvider`,
`DataProvider`, plan de migración por etapas). Agrega un requisito a la
**Etapa 0** (el refactor puro, antes de agregar cualquier segundo
proveedor real): `Quote` ya debe llevar `price_type`/`source`, y la
Cabina ya debe mostrarlos. No se pospone a una etapa posterior, porque
resuelve el problema real detectado hoy sin necesitar todavía un
segundo proveedor -- Yahoo por sí solo, leyendo los campos que ya expone
y que hoy se ignoran, ya alcanza para eliminar la confusión observada.

**Actualización (2026-08-02, más tarde el mismo día)**: la Etapa 0 (la
única independiente de un segundo proveedor real) **fue implementada**,
por decisión explícita del usuario que adelantó específicamente esta
parte antes del primer día de validación real -- ver
[DECISION_LOG.md](DECISION_LOG.md), entrada "Implementada la Etapa 0 del
addendum de trazabilidad de precio". `Quote` (`atlas/data/models/quote.py`)
ya lleva `source`/`price_type`/`market_state`/`price_regular`/
`price_premarket`/`price_afterhours`; `atlas_live/data_fusion/yahoo_finance_live_provider.py`
ya selecciona el precio según sesión; la Cabina ya muestra el desglose
completo en Hero/Plan B y el contexto compacto en las tablas. Verificado
con datos reales (KC, PRPL, servidor real, 192 símbolos, sin
regresiones). **El resto del documento (Etapas 1-3: segundo proveedor
real, failover, discrepancias) sigue sin implementarse**, todavía
gated a varios días de evidencia real, sin cambios respecto a lo ya
acordado.

---

# SESIÓN OVERNIGHT (requerimiento futuro, declarado 2026-08-02)

**Hallazgo, con evidencia real**: al auditar SOXL/KC/PRPL contra
TradingView (navegador real, no simulado), se confirmó que existe una
**cuarta sesión de mercado** -- "Overnight", en un venue de trading
nocturno real llamado **Blue Ocean ATS** ("BOATS"), que corre
aproximadamente 20:00-04:00 ET, después del After-hours estándar. Esta
sesión es visible en TradingView como un indicador secundario, siempre
distinto del precio principal, y **ningún proveedor de Atlas la
entrega** -- confirmado que Yahoo Finance (`.info`, `.fast_info`,
`.history()`) no tiene ningún campo equivalente.

**Decisión explícita del usuario**: no ocultar esta limitación ni
explicarla con un párrafo largo -- exponerla como un dato más, con su
propio lugar en la estructura, marcado honestamente como no disponible.
"No quiero que Atlas solo tenga el dato correcto... quiero que Atlas sea
completamente honesto respecto de qué información posee y cuál no."

**Lo que ya quedó preparado (implementado 2026-08-02, sin fetch real
todavía)**: `Quote.price_overnight: Optional[float] = None`
(`atlas/data/models/quote.py`) -- campo aditivo, siempre `None` hoy
porque ningún proveedor lo llena. Propagado de punta a punta, mismo
camino que `price_regular`/`price_premarket`/`price_afterhours`:
`explosive_engine.py` (metrics) → `demo_ranking.RankedCandidate` →
`live_integration.serialize_ranked_candidate()` → Cabina del Piloto
(fila "Overnight (Blue Ocean ATS)" en el desglose de precio, siempre
visible, mostrando "No disponible con el proveedor actual" mientras el
valor sea `None`).

**Lo que falta (no implementado, fuera de alcance hasta que exista
evidencia real y un proveedor)**: ningún proveedor de Atlas sabe
consultar Blue Ocean ATS todavía. Cuando el Data Fusion Engine incorpore
un proveedor que sí lo reporte (TradingView no tiene API oficial -- ver
sección "INTERFACES DE DATA PROVIDERS" de este documento; sería más
realista vía Alpaca, Polygon u otra fuente oficial que confirme cobertura
de Blue Ocean ATS), ese proveedor solo necesita poblar
`Quote.price_overnight` -- **ningún otro archivo debería requerir
cambios**: ni `explosive_engine.py`, ni `demo_ranking.py`, ni
`live_integration.py`, ni `cabina.js`/`cabina.css`, porque el campo ya
viaja por toda la cadena y la fila de la Cabina ya sabe renderizar un
valor real en cuanto deje de ser `None`. Esto es, en la práctica, la
prueba de que la Etapa 0 dejó la arquitectura lista para incorporar
sesiones nuevas sin romper el diseño actual -- el mismo principio que ya
regía para agregar un segundo proveedor de precio Regular/Premarket/
After-hours, aplicado ahora a una sesión completa nueva.

**No se agrega `"overnight"` como valor posible de `price_type`** -- ese
campo describe qué precio usa el Ranking Score, y el Ranking Score nunca
podrá usar Overnight mientras no exista un proveedor real (no hay nada
que seleccionar). `price_overnight` es deliberadamente un campo aparte,
puramente informativo, para no mezclar "qué sesión decide el precio
usado" con "qué sesiones adicionales existen para mostrar".
