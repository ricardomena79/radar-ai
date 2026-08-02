# DATA_PROVIDER_EVALUATION.md

Objetivo Nº3: selección del proveedor de datos definitivo para Atlas. Evaluación técnica y comparativa, no una opinión -- investigada con búsqueda web sobre documentación oficial de cada proveedor (agosto 2026), citando fuentes. Sujeto a [ATLAS_CONSTITUTION.md](ATLAS_CONSTITUTION.md), Principio 5 ("el proveedor de datos nunca podrá estar acoplado al motor").

**No se implementó ningún cambio de proveedor.** No se creó ninguna cuenta ni se contrató ningún plan -- toda la información viene de documentación pública. Cualquier decisión final debe pasar por el formato de la METODOLOGÍA DE PROPUESTAS antes de tocar `atlas/data/providers/`.

**Requerimientos de Atlas usados como vara de medir** (no criterios genéricos): escanear ~2.577 símbolos (Universo Racional) cada ~5 minutos con cotizaciones en tiempo real o cercanas, en lote (no 2.500 llamadas individuales); velas de 1-5 minutos históricas más allá de los ~60 días que permite Yahoo Finance hoy; cobertura real de microcaps/small caps (las acciones explosivas de este sistema son mayormente small/microcaps, no mega-caps); datos de premarket/after-hours; 6+ meses de velas diarias; short interest y float (hoy completamente ausentes); noticias/catalizadores; SDK oficial de Python; integración simple detrás de la interfaz `DataProvider` ya existente; presupuesto de proyecto individual, no institucional.

---

## TABLA COMPARATIVA

