# Informe técnico: Proyecto Atlas (radar-ai-main)

## 1. Arquitectura completa

Este proyecto **no es una aplicación funcional** todavía — es un **esqueleto de documentación** (visión, filosofía, reglas de negocio) con **un único script Python real**, incompleto y no ejecutado como sistema. No hay backend, frontend, base de datos, API, ni pipeline de datos implementado.

Estructura real en disco:

```
radar-ai-main/
├── README.md                                  (raíz, duplica atlas/atlas/README.md)
├── ROADMAP.md                                  (roadmap fase 1-2-3)
├── DECISIONES.md                               (filosofía y reglas de negocio)
├── docs/
│   ├── ARQUITECTURA.md                         (VACÍO — 0 bytes)
│   └── ROADMAP.md                              (VACÍO — 0 bytes)
└── atlas/
    └── atlas/                                  ← carpeta duplicada anómala
        ├── README.md                           (idéntico al de raíz)
        └── scanner/
            └── atlas/                          ← otra carpeta duplicada anómala
                ├── README.md
                ├── ARCHITECTURE.md
                ├── ATLAS_ENGINE.md
                ├── ATLAS_PRINCIPLES.md
                ├── ATLAS_RULES.md
                ├── config.py                   (1 línea, contenido inválido)
                ├── investigator.py             (único código funcional, 30 líneas)
                └── requirements.txt
```

No hay `.git`, `package.json`, `pyproject.toml`, entorno virtual, tests, CI/CD, ni ninguna otra evidencia de infraestructura de proyecto. El sistema no tiene control de versiones inicializado localmente.

**Anomalía estructural importante**: la ruta `atlas/atlas/scanner/atlas/` sugiere que el repo se extrajo/clonó mal (probablemente un ZIP de GitHub llamado `atlas` descomprimido dentro de otra carpeta `atlas`, y así sucesivamente). Esto no es una arquitectura intencional de módulos, es ruido de organización de archivos.

## 2. Qué hace cada carpeta y archivo

| Archivo | Contenido | Función |
|---|---|---|
| `README.md` (raíz y duplicados) | 13 líneas | Describe "Atlas Core" como "el cerebro de Atlas": calcular Índice de Explosión (IE), Atlas Score, y clasificar COMPRAR/ESPERAR/DESCARTAR. Es descriptivo, no técnico. |
| `ROADMAP.md` (raíz) | Fases 1-3 con checkboxes sin marcar | Plan de trabajo: Fase 1 (motor de decisión, IE, ranking diario, dashboard básico), Fase 2 (scanners de premarket/noticias/volumen/float), Fase 3 (ML, historial, ajuste automático). **Ningún ítem está marcado como hecho.** |
| `DECISIONES.md` | Filosofía de trading | Reglas de negocio: prioriza probabilidad de ganancia en operaciones de 5-20 min sobre "la que más sube"; volumen > precio; solo acciones disponibles en el bróker "Racional". |
| `docs/ARQUITECTURA.md`, `docs/ROADMAP.md` | **Vacíos (0 bytes)** | Archivos placeholder sin contenido. |
| `atlas/atlas/scanner/atlas/ARCHITECTURE.md` | Diseño de 4 módulos | Data Collector → Atlas Engine → Learning Engine → Dashboard. Puramente conceptual, sin código ni interfaces definidas. |
| `atlas/atlas/scanner/atlas/ATLAS_ENGINE.md` | Diagrama de flujo en texto | Árbol de decisión: disponibilidad en Racional → liquidez → interés de mercado (RVOL/volumen) → catalizador (noticias/FDA/earnings) → confirmación de precio → riesgo → Atlas Score → resultado. Es un flujo de decisión conceptual, no algoritmo. |
| `atlas/atlas/scanner/atlas/ATLAS_PRINCIPLES.md` | 7 principios | Gestión de riesgo, disciplina, no operar sin ventaja estadística, aprendizaje continuo. |
| `atlas/atlas/scanner/atlas/ATLAS_RULES.md` | 5 reglas | Descarte automático si no está en Racional, ventana de 5-20 min, no perseguir FOMO, entregar solo 3 oportunidades por sesión (🥇🥈🥉). |
| `config.py` | **1 línea: el texto literal `requirements.txt`** | Archivo roto/placeholder. No es Python válido como configuración — parece un error de copiar-pegar (alguien puso el nombre de otro archivo dentro de este). |
| `investigator.py` | 30 líneas, único código real | Ver sección 3. |
| `requirements.txt` | 6 dependencias | `yfinance`, `pandas`, `requests`, `beautifulsoup4`, `python-dotenv`, `rich`, `loguru`. Ninguna instalada (no hay venv), y solo `yfinance` se usa realmente en el código existente. |

