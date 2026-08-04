/*
 * Atlas Live - frontend (cockpit de trading).
 *
 * Este archivo NO calcula nada. Solo pide datos ya calculados por Atlas
 * Core (vía /api/ranking y /api/symbol/<ticker>) y los muestra en lenguaje
 * natural. Toda la inteligencia (scores, decisión, confianza, riesgo)
 * vive en el backend, que a su vez es una capa delgada sobre
 * atlas_live/scan_worker.py, que a su vez solo llama a Atlas Core.
 *
 * Filosofía de esta pantalla: una sola vista, sin modales ni pestañas.
 * El ranking siempre está visible a un costado; al hacer clic en cualquier
 * fila, el gráfico y el panel de información cambian en el momento. Pensada
 * para recorrer decenas de oportunidades con el mínimo de clics posible
 * durante premarket / la primera hora de mercado.
 */

const RANKING_POLL_MS = 20000;

const state = {
  ranking: [],
  context: null,
  selectedSymbol: null,
  tvWidget: null,
};

// --- Traducciones de lenguaje técnico a lenguaje natural (solo texto,
// ningún dato se recalcula aquí). ---

const COMPONENT_LABELS = {
  momentum: "Fuerza de la tendencia",
  relative_volume: "Interés inusual (volumen)",
  ema_trend: "Dirección de la tendencia",
  vwap_distance: "Posición frente al precio promedio del día",
  atr: "Volatilidad",
  liquidity: "Facilidad para comprar/vender",
  market_cap: "Tamaño de la empresa",
  gap_pct: "Salto de apertura",
  rsi: "Presión de compra/venta",
  change_percent: "Cambio del día",
};

const CONDITION_LABEL_TRANSLATIONS = {
  "Momentum fuerte": "Fuerza de la tendencia",
  "RVOL > umbral": "Interés inusual (volumen alto)",
  "Confirmación sobre VWAP": "Precio sostenido sobre el promedio del día",
  "Gap favorable": "Salto de apertura favorable",
  "Alta liquidez": "Fácil de comprar y vender",
  "Buen Atlas Score": "Buen puntaje general",
  "Money Flow positivo": "Entrada de dinero al sector",
  "Ruptura del máximo intradía": "Rompiendo el máximo del día",
};

const TEXT_REPLACEMENTS = [
  [/Momentum Score/g, "puntaje de fuerza"],
  [/Atlas Score/g, "puntaje general"],
  [/RVOL/g, "volumen relativo"],
  [/VWAP intradía/g, "precio promedio del día"],
  [/VWAP/g, "precio promedio del día"],
  [/Money Flow/g, "entrada de dinero"],
];

function translateLabel(text) {
  return CONDITION_LABEL_TRANSLATIONS[text] || text;
}

function translateText(text) {
  let out = text;
  for (const [pattern, replacement] of TEXT_REPLACEMENTS) {
    out = out.replace(pattern, replacement);
  }
  return out;
}

function componentLabel(name) {
  return COMPONENT_LABELS[name] || name;
}

// --- Formato ---

function fmt(value, decimals = 2) {
  if (value === null || value === undefined) return "—";
  return Number(value).toFixed(decimals);
}

