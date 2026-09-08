/* Cabina 2.0 (2026-09-05, rediseño autorizado explícitamente).
 *
 * Reemplaza la interfaz/navegación anterior (18 vistas) por una sola
 * pantalla: Oportunidades como bloque protagonista (con Aprendizaje/
 * Aciertos en su encabezado) + 2 paneles secundarios (Universo, Mercado).
 * Ningún endpoint backend cambió -- esta reescritura es puramente de
 * presentación. El ranking/scoring de Oportunidades NO se modificó: se
 * reutiliza exactamente el mismo criterio de agrupación que ya usaba la
 * interfaz anterior (bucket por `estado_final`, luego detección más
 * reciente primero) -- ver `FINAL_STATE_ORDER`. La lógica de Mercado
 * (fetch/render/sparkline) es una copia literal de la versión anterior,
 * sin cambios de datos ni de cálculo -- solo se acortó el intervalo de
 * polling (ver `MERCADO_POLL_MS`).
 */

/* ---------------- helpers de formato (mínimos, sin dependencias) ---------------- */

function fmtNum(v, decimals = 1) {
  if (v == null || Number.isNaN(v)) return "--";
  return Number(v).toFixed(decimals);
}

function fmtPct(v, decimals = 1) {
  if (v == null || Number.isNaN(v)) return "--";
  const sign = v > 0 ? "+" : "";
  return `${sign}${Number(v).toFixed(decimals)}%`;
}

function fmtAge(seconds) {
  if (seconds == null) return "--";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  return `${Math.round(minutes / 60)} h`;
}

/* "Estado general" de Inicio (2026-09-07) -- tarjetas chicas alimentadas
 * por los mismos fetch ya existentes (Universo/Mercado/Oportunidades),
 * nunca una consulta nueva: cada render ya-existente llama a esta función
 * con su propio dato apenas lo recibe. */
let _inicioEstado = {};
function _actualizarEstadoGeneralInicio(partial) {
  Object.assign(_inicioEstado, partial);
  const el = document.getElementById("inicio-estado-general");
  if (!el) return;
  const e = _inicioEstado;
  el.innerHTML = `
    <div class="stat-card"><div class="sc-icon">🌎</div><div class="sc-label">Universo Atlas</div><div class="sc-value">${e.universoAtlas ?? "--"}</div><div class="sc-sub">operativo</div></div>
    <div class="stat-card"><div class="sc-icon">🤝</div><div class="sc-label">Racional conocido</div><div class="sc-value">${e.racionalConocido ?? "--"}</div><div class="sc-sub">catálogo parcial</div></div>
    <div class="stat-card"><div class="sc-icon">📈</div><div class="sc-label">Mercado</div><div class="sc-value">${e.mercadoEstado ?? "--"}</div><div class="sc-sub">${e.mercadoSub ?? ""}</div></div>
    <div class="stat-card"><div class="sc-icon">🎯</div><div class="sc-label">Oportunidades</div><div class="sc-value">${e.oportunidadesCount ?? "--"}</div><div class="sc-sub">accionables ahora</div></div>`;
}

/* ============================================================
 * OPORTUNIDADES -- bloque principal
 * Fuente: GET /api/radar-oportunidades (sin cambios de backend).
 * ============================================================ */

// Mismo orden ya usado por la interfaz anterior
// (atlas_live/radar/priority_classifier.py) -- no es un ranking nuevo,
// solo se reutiliza para decidir qué mostrar primero dentro de las 4-6
// tarjetas visibles.
const FINAL_STATE_ORDER = ["OPORTUNIDAD_PRIORITARIA", "VIGILAR", "PREPARACION", "NO_TOCAR"];
const MAX_OPORTUNIDADES_VISIBLES = 6;

let _oportunidades = [];

async function fetchOportunidades() {
  try {
    const res = await fetch("/api/radar-oportunidades");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    _oportunidades = data.oportunidades || [];
  } catch (err) {
    console.error("fetchOportunidades:", err);
  }
  renderOportunidades();
}

function _ordenarOportunidades(oportunidades) {
  // Solo las 2 categorías que priority_classifier.py ya considera
  // accionables -- PREPARACION/NO_TOCAR no compiten por los 6 lugares
  // del bloque principal (siguen existiendo en /api/radar-oportunidades
  // tal cual, esta pantalla simplemente no las prioriza en un espacio
  // reducido a propósito).
  const accionables = oportunidades.filter(
    (o) => o.estado_final === "OPORTUNIDAD_PRIORITARIA" || o.estado_final === "VIGILAR"
  );
  return [...accionables].sort((a, b) => {
    const diff = FINAL_STATE_ORDER.indexOf(a.estado_final) - FINAL_STATE_ORDER.indexOf(b.estado_final);
    if (diff !== 0) return diff;
    return (b.detected_at || "").localeCompare(a.detected_at || "");
  });
}

function _proyeccionHtml(o) {
  // Fuente ÚNICA y canónica de la proyección: la predicción CONGELADA
  // (la misma que audita Precisión de Magnitud) -- nunca se mezcla con
  // `evidencia_historica` (recalculada en vivo) en esta pantalla.
  const pred = o.prediccion_magnitud_congelada;
  if (pred && typeof pred.predicted_pct === "number") {
    return `<div class="proj-label">Proyección Atlas</div>
            <div class="proj-val">+${fmtNum(pred.predicted_pct)}%</div>
            <div class="proj-note">${pred.muestra_n ? `mediana de ${pred.muestra_n} casos similares` : "evidencia histórica"}</div>`;
  }
  return `<div class="proj-label">Proyección Atlas</div>
          <div class="proj-val proj-sin-dato">Sin evidencia suficiente</div>
          <div class="proj-note">Atlas todavía no tiene un grupo comparable para esta condición</div>`;
}

function _porQueHtml(o) {
  const razones = [];
  if (o.gates_fired && o.gates_fired.length) {
    razones.push(o.gates_fired[0].reason || o.gates_fired[0].name);
  }
  if (o.dinero_entra_sector && o.sector) {
    razones.push(`sector ${o.sector} con flujo de dinero activo`);
  }
  if (!razones.length && o.motivo_estado_final) razones.push(o.motivo_estado_final);
  return razones.length ? razones.join(" · ") : "Sin detalle adicional disponible.";
}