## 3. Cómo funciona el scanner Atlas actualmente

El **único código ejecutable** es `investigator.py`. Esto es lo que hace, literalmente:

1. Define una watchlist fija hardcodeada de 6 tickers: `SOXL, NVDA, PLTR, MARA, CCJ, KGC`.
2. Para cada ticker, usa `yfinance` para descargar el historial de precios de los últimos 2 días.
3. Calcula el cambio porcentual entre el cierre de ayer y el de hoy (`(last - prev) / prev * 100`).
4. Imprime el resultado en consola: `TICKER $precio cambio%`.
5. Si falla, imprime `TICKER Error` (excepción silenciada sin logging real, pese a que `loguru` está en requirements).

**Eso es todo.** No calcula IE (Índice de Explosión), no calcula Atlas Score, no evalúa RVOL, float, catalizadores, noticias, ni premarket — pese a que toda la documentación (`ATLAS_ENGINE.md`, `ARCHITECTURE.md`) describe estos cálculos como el núcleo del sistema. No hay clasificación COMPRAR/ESPERAR/DESCARTAR. No hay verificación de disponibilidad en el bróker Racional (la Regla 1, que según `ATLAS_RULES.md` es el primer filtro obligatorio). No genera ranking ni top 3. No hay dashboard.

En resumen: **la documentación describe un sistema de decisión sofisticado; el código es un script de una sola función que imprime el % de cambio diario de 6 acciones grandes (ni siquiera microcaps).**

## 4. Qué está terminado y qué está incompleto

**Terminado / definido:**
- Visión de producto y filosofía de trading (clara y coherente en toda la documentación).
- Reglas de negocio y principios de riesgo.
- Diagrama conceptual de arquitectura de 4 módulos.
- Flujo de decisión conceptual (árbol de sí/no).
- Prueba de concepto mínima de obtención de datos con `yfinance`.

**Incompleto o inexistente (prácticamente todo lo técnico):**
- Cálculo del Índice de Explosión (IE) — mencionado en 3 documentos distintos, no implementado en ningún lado.
- Cálculo de Atlas Score, PE (Probabilidad de Explosión), PX (Probabilidad de Éxito) — solo nombrados, sin fórmula ni código.
- Clasificación COMPRAR/ESPERAR/DESCARTAR — no existe lógica de clasificación.
- Data Collector real (RVOL, float, volumen en dólares, premarket, after-hours, noticias) — `yfinance` free tier ni siquiera provee la mayoría de estos datos de forma confiable (RVOL y float en particular no vienen directos).
- Integración con el bróker "Racional" para verificar disponibilidad — no hay ningún cliente/API para esto, y es la Regla #1 del sistema (debería ser el primer filtro).
- Scanner de premarket, de noticias, de volumen, de float (toda la Fase 2 del roadmap).
- Learning Engine / ML (Fase 3) — no existe ni estructura de datos para guardar histórico de operaciones.
- Dashboard — no existe ningún frontend, ni siquiera un CLI con tabla formateada (pese a tener `rich` en requirements, no se usa).
- `config.py` está roto — contiene texto inválido, no configuración real.
- No hay manejo de errores real, logging, tests, ni forma de programar ejecuciones periódicas (cron/scheduler) pese a que el objetivo es correr "antes de la apertura del mercado".

## 5. Errores y cuellos de botella detectados

**Errores concretos:**
1. `config.py` contiene el texto literal `requirements.txt` — es un archivo corrupto/placeholder, no un módulo de configuración funcional. Cualquier `import config` fallaría en tener nada útil.
2. `investigator.py` captura `except Exception` genérico y solo imprime "Error" sin loggear la causa — imposible diagnosticar fallos de red o tickers inválidos.
3. Duplicación de carpetas `atlas/atlas/scanner/atlas/` y `README.md` repetido en 3 ubicaciones idénticas — indica un problema de organización del repo (probablemente de cómo se extrajo/subió), que dificultará cualquier automatización de rutas o imports en el futuro.
4. `docs/ARQUITECTURA.md` y `docs/ROADMAP.md` están vacíos — parecen placeholders que se olvidaron de llenar, mientras la documentación real vive duplicada dentro de `atlas/atlas/scanner/atlas/`.
5. Dependencias declaradas pero no usadas (`pandas`, `requests`, `beautifulsoup4`, `python-dotenv`, `rich`, `loguru`) — sugiere que hubo intención de construir más (scraping de noticias con BeautifulSoup, logging con loguru, tablas con rich) pero se abandonó.

