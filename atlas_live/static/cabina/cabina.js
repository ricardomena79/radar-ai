/* Cabina del Piloto -- navegación y renderizado. Sin framework, mismo
 * criterio que el resto de atlas_live/static/.
 *
 * Limpieza MOCK completada (2026-08-07, ver DECISION_LOG.md): NINGÚN panel
 * usa datos simulados. Cada sección proviene de un motor real (Radar,
 * Memory Engine, Prediction Journal, Exit Journal, Mission Control, config
 * del backend) o muestra un estado honesto ("Sin evidencia suficiente",
 * "Sin alertas registradas", "sin fuente conectada"). Regla permanente:
 * preferir un panel vacío antes que un dato inventado. `mock_data.js` fue
 * eliminado. */

const SEMAFORO_EMOJI = { verde: "🟢", amarillo: "🟡", rojo: "🔴", neutro: "⚪" };
const SEMAFORO_BADGE = { verde: "badge-verde", amarillo: "badge-amarillo", rojo: "badge-rojo", neutro: "badge-neutro" };
const SESSION_LABEL = {
  premarket: "PREMARKET",
  regular: "MERCADO ABIERTO",
  afterhours: "POSTMARKET",
  closed: "CERRADO",
};

function semaforoHtml(nivel) {
  return `<span class="semaforo" title="${nivel}">${SEMAFORO_EMOJI[nivel] || "⚪"}</span>`;
}

function badgeHtml(text, nivel) {
  return `<span class="badge ${SEMAFORO_BADGE[nivel] || "badge-neutro"}">${text}</span>`;
}

function fmtPct(value, decimals = 1) {
  if (value === null || value === undefined) return '<span class="dim">--</span>';
  const cls = value > 0 ? "num-pos" : value < 0 ? "num-neg" : "";
  const sign = value > 0 ? "+" : "";
  return `<span class="${cls}">${sign}${value.toFixed(decimals)}%</span>`;
}

function fmtNum(value, decimals = 1) {
  if (value === null || value === undefined) return '<span class="dim">--</span>';
  return value.toFixed(decimals);
}

function fmtTime(isoString) {
  if (!isoString) return '<span class="dim">--</span>';
  return isoString.slice(11, 16);
}

// Igual que fmtTime pero con segundos (HH:MM:SS) -- lo pide el indicador de
// frescura del canal rápido, donde el segundo exacto importa. Misma
// convención de la Cabina (se muestra el reloj tal cual viaja, etiquetado
// "ET"), para no mezclar dos horas distintas en la misma tarjeta.
function fmtTimeSec(isoString) {
  if (!isoString) return "--";
  return isoString.slice(11, 19);
}

/* Indicadores de frescura del dato (2026-08-07, ver DECISION_LOG.md
 * "Optimización de latencia"). Semáforo PURO en función de la antigüedad
 * en segundos -- 🟢 0-3s (en vivo), 🟡 3-10s (con retraso), 🔴 >10s (dato
 * viejo). Sin dato: ⚪. Función sin efectos, testeable de forma aislada. */
const FRESH_GREEN_MAX = 3;   // seg -- objetivo del canal rápido (Plan A/B)
const FRESH_AMBER_MAX = 10;  // seg -- todavía utilizable, pero ya con retraso

function freshnessStatus(ageSeconds) {
  if (ageSeconds === null || ageSeconds === undefined || !isFinite(ageSeconds)) {
    return { emoji: "⚪", cls: "fresh-none", label: "Sin dato" };
  }
  if (ageSeconds <= FRESH_GREEN_MAX) return { emoji: "🟢", cls: "fresh-green", label: "En vivo" };
  if (ageSeconds <= FRESH_AMBER_MAX) return { emoji: "🟡", cls: "fresh-amber", label: "Con retraso" };
  return { emoji: "🔴", cls: "fresh-red", label: "Dato viejo" };
}