function _renderOportunidadesEn(el, top) {
  if (!el) return;
  if (!top.length) {
    el.innerHTML = `<div class="empty-state">Atlas no tiene oportunidades accionables en este momento.</div>`;
    return;
  }

  el.innerHTML = top.map((o, i) => {
    const chg = o.change_pct_actual;
    const chgClass = chg > 0 ? "up" : chg < 0 ? "down" : "";
    const rvol = o.relative_volume_hoy ?? o.relative_volume_at_detection;
    const rank1 = i === 0 ? " rank-1" : i === 1 ? " rank-2" : "";
    const detectadaHace = o.minutos_desde_deteccion != null ? `hace ${Math.round(o.minutos_desde_deteccion)} min` : "";

    return `
    <div class="opp-row${rank1}">
      <div class="rank-badge">${i + 1}</div>
      <div class="tk-col">
        <div class="ticker">${o.ticker}</div>
        <div class="meta">${detectadaHace}${o.racional_available ? " · Racional" : ""}</div>
      </div>
      <div class="px-col">
        <div class="price">${o.price_actual != null ? "$" + fmtNum(o.price_actual, 2) : "--"}</div>
        <div class="chg ${chgClass}">${fmtPct(chg)}</div>
      </div>
      <div class="vol-col">
        <div class="vol-val">${rvol != null ? "RVOL " + fmtNum(rvol) + "x" : "--"}</div>
      </div>
      <div class="proj-col">${_proyeccionHtml(o)}</div>
      <div class="why-col">
        <div class="why-line">${_porQueHtml(o)}</div>
      </div>
    </div>`;
  }).join("");
}

const MAX_OPORTUNIDADES_INICIO = 3;

// Un solo cálculo (`_ordenarOportunidades`, sin cambios) alimenta DOS
// vistas -- Oportunidades (hasta 6) e Inicio (hasta 3, preview) -- desde
// el mismo fetch, nunca una consulta nueva por vista.
function renderOportunidades() {
  const accionables = _ordenarOportunidades(_oportunidades);
  const top = accionables.slice(0, MAX_OPORTUNIDADES_VISIBLES);
  _renderOportunidadesEn(document.getElementById("opp-list"), top);
  _renderOportunidadesEn(document.getElementById("inicio-opp-preview"), top.slice(0, MAX_OPORTUNIDADES_INICIO));
  _actualizarEstadoGeneralInicio({ oportunidadesCount: accionables.length });
}

/* ============================================================
 * APRENDIZAJE -- chips dentro del encabezado de Oportunidades.
 * Fuente: GET /api/learning-maturity (sin cambios de backend),
 * campos hoy.precision / hoy.aciertos -- nunca recalculados acá.
 * ============================================================ */

async function fetchAprendizaje() {
  try {
    const res = await fetch("/api/learning-maturity");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    renderAprendizaje(data);
  } catch (err) {
    console.error("fetchAprendizaje:", err);
  }
}

function renderAprendizaje(data) {
  const hoy = (data && data.hoy) || {};
  const pctEl = document.getElementById("inicio-chip-aprendizaje");
  const pctSubEl = document.getElementById("inicio-chip-aprendizaje-sub");
  const aciertosEl = document.getElementById("inicio-chip-aciertos");
  const aciertosSubEl = document.getElementById("inicio-chip-aciertos-sub");
  if (!pctEl || !aciertosEl) return;

  pctEl.textContent = hoy.precision != null ? `${fmtNum(hoy.precision)}%` : "--";
  pctSubEl.textContent = hoy.evaluables != null ? `${hoy.evaluables} evaluados hoy` : "sin datos hoy";

  aciertosEl.textContent = hoy.aciertos != null ? hoy.aciertos : "--";
  aciertosSubEl.textContent = hoy.fallos != null ? `${hoy.fallos} fallos` : "";
}

/* ============================================================
 * UNIVERSO -- panel secundario.
 * Fuente: GET /api/universo-resumen (nuevo, aditivo, solo lectura --
 * ver atlas_live/server.py::api_universo_resumen()).
 * ============================================================ */

async function fetchUniverso() {
  try {
    const res = await fetch("/api/universo-resumen");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    renderUniverso(data);
  } catch (err) {
    console.error("fetchUniverso:", err);
  }
}

function renderUniverso(data) {
  const statsEl = document.getElementById("uni-stats");
  const volEl = document.getElementById("uni-volumen");
  if (!statsEl || !volEl) return;

  // 3 conteos deliberadamente separados (2026-09-07, corrección de
  // presentación autorizada explícitamente) -- "disponibles_racional" es
  // la INTERSECCIÓN entre el universo operativo y el catálogo Racional
  // conocido, nunca el tamaño de Racional en sí. Ver docstring de
  // /api/universo-resumen (server.py) para la fuente exacta de cada dato.
  statsEl.innerHTML = `
    <div class="uni-stat">
      <div class="n">${data.universo_total ?? "--"}</div>
      <div class="l">Universo Atlas operativo</div>
      <div class="uni-stat-note">lo que el radar realmente escanea</div>
    </div>
    <div class="uni-stat rac">
      <div class="n">${data.racional_catalogo_total ?? "--"}</div>
      <div class="l">Catálogo Racional conocido<span class="uni-badge-parcial">PARCIAL</span></div>
      <div class="uni-stat-note">${data.racional_declarado_app || "Racional declara un catálogo mayor en su App -- Atlas todavía no lo tiene completo"}</div>
    </div>
    <div class="uni-stat">
      <div class="n">${data.disponibles_racional ?? "--"}</div>
      <div class="l">Coincidencias Atlas ↔ Racional</div>
      <div class="uni-stat-note">del universo operativo Atlas, cuántos están también en el catálogo Racional conocido -- NO es la cantidad de acciones de Racional</div>
    </div>`;

  _actualizarEstadoGeneralInicio({ universoAtlas: data.universo_total, racionalConocido: data.racional_catalogo_total });

  const top = data.top_volumen || [];
  if (!top.length) {
    volEl.innerHTML = `<div class="empty-state small">Sin datos de volumen todavía (el radar no barrió nada aún).</div>`;
    return;
  }
  const maxVol = Math.max(...top.map((r) => r.volume || 0)) || 1;
  volEl.innerHTML = top.map((r) => `
    <div class="uni-row">
      <span class="t">${r.ticker}</span>
      <div class="bar"><span style="width:${Math.round((r.volume / maxVol) * 100)}%"></span></div>
      <span class="vv">${r.volume != null ? Number(r.volume).toLocaleString("es") : "--"}${r.racional_available ? " · Rac." : ""}</span>
    </div>`).join("");
}

