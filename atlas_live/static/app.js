/*
 * Atlas Live - frontend.
 *
 * Este archivo NO calcula nada. Solo pide datos ya calculados por Atlas
 * Core (vía /api/ranking y /api/symbol/<ticker>) y los muestra en lenguaje
 * natural. Toda la inteligencia (scores, decisión, confianza, riesgo)
 * vive en el backend, que a su vez es una capa delgada sobre
 * atlas_live/scan_worker.py, que a su vez solo llama a Atlas Core.
 *
 * Filosofía de esta pantalla: una persona sin conocimientos de trading
 * debe poder abrir Atlas y entender en 30 segundos qué recomienda hacer.
 * Por eso la decisión, la confianza y el riesgo van primero, en lenguaje
 * simple; los indicadores técnicos solo aparecen si el usuario hace clic
 * en "¿Por qué?".
 */

const RANKING_POLL_MS = 20000;

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

// --- Hero: oportunidad del momento ---

function renderHero(topPick) {
  const hero = document.getElementById("hero");

  if (!topPick) {
    hero.innerHTML = `
      <div class="hero-label">⭐ La decisión de Atlas</div>
      <div class="hero-empty">Todavía no hay un análisis calculado. Puede tardar unos minutos.</div>
    `;
    return;
  }

  // Si la mejor opción disponible no es una compra clara, Atlas lo dice
  // directamente en vez de disfrazar una opción débil como "la oportunidad".
  if (topPick.display_decision.code !== "SI_COMPRARIA") {
    hero.innerHTML = `
      <div class="hero-label">⭐ La decisión de Atlas</div>
      <div class="hero-empty">Hoy yo esperaría. No encontré una oportunidad con suficiente confianza.</div>
    `;
    return;
  }

  hero.innerHTML = `
    <div class="hero-card" data-symbol="${topPick.symbol}">
      <div class="hero-label">⭐ La decisión de Atlas</div>
      <div class="hero-field">
        <div class="label">Empresa</div>
        <div class="hero-symbol">${topPick.symbol}</div>
        <div class="hero-name">${topPick.name || ""}</div>
      </div>
      <div class="hero-field">
        <div class="label">Recomendación</div>
        ${decisionBadge(topPick.display_decision)}
      </div>
      <div class="hero-row">
        <div class="hero-metric">
          <div class="label">Confianza de Atlas</div>
          <div class="value">${fmt(topPick.confidence, 0)}%</div>
        </div>
        <div class="hero-metric">
          <div class="label">Nivel de riesgo</div>
          <div class="value">${riskBadge(topPick.risk_level)}</div>
        </div>
      </div>
    </div>
  `;
  hero.querySelector(".hero-card").addEventListener("click", () => openDetail(topPick.symbol));
}

// --- Lista de oportunidades ---

function renderRanking(ranking) {
  const list = document.getElementById("ranking-list");
  if (!ranking || ranking.length === 0) {
    list.innerHTML = `<div class="empty-note">Sin resultados todavía. El primer análisis puede tardar unos minutos.</div>`;
    return;
  }
  list.innerHTML = ranking.map(row => `
    <div class="opp-card" data-symbol="${row.symbol}">
      <div class="opp-id">
        <span class="opp-symbol">${row.symbol}</span>
        <span class="opp-name">${row.name || ""}</span>
      </div>
      <div class="opp-right">
        ${decisionBadge(row.display_decision)}
        ${riskBadge(row.risk_level)}
        <div class="opp-confidence">
          <div class="value">${fmt(row.confidence, 0)}%</div>
          <div class="label">Confianza</div>
        </div>
      </div>
    </div>
  `).join("");

  list.querySelectorAll(".opp-card").forEach(card => {
    card.addEventListener("click", () => openDetail(card.dataset.symbol));
  });
}

function renderContextStrip(context) {
  const bar = document.getElementById("context-bar");
  if (!context) {
    bar.innerHTML = `<div class="m-item">Estado del mercado: esperando primer análisis…</div>`;
    return;
  }
  const items = [
    ["Mercado general (SPY)", fmt(context.spy_price), context.spy_change_percent],
    ["Tecnológicas (QQQ)", fmt(context.qqq_price), context.qqq_change_percent],
    ["Nerviosismo del mercado (VIX)", fmt(context.vix_price), context.vix_change_percent],
    ["Bitcoin", fmt(context.btc_price, 0), context.btc_change_percent],
  ];
  let html = items.map(([label, value, chg]) => `
    <span class="m-item">${label}: <strong>${value}</strong> <span class="${pctClass(chg)}">${fmtPct(chg)}</span></span>
  `).join("");
  html += `<span class="m-item">Sector con más entrada de dinero: <strong>${context.leading_sector || "—"}</strong></span>`;
  bar.innerHTML = html;
}

