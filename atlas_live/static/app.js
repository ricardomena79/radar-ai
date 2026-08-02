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

// --- Hero: oportunidad más explosiva ---
//
// Ojo: este hero ya NO refleja la "decisión de Atlas" (Decision Engine).
// Refleja el resultado #1 del motor de Radar Explosivo (explosive_engine.py
// en el backend, atlas_live/), que es una pregunta distinta: probabilidad
// de movimiento fuerte en los próximos 5-10 minutos, no calidad de la
// empresa ni recomendación de compra.

function reasonsList(reasons) {
  if (!reasons || reasons.length === 0) return "";
  return `<ul class="reasons-list">${reasons.map(r => `<li>${r}</li>`).join("")}</ul>`;
}

function renderHero(topExplosive) {
  const hero = document.getElementById("hero");

  if (!topExplosive) {
    hero.innerHTML = `
      <div class="hero-label">🔥 Oportunidad más explosiva</div>
      <div class="hero-empty">Todavía no hay ninguna oportunidad de alto momentum. Puede tardar unos minutos, o simplemente no hay nada explosivo ahora mismo.</div>
    `;
    return;
  }

  const exp = topExplosive.explosive;
  const exceptionNote = exp.is_size_exception
    ? `<div class="hero-exception">⚠ Excepción de tamaño — ver motivo abajo</div>`
    : "";
  const highlightClass = isHighlighted(topExplosive.symbol) ? " highlight-new" : "";

  hero.innerHTML = `
    <div class="hero-card${highlightClass}" data-symbol="${topExplosive.symbol}">
      <div class="hero-label">🔥 Oportunidad más explosiva</div>
      <div class="hero-field">
        <div class="label">Empresa</div>
        <div class="hero-symbol">${topExplosive.symbol}</div>
        <div class="hero-name">${topExplosive.name || ""}</div>
      </div>
      <div class="hero-row">
        <div class="hero-metric">
          <div class="label">Puntaje de explosión</div>
          <div class="value">${fmt(exp.score, 0)}</div>
        </div>
      </div>
      ${exceptionNote}
      ${reasonsList(exp.reasons)}
    </div>
  `;
  hero.querySelector(".hero-card").addEventListener("click", () => openDetail(topExplosive.symbol));
}

// --- Listas de oportunidades ---
//
// Radar General y Watchlist muestran el ranking de Decision Engine, sin
// recalcular ni reclasificar nada (solo se filtra/ordena en el navegador
// con campos que el backend ya entrega: display_decision). Radar Explosivo
// es distinto: usa el campo `explosive` que calculó explosive_engine.py,
// un motor propio de atlas_live, independiente de Decision Engine.