/* ============================================================
 * MERCADO -- panel secundario. Copia literal de la lógica anterior
 * (fetch/render/sparkline/edad) -- CERO cambios de datos ni de cálculo,
 * solo el intervalo de polling se acorta (ver nota junto a
 * MERCADO_POLL_MS). Fuente: GET /api/mercado (snapshot ya cacheado por
 * market_view.py -- este fetch nunca dispara una consulta nueva a
 * Tradier, sin importar la frecuencia).
 * ============================================================ */

// Antes: 3000ms. La velocidad REAL de los datos la fija el ciclo de
// fondo de market_view.py (auto-ajustado, piso 10s / techo 90s / margen
// de seguridad 3x sobre la duración real del último ciclo) -- bajar el
// polling del navegador no adelanta ese ciclo, solo reduce la demora
// entre "el snapshot ya está listo" y "se ve en pantalla". Como
// `/api/mercado` está documentado como de costo cero por request (solo
// lee el snapshot cacheado, nunca contacta a Tradier), acortar el
// polling acá es seguro y no genera carga ni ciclos concurrentes nuevos.
// Bajar el piso/margen del ciclo de fondo en sí queda fuera de este
// cambio -- requiere medir duraciones reales de un día de mercado activo
// antes de tocar `market_view.py` (ver informe de la sesión).
const MERCADO_POLL_MS = 1500;
let _mercado = { generated_at: null, cycle_duration_s: null, rows: [] };
let _mercadoSearch = "";

async function fetchMercado() {
  try {
    const res = await fetch("/api/mercado");
    _mercado = await res.json();
  } catch (e) {
    // Sin cambios de estado ante un fallo de red puntual -- se sigue
    // mostrando el último snapshot conocido, con su antigüedad real.
  }
  renderMercado();
}

function _mercadoAgeLabel(generatedAtIso) {
  if (!generatedAtIso) return "Sin datos todavía";
  const ageSeconds = Math.max(0, Math.round((Date.now() - new Date(generatedAtIso).getTime()) / 1000));
  if (ageSeconds < 60) return `Actualizado hace ${ageSeconds}s`;
  const minutes = Math.round(ageSeconds / 60);
  if (minutes < 60) return `Actualizado hace ${minutes} min`;
  const hours = Math.round(minutes / 60);
  return `Actualizado hace ${hours} h`;
}

function _mercadoAgeShort(ageSeconds) {
  if (ageSeconds == null) return "?";
  if (ageSeconds < 60) return `${Math.round(ageSeconds)}s`;
  const minutes = Math.round(ageSeconds / 60);
  if (minutes < 60) return `${minutes}min`;
  const hours = Math.round(minutes / 60);
  return `${hours}h`;
}

