/*
 * Sistema de notificaciones de Atlas Live.
 *
 * Infraestructura modular: cada canal es una función independiente
 * registrada en NOTIFICATION_CHANNELS. Agregar un canal nuevo en el
 * futuro (push al celular, correo, webhook, Telegram, Discord) significa
 * escribir una función con la misma firma (recibe la lista de
 * oportunidades nuevas) y agregarla al registro -- no requiere tocar la
 * lógica de detección de "oportunidad nueva" (eso vive en app.js) ni los
 * demás canales.
 *
 * Este archivo no calcula nada de Atlas: solo reacciona a la lista de
 * símbolos que app.js ya determinó que son elegibles y nuevos.
 */

const NOTIFICATION_SETTINGS_KEY = "atlas_notification_settings";

const DEFAULT_NOTIFICATION_SETTINGS = {
  browserEnabled: false, // requiere permiso explícito del usuario del navegador
  soundEnabled: true,
};

function loadNotificationSettings() {
  try {
    const raw = localStorage.getItem(NOTIFICATION_SETTINGS_KEY);
    return raw ? { ...DEFAULT_NOTIFICATION_SETTINGS, ...JSON.parse(raw) } : { ...DEFAULT_NOTIFICATION_SETTINGS };
  } catch (err) {
    return { ...DEFAULT_NOTIFICATION_SETTINGS };
  }
}

function saveNotificationSettings(settings) {
  try {
    localStorage.setItem(NOTIFICATION_SETTINGS_KEY, JSON.stringify(settings));
  } catch (err) {
    // localStorage no disponible (modo privado, etc.) -- la preferencia
    // simplemente no persiste entre recargas, no es un error fatal.
  }
}

let notificationSettings = loadNotificationSettings();

async function requestBrowserNotificationPermission() {
  if (!("Notification" in window)) return "unsupported";
  if (Notification.permission === "granted") return "granted";
  return await Notification.requestPermission();
}

// --- Canal: notificación del navegador ---
function channelBrowserNotification(opportunities) {
  if (!notificationSettings.browserEnabled) return;
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  opportunities.forEach(row => {
    const exp = row.explosive || {};
    new Notification("🔥 Radar Explosivo -- nueva oportunidad", {
      body: `${row.symbol}${row.name ? " — " + row.name : ""} · puntaje ${exp.score ?? "—"}`,
      tag: `atlas-explosive-${row.symbol}`,
    });
  });
}

// --- Canal: sonido local ---
function channelSound(opportunities) {
  if (!notificationSettings.soundEnabled || opportunities.length === 0) return;
  playChime();
}

function playChime() {
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    [880, 1320].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = freq;
      osc.connect(gain);
      gain.connect(ctx.destination);
      const start = ctx.currentTime + i * 0.15;
      gain.gain.setValueAtTime(0.15, start);
      gain.gain.exponentialRampToValueAtTime(0.001, start + 0.14);
      osc.start(start);
      osc.stop(start + 0.15);
    });
  } catch (err) {
    // audio bloqueado por el navegador (requiere interacción previa del usuario) -- se ignora
  }
}

// --- Canal: resaltado visual ---
// No pinta nada por sí mismo -- marca símbolos como "recién aparecidos" por
// unos segundos; los renderers de app.js (renderExplosivoList/renderHero)
// consultan isHighlighted() para decidir si agregan la clase CSS.
const HIGHLIGHT_DURATION_MS = 8000;
const highlightedSymbols = new Set();

function channelVisualHighlight(opportunities) {
  opportunities.forEach(row => {
    highlightedSymbols.add(row.symbol);
    setTimeout(() => highlightedSymbols.delete(row.symbol), HIGHLIGHT_DURATION_MS);
  });
}

function isHighlighted(symbol) {
  return highlightedSymbols.has(symbol);
}

// --- Registro modular de canales ---
const NOTIFICATION_CHANNELS = [channelBrowserNotification, channelSound, channelVisualHighlight];

function dispatchNotifications(newOpportunities) {
  if (!newOpportunities || newOpportunities.length === 0) return;
  NOTIFICATION_CHANNELS.forEach(channel => {
    try {
      channel(newOpportunities);
    } catch (err) {
      // un canal roto no debe tumbar a los demás
      console.error("Error en canal de notificación:", err);
    }
  });
}