async function refreshRanking() {
  try {
    const res = await fetch("/api/ranking");
    const data = await res.json();

    const topPick = (data.ranking || []).find(r => r.is_top_pick) || (data.ranking || [])[0] || null;
    renderHero(topPick);
    renderRanking(data.ranking);
    renderContextStrip(data.context);

    const statusEl = document.getElementById("scan-status");
    if (data.scanning) {
      statusEl.textContent = "Analizando el mercado...";
    } else if (data.generated_at) {
      const when = new Date(data.generated_at).toLocaleTimeString();
      statusEl.textContent = `Actualizado ${when} · ${data.symbols_ok}/${data.symbols_scanned} analizados`;
    } else {
      statusEl.textContent = "Esperando primer análisis...";
    }
  } catch (err) {
    document.getElementById("scan-status").textContent = "Error al conectar con el servidor";
  }
}

// --- Panel de detalle ---

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

function tradingViewSymbol(symbol) {
  return symbol; // TradingView resuelve el símbolo/exchange automáticamente en la mayoría de los casos
}

async function openDetail(symbol) {
  const overlay = document.getElementById("detail-overlay");
  const body = document.getElementById("detail-body");
  document.getElementById("detail-title").textContent = symbol;
  body.innerHTML = `<div class="loading">Calculando ${symbol}...</div>`;
  overlay.classList.add("open");

  try {
    const res = await fetch(`/api/symbol/${encodeURIComponent(symbol)}`);
    const d = await res.json();
    if (d.error) {
      body.innerHTML = `<div class="empty-note">No se pudo calcular: ${d.error}</div>`;
      return;
    }

    document.getElementById("detail-title").textContent = `${d.symbol} — ${d.name || ""}`;

    body.innerHTML = `
      <div class="detail-summary">
        ${decisionBadge(d.display_decision)}
        <div class="metric">
          <div class="label">Confianza de Atlas</div>
          <div class="value">${fmt(d.decision.confidence, 0)}%</div>
        </div>
        <div class="metric">
          <div class="label">Nivel de riesgo</div>
          <div class="value">${riskBadge(d.risk_level)}</div>
        </div>
        <div class="metric">
          <div class="label">Precio</div>
          <div class="value">${fmt(d.price)} <span class="${pctClass(d.change_pct)}">${fmtPct(d.change_pct)}</span></div>
        </div>
      </div>

      <div id="tv-container"></div>

      <button class="why-toggle" id="why-toggle">¿Por qué Atlas recomienda esto?</button>

      <div id="why-body">
        <div class="detail-section">
          <h3>Lo que respalda esta recomendación</h3>
          ${conditionList(d.decision.met_conditions, "met")}
          <h3 style="margin-top:14px">Lo que todavía falta</h3>
          ${conditionList(d.decision.missing_conditions, "missing")}
        </div>

        <div class="detail-section">
          <h3>Fuerza de la oportunidad (puntaje general: ${fmt(d.atlas_score.total, 1)})</h3>
          ${componentRows(d.atlas_score.components)}
        </div>

        <div class="detail-section">
          <h3>Movimiento del precio (fuerza de la tendencia: ${fmt(d.momentum.total, 1)})</h3>
          ${componentRows(d.momentum.components)}
        </div>

        <div class="detail-section">
          <h3>Estado del mercado usado para esta decisión</h3>
          <div class="component-row"><span class="name">Mercado general (SPY)</span><span>${fmt(d.context_used.spy_price)}</span></div>
          <div class="component-row"><span class="name">Tecnológicas (QQQ)</span><span>${fmt(d.context_used.qqq_price)}</span></div>
          <div class="component-row"><span class="name">Nerviosismo del mercado (VIX)</span><span>${fmt(d.context_used.vix_price)}</span></div>
          <div class="component-row"><span class="name">Bitcoin</span><span>${fmt(d.context_used.btc_price, 0)}</span></div>
          <div class="component-row"><span class="name">Sector líder</span><span>${d.context_used.leading_sector || "—"}</span></div>
          <div class="component-row"><span class="name">Entrada de dinero al sector</span><span>${fmt(d.context_used.sector_money_flow_score, 1)}</span></div>
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

    // TradingView se inyecta después de renderizar el HTML de arriba.
    const tvScript = document.createElement("script");
    tvScript.src = "https://s3.tradingview.com/tv.js";
    tvScript.onload = () => {
      new TradingView.widget({
        autosize: true,
        symbol: tradingViewSymbol(d.symbol),
        interval: "5",
        timezone: "America/New_York",
        theme: "dark",
        style: "1",
        locale: "es",
        toolbar_bg: "#131722",
        container_id: "tv-container",
      });
    };
    document.getElementById("tv-container").appendChild(tvScript);
  } catch (err) {
    body.innerHTML = `<div class="empty-note">Error al calcular el detalle.</div>`;
  }
}

document.getElementById("detail-close").addEventListener("click", () => {
  document.getElementById("detail-overlay").classList.remove("open");
});
document.getElementById("detail-overlay").addEventListener("click", (e) => {
  if (e.target.id === "detail-overlay") e.target.classList.remove("open");
});
document.getElementById("rescan-btn").addEventListener("click", async () => {
  await fetch("/api/rescan", { method: "POST" });
  document.getElementById("scan-status").textContent = "Analizando el mercado...";
});

refreshRanking();
setInterval(refreshRanking, RANKING_POLL_MS);