function fmtPct(value, decimals = 2) {
  if (value === null || value === undefined) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${Number(value).toFixed(decimals)}%`;
}

function fmtX(value, decimals = 2) {
  if (value === null || value === undefined) return "—";
  return `${Number(value).toFixed(decimals)}x`;
}

function pctClass(value) {
  if (value === null || value === undefined) return "";
  return value >= 0 ? "up" : "down";
}

function decisionBadge(displayDecision) {
  if (!displayDecision) return "";
  return `<span class="decision-badge decision-${displayDecision.code}">${displayDecision.emoji} ${displayDecision.label}</span>`;
}

function riskBadge(riskLevel) {
  if (!riskLevel) return "";
  return `<span class="risk-badge risk-${riskLevel}">Riesgo ${riskLevel}</span>`;
}

// --- Franja de contexto de mercado (en el header) ---

function renderContextStrip(context) {
  const bar = document.getElementById("context-bar");
  if (!context) {
    bar.innerHTML = `<span class="m-item">Estado del mercado: esperando primer análisis…</span>`;
    return;
  }
  const items = [
    ["SPY", fmt(context.spy_price), context.spy_change_percent],
    ["QQQ", fmt(context.qqq_price), context.qqq_change_percent],
    ["VIX", fmt(context.vix_price), context.vix_change_percent],
    ["BTC", fmt(context.btc_price, 0), context.btc_change_percent],
  ];
  let html = items.map(([label, value, chg]) => `
    <span class="m-item">${label} <strong>${value}</strong> <span class="${pctClass(chg)}">${fmtPct(chg)}</span></span>
  `).join("");
  html += `<span class="m-item">Sector líder: <strong>${context.leading_sector || "—"}</strong></span>`;
  bar.innerHTML = html;
}

// --- Ranking rail (siempre visible) ---

function renderRankingRail() {
  const list = document.getElementById("ranking-list");
  const ranking = state.ranking;
  if (!ranking || ranking.length === 0) {
    list.innerHTML = `<div class="empty-note">Sin resultados todavía. El primer análisis puede tardar unos minutos.</div>`;
    return;
  }
  list.innerHTML = ranking.map(row => `
    <div class="rail-card ${row.symbol === state.selectedSymbol ? "active" : ""}" data-symbol="${row.symbol}">
      <div class="rail-card-top">
        <span class="rail-symbol">${row.is_top_pick ? "⭐ " : ""}${row.symbol}</span>
        <span class="rail-confidence">${fmt(row.confidence, 0)}%</span>
      </div>
      <div class="rail-card-mid">
        ${decisionBadge(row.display_decision)}
        ${riskBadge(row.risk_level)}
      </div>
      ${row.reason ? `<div class="rail-reason">${translateLabel(row.reason)}</div>` : ""}
    </div>
  `).join("");

  list.querySelectorAll(".rail-card").forEach(card => {
    card.addEventListener("click", () => selectSymbol(card.dataset.symbol));
  });
}

// --- Selección de símbolo: actualiza gráfico + panel de info en el momento ---

function selectSymbol(symbol) {
  if (!symbol || symbol === state.selectedSymbol) return;
  state.selectedSymbol = symbol;
  renderRankingRail();
  loadDetail(symbol);
}

let tvScriptPromise = null;

function loadTradingViewScript() {
  if (window.TradingView) return Promise.resolve();
  if (tvScriptPromise) return tvScriptPromise;
  tvScriptPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://s3.tradingview.com/tv.js";
    script.onload = resolve;
    script.onerror = reject;
    document.head.appendChild(script);
  });
  return tvScriptPromise;
}

function renderChart(symbol) {
  loadTradingViewScript().then(() => {
    document.getElementById("tv-container").innerHTML = "";
    state.tvWidget = new TradingView.widget({
      autosize: true,
      symbol: symbol,
      interval: "5",
      timezone: "America/New_York",
      theme: "dark",
      style: "1",
      locale: "es",
      toolbar_bg: "#131722",
      container_id: "tv-container",
    });
  }).catch(() => {
    document.getElementById("tv-container").innerHTML = `<div class="empty-note">No se pudo cargar el gráfico.</div>`;
  });
}

function renderChartHeader(d) {
  const header = document.getElementById("chart-header");
  header.innerHTML = `
    <div class="chart-header-id">
      <span class="chart-symbol">${d.symbol}</span>
      <span class="chart-name">${d.name || ""}</span>
    </div>
    <div class="chart-header-price">
      <span class="chart-price">${fmt(d.price)}</span>
      <span class="${pctClass(d.change_pct)}">${fmtPct(d.change_pct)}</span>
    </div>
    <div class="chart-header-badges">
      ${decisionBadge(d.display_decision)}
      ${riskBadge(d.risk_level)}
    </div>
  `;
}

function componentRows(components) {
  if (!components || components.length === 0) return `<div class="empty-note">Sin datos.</div>`;
  return components.map(c => `
    <div class="component-row">
      <span class="name">${componentLabel(c.name)}</span>
      <span class="explanation">${translateText(c.explanation)}</span>
    </div>
  `).join("");
}

function conditionList(items, cls) {
  if (!items || items.length === 0) return `<div class="empty-note">Ninguna.</div>`;
  return `<ul class="condition-list ${cls}">${items.map(i => `<li>${translateLabel(i)}</li>`).join("")}</ul>`;
}

function renderInfoBody(d) {
  const body = document.getElementById("info-body");
  const levels = d.levels || {};

  body.innerHTML = `
    <div class="info-metrics">
      <div class="metric">
        <div class="label">Confianza de Atlas</div>
        <div class="value">${fmt(d.decision.confidence, 0)}%</div>
      </div>
      <div class="metric">
        <div class="label">Puntaje general</div>
        <div class="value">${fmt(d.atlas_score.total, 1)}</div>
      </div>
      <div class="metric">
        <div class="label">Fuerza de la tendencia</div>
        <div class="value">${fmt(d.momentum.total, 1)}</div>
      </div>
      <div class="metric">
        <div class="label">Entrada de dinero al sector</div>
        <div class="value">${fmt(d.context_used.sector_money_flow_score, 1)}</div>
      </div>
      <div class="metric">
        <div class="label">Volumen relativo</div>
        <div class="value">${fmtX(d.relative_volume)}</div>
      </div>
    </div>

    <div class="detail-section">
      <h3>Niveles del día</h3>
      <div class="component-row"><span class="name">Apertura</span><span>${fmt(levels.open)}</span></div>
      <div class="component-row"><span class="name">Máximo</span><span>${fmt(levels.high)}</span></div>
      <div class="component-row"><span class="name">Mínimo</span><span>${fmt(levels.low)}</span></div>
      <div class="component-row"><span class="name">Cierre anterior</span><span>${fmt(levels.previous_close)}</span></div>
    </div>

    <div class="detail-section">
      <h3>Por qué Atlas recomienda esto</h3>
      ${conditionList(d.decision.met_conditions, "met")}
      <h3 style="margin-top:14px">Lo que todavía falta</h3>
      ${conditionList(d.decision.missing_conditions, "missing")}
    </div>

    <button class="why-toggle" id="why-toggle">Ver detalle técnico completo</button>
    <div id="why-body">
      <div class="detail-section">
        <h3>Puntaje general (desglose)</h3>
        ${componentRows(d.atlas_score.components)}
      </div>

      <div class="detail-section">
        <h3>Movimiento del precio</h3>
        ${componentRows(d.momentum.components)}
      </div>

      <div class="detail-section">
        <h3>Estado del mercado usado para esta decisión</h3>
        <div class="component-row"><span class="name">Mercado general (SPY)</span><span>${fmt(d.context_used.spy_price)}</span></div>
        <div class="component-row"><span class="name">Tecnológicas (QQQ)</span><span>${fmt(d.context_used.qqq_price)}</span></div>
        <div class="component-row"><span class="name">Nerviosismo del mercado (VIX)</span><span>${fmt(d.context_used.vix_price)}</span></div>
        <div class="component-row"><span class="name">Bitcoin</span><span>${fmt(d.context_used.btc_price, 0)}</span></div>
        <div class="component-row"><span class="name">Sector líder</span><span>${d.context_used.leading_sector || "—"}</span></div>
      </div>

      <div class="detail-section">
        <h3>Se parece a oportunidades anteriores</h3>
        ${d.similar_patterns.length === 0
          ? `<div class="empty-note">Todavía no hay suficiente historial acumulado para comparar.</div>`
          : d.similar_patterns.map(p => `
              <div class="component-row">
                <span class="name">${p.ticker}</span>
                <span>${fmt(p.similarity, 1)}% parecido — ${p.date}</span>
              </div>`).join("")}
      </div>

      <div class="detail-section">
        <h3>Señales de alerta</h3>
        <div class="empty-note">${d.antipatterns_note}</div>
      </div>

      <div class="detail-section">
        <h3>Qué podría cambiar esta recomendación</h3>
        ${conditionList((d.decision.next_events || []).map(translateText), "")}
      </div>
    </div>
  `;

  document.getElementById("why-toggle").addEventListener("click", () => {
    document.getElementById("why-body").classList.toggle("open");
  });
}

async function loadDetail(symbol) {
  document.getElementById("chart-header").innerHTML = `<div class="loading">Calculando ${symbol}...</div>`;
  document.getElementById("info-body").innerHTML = `<div class="loading">Calculando ${symbol}...</div>`;

  try {
    const res = await fetch(`/api/symbol/${encodeURIComponent(symbol)}`);
    const d = await res.json();
    if (d.error) {
      document.getElementById("chart-header").innerHTML = `<div class="empty-note">No se pudo calcular ${symbol}: ${d.error}</div>`;
      document.getElementById("info-body").innerHTML = "";
      return;
    }
    if (state.selectedSymbol !== symbol) return; // el usuario ya seleccionó otra cosa mientras cargaba

    renderChartHeader(d);
    renderInfoBody(d);
    renderChart(d.symbol);
  } catch (err) {
    document.getElementById("chart-header").innerHTML = `<div class="empty-note">Error al calcular ${symbol}.</div>`;
    document.getElementById("info-body").innerHTML = "";
  }
}

// --- Ranking: refresco periódico sin recargar la página ---

async function refreshRanking() {
  try {
    const res = await fetch("/api/ranking");
    const data = await res.json();

    state.ranking = data.ranking || [];
    state.context = data.context;

    renderRankingRail();
    renderContextStrip(state.context);

    const statusEl = document.getElementById("scan-status");
    if (data.scanning) {
      statusEl.textContent = "Analizando el mercado...";
    } else if (data.generated_at) {
      const when = new Date(data.generated_at).toLocaleTimeString();
      statusEl.textContent = `Actualizado ${when} · ${data.symbols_ok}/${data.symbols_scanned} analizados`;
    } else {
      statusEl.textContent = "Esperando primer análisis...";
    }

    // Al abrir Atlas (o en cuanto llega el primer ranking), mostrar de una
    // vez la mejor oportunidad disponible: cero clics para ver algo útil.
    if (!state.selectedSymbol && state.ranking.length > 0) {
      const topPick = state.ranking.find(r => r.is_top_pick) || state.ranking[0];
      selectSymbol(topPick.symbol);
    }
  } catch (err) {
    document.getElementById("scan-status").textContent = "Error al conectar con el servidor";
  }
}

document.getElementById("rescan-btn").addEventListener("click", async () => {
  await fetch("/api/rescan", { method: "POST" });
  document.getElementById("scan-status").textContent = "Analizando el mercado...";
});

refreshRanking();
setInterval(refreshRanking, RANKING_POLL_MS);