**Cuellos de botella de diseño (para cuando se implemente):**
1. **Fuente de datos**: `yfinance` no es apta para trading de microcaps intradía — no da datos de premarket fiables en tiempo real, no da float, no da RVOL directo, y tiene rate limits/latencia no aptos para "antes de la apertura del mercado". Se necesitará una fuente de datos de nivel profesional (Polygon.io, IEX Cloud, Finnhub, Alpaca Data, etc.).
2. **Sin verificación de disponibilidad en Racional**: la Regla #1 del sistema (descartar si no está en Racional) no tiene ninguna implementación ni se sabe si Racional expone una API pública para consultarlo — esto es un bloqueador crítico no resuelto de diseño, no solo de código.
3. **Watchlist estática hardcodeada**: un scanner de microcaps necesita descubrir candidatos dinámicamente (por ejemplo, top gainers premarket, alto RVOL), no analizar una lista fija de 6 large-caps predefinidos.
4. **Sin persistencia**: no hay base de datos ni archivo de histórico — el "Learning Engine" (Fase 3) no puede existir sin antes tener almacenamiento de resultados de operaciones.
5. **Sin scheduler/orquestación**: el objetivo es correr "antes de la apertura", pero no hay ningún mecanismo (cron, APScheduler, GitHub Actions, etc.) para ejecutarlo automáticamente en una ventana horaria.
6. **Ambigüedad de las fórmulas centrales**: IE, Atlas Score, PE y PX se mencionan en 4 documentos distintos pero **nunca se define su fórmula matemática** — es la pieza más importante del sistema (el "cerebro") y es la que menos definición concreta tiene, ni siquiera a nivel de pseudocódigo.

## 6. Mejoras propuestas para un scanner profesional de microcaps

**A. Fuente de datos (crítico, bloqueante)**
- Sustituir/complementar `yfinance` por un proveedor con datos premarket en tiempo real, float, y RVOL: Polygon.io, Finnhub, Alpaca Markets, o Benzinga (para noticias/catalizadores). `yfinance` puede quedar como fallback para datos EOD, no como fuente primaria.
- Resolver primero cómo verificar programáticamente disponibilidad en Racional (¿API?, ¿lista estática mantenida manualmente?, ¿scraping?). Sin esto, la Regla #1 no se puede automatizar.

**B. Definir las fórmulas del núcleo antes de programarlas**
- Formalizar matemáticamente IE, Atlas Score, PE y PX como funciones ponderadas de variables medibles (gap %, RVOL, volumen en $, distancia a resistencia premarket, presencia de catalizador). Hoy son solo nombres — se necesita esta especificación antes de escribir el "cerebro".

**C. Arquitectura de código (implementación real de `ARCHITECTURE.md`)**
- Separar en módulos reales: `data_collector/` (adaptadores por fuente de datos), `engine/` (cálculo de scores y clasificación), `discovery/` (screener dinámico de candidatos, no watchlist fija), `storage/` (histórico de operaciones, SQLite o Postgres), `dashboard/` (CLI con `rich` o web simple).
- Usar `pydantic` o dataclasses para modelar los "datos limpios" que menciona `ARCHITECTURE.md` como salida del Data Collector.

**D. Descubrimiento dinámico de candidatos**
- Reemplazar la watchlist hardcodeada por un screener que traiga automáticamente top gainers/most active premarket desde la fuente de datos, filtrado por rango de precio y float típico de microcaps.

**E. Confiabilidad y observabilidad**
- Usar `loguru` (ya está en requirements pero sin usar) para logging estructurado en vez de `except: print("Error")`.
- Manejo de errores específico por tipo de fallo (timeout de red, ticker inválido, rate limit) para poder reintentar o alertar según el caso.

**F. Automatización y programación**
- Scheduler (APScheduler o cron) para correr el escaneo automáticamente en la ventana premarket, con alertas (Telegram/Discord/email) para el top 3 diario.

**G. Persistencia y aprendizaje (Fase 3)**
- Base de datos ligera (SQLite para empezar) para guardar cada operación sugerida y su resultado real, condición necesaria antes de construir el "Learning Engine".

**H. Higiene de repositorio**
- Resolver la duplicación de carpetas `atlas/atlas/scanner/atlas/`, inicializar git, eliminar/completar los `docs/*.md` vacíos, y arreglar `config.py`.

---

**Resumen de una línea**: el proyecto tiene una visión de producto y reglas de negocio bien pensadas y consistentes, pero técnicamente está en fase de prototipo mínimo — un script que imprime variaciones de precio de 6 acciones — sin ninguna de las piezas (fórmulas de score, verificación de bróker, descubrimiento dinámico, persistencia, dashboard) que la propia documentación define como el núcleo del sistema.