| Criterio | Polygon.io / Massive.com | Databento | Alpaca Market Data | Finnhub | Intrinio |
|---|---|---|---|---|---|
| **Tiempo real** | Solo en tier Advanced ($199/mo); tiers menores 15 min delay | Sí, protocolo binario TCP (no WebSocket), ilimitado en el plan Standard | IEX gratis (~2% del volumen) / SIP completo en pago ($99/mo) | Cotización individual en tiempo real, gratis | Primariamente IEX-only (no consolidado completo) |
| **Premarket / After Hours** | Sí, incluido (con matiz: volumen de velas de minuto puede ser disperso en horario extendido) | Probablemente sí (inferido de la arquitectura de feeds), **no confirmado explícitamente** | Sí, incluido en ambos tiers (según página de precios) | **No verificable** (documentación no cargó) | **No verificado** |
| **Cobertura de microcaps** | Afirma "100% market coverage", ~10.413 tickers, sin matices negativos | Tier gratis inviable (~5% ADV); tier pago ($199) cubre 15 exchanges + 30 ATS + TRF -- sólido | Tier pago (SIP) calificado "Excellent" por revisor externo para small-caps; **OTC/pink sheets NO cubierto** | **No verificable**, sin datos | **No verificado**, riesgo señalado explícitamente; intradía solo IEX+BATS |
| **WebSocket** | Desde $29/mo, pero delayed salvo en Advanced ($199) | No es WebSocket -- TCP binario propio, vía SDK | Gratis: 30 símbolos. Pago: ilimitado | Solo trade tape (no bid/ask ni velas); 50 símbolos gratis, ilimitado en pago | Sí, SDK oficial; acceso "firehose" (todos los símbolos) requiere ventas especial |
| **Snapshots de mercado** | **Sí -- Full Market Snapshot + Grouped Daily**, un solo call para todo el universo | Soporta snapshot al suscribirse (`snapshot=True`), modelo de suscripción multi-símbolo | **Sí -- `/v2/stocks/snapshots`**, batch nativo, encaja perfecto con el uso de Atlas | **No existe.** 2 issues de GitHub abiertos desde 2020 pidiéndolo, nunca implementado | **No existe** vía REST simple. Alternativas: archivos periódicos, WebSocket, o Snowflake/S3 |
| **Velas de 1 minuto** | Todos los tiers, incluido el gratis; profundidad 2/5/10/20+ años según tier | Ilimitado en Standard ($199), historial a 2018 (~7+ años) | ~2016 en adelante (~10 años), ambos tiers | Premium únicamente ($49.99/mo+); **403 en tier gratis, confirmado** por múltiples issues de GitHub | Desde enero 2019 (~7 años), pero solo IEX+BATS |
| **Datos históricos (diarios)** | Misma escalera que velas de minuto, 2-20+ años | Retro-cargado hasta ~2010 | 7+ años, ambos tiers | 10-40+ años según tier pago | **50+ años**, ajustados -- el más profundo de los 5 |
| **Noticias y catalizadores** | Endpoint básico + add-on premium Benzinga (+$99/mo) | **No existe como producto** | Sí, vía Benzinga, tiempo real + histórico a 2015 | Sí en tier gratis (1 año + tiempo real) | Sí (NewsEdge), inclusión por tier sin confirmar |
| **Short Interest** | **Gratis en TODOS los tiers**, incluido el Basic -- el único con esto | **No disponible**, "en desarrollo" desde 2023, sin fecha | **No disponible**, confirmado vía foro oficial | **Posiblemente descontinuado** -- ya no aparece en la documentación actual (señal fuerte, no 100% confirmada) | **Sí, endpoint dedicado**, pero solo ~13 días de historial (cadencia FINRA quincenal) |
| **Float** | **No disponible** -- solo shares outstanding, no float real | **No disponible**, mismo item "en desarrollo" | **No disponible**, confirmado vía foro oficial | Solo en tier $3.500/mo (ownership, no float dedicado) | **Sí -- único proveedor con float real** de SEC 10-K/10-Q, actualización trimestral |
| **SDK para Python** | Oficial, muy activo, excelente | Oficial, bien construido, requiere Python ≥3.10 | Oficial (`alpaca-py`), muy activo, ~1.4k stars | Oficial, activo, simple | Oficial, pero dos SDKs separados (REST + streaming) |
| **Facilidad de integración con Atlas** | Muy buena -- snapshots resuelven el problema de lote directamente | Moderada -- conexión persistente TCP, no REST/WS simple, pero sin riesgo de facturación por reintentos | **La mejor** -- snapshot batch nativo mapea directo a `get_quotes()` | **Mala** -- sin lote, requiere WebSocket con caché local o ~2.500 llamadas secuenciales | Moderada-alta complejidad -- sin lote REST, requiere WebSocket o archivos periódicos |
| **Estabilidad** | Status page pública, 99.69-100% uptime rolling, sin SLA formal en tiers bajos | Status page pública, sin SLA formal publicado | Status page pública, sin SLA numérico encontrado | SLA "99.9%" citado solo por agregador de terceros, no confirmado en fuente oficial | Status page (~99.99% citado, no verificado directo); SLA formal solo en Enterprise |
| **Escalabilidad** | Tiers pagos: llamadas ilimitadas | Datos en vivo ilimitados en el plan de suscripción -- costo fijo predecible | 200 rpm (gratis) / 10.000 rpm (pago) | Tope duro de 30 llamadas/seg en TODOS los tiers -- limitante real para 2.500 símbolos | Requiere WebSocket para escalar, no polling REST |
| **Precio** | $0 / $29 / $79 / **$199** (tiempo real) / custom + $99 add-on de noticias | Sin suscripción (solo histórico) / **$199** Standard / $1.750 / $4.500 | **$0** (inviable) / **$99** Algo Trader Plus | 3 líneas de precio separadas: $0 / $49.99-199.99 (solo velas) / **$3.500** (fundamentals+float) / custom | $150 Individual / desde $333 Startup / desde $1.250 Enterprise |
| **Relación costo/beneficio** | Buena si se prioriza short interest gratis; ~$298/mo con noticias incluidas | Buena, costo fijo predecible, pero dos brechas totales (short interest, float, noticias) | **La mejor** para el requerimiento núcleo (cotizaciones+velas+premarket+microcaps) a menor costo verificado | Barata solo para historial de velas; brecha total en fundamentals a precio accesible | Atractiva en el papel (único con float+short interest) pero con riesgos de integración y cobertura sin verificar |

---

## Hallazgos que cambian la evaluación inicial

