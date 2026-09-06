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

function renderOportunidades() {
  const el = document.getElementById("opp-list");
  if (!el) return;

  const top = _ordenarOportunidades(_oportunidades).slice(0, MAX_OPORTUNIDADES_VISIBLES);

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
  const pctEl = document.getElementById("chip-aprendizaje");
  const pctSubEl = document.getElementById("chip-aprendizaje-sub");
  const aciertosEl = document.getElementById("chip-aciertos");
  const aciertosSubEl = document.getElementById("chip-aciertos-sub");
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

  statsEl.innerHTML = `
    <div class="uni-stat"><div class="n">${data.universo_total ?? "--"}</div><div class="l">acciones en el universo</div></div>
    <div class="uni-stat rac"><div class="n">${data.disponibles_racional ?? "--"}</div><div class="l">disponibles en Racional</div></div>`;

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

/* ---------------- arranque ---------------- */

const OPORTUNIDADES_POLL_MS = 30000;
const UNIVERSO_POLL_MS = 60000;

function init() {
  fetchOportunidades();
  fetchAprendizaje();
  fetchUniverso();
  startMercadoPolling();

  setInterval(fetchOportunidades, OPORTUNIDADES_POLL_MS);
  setInterval(fetchAprendizaje, OPORTUNIDADES_POLL_MS);
  setInterval(fetchUniverso, UNIVERSO_POLL_MS);
}

document.addEventListener("DOMContentLoaded", init);
