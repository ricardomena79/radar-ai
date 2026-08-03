/*
 * Datos SIMULADOS para la Cabina del Piloto -- estructura visual en revisión.
 * Los nombres de campo replican a propósito los que ya produce el Memory
 * Engine real (RankedCandidate / JournaledCandidate / exit_summary) para
 * que conectar datos reales después sea un cambio de fuente, no de forma.
 *
 * Dos campos están marcados explícitamente como "SIN BACKEND REAL TODAVÍA"
 * porque hoy no existe ningún cálculo que los respalde (ni siquiera
 * simulado sobre datos reales): etaMovementMinutes ("tiempo estimado
 * hasta el inicio del movimiento") e historicalTarget ("objetivo histórico
 * esperado"). Se muestran en la cabina porque el usuario pidió responder
 * esas preguntas, pero no hay que asumir que ya existe la lógica detrás.
 */

const MOCK = {

  systemStatus: {
    session: "premarket", // premarket | regular | afterhours | closed
    lastScanAt: "2026-08-04T08:47:00-04:00",
    scanIntervalSeconds: 300,
    healthy: true,
    healthNote: "Último ciclo de escaneo OK, sin errores. Memory Store: 73.123 observaciones. Prediction Journal: ranking de hoy sellado a las 09:26.",
  },

  bestOpportunity: {
    symbol: "NUWE",
    assetType: "equity",
    marketCapBucket: "micro",
    price: 4.85,
    score: 82.2,
    eligibleRadar: true,
    probabilityPct: 64.8,
    confidence: "Alta",
    semaforo: "verde",
    explanation: "Coincide con la condición 'gap_pct >= 10.0', que en los 30 días validados tuvo una tasa de EXPLOSION de 64.80% (75.3x el promedio general de 0.86%), con 179 observaciones de respaldo y un límite inferior de Wilson de 57.56%.",
    evidenceCondition: "gap_pct >= 10.0",
    evidenceSampleSize: 179,
    evidenceWilsonLowerBoundPct: 57.56,
    // SIN BACKEND REAL TODAVÍA -- no existe cálculo detrás, ver nota arriba.
    etaMovementMinutes: 12,
    historicalTarget: { low: 25, median: 45, high: 135 },
  },

  // Plan B: segunda mejor oportunidad -- mismo tipo de evidencia que la
  // principal, presentada como respaldo, no como una segunda recomendación
  // de igual peso.
  planB: {
    symbol: "XRX",
    marketCapBucket: "large",
    price: 11.20,
    score: 59.0,
    eligibleRadar: true,
    probabilityPct: 64.8,
    confidence: "Alta",
    semaforo: "verde",
    explanation: "Misma condición confiable que NUWE ('gap_pct >= 10.0'), pero con score real de Radar Explosivo más bajo (59.0 vs 82.2) -- respaldo si NUWE pierde fuerza antes de la apertura.",
    evidenceSampleSize: 179,
    evidenceWilsonLowerBoundPct: 57.56,
    etaMovementMinutes: 18,
    historicalTarget: { low: 20, median: 32, high: 60 },
  },

  explosiveMicrocaps: [
    { symbol: "NUWE", price: 4.85, marketCap: "180M", score: 82.2, probabilityPct: 64.8, confidence: "Alta", semaforo: "verde", condition: "gap_pct >= 10.0" },
    { symbol: "BJDX", price: 2.10, marketCap: "95M", score: 61.4, probabilityPct: 40.9, confidence: "Media", semaforo: "verde", condition: "relative_volume >= 10.0" },
  ],
  explosiveMicrocapsStatus: {
    activityLevel: "Alta",
    note: "2 candidatos activos, ambos con evidencia confiable (n>=22). El más fuerte supera 6x el umbral de lift habitual.",
  },

  momentumCandidates: [
    { symbol: "CIFR", price: 14.20, changePct: 6.3, score: null, probabilityPct: 17.4, confidence: "Media", semaforo: "amarillo", condition: "relative_volume en [1.0, 2.5)" },
    { symbol: "MSFL", price: 61.10, changePct: 4.1, score: null, probabilityPct: 16.3, confidence: "Baja", semaforo: "amarillo", condition: "relative_volume en [2.5, 5.0)" },
    { symbol: "SOXL", price: 33.75, changePct: 3.4, score: null, probabilityPct: 7.7, confidence: "Media", semaforo: "amarillo", condition: "relative_volume en [0.5, 1.0)" },
  ],
  momentumStatus: {
    activityLevel: "Media",
    note: "3 candidatos, ninguno con confianza Alta -- señal más débil que Explosivas hoy.",
  },

  etfs: [
    { symbol: "SOXL", price: 33.75, changePct: 3.4, category: "Semiconductores 3x", probabilityPct: 7.7, semaforo: "amarillo" },
    { symbol: "UVIX", price: 12.02, changePct: -2.1, category: "Volatilidad 2x", probabilityPct: null, semaforo: "rojo" },
    { symbol: "WGMI", price: 18.44, changePct: 1.8, category: "Mineras cripto", probabilityPct: 3.1, semaforo: "rojo" },
  ],

  doNotTouch: [
    { symbol: "GNOM", price: 3.20, changePct: 2.2, reasonTag: "Falsa ruptura", reason: "FALSE_BREAKOUT en la validación histórica: gap alto, no sostenido", semaforo: "rojo" },
    { symbol: "SOXS", price: 8.90, changePct: -19.5, reasonTag: "Falsa ruptura", reason: "Era elegible al momento de sellar el ranking y cerró en pérdida -- parecía explosiva y no lo sostuvo", semaforo: "rojo" },
    { symbol: "PRPL", price: 1.10, changePct: 0.4, reasonTag: "Dato sospechoso", reason: "Variación >2000% en un día en el histórico -- casi con certeza un error de datos, no un movimiento real. Excluir de cualquier lectura", semaforo: "rojo" },
  ],

  // Actividad de Atlas -- estados que van rotando en la barra de actividad.
  // Simulado: en producción, cada frase saldría de lo que scan_worker /
  // live_integration está haciendo en ese instante exacto del ciclo.
  activityFeed: [
    "Escaneando universo -- 1.847/2.577 símbolos procesados...",
    "Aplicando Ranking Score sobre candidatos elegibles...",
    "Consultando evidencia del Memory Engine (73.123 observaciones)...",
    "Sincronizando snapshot dinámico con el Prediction Journal...",
    "Actualizando trayectorias del Exit Journal...",
  ],

  alerts: [
    { time: "09:12", symbol: "NUWE", message: "Subió al puesto #1 -- gap_pct cruzó el umbral de 10%", semaforo: "verde" },
    { time: "09:05", symbol: "XRX", message: "Entró a Explosivas -- nueva condición confiable matcheada", semaforo: "verde" },
    { time: "08:58", symbol: "SOXS", message: "Marcado 'No tocar' -- antecedente de falsa ruptura detectado", semaforo: "rojo" },
    { time: "08:41", symbol: "CIFR", message: "Entró a Momentum -- relative_volume cruzó 1.0x", semaforo: "amarillo" },
    { time: "08:22", symbol: "BJDX", message: "Detectado por primera vez en el premarket", semaforo: "verde" },
  ],

  atlasOpina: "Actividad de premarket alta en microcaps con catalizadores de gap fuerte -- NUWE concentra la evidencia más sólida del día (n=179, límite de Wilson 57.6%). XRX confirma la misma condición con score real más bajo, buen respaldo si NUWE pierde fuerza antes de la apertura. Momentum viene débil: ningún candidato alcanza confianza Alta, no es el foco de hoy. Precaución particular con SOXS y GNOM -- ambos ya mostraron el patrón de falsa ruptura en la validación histórica.",

  // Calidad del Mercado se conectó a datos reales el 2026-08-03
  // (cabina.js::renderMarketQuality()) -- este mock queda retirado, ver
  // DECISION_LOG.md.

  // "¿Por qué NO?" -- candidatos que a simple vista podrían parecer
  // atractivos (gran suba, alto volumen, nombre conocido) pero que Atlas
  // descartó, con el motivo exacto -- para no tener que preguntarlo.
  whyNot: [
    { symbol: "ZAPP", apparentReason: "Subió +18.4% en premarket -- la mayor suba del universo hoy", excludedBecause: "No pasó el gate de liquidez mínima ($2M) -- volumen en dólares insuficiente para operar con seguridad." },
    { symbol: "TSLA", apparentReason: "Alto volumen y muy conocida", excludedBecause: "Market cap muy por encima del techo de microcap/mid -- penalización fuerte por tamaño, no es el perfil que busca Radar Explosivo." },
    { symbol: "KTRA", apparentReason: "RVOL de 22x -- parece explosiva a primera vista", excludedBecause: "Gap% y cambio% no superaron el umbral mínimo de movimiento -- volumen alto sin movimiento de precio correspondiente." },
  ],

  radarCompleto: [
    { symbol: "NUWE", price: 4.85, changePct: 12.4, score: 82.2, eligible: true, probabilityPct: 64.8, semaforo: "verde" },
    { symbol: "XRX", price: 11.20, changePct: 9.8, score: 59.0, eligible: true, probabilityPct: 64.8, semaforo: "verde" },
    { symbol: "BJDX", price: 2.10, changePct: 8.1, score: 61.4, eligible: true, probabilityPct: 40.9, semaforo: "verde" },
    { symbol: "CIFR", price: 14.20, changePct: 6.3, score: null, eligible: false, probabilityPct: 17.4, semaforo: "amarillo" },
    { symbol: "MSFL", price: 61.10, changePct: 4.1, score: null, eligible: false, probabilityPct: 16.3, semaforo: "amarillo" },
    { symbol: "SOXL", price: 33.75, changePct: 3.4, score: null, eligible: false, probabilityPct: 7.7, semaforo: "amarillo" },
    { symbol: "GNOM", price: 3.20, changePct: 2.2, score: null, eligible: false, probabilityPct: null, semaforo: "rojo" },
    { symbol: "SOXS", price: 8.90, changePct: -19.5, score: null, eligible: true, probabilityPct: null, semaforo: "rojo" },
    { symbol: "AAPL", price: 231.40, changePct: 0.3, score: null, eligible: false, probabilityPct: null, semaforo: "neutro" },
    { symbol: "PRPL", price: 1.10, changePct: 0.4, score: null, eligible: false, probabilityPct: null, semaforo: "rojo" },
  ],

  memoryEngine: {
    baselineWinRatePct: 0.86,
    observationCount: 73123,
    daysBacked: 30,
    reliableConditions: [
      { label: "gap_pct >= 10.0", winRatePct: 64.80, wilsonLowerBoundPct: 57.56, sampleSize: 179, lift: 75.3 },
      { label: "relative_volume >= 10.0", winRatePct: 40.91, wilsonLowerBoundPct: 23.26, sampleSize: 22, lift: 47.6 },
      { label: "gap_pct en [5.0, 10.0)", winRatePct: 21.80, wilsonLowerBoundPct: 18.83, sampleSize: 665, lift: 25.3 },
      { label: "relative_volume en [1.0, 2.5)", winRatePct: 17.44, wilsonLowerBoundPct: 12.50, sampleSize: 172, lift: 20.3 },
      { label: "market_cap < 300M (micro)", winRatePct: 4.40, wilsonLowerBoundPct: 3.39, sampleSize: 1226, lift: 5.1 },
    ],
    lastRecalibratedAt: "2026-08-04T04:00:00-04:00",
  },

  predictionJournal: {
    sealedToday: { sealedAt: "2026-08-04T09:26:00-04:00", candidateCount: 20, topSymbol: "NUWE" },
    recentDays: [
      { date: "2026-08-03", topSymbol: "AGEN", predictedProbabilityPct: 82.7, resultCategory: "EXPLOSION", resultPct: 82.7, anticipationMinutes: 340 },
      { date: "2026-08-01", topSymbol: "WRAP", predictedProbabilityPct: 87.2, resultCategory: "EXPLOSION", resultPct: 48.4, anticipationMinutes: 275 },
      { date: "2026-07-31", topSymbol: "COHU", predictedProbabilityPct: 64.8, resultCategory: "NORMAL", resultPct: 1.9, anticipationMinutes: null },
    ],
  },

  exitJournal: [
    { symbol: "NUWE", date: "2026-08-03", detectedAt: "08:10", entryAt: "09:26", peakAt: "10:05", peakReturnPct: 135.4, finalReturnPct: 128.9, sampleCount: 14 },
    { symbol: "XRX", date: "2026-08-03", detectedAt: "08:40", entryAt: "09:26", peakAt: "09:55", peakReturnPct: 34.1, finalReturnPct: 32.2, sampleCount: 11 },
    { symbol: "AGEN", date: "2026-08-02", detectedAt: "08:05", entryAt: "09:25", peakAt: "15:40", peakReturnPct: 84.0, finalReturnPct: 82.7, sampleCount: 78 },
  ],

  missionControl: [
    { process: "scan_worker (premarket)", state: "Ejecutándose", lastHeartbeat: "hace 12 s", cpu: "4.1%", mem: "180 MB" },
    { process: "Memory Engine -- recalibración diaria", state: "Finalizado", lastHeartbeat: "hoy 04:00", cpu: "--", mem: "--" },
    { process: "Validación histórica V2 (heredado)", state: "Ejecutándose", lastHeartbeat: "hace 3 min", cpu: "2.0%", mem: "310 MB" },
  ],

  config: {
    refreshIntervalSeconds: 300,
    sealWindow: "09:25 - 09:30 ET",
    marketHours: "Premarket 04:00-09:30 · Regular 09:30-16:00 · Afterhours 16:00-20:00 (huso horario de Nueva York)",
    explosionThresholdPct: 10.0,
    falseBreakoutCeilingPct: 5.0,
    microCapCeiling: "$300M",
  },
};