1. **Ningún proveedor de los 5 resuelve TODO lo que le falta a Atlas hoy en un solo vendor a precio de proyecto individual.** El combo short interest + float + noticias + cotizaciones en lote + cobertura de microcaps verificada no existe junto en ninguno de los 5.
2. **Finnhub, asumido como fuerte en fundamentals/short interest en el brief original, resultó ser el hallazgo más sorprendente**: no tiene endpoint de lote (bloqueante arquitectónico para Atlas), y su short interest parece haber sido descontinuado (ya no aparece en su documentación actual). Su punto fuerte real es solo el historial de velas, barato.
3. **Polygon.io se renombró a Massive.com** (30 de octubre de 2025) -- mismo producto, mismas API keys, sin impacto funcional. Es, sorprendentemente, el único proveedor con **short interest gratis en todos los tiers**.
4. **Ninguno de los 4 proveedores "de cotizaciones" (Polygon, Databento, Alpaca, Finnhub) tiene float real.** Solo Intrinio lo tiene, pero Intrinio no tiene endpoint de lote -- el mismo problema arquitectónico que Finnhub, aunque con mejor SDK y datos más completos.
5. **IEX Cloud** (candidato obvio antes de investigar) está **confirmado cerrado** desde el 31 de agosto de 2024 -- se descartó automáticamente, no es una opción vigente.

---

## RECOMENDACIÓN FINAL

### Proveedor primario recomendado: **Alpaca Market Data -- plan Algo Trader Plus ($99/mes)**

Justificación contra los requerimientos reales de Atlas:
- Resuelve directamente los dos dolores más agudos y verificados con yfinance en esta sesión (rate-limiting agresivo bajo carga, tope de ~60 días de velas intradía) con una arquitectura de **snapshot en lote** que mapea uno a uno con `DataProvider.get_quotes(batch)` -- ningún otro proveedor evaluado ofrece esta combinación de simplicidad de integración + cobertura completa + precio.
- Cobertura SIP completa (no solo IEX) calificada externamente como "excelente" para small-caps -- el requerimiento más importante de Atlas (las acciones explosivas son mayormente small/microcaps).
- Premarket/after-hours incluido, ~10 años de velas de 1 minuto (soluciona el techo de 60 días de Yahoo con amplio margen), 7+ años de velas diarias, SDK oficial (`alpaca-py`) maduro y muy activo.
- Es, de los 5, el de **mejor relación costo/beneficio para el requerimiento núcleo** ($99/mes vs. $199/mes de Polygon o Databento para tiempo real real).

### Gap que Alpaca NO resuelve: short interest y float

Ningún cambio de proveedor único cierra esto. Dos caminos posibles, **ninguno decidido todavía**:
- **Opción A**: agregar Polygon/Massive (tier Starter, $29/mes) exclusivamente por su short interest gratis, sin usar sus cotizaciones en tiempo real -- combinación de bajo costo ($99+$29=$128/mes) que cierra short interest pero no float.
- **Opción B**: agregar Intrinio ($150/mes) como fuente secundaria de baja frecuencia solo para float (dato trimestral, no necesita tiempo real) -- más caro pero es el único con float real de SEC.

### Por qué esto no es una decisión de "un solo proveedor para siempre"

El Principio 5 de la Constitución ("el proveedor de datos nunca podrá estar acoplado al motor") ya está reflejado en la arquitectura actual (`atlas/data/providers/`, interfaz `DataProvider` abstracta). Eso significa que Atlas puede combinar **un proveedor primario** (cotizaciones/velas/premarket -- Alpaca) **con una fuente secundaria de baja frecuencia** (short interest y/o float, que se actualizan quincenal/trimestralmente, no en cada escaneo de 5 minutos) sin violar el principio ni complicar el motor -- cada proveedor implementa la misma interfaz.

### Antes de aprobar cualquier cambio real

Varios hallazgos quedaron marcados como **no verificables desde documentación pública** (premarket exacto de Databento/Intrinio, cobertura real de OTC/microcaps de Finnhub/Intrinio, si short interest/noticias están incluidos en el tier de $150/mes de Intrinio). Antes de mover presupuesto real, se recomienda una prueba con clave de API gratuita/trial contra una muestra de ~50 símbolos conocidos como microcaps ilíquidos del Universo Racional, verificando cobertura real -- no solo lo que dice el marketing.

**Ninguna de estas acciones se ejecutó.** Esta es una recomendación para revisar y aprobar, siguiendo el formato PROBLEMA/HIPÓTESIS/PRINCIPIOS/IMPACTO/RIESGOS/VALIDACIÓN/CRITERIOS de la Constitución antes de tocar `atlas/data/providers/` o crear ninguna cuenta.