// "hace X s" / "hace X min" -- antigüedad legible, nunca oculta. Se
// recalcula cada segundo (tickHotFreshness), no en cada fetch.
function fmtAge(ageSeconds) {
  if (ageSeconds === null || ageSeconds === undefined || !isFinite(ageSeconds)) return "sin dato";
  const s = Math.max(0, Math.round(ageSeconds));
  if (s < 60) return `hace ${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem ? `hace ${m}min ${rem}s` : `hace ${m}min`;
}

function fmtMoney(value) {
  // Corrección de interfaz (2026-08-07, ver DECISION_LOG.md): Atlas solo
  // opera el mercado estadounidense hoy -- "US$" en vez del "$" genérico,
  // para no dejar ambigua la moneda. Único punto de formato de precios de
  // toda la Cabina -- cambiar acá alcanza, nunca se formatea "$" a mano
  // en otro lugar (verificado).
  if (value === null || value === undefined) return '<span class="dim">--</span>';
  return "US$" + value.toFixed(2);
}

/* Trazabilidad de precio (2026-08-02) -- ver DATA_FUSION_ENGINE_PROPUESTA.md.
 * Regla permanente: Atlas nunca muestra un precio sin indicar de dónde
 * salió, a qué sesión corresponde, y cuándo se actualizó. Comparar un
 * precio Regular contra uno de After-hours de otra fuente NO es una
 * discrepancia -- son sesiones distintas, por eso esta etiqueta siempre
 * viaja junto al número. */
const PRICE_TYPE_LABEL = { regular: "Regular", premarket: "Premarket", afterhours: "After-hours", unknown: "Sin clasificar" };
const PRICE_SOURCE_LABEL = { yahoo_finance: "Yahoo Finance" };

// Indicador visual del estado real de mercado (2026-08-02, UX) -- se lee
// de `market_state` (el valor CRUDO del proveedor: REGULAR/PRE/POST/
// CLOSED/PREPRE/POSTPOST), no de `price_type`. Son cosas distintas a
// propósito: `price_type` describe qué precio se está USANDO (y por
// diseño, CLOSED cae en price_type="regular", porque ese es el precio
// correcto a mostrar) -- pero el badge visual debe seguir mostrando
// "mercado cerrado" como su propio estado distinto, no disfrazarlo de
// "Regular", o el usuario perdería justo la señal que pidió.
const MARKET_STATE_VISUAL = {
  REGULAR:  { emoji: "🟢", label: "REGULAR",     cls: "mstate-regular" },
  PRE:      { emoji: "🟡", label: "PREMARKET",   cls: "mstate-premarket" },
  PREPRE:   { emoji: "🟡", label: "PREMARKET",   cls: "mstate-premarket" },
  POST:     { emoji: "🟣", label: "AFTER-HOURS", cls: "mstate-afterhours" },
  POSTPOST: { emoji: "🟣", label: "AFTER-HOURS", cls: "mstate-afterhours" },
  CLOSED:   { emoji: "⚫", label: "CLOSED",       cls: "mstate-closed" },
};

function marketStateVisual(marketState) {
  return MARKET_STATE_VISUAL[marketState] || { emoji: "⚪", label: marketState || "SIN DATO", cls: "mstate-unknown" };
}

function marketStateBadgeHtml(marketState) {
  const v = marketStateVisual(marketState);
  return `<span class="mstate-badge ${v.cls}">${v.emoji} ${v.label}</span>`;
}

function priceSourceLabel(source) {
  return PRICE_SOURCE_LABEL[source] || source || "Fuente desconocida";
}

function priceTypeLabel(priceType) {
  return PRICE_TYPE_LABEL[priceType] || "Sin clasificar";
}

/* Línea compacta para usar junto a cualquier precio en tablas -- el badge
 * visual del estado de mercado (punto 1, no depende de leer texto) más
 * los tres datos obligatorios (fuente, tipo, hora), en el mínimo espacio. */
function priceContextLine(c) {
  if (!c || !c.price_type) return '<span class="dim">sin contexto de precio</span>';
  return `<span class="price-context">${marketStateBadgeHtml(c.market_state)} ${priceSourceLabel(c.price_source)} · ${priceTypeLabel(c.price_type)} · ${fmtTime(c.price_as_of)} ET</span>`;
}

/* Desglose completo para Hero/Plan B -- muestra TODOS los precios que
 * Yahoo entregó al mismo tiempo (Regular/Premarket/After-hours), nunca
 * solo el usado, para que nunca parezca que Atlas "oculta" un valor.
 * "Precio usado" queda visualmente destacado (punto 2) con una etiqueta
 * "EN USO" explícita, para que quede claro cuál alimenta el Ranking Score
 * sin tener que leer la fila de abajo. */
function priceBreakdownHtml(c) {
  if (!c || !c.price_type) return "";
  const row = (label, value, isUsed) => `
    <div class="price-row${isUsed ? " price-row--used" : ""}">
      <span class="price-row-label">${label}${isUsed ? ' <span class="price-row-used-pill">EN USO</span>' : ""}</span>
      <span class="price-row-value">${value !== null && value !== undefined ? fmtMoney(value) : '<span class="dim">--</span>'}</span>
    </div>`;

  // Investigación 3 (2026-08-06): un "--" desnudo en Premarket dejaba al
  // usuario sin saber si Atlas falló o si el proveedor simplemente no
  // entregó el dato en esta consulta -- confirmado con evidencia real
  // (yf.Ticker(...).info con preMarketPrice=None) que esto es una
  // ausencia honesta del proveedor, no un bug de Atlas (ver
  // DECISION_LOG.md). Esta fila hace esa causa explícita en vez de dejar
  // que el usuario se pregunte por qué -- mismo patrón ya usado en la
  // fila "Overnight" de abajo, con el agregado del motivo puntual.
  const premarketRow = (() => {
    if (c.price_premarket !== null && c.price_premarket !== undefined) {
      return row("Premarket", c.price_premarket, c.price_type === "premarket");
    }
    return `
    <div class="price-row price-row--unavailable price-row--explained">
      <span class="price-row-label">Premarket</span>
      <span class="price-row-value">No disponible</span>
      <span class="price-row-reason">Proveedor: ${priceSourceLabel(c.price_source)} · Motivo: no reportó precio de premarket en esta consulta.</span>
    </div>`;
  })();
  // Cuarta sesión (Overnight / Blue Ocean ATS, 2026-08-02) -- Atlas no
  // tiene este dato con ningún proveedor actual. Se muestra la fila con
  // el mismo criterio que el resto de la Cabina: nunca ocultar una
  // categoría que existe, nunca inventar un valor. Sin párrafo -- el
  // dato mismo ("No disponible con el proveedor actual") es la
  // explicación. Queda lista para el día en que un proveedor del Data
  // Fusion Engine llene `price_overnight` -- esta misma fila lo mostraría.
  const overnightRow = `
    <div class="price-row price-row--unavailable">
      <span class="price-row-label">Overnight (Blue Ocean ATS)</span>
      <span class="price-row-value">${c.price_overnight !== null && c.price_overnight !== undefined ? fmtMoney(c.price_overnight) : '<span class="dim">No disponible con el proveedor actual</span>'}</span>
    </div>`;
  return `
    <div class="price-breakdown">
      <div class="price-breakdown-header">${marketStateBadgeHtml(c.market_state)}<span class="price-breakdown-header-note">Estado de mercado detectado por ${priceSourceLabel(c.price_source)}</span></div>
      <div class="price-breakdown-used">
        <span class="price-row-label">Precio utilizado <span class="price-row-used-pill">EN USO -- Ranking Score</span></span>
        <span class="price-breakdown-used-value">${fmtMoney(c.price)}</span>
      </div>
      <div class="price-breakdown-grid">
        ${row("Regular", c.price_regular, c.price_type === "regular")}
        ${premarketRow}
        ${row("After-hours", c.price_afterhours, c.price_type === "afterhours")}
        ${overnightRow}
      </div>
      <div class="price-row price-row--meta"><span class="price-row-label">Fuente</span><span class="price-row-value">${priceSourceLabel(c.price_source)}</span></div>
      <div class="price-row price-row--meta"><span class="price-row-label">Actualizado</span><span class="price-row-value">${fmtTime(c.price_as_of)} ET</span></div>
    </div>
    <div class="price-note">Un precio Regular y uno de After-hours pueden diferir legítimamente -- son sesiones distintas del mercado, no un error.</div>`;
}

/* ---------------- Navegación ---------------- */

function setupNav() {
  const items = document.querySelectorAll(".nav-item");
  items.forEach((item) => {
    item.addEventListener("click", () => {
      items.forEach((i) => i.classList.remove("active"));
      item.classList.add("active");
      document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
      document.getElementById("view-" + item.dataset.view).classList.add("active");
    });
  });
}

/* ---------------- Estado global (sidebar) ---------------- */

/* ---------------- Barra superior: reloj de Nueva York + sesión + cuenta
   regresiva, calculados en vivo con la MISMA regla de horario que ya usa
   `market_hours.py` (premarket 04:00-09:30, regular 09:30-16:00,
   afterhours 16:00-20:00, lunes a viernes). Esto NO es un dato de Atlas --
   es aritmética de calendario, así que corre en vivo aunque el resto de
   la cabina siga con datos simulados. No contempla feriados de mercado,
   misma limitación ya documentada en market_hours.py. */

function nyNow() {
  return new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
}

function getSessionNY(ny) {
  const day = ny.getDay(); // 0=domingo .. 6=sábado
  if (day === 0 || day === 6) return "closed";
  const mins = ny.getHours() * 60 + ny.getMinutes();
  if (mins >= 4 * 60 && mins < 9 * 60 + 30) return "premarket";
  if (mins >= 9 * 60 + 30 && mins < 16 * 60) return "regular";
  if (mins >= 16 * 60 && mins < 20 * 60) return "afterhours";
  return "closed";
}

function nextBoundary(ny, session) {
  const target = new Date(ny);
  if (session === "premarket") { target.setHours(9, 30, 0, 0); return { target, label: "Apertura en" }; }
  if (session === "regular") { target.setHours(16, 0, 0, 0); return { target, label: "Cierre en" }; }
  if (session === "afterhours") { target.setHours(20, 0, 0, 0); return { target, label: "Fin postmarket en" }; }
  // closed -> próximo día hábil a las 04:00
  target.setHours(4, 0, 0, 0);
  const isWeekday = ny.getDay() >= 1 && ny.getDay() <= 5;
  const yaPaso = !(isWeekday && ny < target);
  if (yaPaso) {
    do { target.setDate(target.getDate() + 1); } while (target.getDay() === 0 || target.getDay() === 6);
    target.setHours(4, 0, 0, 0);
  }
  return { target, label: "Premarket en" };
}

function fmtHHMMSS(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds));
  const hh = String(Math.floor(s / 3600)).padStart(2, "0");
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

function tickTopbar() {
  const ny = nyNow();
  const session = getSessionNY(ny);
  const { target, label } = nextBoundary(ny, session);
  const secondsLeft = (target - ny) / 1000;

  document.getElementById("topbar-session").textContent = SESSION_LABEL[session] || session.toUpperCase();
  document.getElementById("topbar-clock").textContent =
    ny.toTimeString().slice(0, 8);
  document.getElementById("topbar-countdown-label").innerHTML =
    `${label} <span id="topbar-countdown" class="topbar-mono">${fmtHHMMSS(secondsLeft)}</span>`;
}

/* Panel 1 (CONECTADO): Estado de Atlas + Última actualización salen de
 * /api/ranking, que ya expone scan_worker.STATE.snapshot() real -- no se
 * tocó server.py ni scan_worker.py, el endpoint ya existía. La sesión de
 * mercado, la hora de NY y la cuenta regresiva siguen siendo cálculo de
 * calendario en el cliente (tickTopbar), no dependen de este fetch. */
const STATUS_POLL_MS = 15000;

// Calidad del Mercado (2026-08-03) lee `context` (VIX real, ya calculado
// por MarketContextEngine) del mismo /api/ranking que ya se pollea acá --
// se guarda en una variable global para no duplicar el fetch.
let _lastContext = null;
// Estado real del último ciclo (para la barra de actividad -- limpieza MOCK).
let _systemStatus = null;

async function fetchSystemStatus() {
  try {
    const res = await fetch("/api/ranking");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    const dot = document.getElementById("topbar-status-dot");
    const text = document.getElementById("topbar-status-text");
    const lastUpdate = document.getElementById("topbar-last-update");

    if (data.scanning) {
      dot.className = "dot dot-amber";
      text.textContent = "Escaneando...";
    } else if (data.last_error) {
      dot.className = "dot dot-red";
      text.textContent = "Error en el último ciclo";
    } else if (data.generated_at === null) {
      dot.className = "dot dot-amber";
      text.textContent = "Sin escaneo todavía";
    } else {
      dot.className = "dot dot-green";
      text.textContent = `Sistema OK (${data.symbols_ok}/${data.symbols_scanned})`;
    }

    lastUpdate.textContent = data.generated_at ? data.generated_at.slice(11, 19) + " UTC" : "--";
    _lastContext = data.context;
    _systemStatus = data;
    renderMarketQuality();
    renderActivity();
    renderOpina();
  } catch (err) {
    document.getElementById("topbar-status-dot").className = "dot dot-red";
    document.getElementById("topbar-status-text").textContent = "Sin conexión con el servidor";
    _systemStatus = null;
    renderActivity();
    console.error("fetchSystemStatus:", err);
  }
}

function renderGlobalStatus() {
  fetchSystemStatus();
  setInterval(fetchSystemStatus, STATUS_POLL_MS);
  tickTopbar();
  setInterval(tickTopbar, 1000);
}

/* ---------------- Dashboard: Oportunidad del Día (dominante) + 3 bloques ---------------- */

// Calidad del Mercado (2026-08-03) -- factores reales, SIN veredicto
// compuesto ("BUENA/REGULAR/MALA"): combinarlos en un solo número sería
// un algoritmo nuevo sin validar (Principio 3 de la Constitución). El
// usuario prefirió explícitamente mostrar los factores por separado
// hasta que exista una fórmula validada -- ver DECISION_LOG.md.
// Umbral de VIX reutilizado tal cual de scan_worker.py (RISK_VIX_HIGH=25,
// RISK_VIX_LOW=18), no inventado acá.
const VIX_HIGH = 25.0;
const VIX_LOW = 18.0;

function vixLabel(vix) {
  if (vix === null || vix === undefined) return '<span class="dim">sin dato</span>';
  const nivel = vix >= VIX_HIGH ? "Alta" : vix <= VIX_LOW ? "Baja" : "Normal";
  return `${fmtNum(vix)} (${nivel})`;
}

function renderMarketQuality() {
  const el = document.getElementById("dashboard-quality");
  const candidates = _memoryRanking.candidates;
  const total = candidates.length;
  const vix = _lastContext ? _lastContext.vix_price : null;
  // Regla de consenso (2026-08-03): estos 4 conteos también deben exigir
  // eligible_radar -- si no, "Calidad del Mercado" podría reportar
  // "oportunidades" u "candidatos que superan el Ranking" inflados con
  // símbolos que Radar Explosivo ya rechazó. Reutiliza los mismos
  // helpers que ya protegen Explosivas/Momentum/No tocar, en vez de
  // duplicar el criterio.
  const superanRanking = candidates.filter(c => c.eligible_radar && (c.semaforo === "verde" || c.semaforo === "amarillo")).length;
  const altaConfianza = candidates.filter(c => c.eligible_radar && c.confidence === "Alta").length;
  const microcapsVerde = _explosivasReal().length;
  const noTocar = _noTocarReal().length;

  el.className = "quality-bar";
  el.innerHTML = `
    <span class="quality-label">📊 Calidad del Mercado -- factores reales <span class="dim" style="font-weight:400;font-size:11px">(sin veredicto compuesto todavía -- ver diseño en discusión)</span></span>
    <span class="quality-factors">
      <span class="quality-factor">VIX: <b>${vixLabel(vix)}</b></span>
      <span class="quality-factor">Candidatos que superan el Ranking: <b>${total ? `${superanRanking}/${total} (${fmtNum(superanRanking / total * 100, 0)}%)` : '<span class="dim">sin datos</span>'}</b></span>
      <span class="quality-factor">Oportunidades de alta confianza: <b>${altaConfianza}</b></span>
      <span class="quality-factor">Microcaps con evidencia confiable: <b>${microcapsVerde}</b></span>
      <span class="quality-factor">Símbolos "No tocar" hoy: <b>${noTocar}</b></span>
    </span>`;
}

/* "¿Por qué NO?" -- datos REALES de /api/explosive-diagnostics (tabla de
 * exclusiones del Radar: symbol, etapa que falló, motivo real). Se muestran
 * los descartes más "llamativos" (mayor gap% o RVOL) para responder la
 * pregunta obvia; el motivo es el que registró el propio motor, nunca
 * inventado. Estado honesto si no hay descartes reales. */
let _explosiveDiagnostics = null;

async function fetchExplosiveDiagnostics() {
  try {
    const res = await fetch("/api/explosive-diagnostics");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _explosiveDiagnostics = await res.json();
  } catch (err) {
    console.error("fetchExplosiveDiagnostics:", err);
  }
  renderWhyNot();
}

function _whyNotApparentReason(r) {
  const bits = [];
  if (r.gap_pct !== null && r.gap_pct !== undefined) bits.push(`Gap ${r.gap_pct >= 0 ? "+" : ""}${r.gap_pct.toFixed(1)}%`);
  if (r.relative_volume !== null && r.relative_volume !== undefined) bits.push(`RVOL ${r.relative_volume.toFixed(1)}x`);
  return bits.length ? bits.join(" · ") : "Apareció en el escaneo";
}

function renderWhyNot() {
  const el = document.getElementById("dashboard-whynot");
  const table = (_explosiveDiagnostics && _explosiveDiagnostics.available && _explosiveDiagnostics.table) || [];
  // Solo exclusiones con motivo real; priorizar las de mayor gap/RVOL (las
  // que "a primera vista" parecerían atractivas), máximo 5.
  const excluded = table
    .filter(r => r.status === "Excluida" && r.reason)
    .sort((a, b) => (Math.abs(b.gap_pct || 0) + (b.relative_volume || 0)) - (Math.abs(a.gap_pct || 0) + (a.relative_volume || 0)))
    .slice(0, 5);
  el.innerHTML = excluded.length
    ? excluded.map(w => `
        <div class="whynot-item">
          <div class="whynot-q">¿Por qué no <span class="sym">${w.symbol}</span>?${w.name ? ` <span class="dim">(${w.name})</span>` : ""} -- ${_whyNotApparentReason(w)}</div>
          <div class="whynot-a">${w.reason}</div>
        </div>`).join("")
    : `<div class="empty-state">Sin descartes que aclarar en este momento -- ningún candidato llamativo quedó fuera del Radar.</div>`;
}

/* ---------------- Panel 2-6 (CONECTADO): /api/memory-ranking ----------
 * Mismo Ranking Score ya validado en atlas_live/memory/ (ver MEMORY_ENGINE.md),
 * servido por scan_worker.py sin recalcular nada nuevo. `etaMovementMinutes`
 * y `historicalTarget` NO existen en el backend real (nunca existieron,
 * ver mock_data.js) -- se muestran como "sin cálculo real" en vez de
 * inventar un número, ahora que se conecta de verdad. */
const MEMORY_POLL_MS = 30000;
let _memoryRanking = { generated_at: null, candidates: [] };

/* Motor Predictivo -- Cabina del Piloto, Sprint 4 (2026-08-06, ver
 * DECISIONES.md). Última predicción de `entry_window` para el candidato
 * #1 del Hero (mismo símbolo que ya decide `renderHero`/`renderOportunidad`,
 * no un candidato distinto) -- `/api/predictive-engine/<symbol>`, solo
 * lectura sobre atlas_live/predictive_engine/prediction_log.py. */
let _entryWindow = null;

async function fetchEntryWindow(symbol) {
  if (!symbol) {
    _entryWindow = null;
    return;
  }
  try {
    const res = await fetch(`/api/predictive-engine/${encodeURIComponent(symbol)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _entryWindow = await res.json();
  } catch (err) {
    console.error("fetchEntryWindow:", err);
    _entryWindow = null;
  }
}