function _sparklineSvg(points, isUp) {
  if (!points || points.length < 2) {
    return `<svg class="mercado-spark" viewBox="0 0 100 30"><line x1="0" y1="15" x2="100" y2="15" stroke="var(--border)" stroke-width="1"/></svg>`;
  }
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const coords = points.map((p, i) => {
    const x = (i / (points.length - 1)) * 100;
    const y = 28 - ((p - min) / span) * 26;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const color = isUp ? "var(--green)" : "var(--red)";
  return `<svg class="mercado-spark" viewBox="0 0 100 30"><polyline points="${coords}" fill="none" stroke="${color}" stroke-width="2"/></svg>`;
}

function _mercadoInitials(symbol) {
  return (symbol || "?").slice(0, 2).toUpperCase();
}

function renderMercado() {
  const listEl = document.getElementById("mercado-list");
  const metaEl = document.getElementById("mercado-meta");
  const dotEl = document.getElementById("mkt-status-dot");
  const textEl = document.getElementById("mkt-status-text");
  if (!listEl || !metaEl) return;

  const rows = _mercado.rows || [];

  if (dotEl && textEl) {
    const hayDatos = rows.length > 0;
    dotEl.className = "dot" + (hayDatos ? "" : " dot-off");
    textEl.textContent = hayDatos ? _mercadoAgeLabel(_mercado.generated_at) : "Mercado sin datos";
    _actualizarEstadoGeneralInicio({
      mercadoEstado: hayDatos ? "Con datos" : "Sin datos",
      mercadoSub: hayDatos ? _mercadoAgeLabel(_mercado.generated_at) : "esperando ciclo",
    });
  }

  metaEl.textContent = _mercado.total_universe
    ? `${_mercado.total_universe} instrumentos · ${_mercadoAgeLabel(_mercado.generated_at)}`
    : "sin cambios de lógica -- solo más rápido";

  if (!rows.length) {
    listEl.innerHTML = `<div class="empty-state small">${_mercado.ultimo_error ? "Error: " + _mercado.ultimo_error : "Esperando el primer ciclo de Mercado..."}</div>`;
    return;
  }

  const q = _mercadoSearch.trim().toUpperCase();
  const filtered = q
    ? rows.filter((r) => r.symbol.toUpperCase().includes(q) || (r.name || "").toUpperCase().includes(q))
    : rows;

  if (!filtered.length) {
    listEl.innerHTML = `<div class="empty-state small">Sin resultados para "${_mercadoSearch}".</div>`;
    return;
  }

  listEl.innerHTML = filtered.map((r) => {
    const isUp = (r.change_pct ?? 0) >= 0;
    const priceText = r.price != null ? r.price.toFixed(2) : "--";
    let pctClass = "";
    if (r.change_pct > 0) pctClass = "mercado-up";
    else if (r.change_pct < 0) pctClass = "mercado-down";
    const changePctText = r.change_pct == null
      ? "s/d"
      : `<span class="${pctClass}">${r.change_pct > 0 ? "+" : ""}${r.change_pct.toFixed(2)}%</span>`;

    let extIcon = '<span class="mercado-ext" title="Dato fresco de este ciclo">🟢</span>';
    if (r.data_status === "STALE") {
      extIcon = `<span class="mercado-ext mercado-stale" title="Último dato conocido (${_mercadoAgeShort(r.data_age_seconds)})">⏱</span>`;
    } else if (r.data_status === "SIN_DATO") {
      extIcon = '<span class="mercado-ext mercado-sindato" title="Sin dato todavía">—</span>';
    } else if (r.price_is_stale) {
      extIcon = '<span class="mercado-ext mercado-stale" title="Precio vencido -- fuera de sesión">⏱</span>';
    }

    return `
      <div class="mercado-row" data-symbol="${r.symbol}">
        <div class="mercado-logo">${_mercadoInitials(r.symbol)}</div>
        <div class="mercado-id">
          <div class="mercado-name">${r.name || r.symbol}</div>
          <div class="mercado-ticker">${r.symbol}</div>
        </div>
        <div class="mercado-price">${priceText}</div>
        <div class="mercado-change-pct">${changePctText}</div>
        ${extIcon}
        ${_sparklineSvg(r.sparkline, isUp)}
      </div>`;
  }).join("");
}

function startMercadoPolling() {
  fetchMercado();
  setInterval(fetchMercado, MERCADO_POLL_MS);
  const searchInput = document.getElementById("mercado-search");
  if (searchInput) {
    searchInput.addEventListener("input", () => {
      _mercadoSearch = searchInput.value;
      renderMercado();
    });
  }
}

/* ============================================================
 * UNIVERSO YAHOO -- sector independiente de Universo Racional.
 * Capa 1 (identidad, ~6.600+): GET /api/universo-yahoo, se carga UNA sola
 * vez al abrir la Cabina; la búsqueda por ticker/nombre filtra 100% en el
 * navegador, sin ninguna consulta nueva mientras se tipea.
 * Capa 2 (detalle real de Yahoo): GET /api/universo-yahoo/<symbol>, SOLO
 * al hacer clic explícito en un resultado -- nunca automático, nunca en
 * lote. Nunca muestra ni compara disponibilidad en Racional (a propósito).
 * ============================================================ */

let _universoYahoo = [];
const YAHOO_MAX_RESULTADOS = 50;

async function fetchUniversoYahoo() {
  const metaEl = document.getElementById("yahoo-universo-meta");
  try {
    const res = await fetch("/api/universo-yahoo");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    _universoYahoo = data.instrumentos || [];
    if (metaEl) metaEl.textContent = `${data.total ?? _universoYahoo.length} instrumentos cargados`;
  } catch (err) {
    console.error("fetchUniversoYahoo:", err);
    if (metaEl) metaEl.textContent = "No se pudo cargar el universo";
  }
  renderUniversoYahooResultados(document.getElementById("yahoo-search")?.value || "");
}

function _filtrarUniversoYahoo(query) {
  const q = query.trim().toUpperCase();
  if (!q) return [];
  return _universoYahoo
    .filter((r) => r.symbol.toUpperCase().includes(q) || (r.name || "").toUpperCase().includes(q))
    .slice(0, YAHOO_MAX_RESULTADOS);
}

function renderUniversoYahooResultados(query) {
  const el = document.getElementById("yahoo-results");
  if (!el) return;
  if (!query.trim()) {
    el.innerHTML = `<div class="empty-state small">Escribí un ticker o nombre para buscar entre ${_universoYahoo.length} instrumentos.</div>`;
    return;
  }
  const matches = _filtrarUniversoYahoo(query);
  if (!matches.length) {
    el.innerHTML = `<div class="empty-state small">Sin resultados para "${query}".</div>`;
    return;
  }
  el.innerHTML = matches.map((r) => `
    <div class="yahoo-row" data-symbol="${r.symbol}">
      <span class="yahoo-row-symbol">${r.symbol}</span>
      <span class="yahoo-row-name">${r.name || ""}</span>
      <span class="yahoo-row-exchange">${r.exchange || ""}</span>
      <span class="yahoo-row-type">${r.type || ""}</span>
    </div>`).join("");
  el.querySelectorAll(".yahoo-row").forEach((row) => {
    row.addEventListener("click", () => fetchUniversoYahooDetalle(row.dataset.symbol));
  });
}

async function fetchUniversoYahooDetalle(symbol) {
  const el = document.getElementById("yahoo-detail");
  if (!el) return;
  el.innerHTML = `<div class="empty-state small">Consultando Yahoo para ${symbol}...</div>`;
  try {
    const res = await fetch(`/api/universo-yahoo/${encodeURIComponent(symbol)}`);
    if (res.status === 404) {
      el.innerHTML = `<div class="empty-state small">Sin datos de Yahoo para ${symbol}.</div>`;
      return;
    }
    if (!res.ok) throw new Error("HTTP " + res.status);
    const q = await res.json();
    renderUniversoYahooDetalle(q);
  } catch (err) {
    console.error("fetchUniversoYahooDetalle:", err);
    el.innerHTML = `<div class="empty-state small">Error al consultar Yahoo para ${symbol}.</div>`;
  }
}

function renderUniversoYahooDetalle(q) {
  const el = document.getElementById("yahoo-detail");
  if (!el) return;
  const chg = q.change_percent;
  const chgClass = chg > 0 ? "up" : chg < 0 ? "down" : "";
  const campo = (label, value) => `<div class="yd-item"><span class="yd-k">${label}</span><span class="yd-v">${value}</span></div>`;
  el.innerHTML = `
    <div class="yahoo-detail-card">
      <div class="yahoo-detail-head">
        <span class="yahoo-detail-symbol">${q.symbol}</span>
        <span class="yahoo-detail-name">${q.name || ""}</span>
      </div>
      <div class="yahoo-detail-grid">
        ${campo("Precio", q.last_price != null ? "$" + fmtNum(q.last_price, 2) : "--")}
        <div class="yd-item"><span class="yd-k">Cambio %</span><span class="yd-v ${chgClass}">${fmtPct(chg)}</span></div>
        ${campo("Volumen", q.volume != null ? Number(q.volume).toLocaleString("es") : "--")}
        ${campo("Vol. promedio", q.average_volume != null ? Number(q.average_volume).toLocaleString("es") : "--")}
        ${campo("RVOL", q.relative_volume != null ? fmtNum(q.relative_volume) + "x" : "--")}
        ${campo("Apertura", q.open != null ? "$" + fmtNum(q.open, 2) : "--")}
        ${campo("Máximo", q.high != null ? "$" + fmtNum(q.high, 2) : "--")}
        ${campo("Mínimo", q.low != null ? "$" + fmtNum(q.low, 2) : "--")}
        ${campo("Cierre anterior", q.previous_close != null ? "$" + fmtNum(q.previous_close, 2) : "--")}
        ${campo("Market Cap", q.market_cap != null ? Number(q.market_cap).toLocaleString("es") : "--")}
        ${campo("Sector", q.sector || "--")}
        ${campo("Industria", q.industry || "--")}
        ${campo("Sesión", q.price_type || "--")}
        ${campo("Estado mercado", q.market_state || "--")}
      </div>
    </div>`;
}

/* ---------------- TOP 100 EN MOVIMIENTO (protagonista de Universo Yahoo) ----------------
 * Fuente: GET /api/universo-yahoo/top -- ranking mecánico puro sobre las
 * quotes que el radar YA obtiene (radar_worker.get_last_quotes(), Tradier,
 * en memoria) -- ver atlas_live/universo_yahoo_ranking.py para la fórmula
 * exacta. CERO llamadas nuevas a Tradier/Yahoo para construir este
 * ranking; el clic en una fila reutiliza fetchUniversoYahooDetalle(), el
 * MISMO mecanismo de detalle puntual ya existente (1 sola llamada). */

async function fetchUniversoYahooTop() {
  const metaEl = document.getElementById("yahoo-top-meta");
  const el = document.getElementById("yahoo-top-results");
  if (!el) return;
  try {
    const res = await fetch("/api/universo-yahoo/top");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    renderUniversoYahooTop(data);
    if (metaEl) {
      const antiguedad = data.ultimo_sweep_at ? _mercadoAgeLabel(data.ultimo_sweep_at) : "sin barrido todavía";
      metaEl.textContent = `${data.candidatos_tras_filtros ?? 0} cumplen los filtros de ${data.quotes_disponibles ?? 0} cotizados · ${antiguedad}`;
    }
  } catch (err) {
    console.error("fetchUniversoYahooTop:", err);
    if (metaEl) metaEl.textContent = "No se pudo cargar el ranking";
    el.innerHTML = `<div class="empty-state small">No se pudo cargar el Top 100 ahora mismo.</div>`;
  }
}

function renderUniversoYahooTop(data) {
  const el = document.getElementById("yahoo-top-results");
  if (!el) return;

  if (!data.quotes_disponibles) {
    el.innerHTML = `<div class="empty-state small">El radar todavía no tiene cotizaciones disponibles -- sin datos para calcular el ranking (nunca se inventa uno).</div>`;
    return;
  }
  const top = data.top || [];
  if (!top.length) {
    el.innerHTML = `<div class="empty-state small">Ninguna acción cumple ahora mismo los criterios (subiendo + volumen relativo + liquidez mínima) sobre ${data.quotes_disponibles} instrumentos cotizados.</div>`;
    return;
  }

  el.innerHTML = top.map((c) => `
    <div class="yahoo-row yahoo-top-row" data-symbol="${c.symbol}">
      <span class="yahoo-top-rank">${c.rank}</span>
      <span class="yahoo-row-symbol">${c.symbol}</span>
      <span class="yahoo-row-name">${c.name || ""}</span>
      <span class="yahoo-top-price">${c.price != null ? "$" + fmtNum(c.price, 2) : "--"}</span>
      <span class="yahoo-top-chg up">${fmtPct(c.change_percent)}</span>
      <span class="yahoo-top-rvol">${fmtNum(c.relative_volume)}x</span>
    </div>`).join("");
  el.querySelectorAll(".yahoo-top-row").forEach((row) => {
    row.addEventListener("click", () => fetchUniversoYahooDetalle(row.dataset.symbol));
  });
}

const TOP_MOVIMIENTO_POLL_MS = 60000; // mismo orden que UNIVERSO_POLL_MS -- el
// costo de recalcular es nulo (solo ordena quotes ya en memoria), pero no
// tiene sentido pedirlo más seguido que la cadencia real del radar (30-120s).

function initUniversoYahoo() {
  fetchUniversoYahoo();
  fetchUniversoYahooTop();
  const input = document.getElementById("yahoo-search");
  if (input) {
    input.addEventListener("input", () => renderUniversoYahooResultados(input.value));
  }
  setInterval(fetchUniversoYahooTop, TOP_MOVIMIENTO_POLL_MS);
}

/* ============================================================
 * LEARNING (vista completa) -- reutiliza /api/learning-maturity, el
 * MISMO endpoint que ya alimenta los chips de Inicio, más
 * /api/aprendizaje-seguridad-resumen (Hito 4, ya existente). Fetch propio
 * de esta vista -- no duplica el polling de los chips de Inicio, que
 * siguen viniendo de fetchAprendizaje().
 * ============================================================ */

const statCard = (icon, label, value, sub) => `
  <div class="stat-card">
    <div class="sc-icon">${icon}</div>
    <div class="sc-label">${label}</div>
    <div class="sc-value">${value ?? '<span class="dim">--</span>'}</div>
    ${sub ? `<div class="sc-sub">${sub}</div>` : ""}
  </div>`;

async function fetchLearningFull() {
  try {
    const res = await fetch("/api/learning-maturity");
    if (!res.ok) throw new Error("HTTP " + res.status);
    renderLearningFull(await res.json());
  } catch (err) {
    console.error("fetchLearningFull:", err);
  }
}

function renderLearningFull(data) {
  const el = document.getElementById("learning-headline");
  const ejesEl = document.getElementById("learning-ejes");
  if (!el || !ejesEl) return;
  const hoy = data.hoy || {}, acum = data.acumulada || {}, rec = data.reciente || {}, m = data.madurez || {};
  const pctTxt = (v) => v != null ? `${fmtNum(v)}%` : "--";

  el.innerHTML =
    statCard("🔎", "Estudiadas hoy", hoy.estudiadas, "universo escaneado") +
    statCard("🕵️", "Candidatas hoy", hoy.candidatas) +
    statCard("✅", "Señales hoy", hoy.senales, "sobrevivieron confirmación") +
    statCard("📋", "Evaluables hoy", hoy.evaluables, "con resultado cerrado") +
    statCard("🎯", "Aciertos hoy", hoy.aciertos, `${hoy.fallos ?? "--"} fallos`) +
    statCard("📊", "Precisión del día", pctTxt(hoy.precision)) +
    statCard("📊", "Precisión acumulada", pctTxt(acum.precision), acum.dias ? `${acum.dias} días` : "") +
    statCard("📊", "Precisión reciente", pctTxt(rec.precision), rec.dias_incluidos ? `últimos ${rec.dias_incluidos} días` : "") +
    statCard("🧠", "Madurez actual", m.estado || "Sin evidencia", m.eje_limitante ? `limita: ${m.eje_limitante}` : "");

  const ejes = (m.ejes || []);
  ejesEl.innerHTML = ejes.length ? `
    <div class="table-scroll"><table class="simple-table">
      <thead><tr><th>Eje</th><th>Estado</th><th>Evidencia</th></tr></thead>
      <tbody>${ejes.map((a) => `<tr><td>${a.nombre}${a.nombre === m.eje_limitante ? " ⛔" : ""}</td><td>${a.estado}</td><td>${a.explicacion || ""}</td></tr>`).join("")}</tbody>
    </table></div>` : `<div class="empty-state small">Sin datos.</div>`;
}

async function fetchSeguridadAprendizaje() {
  try {
    const res = await fetch("/api/aprendizaje-seguridad-resumen");
    if (!res.ok) throw new Error("HTTP " + res.status);
    renderSeguridadAprendizaje(await res.json());
  } catch (err) {
    console.error("fetchSeguridadAprendizaje:", err);
  }
}

function renderSeguridadAprendizaje(data) {
  const el = document.getElementById("seguridad-aprendizaje-body");
  const mecEl = document.getElementById("seguridad-mecanismo");
  if (!el) return;
  if (mecEl) mecEl.textContent = `Mecanismo: ${data.activation_mechanism_state || "OFF"}`;

  const conteoCards = (titulo, obj) => {
    if (!obj || !obj.ok) return statCard("⚠️", titulo, "sin datos");
    const partes = Object.entries(obj.conteos_por_estado || {}).map(([k, v]) => `${k}: ${v}`).join(" · ");
    return statCard("🛡️", titulo, obj.n_eventos ?? 0, partes || "sin eventos");
  };

  el.innerHTML = `<div class="stat-cards">
    ${conteoCards("Elegibilidad (Hito 3.3)", data.eligibilidad)}
    ${conteoCards("Observación Shadow (3.4)", data.shadow_observation)}
    ${conteoCards("Activación (3.5)", data.activacion)}
    ${conteoCards("Evaluación continua (3.6)", data.evaluacion_continua)}
  </div>`;
}

function initLearningView() {
  fetchLearningFull();
  fetchSeguridadAprendizaje();
  setInterval(fetchLearningFull, UNIVERSO_POLL_MS);
  setInterval(fetchSeguridadAprendizaje, UNIVERSO_POLL_MS);
}

/* ============================================================
 * PREDICTION JOURNAL -- panel existente, reconectado.
 * Fuente: GET /api/prediction-journal (sin cambios de backend).
 * ============================================================ */

async function fetchPredictionJournal() {
  try {
    const res = await fetch("/api/prediction-journal");
    if (!res.ok) throw new Error("HTTP " + res.status);
    renderPredictionJournal(await res.json());
  } catch (err) {
    console.error("fetchPredictionJournal:", err);
  }
}

function renderPredictionJournal(pj) {
  const el = document.getElementById("prediction-journal-body");
  if (!el) return;
  const sellado = pj.sealed_today
    ? `<div class="stat-cards">
        ${statCard("🕐", "Sellado a las", fmtTimeSimple(pj.sealed_today.sealed_at))}
        ${statCard("📋", "Candidatos sellados", pj.sealed_today.candidate_count)}
        ${statCard("🏆", "Top del día", pj.sealed_today.top_symbol || "--")}
      </div>`
    : `<div class="empty-state small">Todavía no se selló el ranking de hoy (${pj.date}) -- el sellado ocurre 09:25-09:30 ET.</div>`;

  const rows = pj.recent_days || [];
  el.innerHTML = sellado + `
    <div class="panel panel-full">
      <div class="panel-head"><span class="panel-title">Últimos días sellados</span></div>
      <div class="table-scroll"><table class="simple-table">
        <thead><tr><th>Fecha</th><th>Top símbolo</th><th>Prob. predicha</th><th>Resultado</th><th>Rendimiento</th><th>Anticipación</th></tr></thead>
        <tbody>${rows.length ? rows.map((d) => `<tr>
          <td>${d.date}</td><td>${d.top_symbol || "--"}</td>
          <td>${d.predicted_probability_pct != null ? fmtNum(d.predicted_probability_pct) + "%" : "--"}</td>
          <td>${d.result_category || "sin calificar"}</td>
          <td>${d.result_pct != null ? fmtPct(d.result_pct) : "--"}</td>
          <td>${d.anticipation_minutes != null ? Math.round(d.anticipation_minutes) + " min" : "--"}</td>
        </tr>`).join("") : '<tr><td colspan="6" class="empty-state small">Sin días sellados todavía.</td></tr>'}</tbody>
      </table></div>
    </div>`;
}

function initPredictionJournalView() {
  fetchPredictionJournal();
  setInterval(fetchPredictionJournal, UNIVERSO_POLL_MS);
}

/* ============================================================
 * EXIT JOURNAL -- panel existente, reconectado.
 * Fuente: GET /api/exit-journal (sin cambios de backend).
 * ============================================================ */

async function fetchExitJournal() {
  try {
    const res = await fetch("/api/exit-journal");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    renderExitJournal(data.summaries || []);
  } catch (err) {
    console.error("fetchExitJournal:", err);
  }
}

function renderExitJournal(summaries) {
  const el = document.getElementById("exit-journal-body");
  if (!el) return;
  el.innerHTML = `<div class="table-scroll"><table class="simple-table">
    <thead><tr><th>Símbolo</th><th>Fecha</th><th>Detección</th><th>Entrada (sellado)</th><th>Máximo</th><th>Rendimiento final</th><th>Muestras</th></tr></thead>
    <tbody>${summaries.length ? summaries.map((r) => `<tr>
      <td>${r.symbol}</td><td>${r.date}</td>
      <td>${fmtTimeSimple(r.detected_at)}</td><td>${fmtTimeSimple(r.entry_at)}</td>
      <td>${r.peak_at ? `${fmtTimeSimple(r.peak_at)} (${fmtPct(r.peak_return_pct)})` : "--"}</td>
      <td>${r.final_return_pct != null ? fmtPct(r.final_return_pct) : "--"}</td>
      <td>${r.sample_count ?? "--"}</td>
    </tr>`).join("") : '<tr><td colspan="7" class="empty-state small">Sin resúmenes todavía.</td></tr>'}</tbody>
  </table></div>`;
}

function initExitJournalView() {
  fetchExitJournal();
  setInterval(fetchExitJournal, UNIVERSO_POLL_MS);
}

/* ============================================================
 * MARCADOR HISTÓRICO -- panel existente, reconectado (versión
 * simplificada: calidad + bandas acumulativas + últimos eventos; el
 * detalle de grupos A/B/C/D y discriminación sigue disponible en el
 * propio /api/explosion-history para quien lo consulte directo).
 * ============================================================ */

async function fetchExplosionHistory() {
  try {
    const res = await fetch("/api/explosion-history");
    if (!res.ok) throw new Error("HTTP " + res.status);
    renderExplosionHistory(await res.json());
  } catch (err) {
    console.error("fetchExplosionHistory:", err);
  }
}

function renderExplosionHistory(data) {
  const calEl = document.getElementById("marcador-calidad");
  const bandEl = document.getElementById("marcador-bandas");
  const listEl = document.getElementById("marcador-lista");
  if (!calEl || !bandEl || !listEl) return;
  const cal = (data.por_banda && data.por_banda.calidad) || {};
  calEl.innerHTML =
    statCard("🔥", "Explosiones (≥30%)", cal.eventos_incluidos, "no artefactos") +
    statCard("✅", "Limpias", cal.limpias_start_observado, "start observado") +
    statCard("⏭", "Pre-iniciadas", cal.pre_iniciadas) +
    statCard("🚫", "Artefactos excluidos", cal.artefactos_excluidos);

  const bandas = (data.por_banda && data.por_banda.por_banda_acumulativa) || {};
  bandEl.innerHTML = ["30", "50", "100", "150", "200"].map((b) => {
    const d = bandas[b];
    return statCard("📈", `≥ +${b}%`, d && d.n ? d.n : "0", d && d.n ? `máx ${d.max_absoluto_pct}%` : "");
  }).join("");

  const eventos = (data.eventos || []).slice(0, 30);
  listEl.innerHTML = eventos.length ? `<div class="table-scroll"><table class="simple-table">
    <thead><tr><th>Símbolo</th><th>Día</th><th>Cal.</th><th>Máx</th><th>Duración</th></tr></thead>
    <tbody>${eventos.map((e) => `<tr>
      <td>${e.symbol}</td><td>${e.date}</td>
      <td>${e.quality === "limpia" ? "✅" : e.quality === "pre_iniciada" ? "⏭" : "?"}</td>
      <td>+${e.max_return_pct}%</td>
      <td>${e.duracion_movimiento_min != null ? e.duracion_movimiento_min + " min" : "--"}</td>
    </tr>`).join("")}</tbody>
  </table></div>` : `<div class="empty-state small">Sin explosiones ≥30% en el histórico disponible.</div>`;
}

function initMarcadorHistoricoView() {
  fetchExplosionHistory();
  setInterval(fetchExplosionHistory, UNIVERSO_POLL_MS);
}

/* ============================================================
 * ESTUDIO HISTÓRICO -- panel existente, reconectado.
 * Fuente: GET /api/market-study (sin cambios de backend).
 * ============================================================ */

async function fetchMarketStudy() {
  try {
    const res = await fetch("/api/market-study");
    if (!res.ok) throw new Error("HTTP " + res.status);
    renderMarketStudy(await res.json());
  } catch (err) {
    console.error("fetchMarketStudy:", err);
  }
}

function renderMarketStudy(data) {
  const statusEl = document.getElementById("estudio-status");
  const listEl = document.getElementById("estudio-lista");
  if (!statusEl || !listEl) return;
  const s = data.status || {};
  statusEl.innerHTML =
    statCard("⚙️", "Estado", s.state || "IDLE") +
    statCard("📚", "Procesados", s.procesados, s.universe_total ? `de ${s.universe_total}` : "") +
    statCard("📈", "Progreso", s.progreso_pct != null ? `${s.progreso_pct}%` : "--") +
    statCard("🔥", "Explosiones totales", s.explosiones_totales) +
    statCard("🤝", "En Racional", s.en_racional) +
    statCard("🌎", "Fuera de Racional", s.fuera_de_racional);

  const top = (data.top_explosions || []).slice(0, 25);
  listEl.innerHTML = top.length ? `<div class="table-scroll"><table class="simple-table">
    <thead><tr><th>Símbolo</th><th>Nombre</th><th>Fecha</th><th>Máx. intradía</th><th>Banda</th><th>Racional</th></tr></thead>
    <tbody>${top.map((r) => `<tr>
      <td>${r.ticker}</td><td>${r.name || "--"}</td><td>${r.date}</td>
      <td>${r.max_intraday_pct != null ? "+" + r.max_intraday_pct + "%" : "--"}</td>
      <td>${r.band || "--"}</td><td>${r.available_in_racional ? "Sí" : "No"}</td>
    </tr>`).join("")}</tbody>
  </table></div>` : `<div class="empty-state small">Sin explosiones estudiadas todavía.</div>`;
}

function initEstudioHistoricoView() {
  fetchMarketStudy();
  setInterval(fetchMarketStudy, UNIVERSO_POLL_MS);
}

/* ============================================================
 * MISSION CONTROL -- panel existente, reconectado.
 * Fuente: GET /api/mission-control (sin cambios de backend).
 * ============================================================ */

async function fetchMissionControl() {
  try {
    const res = await fetch("/api/mission-control");
    if (!res.ok) throw new Error("HTTP " + res.status);
    renderMissionControl(await res.json());
  } catch (err) {
    console.error("fetchMissionControl:", err);
  }
}

function renderMissionControl(data) {
  const histEl = document.getElementById("mission-control-market-state");
  const procEl = document.getElementById("mission-control-procesos");
  if (!histEl || !procEl) return;

  const hist = (data.market_state_history || []).slice(0, 20);
  histEl.innerHTML = hist.length ? `<div class="table-scroll"><table class="simple-table">
    <thead><tr><th>Hora del cambio</th><th>Estado nuevo</th><th>Estado anterior</th></tr></thead>
    <tbody>${hist.map((h) => `<tr><td>${fmtTimeSimple(h.timestamp)}</td><td>${h.market_state || "--"}</td><td>${h.previous_market_state || "-- (primer estado)"}</td></tr>`).join("")}</tbody>
  </table></div>` : `<div class="empty-state small">Sin cambios de sesión detectados todavía.</div>`;

  const procs = data.processes || [];
  procEl.innerHTML = procs.length ? `<div class="table-scroll"><table class="simple-table">
    <thead><tr><th>Proceso</th><th>Estado</th><th>Último latido</th><th>Progreso</th></tr></thead>
    <tbody>${procs.map((r) => {
      const p = r.progress || {};
      return `<tr><td>${r.process_type || ""} -- ${r.label || ""}</td><td>${r.state || "--"}</td><td>${fmtTimeSimple(r.last_heartbeat)}</td><td>${p.total ? `${p.done ?? 0} / ${p.total} ${p.unit || ""}` : "--"}</td></tr>`;
    }).join("")}</tbody>
  </table></div>` : `<div class="empty-state small">Sin procesos con heartbeat activo.</div>`;
}

function initMissionControlView() {
  fetchMissionControl();
  setInterval(fetchMissionControl, UNIVERSO_POLL_MS);
}

function fmtTimeSimple(iso) {
  if (!iso) return "--";
  try {
    return new Date(iso).toLocaleString("es", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" });
  } catch (e) {
    return iso;
  }
}

/* ============================================================
 * SIDEBAR -- navegación tipo SPA (2026-09-07, reestructuración
 * autorizada explícitamente). `activateView()` solo muestra/oculta
 * secciones -- Oportunidades/Aprendizaje/Universo/Mercado/Universo Yahoo
 * siguen exactamente igual que en Cabina 2.0 (arrancan y sondean desde
 * `init()`, sin importar la vista activa -- "mantener el polling vivo").
 * Los 6 paneles reconectados (Learning completo, Prediction Journal,
 * Exit Journal, Marcador Histórico, Estudio Histórico, Mission Control)
 * son de carga PEREZOSA: su fetch + su propio setInterval arrancan la
 * PRIMERA vez que se abre esa sección -- `_initializedViews` (un Set)
 * garantiza que esto ocurra una única vez por sección durante toda la
 * sesión, sin importar cuántas veces se vuelva a esa sección -- nunca se
 * duplica un timer ni un listener. */
function initHistoricoView() {
  initMarcadorHistoricoView();
  initEstudioHistoricoView();
}

const LAZY_VIEW_INIT = {
  "learning": initLearningView,
  "prediction-journal": initPredictionJournalView,
  "exit-journal": initExitJournalView,
  "historico": initHistoricoView,
  "mission-control": initMissionControlView,
};
const _initializedViews = new Set();

function activateView(viewId) {
  document.querySelectorAll(".view").forEach((el) => el.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach((el) => el.classList.remove("active"));
  const viewEl = document.getElementById(`view-${viewId}`);
  const navEl = document.querySelector(`.nav-item[data-view="${viewId}"]`);
  if (viewEl) viewEl.classList.add("active");
  if (navEl) navEl.classList.add("active");
  const titleEl = document.getElementById("topbar-view-title");
  if (titleEl && navEl) titleEl.textContent = navEl.textContent.trim();

  if (!_initializedViews.has(viewId)) {
    _initializedViews.add(viewId);
    const initFn = LAZY_VIEW_INIT[viewId];
    if (initFn) initFn();
  }
}

function setupSidebar() {
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => activateView(btn.dataset.view));
  });
}

/* Tabs internos de "Histórico" (2026-09-07) -- puramente de presentación:
 * alternan qué bloque se ve, nunca vuelven a pedir datos (ambos ya se
 * cargaron una vez al abrir "Histórico", ver initHistoricoView()). */
function setupHistoricoTabs() {
  document.querySelectorAll(".historico-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tabId = btn.dataset.historicoTab;
      document.querySelectorAll(".historico-tab").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".historico-tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      const panel = document.getElementById(`historico-tab-${tabId}`);
      if (panel) panel.classList.add("active");
    });
  });
}