function renderCardList(containerId, rows, emptyMessage) {
  const list = document.getElementById(containerId);
  if (!rows || rows.length === 0) {
    list.innerHTML = `<div class="empty-note">${emptyMessage}</div>`;
    return;
  }
  list.innerHTML = rows.map(row => `
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

// Radar Explosivo: usa exclusivamente el resultado del motor propio
// (row.explosive), no display_decision. Elegibles = pasaron los filtros
// de explosive_engine.py (RVOL, gap/cambio, volatilidad, tamaño...);
// ordenados por su puntaje de explosión, no por confianza de Decision Engine.
function explosivoRows(ranking) {
  return (ranking || [])
    .filter(row => row.explosive && row.explosive.eligible)
    .slice()
    .sort((a, b) => (b.explosive.score ?? -Infinity) - (a.explosive.score ?? -Infinity));
}

// Watchlist: interesantes pero sin recomendación de compra todavía
// (VIGILAR en Decision Engine, mostrado como Esperaría/Solo observaría).
function watchlistRows(ranking) {
  return (ranking || []).filter(row =>
    row.display_decision &&
    (row.display_decision.code === "ESPERARIA" || row.display_decision.code === "SOLO_OBSERVARIA")
  );
}

function renderRanking(ranking) {
  renderCardList("ranking-list", ranking, "Sin resultados todavía. El primer análisis puede tardar unos minutos.");
}

function renderExplosivoList(rows) {
  const list = document.getElementById("explosivo-list");
  if (!rows || rows.length === 0) {
    list.innerHTML = `<div class="empty-note">Sin oportunidades de alto momentum en este momento.</div>`;
    return;
  }
  list.innerHTML = rows.map(row => `
    <div class="opp-card explosive-card${isHighlighted(row.symbol) ? " highlight-new" : ""}" data-symbol="${row.symbol}">
      <div class="opp-header">
        <div class="opp-id">
          <span class="opp-symbol">${row.symbol}</span>
          <span class="opp-name">${row.name || ""}</span>
          ${row.explosive.is_size_exception ? `<span class="exception-badge">⚠ Excepción de tamaño</span>` : ""}
          ${isHighlighted(row.symbol) ? `<span class="new-badge">NUEVA</span>` : ""}
        </div>
        <div class="opp-confidence">
          <div class="value">${fmt(row.explosive.score, 0)}</div>
          <div class="label">Puntaje explosión</div>
        </div>
      </div>
      ${reasonsList(row.explosive.reasons)}
    </div>
  `).join("");

  list.querySelectorAll(".opp-card").forEach(card => {
    card.addEventListener("click", () => openDetail(card.dataset.symbol));
  });
}

function renderWatchlist(ranking) {
  renderCardList(
    "watchlist-list",
    watchlistRows(ranking),
    "Nada interesante todavía sin recomendación."
  );
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

// Símbolos elegibles vistos en el último poll -- `null` significa "todavía
// no hubo un primer poll", para no disparar notificaciones de "nuevas"
// oportunidades apenas se carga la página (no son nuevas, son las que ya
// existían). Se vuelve un Set real recién después del primer poll.
let previousEligibleSymbols = null;

function detectNewOpportunities(eligibleExplosive) {
  const currentSymbols = new Set(eligibleExplosive.map(row => row.symbol));

  if (previousEligibleSymbols === null) {
    previousEligibleSymbols = currentSymbols;
    return [];
  }

  const newRows = eligibleExplosive.filter(row => !previousEligibleSymbols.has(row.symbol));
  previousEligibleSymbols = currentSymbols;
  return newRows;
}

async function refreshRanking() {
  try {
    const res = await fetch("/api/ranking");
    const data = await res.json();

    const eligibleExplosive = explosivoRows(data.ranking);

    // Se detectan y despachan ANTES de renderizar, para que el resaltado
    // visual (channelVisualHighlight) ya esté activo cuando se pintan las
    // tarjetas de este mismo ciclo.
    const newOpportunities = detectNewOpportunities(eligibleExplosive);
    dispatchNotifications(newOpportunities);

    renderHero(eligibleExplosive[0] || null);
    renderExplosivoList(eligibleExplosive);
    renderRanking(data.ranking);
    renderWatchlist(data.ranking);
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

// --- Navegación entre las tres secciones (menú lateral) ---
// Puramente de presentación: no cambia qué datos se piden ni cómo se
// calculan, solo qué sección queda visible. Radar Explosivo es la
// pantalla principal (primera visible al cargar).

const VIEW_TITLES = {
  explosivo: "🔥 Radar Explosivo",
  general: "📈 Radar General",
  watchlist: "📋 Watchlist",
  diagnostico: "🔬 Diagnóstico",
};

function showView(viewName) {
  document.querySelectorAll(".view").forEach(section => {
    section.classList.toggle("active", section.id === `view-${viewName}`);
  });
  document.querySelectorAll(".nav-item").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.view === viewName);
  });
  document.getElementById("view-title").textContent = VIEW_TITLES[viewName] || "ATLAS";

  if (viewName === "diagnostico") {
    loadDiagnostics();
  }
}

document.querySelectorAll(".nav-item").forEach(btn => {
  btn.addEventListener("click", () => showView(btn.dataset.view));
});

// --- Diagnóstico del Radar Explosivo ---
//
// A diferencia de las otras tres vistas, esta NO se refresca con el
// polling de refreshRanking() (sería pedir el detalle de ~200 símbolos
// cada 20 segundos sin necesidad): se pide bajo demanda, cada vez que el
// usuario abre la pestaña, contra /api/explosive-diagnostics.

function fmtCap(value) {
  if (value === null || value === undefined) return "—";
  const abs = Math.abs(value);
  if (abs >= 1e12) return `$${(value / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(value / 1e6).toFixed(0)}M`;
  return `$${fmt(value, 0)}`;
}

async function loadDiagnostics() {
  const content = document.getElementById("diagnostico-content");
  content.innerHTML = `<div class="loading">Cargando...</div>`;
  try {
    const res = await fetch("/api/explosive-diagnostics");
    const data = await res.json();
    if (!data.available) {
      content.innerHTML = `<div class="empty-note">Todavía no hay un escaneo completo para diagnosticar. Espera al primer análisis.</div>`;
      return;
    }
    renderDiagnostics(data);
  } catch (err) {
    content.innerHTML = `<div class="empty-note">Error al cargar el diagnóstico.</div>`;
  }
}

function renderDiagnostics(data) {
  const content = document.getElementById("diagnostico-content");

  const funnelSteps = [
    { label: "Universo analizado", count: data.total_universe },
    ...data.funnel.map(f => ({ label: f.label, count: f.count })),
    { label: "Radar Explosivo final", count: data.final_eligible_count, isFinal: true },
  ];

  const funnelHtml = `
    <div class="funnel">
      ${funnelSteps.map(step => `
        <div class="funnel-step ${step.isFinal ? "funnel-final" : ""}">
          <div class="funnel-label">${step.label}</div>
          <div class="funnel-count">${step.count}</div>
        </div>
      `).join("")}
    </div>
    ${data.data_errors > 0 ? `
      <div class="funnel-note">
        ${data.data_errors} símbolo(s) no se pudieron evaluar (error de datos del proveedor) --
        no cuentan como descartados por ningún filtro del Radar Explosivo.
      </div>
    ` : ""}
  `;

  const tableRows = data.table.map(row => `
    <tr class="${row.status === "Aprobada" ? "row-approved" : "row-excluded"}" data-symbol="${row.symbol}">
      <td>${row.symbol}</td>
      <td>${row.status}</td>
      <td>${row.reason}</td>
      <td>${fmtCap(row.market_cap)}</td>
      <td>${row.gap_pct === null || row.gap_pct === undefined ? "—" : fmtPct(row.gap_pct)}</td>
      <td>${row.relative_volume === null || row.relative_volume === undefined ? "—" : row.relative_volume.toFixed(1) + "x"}</td>
      <td>${row.score === null || row.score === undefined ? "—" : fmt(row.score, 0)}</td>
    </tr>
  `).join("");

  content.innerHTML = `
    ${funnelHtml}
    <h2 class="section-title" style="margin-top:28px">Aprobadas y descartadas más cercanas a calificar</h2>
    <div class="diag-table-wrap">
      <table class="diag-table">
        <thead>
          <tr>
            <th>Ticker</th><th>Estado</th><th>Motivo</th>
            <th>Market cap</th><th>Gap</th><th>RVOL</th><th>Puntaje</th>
          </tr>
        </thead>
        <tbody>${tableRows}</tbody>
      </table>
    </div>
  `;

  content.querySelectorAll("tbody tr").forEach(tr => {
    tr.addEventListener("click", () => openDetail(tr.dataset.symbol));
  });
}

// --- Controles de notificaciones (navegador / sonido) ---
// Solo lee/escribe `notificationSettings` (definido en notifications.js) y
// pide permiso del navegador cuando corresponde -- no conoce nada de cómo
// se despachan las notificaciones, eso es responsabilidad de cada canal.

const notifBrowserToggle = document.getElementById("notif-browser-toggle");
const notifSoundToggle = document.getElementById("notif-sound-toggle");

notifBrowserToggle.checked = notificationSettings.browserEnabled && Notification?.permission === "granted";
notifSoundToggle.checked = notificationSettings.soundEnabled;

notifBrowserToggle.addEventListener("change", async () => {
  if (notifBrowserToggle.checked) {
    const permission = await requestBrowserNotificationPermission();
    if (permission !== "granted") {
      notifBrowserToggle.checked = false;
      notificationSettings.browserEnabled = false;
      saveNotificationSettings(notificationSettings);
      if (permission === "unsupported") {
        alert("Este navegador no soporta notificaciones.");
      } else {
        alert("Notificaciones bloqueadas -- habilitalas en la configuración del navegador para este sitio.");
      }
      return;
    }
  }
  notificationSettings.browserEnabled = notifBrowserToggle.checked;
  saveNotificationSettings(notificationSettings);
});

notifSoundToggle.addEventListener("change", () => {
  notificationSettings.soundEnabled = notifSoundToggle.checked;
  saveNotificationSettings(notificationSettings);
});

refreshRanking();
setInterval(refreshRanking, RANKING_POLL_MS);