const ENTRY_WINDOW_RECOMMENDATION_LABEL = {
  esperar: "Esperar",
  comprar_ahora: "Comprar ahora",
  movimiento_pudo_haber_empezado: "La ventana ya pasó",
};

/* HTML de los metric-value de la ventana óptima de entrada, reutilizado
 * tanto por el Hero del Dashboard como por el detalle de Oportunidad del
 * día -- misma fuente (`_entryWindow`), mismo criterio: nunca un número
 * inventado, "Evidencia insuficiente" es un estado honesto, no un error. */
function entryWindowMetricsHtml(labelClass = "hero-metric-label", valueClass = "hero-metric-value", wrap = null) {
  const p = _entryWindow;
  const item = (label, value, dim) => {
    const inner = `<div class="${labelClass}">${label}</div><div class="${valueClass}${dim ? " dim" : ""}"${dim ? ' style="font-size:14px"' : ""}>${value}</div>`;
    return wrap ? `<div class="${wrap}">${inner}</div>` : `<div>${inner}</div>`;
  };
  if (!p || !p.available || p.confidence === "insuficiente") {
    const detalle = p && p.available
      ? `${p.sample_size} caso(s) histórico(s) -- se necesitan al menos 10 para estimar`
      : "todavía no se registró ninguna predicción para este símbolo hoy";
    return (
      item("Ventana óptima de entrada", "Evidencia insuficiente", true) +
      item("Condición de evidencia", p && p.evidence_condition ? p.evidence_condition : detalle, true)
    );
  }
  const recomendacion = ENTRY_WINDOW_RECOMMENDATION_LABEL[p.recommendation] || p.recommendation || "sin recomendación";
  return (
    item("Ventana óptima de entrada", recomendacion, false) +
    item("Mediana histórica", `${fmtNum(p.value)} min`, false) +
    item("Rango P25-P75", `${fmtNum(p.range_low)}-${fmtNum(p.range_high)} min`, false) +
    item("Casos similares", p.sample_size, false) +
    item("Nivel de confianza", p.confidence, false) +
    item("Condición de evidencia", p.evidence_condition || "sin condición confiable", false)
  );
}

async function fetchMemoryRanking() {
  try {
    const res = await fetch("/api/memory-ranking");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _memoryRanking = await res.json();
  } catch (err) {
    console.error("fetchMemoryRanking:", err);
  }
  const top = _memoryRanking.candidates && _memoryRanking.candidates[0];
  await fetchEntryWindow(top && top.eligible_radar ? top.symbol : null);
  renderHero();
  renderPlanB();
  renderTripleColumns();
  renderRadarCompleto();
  renderMicrocaps();
  renderMomentum();
  renderNoTocar();
  renderMarketQuality();
  renderOpina();
  if (document.querySelector('.nav-item[data-view="oportunidad"]').classList.contains("active")) {
    renderOportunidad();
  }
}

function startMemoryRankingPolling() {
  fetchMemoryRanking();
  setInterval(fetchMemoryRanking, MEMORY_POLL_MS);
}

/* ---------------- Canal de actualización rápida (Plan A + Plan B) --------
 * Optimización de latencia (2026-08-07, ver DECISION_LOG.md). EXCLUSIVO
 * para los 2 símbolos visibles -- la Oportunidad del Día (Hero = candidato
 * #1 elegible) y el Plan B (candidato #2 elegible). El scanner del universo
 * (~244) NO cambia; este canal solo refresca esos 2 precios contra
 * `/api/hot-quote`, para mantenerlos con antigüedad <=3s cuando el
 * proveedor lo permite. Presupuesto: 2 símbolos cada 3s ~= 40 req/min,
 * dentro de Finnhub (60/min).
 *
 * "Último recibido, nunca ocultar la antigüedad": si el proveedor no
 * entrega un dato nuevo (mismo price_as_of, error, o rate-limit) NO se
 * reinicia el reloj -- se conserva el último precio bueno y su antigüedad
 * sigue creciendo (🟢->🟡->🔴). El timestamp solo avanza cuando llega un
 * price_as_of genuinamente nuevo. */
const HOT_POLL_MS = 3000;
// symbol -> { price, change_pct, price_type, market_state, source,
//             price_as_of, baseAgeMs, receivedAt, lastStatus, gotNew }
const _hotQuotes = {};

// Símbolos que alimenta el canal rápido AHORA: Hero (candidato[0] elegible)
// y Plan B (candidato[1] elegible). Mismo criterio de elegibilidad que ya
// usan renderHero/renderPlanB -- si un candidato no es elegible, no se
// refresca (no se muestra).
function hotSymbols() {
  const c = _memoryRanking.candidates || [];
  const out = [];
  if (c[0] && c[0].eligible_radar) out.push(c[0].symbol);
  if (c[1] && c[1].eligible_radar && c[1].symbol !== (c[0] && c[0].symbol)) out.push(c[1].symbol);
  return out;
}