/* ============================================================
 * CAPACITY MONITOR (2026-09-07, movido al sidebar el mismo día -- marcador
 * PERMANENTE, visible en cualquier vista, no solo Inicio). Sigue sin ser
 * una sección propia -- Mission Control sigue sin reintroducirse. Fuente:
 * GET /api/capacidad-resumen (público, sin token -- Cabina nunca maneja
 * ATLAS_ADMIN_TOKEN). Polling deliberadamente lento (10 min): la
 * capacidad del disco cambia despacio, no hace falta pedirla seguido.
 * Umbrales OK/WARNING/CRITICAL: NUNCA se calculan acá -- vienen ya
 * resueltos en `data.status` desde `capacity_monitor.py`.
 * ============================================================ */

function _fmtGB(bytes) {
  if (bytes == null) return "--";
  return (bytes / (1024 * 1024 * 1024)).toFixed(2) + " GB";
}

function renderCapacidadSinDatos() {
  const el = document.getElementById("capacity-widget");
  if (!el) return;
  el.classList.add("sin-datos");
  el.textContent = "Capacidad: sin datos";
}

async function fetchCapacidad() {
  try {
    const res = await fetch("/api/capacidad-resumen");
    if (!res.ok) throw new Error("HTTP " + res.status);
    renderCapacidad(await res.json());
  } catch (err) {
    console.error("fetchCapacidad:", err);
    renderCapacidadSinDatos();
  }
}

