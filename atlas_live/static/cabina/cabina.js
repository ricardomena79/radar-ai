/* Cabina del Piloto -- navegación y renderizado. Sin framework, mismo
 * criterio que el resto de atlas_live/static/.
 *
 * Integración en curso, panel por panel (ver conversación de aprobación).
 * Conectado a datos reales: Panel 1 (barra superior -- Estado de Atlas y
 * Última actualización, vía /api/ranking). Todo lo demás sigue en MOCK
 * (mock_data.js) hasta que se conecte en su propio paso. */

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

function fmtMoney(value) {
  if (value === null || value === undefined) return '<span class="dim">--</span>';
  return "$" + value.toFixed(2);
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
        ${row("Premarket", c.price_premarket, c.price_type === "premarket")}
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
    renderMarketQuality();
  } catch (err) {
    document.getElementById("topbar-status-dot").className = "dot dot-red";
    document.getElementById("topbar-status-text").textContent = "Sin conexión con el servidor";
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

function renderWhyNot() {
  const items = MOCK.whyNot;
  document.getElementById("dashboard-whynot").innerHTML = items.length
    ? items.map(w => `
        <div class="whynot-item">
          <div class="whynot-q">¿Por qué no <span class="sym">${w.symbol}</span>? -- ${w.apparentReason}</div>
          <div class="whynot-a">${w.excludedBecause}</div>
        </div>`).join("")
    : `<div class="empty-state">Nada que aclarar hoy -- ningún candidato llamativo quedó fuera sin explicación.</div>`;
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
  if (document.querySelector('.nav-item[data-view="oportunidad"]').classList.contains("active")) {
    renderOportunidad();
  }
}

function startMemoryRankingPolling() {
  fetchMemoryRanking();
  setInterval(fetchMemoryRanking, MEMORY_POLL_MS);
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

async function fetchMissionControl() {
  try {
    const res = await fetch("/api/mission-control");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _missionControlProcesses = data.processes || [];
    _missionControlMarketStateHistory = data.market_state_history || [];
  } catch (err) {
    console.error("fetchMissionControl:", err);
  }
  renderMissionControl();
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

function startPanelStatusPolling() {
  fetchMemoryEngine();
  fetchPredictionJournal();
  fetchExitJournal();
  fetchMissionControl();
  fetchLearningStatus();
  setInterval(fetchMemoryEngine, PANEL_STATUS_POLL_MS);
  setInterval(fetchPredictionJournal, PANEL_STATUS_POLL_MS);
  setInterval(fetchExitJournal, PANEL_STATUS_POLL_MS);
  setInterval(fetchMissionControl, PANEL_STATUS_POLL_MS);
  setInterval(fetchLearningStatus, PANEL_STATUS_POLL_MS);
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
      <span class="hero-price">${fmtMoney(o.price)}</span>
    </div>
    <div class="hero-price-context">${priceContextLine(o)}</div>
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
      <span class="plan-b-price">${fmtMoney(b.price)}</span>
    </div>
    <div class="plan-b-price-context">${priceContextLine(b)}</div>
    <div class="plan-b-metrics">
      <div><div class="plan-b-metric-label">Prob.</div><div class="plan-b-metric-value">${b.probability_pct !== null ? fmtNum(b.probability_pct) + "%" : "--"}</div></div>
      <div><div class="plan-b-metric-label">Confianza</div><div class="plan-b-metric-value">${b.confidence}</div></div>
      <div><div class="plan-b-metric-label">ETA</div><div class="plan-b-metric-value dim" style="font-size:12px">s/d</div></div>
    </div>
    <div class="plan-b-explain">${b.explanation}</div>`;
}

function renderOpina() {
  document.getElementById("dashboard-opina").textContent = MOCK.atlasOpina;
}

function renderAlerts() {
  const alerts = MOCK.alerts;
  document.getElementById("dashboard-alerts").innerHTML = alerts.length
    ? alerts.map(a => `
        <div class="alert-item">
          <span class="alert-time">${a.time}</span>
          ${semaforoHtml(a.semaforo)}
          <span class="alert-sym">${a.symbol}</span>
          <span class="alert-msg">${a.message}</span>
        </div>`).join("")
    : `<div class="empty-state">Sin alertas todavía en esta sesión.</div>`;
}

let _activityIndex = 0;
function startActivityFeed() {
  const feed = MOCK.activityFeed;
  const el = document.getElementById("activity-text");
  const tick = () => {
    el.textContent = feed[_activityIndex % feed.length];
    _activityIndex += 1;
  };
  tick();
  setInterval(tick, 4000);
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

function renderEtf() {
  renderGenericTable("etf-table", MOCK.etfs, [
    { label: "Símbolo", render: r => `<span class="sym">${r.symbol}</span>` },
    // Panel ETF sigue en MOCK (fuera del orden de integración ya acordado)
    // -- no tiene fuente/sesión/hora reales, así que se etiqueta como tal
    // en vez de inventar un contexto que no existe (2026-08-02, punto 6).
    { label: "Precio", render: r => `${fmtMoney(r.price)}<br><span class="dim">Dato simulado -- sin fuente real todavía</span>` },
    { label: "Cambio", render: r => fmtPct(r.changePct) },
    { label: "Categoría", render: r => r.category },
    { label: "Probabilidad", render: r => r.probabilityPct !== null ? fmtNum(r.probabilityPct) + "%" : '<span class="dim">sin evidencia</span>' },
    { label: "Semáforo", render: r => semaforoHtml(r.semaforo) },
  ]);
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

function renderConfig() {
  const c = MOCK.config;
  document.getElementById("config-body").innerHTML = `
    <div class="info-block">
      <h3>Parámetros vigentes (solo lectura en esta etapa)</h3>
      <div class="kv-row"><span class="k">Intervalo de escaneo</span><span class="v">${c.refreshIntervalSeconds / 60} min</span></div>
      <div class="kv-row"><span class="k">Ventana de sellado</span><span class="v">${c.sealWindow}</span></div>
      <div class="kv-row"><span class="k">Horario de mercado</span><span class="v">${c.marketHours}</span></div>
      <div class="kv-row"><span class="k">Umbral EXPLOSION</span><span class="v">&ge; ${c.explosionThresholdPct}%</span></div>
      <div class="kv-row"><span class="k">Techo FALSE_BREAKOUT</span><span class="v">&lt; ${c.falseBreakoutCeilingPct}%</span></div>
      <div class="kv-row"><span class="k">Techo microcap</span><span class="v">${c.microCapCeiling}</span></div>
    </div>
    <div class="detail-note">Edición de estos parámetros: pendiente de definir cuando se conecte esta pantalla al backend real.</div>`;
}

/* ---------------- Arranque ---------------- */

function init() {
  setupNav();
  renderGlobalStatus();
  startActivityFeed();
  renderMarketQuality();
  startMemoryRankingPolling(); // Paneles 2-6: hero, Plan B, Explosivas, Momentum, No tocar, Radar Completo
  renderOpina();
  renderAlerts();
  renderWhyNot();
  renderEtf(); // sigue en MOCK -- no estaba en el orden de integración pedido
  startPanelStatusPolling(); // Paneles 9-12: Memory Engine, Prediction Journal, Exit Journal, Mission Control
  renderConfig();
  document.getElementById("btn-save-snapshot").addEventListener("click", saveDaySnapshot);
}

document.addEventListener("DOMContentLoaded", init);