async function fetchHotQuotes() {
  const symbols = hotSymbols();
  if (symbols.length === 0) { renderHotWidgets(); return; }
  try {
    const res = await fetch(`/api/hot-quote?symbols=${encodeURIComponent(symbols.join(","))}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const serverMs = Date.parse(data.server_time);
    const clientNow = Date.now();
    for (const q of (data.quotes || [])) {
      const prev = _hotQuotes[q.symbol];
      if (q.status === "ok" && q.price_as_of) {
        const isNew = !prev || prev.price_as_of !== q.price_as_of;
        if (isNew) {
          // Dato genuinamente nuevo: el reloj de antigüedad se reancla al
          // timestamp real del proveedor (baseAgeMs = server_time - dato).
          const asOfMs = Date.parse(q.price_as_of);
          _hotQuotes[q.symbol] = {
            price: q.price,
            change_pct: q.change_pct,
            price_type: q.price_type,
            market_state: q.market_state,
            source: q.source,
            price_as_of: q.price_as_of,
            baseAgeMs: (isFinite(serverMs) && isFinite(asOfMs)) ? (serverMs - asOfMs) : 0,
            receivedAt: clientNow,
            lastStatus: "ok",
            gotNew: true,
          };
        } else {
          // Mismo timestamp: el proveedor devolvió, pero sin dato nuevo. NO
          // se reancla el reloj -- la antigüedad sigue creciendo sola.
          prev.lastStatus = "ok";
          prev.gotNew = false;
        }
      } else if (prev) {
        // Proveedor no entregó (error/rate-limit): se conserva el último
        // recibido, su antigüedad sigue creciendo. Nunca se oculta.
        prev.lastStatus = q.status || "unavailable";
        prev.gotNew = false;
      }
      // Si status != ok y no hay prev, no hay nada que mostrar todavía --
      // el Hero mostrará su precio de ciclo con su propia antigüedad.
    }
  } catch (err) {
    console.error("fetchHotQuotes:", err);
    // Fallo de red del canal: se conserva todo lo previo, no se resetea.
  }
  renderHotWidgets();
}

// Antigüedad EN VIVO del último dato bueno de un símbolo, sin skew de reloj:
// (server_time - price_as_of)  [ambos del servidor]  +  (ahora - recibido)
// [ambos del cliente]. Devuelve segundos, o null si no hay dato.
function hotAgeSeconds(entry) {
  if (!entry || !isFinite(entry.baseAgeMs)) return null;
  return entry.baseAgeMs / 1000 + (Date.now() - entry.receivedAt) / 1000;
}

// Widget de frescura para un símbolo -- los 5 indicadores pedidos: hora
// exacta del dato, antigüedad, proveedor, semáforo, y aviso de "último
// recibido" cuando el proveedor no entregó un dato nuevo.
function hotFreshnessHtml(entry) {
  if (!entry) {
    return `<span class="hot-fresh-dot">⚪</span><span class="hot-fresh-label">Refrescando precio en vivo…</span>`;
  }
  const age = hotAgeSeconds(entry);
  const st = freshnessStatus(age);
  const stale = entry.lastStatus !== "ok" || entry.gotNew === false;
  const note = stale
    ? `<span class="hot-fresh-note" title="El proveedor no entregó un dato nuevo en la última consulta">· último recibido</span>`
    : "";
  return `
    <span class="hot-fresh-dot">${st.emoji}</span>
    <span class="hot-fresh-label">${st.label}</span>
    <span class="hot-fresh-price">${fmtMoney(entry.price)}</span>
    <span class="hot-fresh-age">${fmtAge(age)}</span>
    <span class="hot-fresh-time">dato: ${fmtTimeSec(entry.price_as_of)} ET</span>
    <span class="hot-fresh-src">${priceSourceLabel(entry.source)}</span>
    ${note}`;
}

// Rellena los contenedores de frescura del Hero y el Plan B. Se llama desde
// el fetch (cada 3s) Y desde el tick de 1s (para que "hace X s" avance solo
// aunque no llegue dato nuevo). Actualiza también el precio destacado.
function renderHotWidgets() {
  const c = _memoryRanking.candidates || [];
  const heroSym = (c[0] && c[0].eligible_radar) ? c[0].symbol : null;
  const planBSym = (c[1] && c[1].eligible_radar) ? c[1].symbol : null;

  const heroBox = document.getElementById("hot-fresh-hero");
  if (heroBox) {
    const e = heroSym ? _hotQuotes[heroSym] : null;
    heroBox.className = "hot-fresh " + freshnessStatus(hotAgeSeconds(e)).cls;
    heroBox.innerHTML = hotFreshnessHtml(e);
    if (e) {
      const pv = document.getElementById("hero-price-value");
      if (pv) pv.innerHTML = fmtMoney(e.price);
    }
  }
  const planBox = document.getElementById("hot-fresh-planb");
  if (planBox) {
    const e = planBSym ? _hotQuotes[planBSym] : null;
    planBox.className = "hot-fresh " + freshnessStatus(hotAgeSeconds(e)).cls;
    planBox.innerHTML = hotFreshnessHtml(e);
    if (e) {
      const pv = document.getElementById("planb-price-value");
      if (pv) pv.innerHTML = fmtMoney(e.price);
    }
  }
}

function tickHotFreshness() {
  renderHotWidgets();
}

function startHotChannel() {
  fetchHotQuotes();
  setInterval(fetchHotQuotes, HOT_POLL_MS);
  setInterval(tickHotFreshness, 1000);
}

/* Paneles 9-12 (Memory Engine, Prediction Journal, Exit Journal, Mission
 * Control) -- cambian con mucha menos frecuencia que el ranking (una vez
 * por día de mercado, o solo cuando corre un proceso instrumentado), así
 * que su intervalo de sondeo es más largo. Datos reales de sus propios
 * módulos en atlas_live/memory/ y atlas_live/mission_control/ -- estados
 * vacíos (sin días sellados, sin procesos activos) son resultados
 * legítimos mientras no haya corrido todavía una sesión completa en vivo,
 * no un error. */
const PANEL_STATUS_POLL_MS = 60000;
let _memoryEngine = null;
let _predictionJournal = null;
let _exitJournalSummaries = [];
let _missionControlProcesses = [];
let _learningStatus = null;

async function fetchMemoryEngine() {
  try {
    const res = await fetch("/api/memory-engine");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _memoryEngine = await res.json();
  } catch (err) {
    console.error("fetchMemoryEngine:", err);
  }
  renderMemoryEngine();
}

async function fetchPredictionJournal() {
  try {
    const res = await fetch("/api/prediction-journal");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _predictionJournal = await res.json();
  } catch (err) {
    console.error("fetchPredictionJournal:", err);
  }
  renderPredictionJournal();
}

async function fetchExitJournal() {
  try {
    const res = await fetch("/api/exit-journal");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _exitJournalSummaries = data.summaries || [];
  } catch (err) {
    console.error("fetchExitJournal:", err);
  }
  renderExitJournal();
}

let _missionControlMarketStateHistory = [];
let _missionControlFailoverHistory = [];

async function fetchMissionControl() {
  try {
    const res = await fetch("/api/mission-control");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _missionControlProcesses = data.processes || [];
    _missionControlMarketStateHistory = data.market_state_history || [];
    _missionControlFailoverHistory = data.provider_failover_history || [];
  } catch (err) {
    console.error("fetchMissionControl:", err);
  }
  renderMissionControl();
  renderAlerts();
}

/* Indicadores permanentes de la barra superior (aprobados el 2026-08-02,
 * antes de diseñar el Learning Engine): 🧠 Aprendizaje y 🎯 Confianza de
 * Atlas. Estructura preparada para conectarse al Learning Comparator
 * cuando exista -- hoy `/api/learning-status` no calcula nada, devuelve
 * el estado real y honesto ("Observando", sin observaciones nuevas)
 * porque el Learning Store todavía no existe. Ver atlas_live/memory/learning_status.py. */
function renderLearningStatus(data) {
  const l = data.learning;
  const c = data.confidence;

  const stateCls = l.state === "Listo para comparar" ? "pill-green"
    : l.state === "Aprendiendo" ? "pill-amber" : "pill-dim";
  const progressStr = l.progress_pct !== null ? `${l.progress_pct.toFixed(0)}%` : "--";
  document.getElementById("topbar-learning-text").innerHTML =
    `<span class="${stateCls}">${l.state}</span> · ${progressStr} · ${l.days_accumulated} d · ${l.new_observations} obs`;
  document.getElementById("topbar-learning").title = l.note;

  const confCls = c.available ? "pill-green" : "pill-dim";
  const confText = c.available ? `${c.value_pct.toFixed(0)}%` : c.state;
  document.getElementById("topbar-confidence-text").innerHTML = `<span class="${confCls}">${confText}</span>`;
  document.getElementById("topbar-confidence").title = c.note;
}

async function fetchLearningStatus() {
  try {
    const res = await fetch("/api/learning-status");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _learningStatus = await res.json();
    renderLearningStatus(_learningStatus);
  } catch (err) {
    console.error("fetchLearningStatus:", err);
  }
}

/* Panel de Evolución (2026-08-07, ver DECISION_LOG.md) -- precisión del
 * modelo, rendimiento financiero y evolución del aprendizaje, cada uno
 * desde datos reales ya existentes (reutiliza performance_panel para 1 y
 * 2). Cualquier dato ausente se muestra "No disponible", nunca un número
 * inventado. */
let _evolution = null;

async function fetchEvolution() {
  try {
    const res = await fetch("/api/evolution");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _evolution = await res.json();
  } catch (err) {
    console.error("fetchEvolution:", err);
    _evolution = null;
  }
  renderEvolution();
}

function renderEvolution() {
  const nd = (v, suf = "") => (v === null || v === undefined) ? '<span class="dim">No disponible</span>' : (v + suf);
  const ndPct = (v) => (v === null || v === undefined) ? '<span class="dim">No disponible</span>' : (fmtNum(v) + "%");
  const precEl = document.getElementById("evolucion-precision");
  const finEl = document.getElementById("evolucion-financiero");
  const aprEl = document.getElementById("evolucion-aprendizaje");
  if (!precEl || !finEl || !aprEl) return;

  if (!_evolution) {
    precEl.innerHTML = finEl.innerHTML = aprEl.innerHTML = `<div class="empty-state">No disponible.</div>`;
    return;
  }

  const p = _evolution.precision_del_modelo;
  precEl.innerHTML = `
    <div class="detail-grid">
      <div class="detail-metric"><div class="detail-metric-label">Aciertos hoy</div><div class="detail-metric-value">${nd(p.aciertos_hoy)}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Aciertos de la semana</div><div class="detail-metric-value">${nd(p.aciertos_semana)}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Aciertos del mes</div><div class="detail-metric-value">${nd(p.aciertos_mes)}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Aciertos históricos</div><div class="detail-metric-value">${nd(p.aciertos_historico)}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Precisión histórica</div><div class="detail-metric-value">${ndPct(p.precision_historica_pct)}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Muestra</div><div class="detail-metric-value">${nd(p.muestra_historica)}</div></div>
    </div>
    <div class="detail-note" style="margin-top:8px">"Acierto" = el símbolo alcanzó una EXPLOSION real (misma definición del Clasificador del proyecto). No es lo mismo que rentabilidad.</div>`;

  const f = _evolution.rendimiento_financiero;
  finEl.innerHTML = `
    <div class="detail-grid">
      <div class="detail-metric"><div class="detail-metric-label">Win Rate (financiero)</div><div class="detail-metric-value">${ndPct(f.win_rate_financiero_pct)}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Profit Factor</div><div class="detail-metric-value">${(f.profit_factor === null || f.profit_factor === undefined) ? '<span class="dim">No disponible</span>' : fmtNum(f.profit_factor, 2)}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Ganancia promedio</div><div class="detail-metric-value">${ndPct(f.ganancia_promedio_pct)}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Pérdida promedio</div><div class="detail-metric-value">${ndPct(f.perdida_promedio_pct)}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Expectativa matemática</div><div class="detail-metric-value">${ndPct(f.expectativa_pct)}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Drawdown <span class="dim" style="font-size:10px">(hipotético)</span></div><div class="detail-metric-value">${(f.drawdown_hipotetico_pct === null || f.drawdown_hipotetico_pct === undefined) ? '<span class="dim">No disponible</span>' : fmtNum(f.drawdown_hipotetico_pct) + " pts"}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Mejor operación</div><div class="detail-metric-value">${ndPct(f.mejor_operacion_global_pct)}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Peor operación</div><div class="detail-metric-value">${ndPct(f.peor_operacion_global_pct)}</div></div>
    </div>
    <div class="detail-note" style="margin-top:8px">El drawdown es una curva de capital <b>hipotética</b> -- no representa dinero real, Atlas no gestiona una cuenta.</div>`;

  const a = _evolution.evolucion_aprendizaje;
  const condiciones = (a.condiciones_evidencia_suficiente === null || a.condiciones_evidencia_suficiente === undefined)
    ? '<span class="dim">No disponible</span>'
    : `${a.condiciones_evidencia_suficiente} / ${a.condiciones_totales_evaluadas}`;
  aprEl.innerHTML = `
    <div class="detail-grid">
      <div class="detail-metric"><div class="detail-metric-label">Trayectorias almacenadas</div><div class="detail-metric-value">${nd(a.trayectorias_almacenadas)}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Muestras analizadas</div><div class="detail-metric-value">${nd(a.muestras_analizadas)}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Casos similares acumulados</div><div class="detail-metric-value">${nd(a.casos_similares_acumulados)}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Condiciones con evidencia</div><div class="detail-metric-value">${condiciones}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Nivel de aprendizaje</div><div class="detail-metric-value">${ndPct(a.nivel_aprendizaje_pct)}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Última actualización</div><div class="detail-metric-value">${nd(a.ultima_actualizacion)}</div></div>
    </div>
    <div class="detail-note" style="margin-top:8px">"Nivel de aprendizaje" = fracción de las condiciones evaluadas que ya alcanzaron confiabilidad estadística (límite de Wilson). Crece a medida que Atlas acumula evidencia real.</div>`;
}

function startPanelStatusPolling() {
  fetchMemoryEngine();
  fetchPredictionJournal();
  fetchExitJournal();
  fetchMissionControl();
  fetchLearningStatus();
  fetchPerformance();
  fetchEvolution();
  setInterval(fetchMemoryEngine, PANEL_STATUS_POLL_MS);
  setInterval(fetchPredictionJournal, PANEL_STATUS_POLL_MS);
  setInterval(fetchExitJournal, PANEL_STATUS_POLL_MS);
  setInterval(fetchMissionControl, PANEL_STATUS_POLL_MS);
  setInterval(fetchLearningStatus, PANEL_STATUS_POLL_MS);
  setInterval(fetchPerformance, PANEL_STATUS_POLL_MS);
  setInterval(fetchEvolution, PANEL_STATUS_POLL_MS);
}

/* 📸 Guardar Estado del Día -- aprobado el 2026-08-02, último elemento
 * antes de la primera validación en mercado real. Solo captura el
 * estado ya calculado que la Cabina tiene en memoria en este instante
 * (las mismas variables que ya alimentan cada panel) y lo descarga como
 * JSON -- no recalcula, no analiza, no interpreta nada. Sirve para
 * comparar manualmente distintos momentos del día más tarde. */
function collectDaySnapshot() {
  // Regla de consenso (2026-08-03): mismo criterio que renderHero()/
  // renderPlanB() -- el snapshot descargado no debe capturar un
  // candidato inelegible como "oportunidad_del_dia"/"plan_b" aunque sea
  // candidates[0]/[1] en el array crudo (pasaría solo si hay menos de 2
  // candidatos elegibles hoy).
  const c0 = _memoryRanking.candidates[0];
  const c1 = _memoryRanking.candidates[1];
  const oportunidad = (c0 && c0.eligible_radar) ? c0 : null;
  const planB = (c1 && c1.eligible_radar) ? c1 : null;
  const explosivasAll = _explosivasReal();
  const momentumAll = _momentumReal();
  const noTocarAll = _noTocarReal();

  return {
    snapshot_taken_at: new Date().toISOString(),
    ranking_generated_at: _memoryRanking.generated_at,
    oportunidad_del_dia: oportunidad,
    plan_b: planB,
    top_explosivas: { total_count: explosivasAll.length, items: explosivasAll.slice(0, DASHBOARD_TOP_N) },
    top_momentum: { total_count: momentumAll.length, items: momentumAll.slice(0, DASHBOARD_TOP_N) },
    no_tocar: { total_count: noTocarAll.length, items: noTocarAll.slice(0, DASHBOARD_TOP_N) },
    memory_engine: _memoryEngine,
    prediction_journal: _predictionJournal,
    exit_journal: { summaries: _exitJournalSummaries },
    mission_control: { processes: _missionControlProcesses },
    aprendizaje: _learningStatus ? _learningStatus.learning : null,
    confianza: _learningStatus ? _learningStatus.confidence : null,
  };
}

function saveDaySnapshot() {
  const snapshot = collectDaySnapshot();
  const stamp = snapshot.snapshot_taken_at.replace(/[:.]/g, "-");
  const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `atlas_estado_${stamp}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function renderHero() {
  const o = _memoryRanking.candidates[0];
  const el = document.getElementById("dashboard-hero");
  // Regla de consenso (2026-08-03): el servidor ya ordena elegibles
  // primero, pero si NO existe ningún candidato elegible hoy,
  // candidates[0] puede ser inelegible -- Hero nunca debe recomendarlo.
  if (!o || !o.eligible_radar) {
    el.innerHTML = `<div class="hero-empty">${_memoryRanking.generated_at === null
      ? "Esperando el primer escaneo del día..."
      : "Sin oportunidad destacada en este momento -- ningún candidato pasa el filtro de Radar Explosivo y el Memory Engine a la vez."}</div>`;
    return;
  }
  el.innerHTML = `
    <div class="hero-label">🎯 Atlas Recomienda</div>
    <div class="hero-top">
      <span class="hero-symbol">${o.symbol}</span>
      <span class="hero-semaforo">${semaforoHtml(o.semaforo)}</span>
      ${badgeHtml(o.market_cap_bucket === "micro" ? "MICROCAP" : (o.market_cap_bucket || "?").toUpperCase(), o.semaforo)}
      <span class="hero-price" id="hero-price-value">${fmtMoney(o.price)}</span>
    </div>
    <div class="hero-price-context">${priceContextLine(o)}</div>
    <div class="hot-fresh fresh-none" id="hot-fresh-hero"><span class="hot-fresh-dot">⚪</span><span class="hot-fresh-label">Refrescando precio en vivo…</span></div>
    <div class="hero-metrics">
      <div><div class="hero-metric-label">Score Radar</div><div class="hero-metric-value">${o.eligible_radar ? fmtNum(o.score) : "N/A"}</div></div>
      <div><div class="hero-metric-label">Probabilidad</div><div class="hero-metric-value">${o.probability_pct !== null ? fmtNum(o.probability_pct) + "%" : '<span class="dim">sin evidencia</span>'}</div></div>
      <div><div class="hero-metric-label">Confianza</div><div class="hero-metric-value">${o.confidence}</div></div>
      ${entryWindowMetricsHtml()}
    </div>
    ${priceBreakdownHtml(o)}
    <div class="hero-explain">${o.explanation}</div>
    <a class="hero-link" data-view="oportunidad">Ver detalle completo de la oportunidad →</a>`;

  // El link interno reutiliza la misma navegación que el menú lateral.
  el.querySelector(".hero-link").addEventListener("click", () => {
    document.querySelector('.nav-item[data-view="oportunidad"]').click();
  });
}

function renderPlanB() {
  const b = _memoryRanking.candidates[1];
  const el = document.getElementById("dashboard-plan-b");
  // Regla de consenso (2026-08-03): mismo criterio que Hero -- si el
  // segundo candidato del ranking no es elegible (porque hay menos de 2
  // candidatos elegibles hoy), Plan B no debe mostrarlo.
  if (!b || !b.eligible_radar) {
    el.innerHTML = `<div class="hero-empty">Sin Plan B disponible hoy.</div>`;
    return;
  }
  el.innerHTML = `
    <div class="plan-b-label">Plan B</div>
    <div class="plan-b-top">
      <span class="plan-b-symbol">${b.symbol}</span>
      ${semaforoHtml(b.semaforo)}
      <span class="plan-b-price" id="planb-price-value">${fmtMoney(b.price)}</span>
    </div>
    <div class="plan-b-price-context">${priceContextLine(b)}</div>
    <div class="hot-fresh fresh-none" id="hot-fresh-planb"><span class="hot-fresh-dot">⚪</span><span class="hot-fresh-label">Refrescando precio en vivo…</span></div>
    <div class="plan-b-metrics">
      <div><div class="plan-b-metric-label">Prob.</div><div class="plan-b-metric-value">${b.probability_pct !== null ? fmtNum(b.probability_pct) + "%" : "--"}</div></div>
      <div><div class="plan-b-metric-label">Confianza</div><div class="plan-b-metric-value">${b.confidence}</div></div>
      <div><div class="plan-b-metric-label">ETA</div><div class="plan-b-metric-value dim" style="font-size:12px">s/d</div></div>
    </div>
    <div class="plan-b-explain">${b.explanation}</div>`;
}

/* "Atlas Opina" -> Resumen Factual (limpieza MOCK 2026-08-07). Atlas NO
 * genera opinión en lenguaje natural; este bloque arma un resumen
 * determinista SOLO con datos reales ya en pantalla (candidatos del Memory
 * Ranking + VIX real del contexto). Si no hay evidencia suficiente (sin
 * escaneo o sin candidatos), muestra un estado honesto, nunca un ejemplo. */
function renderOpina() {
  const el = document.getElementById("dashboard-opina");
  const cands = (_memoryRanking && _memoryRanking.candidates) || [];
  const scanned = _memoryRanking && _memoryRanking.generated_at !== null;
  if (!scanned || cands.length === 0) {
    el.textContent = "Sin evidencia suficiente para emitir un resumen.";
    return;
  }
  const elegibles = cands.filter(c => c.eligible_radar);
  const top = elegibles[0] || null;
  const vix = _lastContext ? _lastContext.vix_price : null;
  const partes = [];
  partes.push(`${cands.length} candidatos analizados en el último escaneo; ${elegibles.length} superan Radar Explosivo y Memory Engine a la vez.`);
  if (top) {
    const wilson = (top.evidence_wilson_lower_bound_pct !== null && top.evidence_wilson_lower_bound_pct !== undefined)
      ? `${top.evidence_wilson_lower_bound_pct.toFixed(1)}%` : "s/d";
    partes.push(`El más fuerte es ${top.symbol} (confianza ${top.confidence}), por la condición "${top.evidence_condition || "s/d"}" con ${top.evidence_sample_size ?? "s/d"} observaciones de respaldo y límite inferior de Wilson ${wilson}.`);
  } else {
    partes.push("Ningún candidato pasa ambos filtros a la vez en este momento -- sin recomendación destacada.");
  }
  if (vix !== null && vix !== undefined) {
    const nivel = vix >= VIX_HIGH ? "alta" : vix <= VIX_LOW ? "baja" : "normal";
    partes.push(`VIX en ${vix.toFixed(1)} (volatilidad ${nivel}).`);
  }
  el.textContent = partes.join(" ");
}

/* "Alertas" -> SOLO eventos reales del motor (limpieza MOCK 2026-08-07):
 * cambios de estado de mercado y failover de proveedor que ya registra
 * Mission Control (timeline real). Cada alerta se justifica con su evento y
 * su hora reales. Si no hay eventos reales, estado honesto -- nunca ejemplos. */
function renderAlerts() {
  const el = document.getElementById("dashboard-alerts");
  const events = [];
  for (const h of _missionControlMarketStateHistory) {
    const prev = h.previous_market_state;
    const cur = h.market_state || "?";
    events.push({
      timestamp: h.timestamp,
      semaforo: "amarillo",
      tag: "Mercado",
      msg: prev ? `Cambio de estado de mercado: ${prev} -> ${cur}` : `Estado de mercado: ${cur}`,
    });
  }
  for (const f of _missionControlFailoverHistory) {
    events.push({
      timestamp: f.timestamp,
      semaforo: f.severity === "warning" ? "rojo" : "amarillo",
      tag: "Proveedor",
      msg: f.message || `Failover de proveedor: ${f.previous_provider_source || "?"} -> ${f.provider_source || "?"}`,
    });
  }
  events.sort((a, b) => (b.timestamp || "").localeCompare(a.timestamp || ""));
  const top = events.slice(0, 12);
  el.innerHTML = top.length
    ? top.map(a => `
        <div class="alert-item">
          <span class="alert-time">${fmtTime(a.timestamp)}</span>
          ${semaforoHtml(a.semaforo)}
          <span class="alert-sym">${a.tag}</span>
          <span class="alert-msg">${a.msg}</span>
        </div>`).join("")
    : `<div class="empty-state">Sin alertas registradas en esta sesión.</div>`;
}

/* Barra de actividad -> estado REAL del último ciclo de escaneo (limpieza
 * MOCK 2026-08-07). Sale de /api/ranking (`scanning`, `generated_at`,
 * `symbols_ok`/`symbols_scanned`, `last_error`), no de frases rotativas
 * inventadas. Estado honesto cuando no hay conexión o aún no corrió el
 * primer escaneo. */
function renderActivity() {
  const el = document.getElementById("activity-text");
  if (!el) return;
  const s = _systemStatus;
  if (s === null) {
    el.textContent = "Sin conexión con el servidor de Atlas.";
    return;
  }
  if (s.scanning) {
    el.textContent = "Escaneando el universo de símbolos...";
  } else if (s.last_error) {
    el.textContent = "El último ciclo de escaneo terminó con un error.";
  } else if (s.generated_at === null) {
    el.textContent = "Esperando el primer escaneo del día...";
  } else {
    const hhmmss = s.generated_at.slice(11, 19);
    el.textContent = `Último ciclo: ${hhmmss} UTC · ${s.symbols_ok}/${s.symbols_scanned} símbolos con dato correcto.`;
  }
}

// Regla de consenso (2026-08-03, aprobada explícitamente por el usuario,
// ver MEMORY_ENGINE.md): Radar Explosivo es el filtro de operabilidad,
// el Memory Engine evalúa la evidencia histórica -- la recomendación
// final SOLO existe cuando ambos están de acuerdo. Un candidato con
// `eligible_radar === false` nunca puede aparecer como Explosiva,
// Momentum, Hero ni Plan B, sin importar su semáforo o Ranking Score
// (el servidor ya lo ordena por debajo de los elegibles -- este filtro
// es la segunda capa de protección, explícita en la Cabina). Si
// cualquiera de los dos sistemas rechaza un símbolo, va a "No tocar"
// con el motivo real de cuál de los dos lo rechazó.
function _explosivasReal() { return _memoryRanking.candidates.filter(c => c.eligible_radar && c.market_cap_bucket === "micro" && c.semaforo === "verde"); }
function _momentumReal() { return _memoryRanking.candidates.filter(c => c.eligible_radar && c.semaforo === "amarillo"); }
function _noTocarReal() { return _memoryRanking.candidates.filter(c => !c.eligible_radar || c.semaforo === "rojo"); }

// El Dashboard muestra como máximo DASHBOARD_TOP_N por columna -- hallazgo
// real al conectar datos en vivo: con el universo completo, "Momentum"
// puede tener más de 100 candidatos (la banda de evidencia más débil
// matchea a casi cualquier símbolo con volumen relativo moderado). Mostrar
// eso sin acotar rompe "Dashboard = solo lo necesario para decidir rápido"
// -- las listas completas siguen disponibles sin recortar en sus propias
// pantallas (Panel 3/4/5, renderMicrocaps/renderMomentum/renderNoTocar).
const DASHBOARD_TOP_N = 5;

function renderTripleColumns() {
  const explosivasAll = _explosivasReal();
  const explosivas = explosivasAll.slice(0, DASHBOARD_TOP_N);
  document.getElementById("explosivas-status").innerHTML = _memoryRanking.candidates.length
    ? `<b style="color:var(--green)">${explosivasAll.length} candidato(s)</b> microcap con evidencia confiable ahora mismo${explosivasAll.length > DASHBOARD_TOP_N ? ` -- mostrando los ${DASHBOARD_TOP_N} más fuertes` : ""}.`
    : `<span class="dim">Esperando datos del escaneo...</span>`;
  document.getElementById("dashboard-explosivas").innerHTML = explosivas.length
    ? explosivas.map(m => `
        <div class="triple-item">
          <span class="sym">${semaforoHtml(m.semaforo)} ${m.symbol}</span>
          <span class="triple-detail">${fmtNum(m.probability_pct)}% · ${m.confidence}</span>
        </div>`).join("")
    : `<div class="empty-state">Ninguna microcap explosiva ahora.</div>`;

  const momentumAll = _momentumReal();
  const momentum = momentumAll.slice(0, DASHBOARD_TOP_N);
  document.getElementById("momentum-status").innerHTML = _memoryRanking.candidates.length
    ? `<b style="color:var(--amber)">${momentumAll.length} candidato(s)</b> con evidencia moderada (lift &lt;10x el baseline)${momentumAll.length > DASHBOARD_TOP_N ? ` -- mostrando los ${DASHBOARD_TOP_N} más fuertes` : ""}.`
    : `<span class="dim">Esperando datos del escaneo...</span>`;
  document.getElementById("dashboard-momentum").innerHTML = momentum.length
    ? momentum.map(m => `
        <div class="triple-item">
          <span class="sym">${semaforoHtml(m.semaforo)} ${m.symbol}</span>
          <span class="triple-detail">${fmtPct(m.change_pct)} · ${fmtNum(m.probability_pct)}%</span>
        </div>`).join("")
    : `<div class="empty-state">Sin candidatos de momentum.</div>`;

  const noTocarAll = _noTocarReal();
  const noTocar = noTocarAll.slice(0, DASHBOARD_TOP_N);
  document.getElementById("no-tocar-status").innerHTML =
    `<b style="color:var(--red)">${noTocarAll.length} símbolo(s)</b> sin evidencia histórica confiable hoy${noTocarAll.length > DASHBOARD_TOP_N ? ` -- mostrando ${DASHBOARD_TOP_N}` : ""}.`;
  document.getElementById("dashboard-no-tocar").innerHTML = noTocar.length
    ? noTocar.map(d => `
        <div class="triple-item">
          <span class="sym">🔴 ${d.symbol}</span>
          <span class="triple-reason">${badgeHtml("Sin evidencia", "rojo")}<br>${d.explanation}</span>
        </div>`).join("")
    : `<div class="empty-state">Nada marcado para evitar hoy.</div>`;
}

/* ---------------- Oportunidad del día (detalle, Q5-Q8) ---------------- */

function renderOportunidad() {
  const o = _memoryRanking.candidates[0];
  const el = document.getElementById("oportunidad-detail");
  // Regla de consenso (2026-08-03): mismo criterio que renderHero() --
  // este es su detalle completo, mismo candidato.
  if (!o || !o.eligible_radar) {
    el.innerHTML = `<div class="empty-state">${_memoryRanking.generated_at === null ? "Esperando el primer escaneo del día..." : "Sin oportunidad destacada en este momento -- ningún candidato pasa el filtro de Radar Explosivo y el Memory Engine a la vez."}</div>`;
    return;
  }
  el.innerHTML = `
    <div class="detail-hero">
      <div class="detail-hero-top">
        <span class="detail-symbol">${o.symbol}</span>
        ${semaforoHtml(o.semaforo)}
        ${badgeHtml(o.market_cap_bucket === "micro" ? "MICROCAP" : (o.market_cap_bucket || "?").toUpperCase(), o.semaforo)}
        <span class="dim">${fmtMoney(o.price)}</span>
      </div>
      ${priceBreakdownHtml(o)}

      <div class="detail-grid">
        <div class="detail-metric">
          <div class="detail-metric-label">5. Score Radar Explosivo</div>
          <div class="detail-metric-value">${o.eligible_radar ? fmtNum(o.score) : "N/A (fuera del gate)"}</div>
        </div>
        <div class="detail-metric">
          <div class="detail-metric-label">8. Confianza de Atlas</div>
          <div class="detail-metric-value">${o.confidence}</div>
        </div>
        ${entryWindowMetricsHtml("detail-metric-label", "detail-metric-value", "detail-metric")}
      </div>

      <div class="detail-explain">
        <b>5. Por qué Atlas la recomienda:</b><br>${o.explanation}
      </div>
      <div class="detail-note">
        6-7. Ventana óptima de entrada -- Motor Predictivo, capacidad <code>entry_window</code>
        (${_entryWindow && _entryWindow.available ? _entryWindow.explanation : "todavía sin evidencia suficiente para este símbolo"}).
      </div>

      <div class="detail-grid" style="margin-top:6px">
        <div class="detail-metric">
          <div class="detail-metric-label">Evidencia histórica</div>
          <div class="detail-metric-value" style="font-size:13px">${o.evidence_condition || '<span class="dim">sin condición confiable</span>'}</div>
        </div>
        <div class="detail-metric">
          <div class="detail-metric-label">Muestra / Wilson (mínimo)</div>
          <div class="detail-metric-value" style="font-size:13px">${o.evidence_sample_size ? `n=${o.evidence_sample_size} · ${fmtNum(o.evidence_wilson_lower_bound_pct)}%` : '<span class="dim">--</span>'}</div>
        </div>
      </div>
    </div>`;
}

/* ---------------- Tablas genéricas ---------------- */

function renderGenericTable(elementId, rows, columns) {
  const el = document.getElementById(elementId);
  if (!rows.length) {
    el.innerHTML = `<tbody><tr><td class="empty-state">Sin candidatos en esta categoría en este momento.</td></tr></tbody>`;
    return;
  }
  const head = "<tr>" + columns.map(c => `<th>${c.label}</th>`).join("") + "</tr>";
  const body = rows.map(r => "<tr>" + columns.map(c => `<td>${c.render(r)}</td>`).join("") + "</tr>").join("");
  el.innerHTML = `<thead>${head}</thead><tbody>${body}</tbody>`;
}

function renderMicrocaps() {
  renderGenericTable("microcaps-table", _explosivasReal(), [
    { label: "Símbolo", render: r => `<span class="sym">${r.symbol}</span>` },
    { label: "Precio", render: r => `${fmtMoney(r.price)}<br>${priceContextLine(r)}` },
    { label: "Cambio", render: r => fmtPct(r.change_pct) },
    { label: "Score", render: r => fmtNum(r.score) },
    { label: "Probabilidad", render: r => fmtNum(r.probability_pct) + "%" },
    { label: "Confianza", render: r => r.confidence },
    { label: "Evidencia", render: r => `<span class="dim">${r.evidence_condition || "--"}</span>` },
    { label: "Semáforo", render: r => semaforoHtml(r.semaforo) },
  ]);
}

function renderMomentum() {
  renderGenericTable("momentum-table", _momentumReal(), [
    { label: "Símbolo", render: r => `<span class="sym">${r.symbol}</span>` },
    { label: "Precio", render: r => `${fmtMoney(r.price)}<br>${priceContextLine(r)}` },
    { label: "Cambio", render: r => fmtPct(r.change_pct) },
    { label: "Probabilidad", render: r => fmtNum(r.probability_pct) + "%" },
    { label: "Confianza", render: r => r.confidence },
    { label: "Evidencia", render: r => `<span class="dim">${r.evidence_condition || "--"}</span>` },
    { label: "Semáforo", render: r => semaforoHtml(r.semaforo) },
  ]);
}

/* Panel ETF -> estado honesto (limpieza MOCK 2026-08-07). Atlas todavía no
 * publica un feed dedicado de ETF apalancados por categoría; en vez de
 * mostrar filas simuladas, se declara explícitamente que no hay fuente
 * conectada. Regla permanente: preferir un panel vacío antes que un dato
 * inventado. Cuando exista el backend real (leveraged_etf_families +
 * precios en vivo) se conecta aquí. */
function renderEtf() {
  const el = document.getElementById("etf-table");
  if (!el) return;
  el.innerHTML = `<div class="empty-state">Panel ETF sin fuente de datos conectada todavía -- Atlas no publica un feed dedicado de ETF apalancados por ahora. No se muestran ejemplos.</div>`;
}

// Motivo mostrado en "No tocar": si Radar Explosivo lo rechazó, ESE es el
// motivo real (aunque el Memory Engine tuviera semáforo verde/amarillo --
// regla de consenso, 2026-08-03) -- nunca se muestran los dos mezclados
// como si fueran lo mismo.
function _noTocarMotivo(r) {
  if (!r.eligible_radar) {
    return `<span class="no-tocar-radar-reason">🚫 Radar Explosivo: ${r.radar_excluded_reason || "no elegible"}</span>`;
  }
  return r.explanation;
}

function renderNoTocar() {
  renderGenericTable("no-tocar-table", _noTocarReal(), [
    { label: "Símbolo", render: r => `<span class="sym">${r.symbol}</span>` },
    { label: "Precio", render: r => `${fmtMoney(r.price)}<br>${priceContextLine(r)}` },
    { label: "Cambio", render: r => fmtPct(r.change_pct) },
    { label: "Motivo", render: r => _noTocarMotivo(r) },
    { label: "Semáforo", render: r => semaforoHtml(r.semaforo) },
  ]);
}

function renderRadarCompleto() {
  renderGenericTable("radar-completo-table", _memoryRanking.candidates, [
    { label: "Símbolo", render: r => `<span class="sym">${r.symbol}</span>` },
    { label: "Precio", render: r => `${fmtMoney(r.price)}<br>${priceContextLine(r)}` },
    { label: "Cambio", render: r => fmtPct(r.change_pct) },
    { label: "Score Radar", render: r => fmtNum(r.score) },
    { label: "Elegible", render: r => r.eligible_radar
        ? "Sí"
        : `<span class="no-tocar-radar-reason">No -- ${r.radar_excluded_reason || "no elegible"}</span>` },
    { label: "Probabilidad ME", render: r => r.probability_pct !== null ? fmtNum(r.probability_pct) + "%" : '<span class="dim">--</span>' },
    { label: "Semáforo", render: r => semaforoHtml(r.semaforo) },
  ]);
}

function renderExitJournal() {
  renderGenericTable("exit-journal-table", _exitJournalSummaries, [
    { label: "Símbolo", render: r => `<span class="sym">${r.symbol}</span>` },
    { label: "Fecha", render: r => r.date },
    { label: "Detección", render: r => fmtTime(r.detected_at) },
    { label: "Entrada (sellado)", render: r => fmtTime(r.entry_at) },
    { label: "Máximo", render: r => r.peak_at ? `${fmtTime(r.peak_at)} (${fmtPct(r.peak_return_pct)})` : '<span class="dim">--</span>' },
    { label: "Rendimiento final", render: r => r.final_return_pct !== null ? fmtPct(r.final_return_pct) : '<span class="dim">--</span>' },
    { label: "Muestras", render: r => r.sample_count },
  ]);
}

/* Panel de Desempeño (2026-08-07, ver DECISION_LOG.md) -- Nivel 1
 * (Oportunidad Oficial del Día, Prediction Journal) y Nivel 2
 * (Rendimiento histórico, Exit Journal) nunca se mezclan en el mismo
 * número: "acierto del modelo" (¿pasó lo que Atlas predijo?) y
 * "rentabilidad" (¿fue rentable?) son dos conceptos separados, a
 * pedido explícito del usuario. */
let _performance = null;

async function fetchPerformance() {
  try {
    const res = await fetch("/api/performance");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _performance = await res.json();
  } catch (err) {
    console.error("fetchPerformance:", err);
    _performance = null;
  }
  renderPerformance();
}

function renderPerformance() {
  const dia = _performance ? _performance.oportunidad_del_dia : null;
  const g = _performance ? _performance.rendimiento_global : null;

  const diaEl = document.getElementById("desempeno-dia");
  if (!dia || !dia.available) {
    diaEl.innerHTML = `<div class="empty-state">Sin ranking sellado ese día -- nada que mostrar todavía.</div>`;
  } else {
    diaEl.innerHTML = `
      <div class="detail-grid">
        <div class="detail-metric"><div class="detail-metric-label">Símbolo</div><div class="detail-metric-value">${dia.symbol}</div></div>
        <div class="detail-metric"><div class="detail-metric-label">Resultado</div><div class="detail-metric-value">${dia.graded ? (dia.resultado || '<span class="dim">--</span>') : '<span class="dim">Sin calificar todavía</span>'}</div></div>
        <div class="detail-metric"><div class="detail-metric-label">Rentabilidad</div><div class="detail-metric-value">${fmtPct(dia.rentabilidad_pct)}</div></div>
        <div class="detail-metric"><div class="detail-metric-label">Tiempo hasta el objetivo</div><div class="detail-metric-value">${dia.tiempo_hasta_objetivo_min !== null ? dia.tiempo_hasta_objetivo_min.toFixed(0) + " min" : '<span class="dim">--</span>'}</div></div>
      </div>
      <div class="detail-explain" style="margin-top:12px"><b>Motivo:</b> ${dia.motivo || '<span class="dim">--</span>'}</div>`;
  }

  if (!g) {
    document.getElementById("desempeno-resumen").innerHTML = "";
    document.getElementById("desempeno-precision").innerHTML = "";
    document.getElementById("desempeno-financiero").innerHTML = "";
    document.getElementById("desempeno-evolucion").innerHTML = "";
    document.getElementById("desempeno-score").innerHTML = "";
    return;
  }

  document.getElementById("desempeno-resumen").innerHTML = `
    <div class="detail-metric"><div class="detail-metric-label">Recomendaciones hoy</div><div class="detail-metric-value">${g.recomendaciones_emitidas_hoy}</div></div>
    <div class="detail-metric"><div class="detail-metric-label">Abiertas</div><div class="detail-metric-value">${g.operaciones_abiertas_hoy}</div></div>
    <div class="detail-metric"><div class="detail-metric-label">Cerradas</div><div class="detail-metric-value">${g.operaciones_cerradas_hoy}</div></div>
    <div class="detail-metric"><div class="detail-metric-label">Win Rate diario</div><div class="detail-metric-value">${g.win_rate_periodos.diario_pct !== null ? fmtNum(g.win_rate_periodos.diario_pct) + "%" : '<span class="dim">--</span>'}</div></div>
    <div class="detail-metric"><div class="detail-metric-label">Win Rate semanal</div><div class="detail-metric-value">${g.win_rate_periodos.semanal_pct !== null ? fmtNum(g.win_rate_periodos.semanal_pct) + "%" : '<span class="dim">--</span>'}</div></div>
    <div class="detail-metric"><div class="detail-metric-label">Win Rate mensual</div><div class="detail-metric-value">${g.win_rate_periodos.mensual_pct !== null ? fmtNum(g.win_rate_periodos.mensual_pct) + "%" : '<span class="dim">--</span>'}</div></div>`;

  const p = g.precision_del_modelo;
  const dva = p.detectadas_vs_acertadas;
  document.getElementById("desempeno-precision").innerHTML = `
    <div class="detail-grid">
      <div class="detail-metric"><div class="detail-metric-label">Tasa de acierto</div><div class="detail-metric-value">${p.tasa_acierto_pct !== null ? fmtNum(p.tasa_acierto_pct) + "%" : '<span class="dim">--</span>'}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Muestra (histórico)</div><div class="detail-metric-value">${p.muestra}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Detectadas hoy</div><div class="detail-metric-value">${dva.detectadas}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Acertadas hoy</div><div class="detail-metric-value">${dva.acertadas}</div></div>
    </div>`;

  const f = g.rendimiento_financiero;
  document.getElementById("desempeno-financiero").innerHTML = `
    <div class="detail-grid">
      <div class="detail-metric"><div class="detail-metric-label">Win Rate (financiero)</div><div class="detail-metric-value">${f.win_rate_financiero_pct !== null ? fmtNum(f.win_rate_financiero_pct) + "%" : '<span class="dim">--</span>'}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Profit Factor</div><div class="detail-metric-value">${f.profit_factor !== null ? fmtNum(f.profit_factor, 2) : '<span class="dim">--</span>'}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Ganancia promedio</div><div class="detail-metric-value">${fmtPct(f.ganancia_promedio_pct)}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Pérdida promedio</div><div class="detail-metric-value">${fmtPct(f.perdida_promedio_pct)}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Expectativa matemática</div><div class="detail-metric-value">${fmtPct(f.expectativa_pct)}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Máximo drawdown <span class="dim" style="font-size:10px">(hipotético)</span></div><div class="detail-metric-value">${f.drawdown_hipotetico_pct !== null ? fmtNum(f.drawdown_hipotetico_pct) + " pts" : '<span class="dim">--</span>'}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Mejor operación hoy</div><div class="detail-metric-value">${fmtPct(f.mejor_operacion_hoy_pct)}</div></div>
      <div class="detail-metric"><div class="detail-metric-label">Peor operación hoy</div><div class="detail-metric-value">${fmtPct(f.peor_operacion_hoy_pct)}</div></div>
    </div>
    <div class="detail-note" style="margin-top:10px">El drawdown es una curva de capital <b>hipotética</b> (una unidad fija por operación) -- no representa dinero real, Atlas no gestiona una cuenta.</div>`;

  const evo = g.evolucion.slice(-30);
  document.getElementById("desempeno-evolucion").innerHTML = `
    <h3>Evolución del Win Rate (últimos ${evo.length} días con operaciones)</h3>
    ${evo.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Fecha</th><th>Win Rate</th><th>Operaciones</th></tr></thead><tbody>
      ${evo.map(e => `<tr><td>${e.fecha}</td><td>${e.win_rate_pct !== null ? fmtNum(e.win_rate_pct) + "%" : '<span class="dim">--</span>'}</td><td>${e.n}</td></tr>`).join("")}
    </tbody></table></div>` : `<div class="empty-state">Sin operaciones cerradas todavía.</div>`}`;

  const score = g.atlas_score;
  document.getElementById("desempeno-score").innerHTML = `
    <h3>Atlas Score</h3>
    ${score.score !== null ? `
      <div class="hero-metric-value" style="font-size:36px">${score.score} / 100</div>
      <div class="detail-grid" style="margin-top:10px">
        ${Object.entries(score.componentes).map(([k, v]) => `
          <div class="detail-metric">
            <div class="detail-metric-label">${k.replace(/_/g, " ")} (peso ${(score.pesos_usados[k] * 100).toFixed(0)}%)</div>
            <div class="detail-metric-value">${v !== null ? v.toFixed(1) : '<span class="dim">sin dato</span>'}</div>
          </div>`).join("")}
      </div>
      <div class="detail-note" style="margin-top:10px">Combinación configurable (<code>performance_config.json</code>) de los componentes de arriba -- nunca un juicio inventado. Cambiar los pesos queda registrado en <code>DECISION_LOG.md</code>.</div>
    ` : `<div class="empty-state">Sin operaciones cerradas todavía -- el Atlas Score necesita evidencia real, no se fabrica.</div>`}`;
}

function renderMissionControl() {
  // Historial de cambios de marketState (punto 5, 2026-08-02) -- hora
  // exacta de cada transición detectada, para diagnóstico y para el
  // futuro Learning Engine. Reutiliza el Timeline ya existente, no un
  // mecanismo de registro nuevo.
  const historyRows = _missionControlMarketStateHistory.map(h => `
    <tr>
      <td>${fmtTime(h.timestamp)} ET</td>
      <td>${marketStateBadgeHtml(h.market_state)}</td>
      <td>${h.previous_market_state ? marketStateBadgeHtml(h.previous_market_state) : '<span class="dim">-- (primer estado detectado)</span>'}</td>
    </tr>`).join("");
  document.getElementById("mission-control-market-state").innerHTML = `
    <h3>Historial de sesión de mercado detectada</h3>
    <div class="table-wrap">
      <table class="data-table">
        <thead><tr><th>Hora del cambio</th><th>Estado nuevo</th><th>Estado anterior</th></tr></thead>
        <tbody>${historyRows || '<tr><td class="empty-state" colspan="3">Sin cambios de sesión detectados todavía.</td></tr>'}</tbody>
      </table>
    </div>`;

  renderGenericTable("mission-control-table", _missionControlProcesses, [
    { label: "Proceso", render: r => `${r.process_type} -- ${r.label}` },
    { label: "Estado", render: r => r.state },
    { label: "Último latido", render: r => fmtTime(r.last_heartbeat) },
    { label: "Progreso", render: r => {
        const p = r.progress || {};
        return p.total ? `${p.done ?? 0} / ${p.total} ${p.unit || ""}` : '<span class="dim">--</span>';
      } },
    { label: "CPU", render: r => r.cpu_percent !== undefined ? `${r.cpu_percent}%` : '<span class="dim">--</span>' },
    { label: "Memoria", render: r => r.memory_mb !== undefined ? `${r.memory_mb} MB` : '<span class="dim">--</span>' },
  ]);
}

/* ---------------- Bloques a medida ---------------- */

function renderMemoryEngine() {
  const m = _memoryEngine;
  if (!m) {
    document.getElementById("memory-engine-body").innerHTML = `<div class="detail-note">Cargando estado del Memory Engine...</div>`;
    return;
  }
  const rows = m.reliable_conditions.map(c => `
    <tr>
      <td class="dim">${c.label}</td>
      <td>${fmtNum(c.win_rate_pct)}%</td>
      <td>${fmtNum(c.wilson_lower_bound_pct)}%</td>
      <td>${c.sample_size}</td>
      <td class="num-pos">${c.lift !== null ? fmtNum(c.lift) + "x" : '<span class="dim">--</span>'}</td>
    </tr>`).join("");

  document.getElementById("memory-engine-body").innerHTML = `
    <div class="info-block">
      <h3>Estado de la evidencia -- Atlas Alpha 1.0</h3>
      <div class="kv-row"><span class="k">Observaciones acumuladas</span><span class="v">${(m.observation_count ?? 0).toLocaleString()}</span></div>
      <div class="kv-row"><span class="k">Días de evidencia histórica</span><span class="v">${m.days_backed ?? "--"}</span></div>
      <div class="kv-row"><span class="k">Tasa base poblacional (EXPLOSION)</span><span class="v">${fmtNum(m.baseline_win_rate_pct)}%</span></div>
      <div class="kv-row"><span class="k">Última recalibración</span><span class="v">${m.last_recalibrated_on || "--"}</span></div>
    </div>
    <div class="table-wrap">
      <table class="data-table">
        <thead><tr><th>Condición confiable</th><th>Win rate</th><th>Wilson (mínimo)</th><th>Muestra</th><th>Lift</th></tr></thead>
        <tbody>${rows || '<tr><td class="empty-state" colspan="5">Sin condiciones confiables en esta evidencia.</td></tr>'}</tbody>
      </table>
    </div>`;
}

function renderPredictionJournal() {
  const pj = _predictionJournal;
  if (!pj) {
    document.getElementById("prediction-journal-body").innerHTML = `<div class="detail-note">Cargando Prediction Journal...</div>`;
    return;
  }
  const rows = pj.recent_days.map(d => `
    <tr>
      <td>${d.date}</td>
      <td class="sym">${d.top_symbol}</td>
      <td>${fmtNum(d.predicted_probability_pct)}%</td>
      <td>${d.result_category || '<span class="dim">sin calificar</span>'}</td>
      <td>${d.result_pct !== null ? fmtPct(d.result_pct) : '<span class="dim">--</span>'}</td>
      <td>${d.anticipation_minutes !== null ? Math.round(d.anticipation_minutes) + " min" : '<span class="dim">--</span>'}</td>
    </tr>`).join("");

  const sellado = pj.sealed_today
    ? `<div class="kv-row"><span class="k">Sellado a las</span><span class="v">${fmtTime(pj.sealed_today.sealed_at)}</span></div>
       <div class="kv-row"><span class="k">Candidatos sellados</span><span class="v">${pj.sealed_today.candidate_count}</span></div>
       <div class="kv-row"><span class="k">Top del día</span><span class="v">${pj.sealed_today.top_symbol || "--"}</span></div>`
    : `<div class="detail-note">Todavía no se selló el ranking de hoy (${pj.date}) -- el sellado ocurre en la ventana 09:25-09:30 ET del premarket.</div>`;

  document.getElementById("prediction-journal-body").innerHTML = `
    <div class="info-block">
      <h3>Ranking oficial de hoy</h3>
      ${sellado}
    </div>
    <div class="table-wrap">
      <table class="data-table">
        <thead><tr><th>Fecha</th><th>Top símbolo</th><th>Prob. predicha</th><th>Resultado real</th><th>Rendimiento</th><th>Anticipación</th></tr></thead>
        <tbody>${rows || '<tr><td class="empty-state" colspan="6">Todavía no hay días sellados.</td></tr>'}</tbody>
      </table>
    </div>`;
}

/* "Configuración" -> valores REALES de /api/config (limpieza MOCK
 * 2026-08-07): los lee de los propios módulos del backend (scan_worker,
 * classifier, explosive_config, market_hours), no de constantes
 * hardcodeadas en la interfaz. Estado honesto si el endpoint falla. */
async function renderConfig() {
  const el = document.getElementById("config-body");
  let c;
  try {
    const res = await fetch("/api/config");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    c = await res.json();
  } catch (err) {
    console.error("renderConfig:", err);
    el.innerHTML = `<div class="empty-state">Configuración no disponible en este momento -- no se pudo leer del servidor.</div>`;
    return;
  }
  const mh = c.market_hours || {};
  const microMM = c.microcap_ceiling_usd ? `US$${(c.microcap_ceiling_usd / 1e6).toFixed(0)}M` : "--";
  const dollarVol = c.min_dollar_volume_usd ? `US$${(c.min_dollar_volume_usd / 1e6).toFixed(1)}M` : "--";
  el.innerHTML = `
    <div class="info-block">
      <h3>Parámetros vigentes (valores reales del backend, solo lectura)</h3>
      <div class="kv-row"><span class="k">Intervalo de escaneo</span><span class="v">${(c.refresh_interval_seconds / 60).toFixed(0)} min</span></div>
      <div class="kv-row"><span class="k">Ventana de sellado</span><span class="v">${c.seal_window}</span></div>
      <div class="kv-row"><span class="k">Horario de mercado</span><span class="v">Premarket ${mh.premarket} · Regular ${mh.regular} · Afterhours ${mh.afterhours} (${mh.timezone})</span></div>
      <div class="kv-row"><span class="k">Umbral EXPLOSION</span><span class="v">&ge; ${c.explosion_threshold_pct}%</span></div>
      <div class="kv-row"><span class="k">Techo FALSE_BREAKOUT</span><span class="v">&lt; ${c.false_breakout_ceiling_pct}%</span></div>
      <div class="kv-row"><span class="k">Umbral LOSER</span><span class="v">&le; ${c.loser_threshold_pct}%</span></div>
      <div class="kv-row"><span class="k">Techo microcap</span><span class="v">${microMM}</span></div>
      <div class="kv-row"><span class="k">Precio mínimo</span><span class="v">US$${c.min_price_usd}</span></div>
      <div class="kv-row"><span class="k">Volumen $ mínimo (liquidez)</span><span class="v">${dollarVol}</span></div>
      <div class="kv-row"><span class="k">Top N publicado</span><span class="v">${c.top_n}</span></div>
    </div>
    <div class="detail-note">Estos valores se leen en vivo de los módulos del backend (scan_worker, classifier, explosive_config, market_hours) -- no están hardcodeados en la interfaz.</div>`;
}

/* ---------------- Arranque ---------------- */

function init() {
  setupNav();
  renderGlobalStatus();      // barra de actividad + estado real (renderActivity)
  renderActivity();          // estado honesto inmediato hasta el primer fetch
  renderMarketQuality();
  startMemoryRankingPolling(); // Paneles 2-6: hero, Plan B, Explosivas, Momentum, No tocar, Radar Completo
  startHotChannel(); // Canal rápido (Plan A + Plan B): precio <=3s + indicadores de frescura
  renderOpina();             // Resumen Factual (datos reales; honesto si no hay)
  renderAlerts();            // solo eventos reales de Mission Control
  renderWhyNot();            // descartes reales de /api/explosive-diagnostics
  fetchExplosiveDiagnostics();
  setInterval(fetchExplosiveDiagnostics, MEMORY_POLL_MS);
  renderEtf();               // estado honesto -- sin fuente simulada
  startPanelStatusPolling(); // Paneles 9-12: Memory Engine, Prediction Journal, Exit Journal, Mission Control
  renderConfig();            // valores reales de /api/config
  document.getElementById("btn-save-snapshot").addEventListener("click", saveDaySnapshot);
}

document.addEventListener("DOMContentLoaded", init);