function renderCapacidad(data) {
  const el = document.getElementById("capacity-widget");
  if (!el) return;
  el.classList.remove("sin-datos");
  const pct = data.used_pct;
  const statusClass = "status-" + (data.status || "ok").toLowerCase();
  const barPct = pct != null ? Math.min(100, Math.max(0, pct)) : 0;

  const crecimientoTxt = data.growth && data.growth.history_status === "ok"
    ? `+${data.growth.mb_per_day} MB/día`
    : "calculando...";

  const proj = data.projection || {};
  const proyeccionTxt = proj.days_to_80_pct != null
    ? `${proj.days_to_80_pct} días al 80%`
    : "disponible cuando exista histórico suficiente";

  el.innerHTML = `
    <div class="cw-head">
      <span class="cw-title">Capacidad Atlas</span>
      <span class="cw-pct ${statusClass}">${pct != null ? pct + "%" : "--"}</span>
    </div>
    <div class="capacity-bar"><div class="capacity-bar-fill ${statusClass}" style="width:${barPct}%"></div></div>
    <div class="cw-detail">
      <span>${_fmtGB(data.used_bytes)} / ${_fmtGB(data.total_bytes)}</span>
      <span class="dim">Libre: ${_fmtGB(data.free_bytes)}</span>
      <span class="dim">Crecimiento: ${crecimientoTxt}</span>
      <span class="dim">Proyección: ${proyeccionTxt}</span>
    </div>`;
}

/* ---------------- arranque ---------------- */

const OPORTUNIDADES_POLL_MS = 30000;
const UNIVERSO_POLL_MS = 60000;
const CAPACITY_POLL_MS = 600000; // 10 min -- la capacidad cambia despacio

function init() {
  setupSidebar();
  setupHistoricoTabs();
  activateView("inicio");

  fetchOportunidades();
  fetchAprendizaje();
  fetchUniverso();
  startMercadoPolling();
  initUniversoYahoo();
  fetchCapacidad();

  setInterval(fetchOportunidades, OPORTUNIDADES_POLL_MS);
  setInterval(fetchAprendizaje, OPORTUNIDADES_POLL_MS);
  setInterval(fetchUniverso, UNIVERSO_POLL_MS);
  setInterval(fetchCapacidad, CAPACITY_POLL_MS);
}

document.addEventListener("DOMContentLoaded", init);
