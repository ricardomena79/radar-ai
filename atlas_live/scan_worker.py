"""Motor de refresco de Atlas Live.

Recorre un watchlist tomado del Universo Racional, calcula el ranking
completo usando exclusivamente Atlas Core (Data Collector, Atlas Score,
Momentum Engine, Money Flow Engine, Market Context Engine, Decision
Engine), registra cada decisión real en la Knowledge Base vía Decision
Recorder, y cachea el resultado en memoria para que `server.py` lo sirva.

Esta es la primera vez que Decision Recorder se usa en un flujo real (no
de prueba): las bases que escribe son las reales por defecto
(`atlas_knowledge.db`, `decision_journal.db`), no las de prueba.

No contiene lógica de negocio propia: cada número que produce viene de
una llamada directa a un componente de Atlas Core ya construido y
aprobado. Lo único que hace este módulo es orquestar el orden de esas
llamadas y darle forma de JSON al resultado.
"""

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import yfinance as yf

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.universe import Asset, get_equities, get_etfs
from atlas.decision_journal import DecisionJournal
from atlas.decision_recorder import DecisionRecorder
from atlas.engine.atlas_score import calculate_atlas_score
from atlas.engine.decision_engine import COMPRAR, DESCARTAR, VIGILAR, DecisionEngine
from atlas.engine.market_context_engine import MarketContextEngine
from atlas.engine.momentum_engine import calculate_momentum_score
from atlas.engine.money_flow_engine import MoneyFlowEngine
from atlas.knowledge import NORMAL, KnowledgeEngine

from atlas_live import explosive_diagnostics, explosive_engine
from atlas_live.data_fusion.registry import get_default_provider
from atlas_live.explosive_config import load_config as load_explosive_config
from atlas_live.mission_control import timeline

# --- Configuración, pensada para poder ajustarse sin tocar el resto del archivo ---
WATCHLIST_EQUITIES = 150
WATCHLIST_ETFS = 50
# Piso fijo, siempre escaneado. Ampliado (2026-08-05) tras confirmar en vivo
# el 2026-08-04 que movers reales (AMD, TSLA, MSTR, COIN) quedaban fuera del
# muestreo estratificado y nunca eran vistos por Atlas, sin importar cuánto
# se movieran -- ver _prefilter_movers() más abajo para el resto del arreglo.
REQUIRED_SYMBOLS = ["AAPL", "NVDA", "PLTR", "SOXL", "AMD", "TSLA", "MSTR", "COIN"]
MAX_WORKERS = 10
REFRESH_INTERVAL_SECONDS = 300  # 5 minutos
TOP_N = 20

# --- Pre-filtro dinámico de movimiento real (2026-08-05) ---
#
# Problema que resuelve: _stratified_sample() (más abajo) siempre elegía las
# mismas posiciones fijas del universo (cada símbolo N), sin relación con
# quién se estaba moviendo hoy -- un mover real fuera de esas posiciones era
# invisible para Atlas indefinidamente, sin importar su volumen o su gap.
#
# Diagnóstico previo a este cambio (no se adivinó el diseño): consultar el
# universo completo (2575 símbolos) con datos reales cada ciclo es caro --
# se midió en vivo entre ~270s y ~700s según el método, comparable o peor
# que el escaneo completo actual (~266s). Por eso el pre-filtro NO evalúa
# el universo completo en cada ciclo: evalúa un bloque rotativo (ver
# _next_prefilter_chunk), así el costo agregado por ciclo es acotado
# (~40s medidos) y, a lo largo de varios ciclos, termina recorriendo el
# universo completo -- mismo criterio de "rotación para cobertura" ya
# discutido antes de este cambio.
#
# _lightweight_change_pct() usa yf.Ticker().fast_info directamente, NO
# collector.get_quote()/MultiProvider: es una consulta deliberadamente más
# barata (precio + cierre anterior, nada más) solo para RANKEAR qué
# símbolos merecen un lugar en el watchlist real. No construye ningún
# Quote -- el precio/volumen que Atlas termina reportando para un símbolo
# siempre sigue viniendo exclusivamente de _score_symbol() vía el Data
# Fusion Engine, sin excepción. Esto no reabre la regla de arquitectura ya
# declarada (YahooFinanceLiveProvider como único punto autorizado para
# construir un Quote) porque este pre-filtro nunca construye uno.
PREFILTER_CHUNK_SIZE = 400
PREFILTER_WORKERS = 30
TOP_MOVERS_EQUITIES = 30
TOP_MOVERS_ETFS = 10

_prefilter_cursor = 0

# --- Traducción de la decisión de Atlas Core a lenguaje de presentación ---
# Esto NO reclasifica nada: toma la misma decisión y confianza que ya
# calculó Decision Engine (COMPRAR/VIGILAR/DESCARTAR, 0-100) y les da una
# etiqueta de 4 niveles para pantalla, en primera persona ("si Atlas
# invirtiera su propio dinero"). VIGILAR se divide en dos matices
# (Esperaría/Solo observaría) según qué tan cerca esté la confianza del
# umbral de compra -- es una re-etiquetación del mismo número, no un
# nuevo cálculo.
VIGILAR_SPLIT_CONFIDENCE = 50.0


def _display_decision(decision: str, confidence: float) -> Dict[str, str]:
    if decision == COMPRAR:
        return {"code": "SI_COMPRARIA", "emoji": "🟢", "label": "Sí compraría"}
    if decision == DESCARTAR:
        return {"code": "NO_COMPRARIA", "emoji": "🔴", "label": "No compraría"}
    if confidence >= VIGILAR_SPLIT_CONFIDENCE:
        return {"code": "ESPERARIA", "emoji": "🟡", "label": "Esperaría"}
    return {"code": "SOLO_OBSERVARIA", "emoji": "🟠", "label": "Solo observaría"}


# --- Nivel de riesgo de presentación ---
# Reutiliza dos números que Atlas Core ya calcula (el componente ATR del
# Atlas Score, que mide volatilidad relativa al precio, y el VIX del
# contexto de mercado) y los agrupa en 3 baldes fijos para pantalla. No es
# un indicador nuevo: es una lectura de umbrales fijos sobre salidas que
# Atlas Core ya produce.
RISK_ATR_HIGH = 70.0
RISK_ATR_LOW = 30.0
RISK_VIX_HIGH = 25.0
RISK_VIX_LOW = 18.0


def _risk_level(atr_score: Optional[float], vix_price: Optional[float]) -> str:
    if atr_score is None:
        return "MEDIO"
    if atr_score >= RISK_ATR_HIGH or (vix_price is not None and vix_price >= RISK_VIX_HIGH):
        return "ALTO"
    if atr_score <= RISK_ATR_LOW and (vix_price is None or vix_price < RISK_VIX_LOW):
        return "BAJO"
    return "MEDIO"


def _stratified_sample(assets: List[Asset], count: int) -> List[Asset]:
    """Toma `count` elementos distribuidos a lo largo de toda la lista (no solo los primeros)."""
    if len(assets) <= count:
        return list(assets)
    step = max(1, len(assets) // count)
    return assets[::step][:count]


def _next_prefilter_chunk() -> List[Asset]:
    """Bloque rotativo del universo completo para el pre-filtro de movimiento.

    Avanza un cursor global en cada llamada (una por ciclo de escaneo real,
    ver run_scan_once) para que, a lo largo de varios ciclos, el pre-filtro
    termine cubriendo el universo completo en vez de evaluar siempre el
    mismo subconjunto -- ver el comentario de PREFILTER_CHUNK_SIZE arriba
    para el motivo de por qué no se evalúa todo junto en un solo ciclo.
    """
    global _prefilter_cursor
    universe = get_equities() + get_etfs()
    if not universe:
        return []
    start = _prefilter_cursor % len(universe)
    end = start + PREFILTER_CHUNK_SIZE
    chunk = universe[start:end] if end <= len(universe) else universe[start:] + universe[: end - len(universe)]
    _prefilter_cursor = end % len(universe)
    return chunk


def _lightweight_change_pct(asset: Asset) -> Optional[Tuple[Asset, float]]:
    """Consulta liviana de movimiento -- ver el comentario junto a
    PREFILTER_CHUNK_SIZE: deliberadamente no construye un Quote ni pasa por
    el Data Fusion Engine, solo pide precio + cierre anterior para rankear
    candidatos a entrar al watchlist real."""
    try:
        fast_info = yf.Ticker(asset.symbol).fast_info
        last_price = fast_info.get("lastPrice")
        previous_close = fast_info.get("previousClose")
        if not last_price or not previous_close:
            return None
        change_pct = (last_price - previous_close) / previous_close * 100
        return asset, change_pct
    except Exception:
        return None


def _prefilter_movers(chunk: List[Asset]) -> List[Asset]:
    """Evalúa el bloque rotativo actual y devuelve los símbolos con mayor
    movimiento real (|% cambio|) de hoy, separados por tipo. Un símbolo sin
    datos (fallo de red, delisted, etc.) simplemente no compite -- no
    interrumpe al resto ni tumba el ciclo."""
    if not chunk:
        return []
    results: List[Tuple[Asset, float]] = []
    with ThreadPoolExecutor(max_workers=PREFILTER_WORKERS) as executor:
        futures = [executor.submit(_lightweight_change_pct, asset) for asset in chunk]
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                results.append(result)

    equities = sorted((r for r in results if r[0].type == "EQUITY"), key=lambda r: abs(r[1]), reverse=True)
    etfs = sorted((r for r in results if r[0].type == "ETF"), key=lambda r: abs(r[1]), reverse=True)
    return [a for a, _ in equities[:TOP_MOVERS_EQUITIES]] + [a for a, _ in etfs[:TOP_MOVERS_ETFS]]


def _build_watchlist(include_dynamic_movers: bool = True) -> Dict[str, Asset]:
    """Arma el watchlist real que se escanea cada ciclo, en tres capas:

    1. Muestreo estratificado de siempre (base de diversidad, sin cambios).
    2. Movers reales detectados por el pre-filtro rotativo -- lo nuevo de
       este cambio, ver PREFILTER_CHUNK_SIZE arriba.
    3. REQUIRED_SYMBOLS -- piso fijo, exactamente igual que antes.

    `include_dynamic_movers=False` se usa desde el único call site que no
    necesita la detección de movers (get_symbol_detail(), fallback de Money
    Flow Engine cuando todavía no hay ningún escaneo en caché) para no
    pagar el costo del pre-filtro en un camino que no lo necesita.
    """
    equities = _stratified_sample(get_equities(), WATCHLIST_EQUITIES)
    etfs = _stratified_sample(get_etfs(), WATCHLIST_ETFS)
    watchlist = {asset.symbol: asset for asset in equities + etfs}

    if include_dynamic_movers:
        for asset in _prefilter_movers(_next_prefilter_chunk()):
            watchlist[asset.symbol] = asset

    for symbol in REQUIRED_SYMBOLS:
        if symbol not in watchlist:
            # Busca en equities y ETFs -- SOXL (ya en REQUIRED_SYMBOLS antes
            # de este cambio) es un ETF apalancado, no una acción: buscar
            # solo en get_equities() lo dejaba sin garantía real pese a
            # figurar en la lista (bug preexistente, corregido acá).
            for asset in get_equities() + get_etfs():
                if asset.symbol == symbol:
                    watchlist[symbol] = asset
                    break
    return watchlist


class _State:
    """Último resultado de escaneo, protegido por lock para lectura/escritura concurrente."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.context: Optional[Dict[str, Any]] = None
        self.ranking: List[Dict[str, Any]] = []
        self.generated_at: Optional[str] = None
        self.scan_duration_seconds: Optional[float] = None
        self.symbols_scanned: int = 0
        self.symbols_ok: int = 0
        self.errors: int = 0
        self.scanning: bool = False
        self.last_error: Optional[str] = None
        # Cache interna (no forma parte del snapshot JSON): el Money Flow
        # Engine ya escaneado, para que get_symbol_detail() lo reutilice.
        self.money_flow_engine: Optional[MoneyFlowEngine] = None
        # Cache interna del modo Diagnóstico: se sirve bajo demanda (no en
        # cada poll de /api/ranking) vía get_explosive_diagnostics().
        self.explosive_diagnostics: Optional[Dict[str, Any]] = None
        # Cabina del Piloto, Panel 2 en adelante (2026-08-02): ranking del
        # Memory Engine (Ranking Score) ya serializado, servido bajo demanda
        # vía get_memory_ranking() -- no forma parte de snapshot()/`/api/ranking`
        # para no cambiar ese contrato ya en uso.
        self.memory_ranking: List[Dict[str, Any]] = []
        self.memory_ranking_generated_at: Optional[str] = None
        # Trazabilidad de precio (2026-08-02): último `marketState` de
        # Yahoo Finance detectado, para no duplicar el evento en el
        # Timeline de Mission Control en cada ciclo si no cambió.
        self.last_market_state: Optional[str] = None

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "context": self.context,
                "ranking": self.ranking,
                "generated_at": self.generated_at,
                "scan_duration_seconds": self.scan_duration_seconds,
                "symbols_scanned": self.symbols_scanned,
                "symbols_ok": self.symbols_ok,
                "errors": self.errors,
                "scanning": self.scanning,
                "last_error": self.last_error,
            }

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            for key, value in kwargs.items():
                setattr(self, key, value)

    def diagnostics_snapshot(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self.explosive_diagnostics

    def memory_ranking_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {"generated_at": self.memory_ranking_generated_at, "candidates": self.memory_ranking}


STATE = _State()


def _score_symbol(asset: Asset, collector: DataCollector, money_flow_engine: MoneyFlowEngine,
                   recorder: DecisionRecorder, context, explosive_cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        quote = collector.get_quote(asset.symbol)
        atlas_score = calculate_atlas_score(asset.symbol, collector)
        momentum_result = calculate_momentum_score(asset.symbol, collector)

        money_flow_score = None
        for group in money_flow_engine.top(10_000):
            if group.name == quote.sector:
                money_flow_score = group.money_flow_score
                break

        decision_engine = DecisionEngine(collector=collector, money_flow_engine=money_flow_engine)
        decision_result = decision_engine.decide(asset.symbol)

        try:
            recorder.record_decision(quote=quote, decision_result=decision_result, context=context)
        except Exception:
            pass  # el registro nunca debe tumbar el escaneo

        atr_component = atlas_score.component("atr")
        atr_score = atr_component.score if atr_component else None

        # Radar Explosivo: motor propio de atlas_live, independiente de
        # Decision Engine. Reutiliza el quote/momentum_result/money_flow ya
        # calculados arriba -- no repite ninguna llamada a Atlas Core.
        explosive_result = explosive_engine.evaluate(
            quote=quote,
            momentum_result=momentum_result,
            sector_money_flow_score=money_flow_score,
            config=explosive_cfg,
        )

        return {
            "symbol": asset.symbol,
            "name": asset.name,
            "asset_type": asset.type,
            "price": quote.last_price,
            "change_pct": quote.change_percent,
            "atlas_score": atlas_score.total,
            "momentum_score": momentum_result.momentum_score,
            "money_flow_score": money_flow_score,
            "decision": decision_result.decision,
            "confidence": decision_result.confidence,
            "display_decision": _display_decision(decision_result.decision, decision_result.confidence),
            "risk_level": _risk_level(atr_score, context.vix_price if context else None),
            "explosive": {
                "eligible": explosive_result.eligible,
                "score": explosive_result.score,
                "reasons": explosive_result.reasons,
                "excluded_reason": explosive_result.excluded_reason,
                "is_size_exception": explosive_result.is_size_exception,
                "failed_stage": explosive_result.failed_stage,
                "stage_trace": explosive_result.stage_trace,
                "metrics": explosive_result.metrics,
            },
        }
    except Exception:
        return None


def run_scan_once() -> None:
    """Ejecuta un ciclo completo de escaneo y actualiza el estado cacheado."""
    STATE.update(scanning=True, last_error=None)
    start = time.monotonic()

    try:
        # Etapa 0 de la unificación de interfaz (2026-08-05): Atlas Live
        # ya no depende directamente de Yahoo -- get_default_provider()
        # arma Yahoo + Finnhub de respaldo (MultiProvider), con
        # degradación segura si Finnhub no está configurado. Ver
        # atlas_live/data_fusion/registry.py.
        collector = DataCollector(get_default_provider())
        watchlist = _build_watchlist()
        assets = list(watchlist.values())

        collector.get_quotes([a.symbol for a in assets])

        money_flow_engine = MoneyFlowEngine(collector=collector, universe_provider=lambda: watchlist)
        money_flow_engine.scan()

        # Se cachea para que get_symbol_detail() no tenga que repetir un
        # escaneo completo del universo (~200 símbolos) por cada clic: el
        # money flow por sector no cambia dentro de la ventana de refresco.
        STATE.update(money_flow_engine=money_flow_engine)

        top_sector = money_flow_engine.top(1)
        leading_sector = top_sector[0].name if top_sector else None
        leading_sector_score = top_sector[0].money_flow_score if top_sector else None
        top_industries = money_flow_engine.top_industries(1)
        leading_industry = top_industries[0].name if top_industries else None

        market_context = MarketContextEngine(collector=collector).get_context(money_flow_engine=money_flow_engine)

        context_payload = {
            "spy_price": market_context.spy_price,
            "spy_change_percent": market_context.spy_change_percent,
            "qqq_price": market_context.qqq_price,
            "qqq_change_percent": market_context.qqq_change_percent,
            "vix_price": market_context.vix_price,
            "vix_change_percent": market_context.vix_change_percent,
            "btc_price": market_context.btc_price,
            "btc_change_percent": market_context.btc_change_percent,
            "leading_sector": leading_sector,
            "leading_sector_money_flow_score": leading_sector_score,
            "leading_industry": leading_industry,
            "day_of_week": market_context.day_of_week,
        }

        knowledge = KnowledgeEngine()
        journal = DecisionJournal()
        recorder = DecisionRecorder(knowledge_engine=knowledge, decision_journal=journal)

        # Se carga una sola vez por ciclo (no por símbolo) para no releer el
        # archivo de configuración del Radar Explosivo cientos de veces.
        explosive_cfg = load_explosive_config()

        results: List[Dict[str, Any]] = []
        errors = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [
                executor.submit(_score_symbol, asset, collector, money_flow_engine, recorder, market_context, explosive_cfg)
                for asset in assets
            ]
            for future in as_completed(futures):
                row = future.result()
                if row is None:
                    errors += 1
                else:
                    results.append(row)

        recorder.close()

        # La "mejor oportunidad disponible" es la de mayor confianza
        # acumulada (el número que Decision Engine ya diseñó para resumir
        # los 8 factores), con el Atlas Score como desempate.
        results.sort(key=lambda r: (r["confidence"], r["atlas_score"]), reverse=True)
        for rank, row in enumerate(results, start=1):
            row["rank"] = rank
            row["is_top_pick"] = rank == 1

        # Diagnóstico del Radar Explosivo: se arma sobre TODO `results` (el
        # universo escaneado completo), no sobre `results[:TOP_N]`, porque
        # el embudo tiene que reflejar cuántos símbolos hay en cada etapa
        # de verdad, no solo los que entraron al ranking de Decision Engine.
        diagnostics = explosive_diagnostics.build_diagnostics(results, errors)

        # Integración en tiempo real del Memory Engine (Ranking Score +
        # Prediction Journal) -- envuelta en su propio try/except, igual
        # que record_decision() más arriba: el Memory Engine nunca debe
        # tumbar el escaneo principal. No modifica nada de lo anterior, solo
        # lee `results` ya calculado. Ver atlas_live/memory/live_integration.py.
        memory_ranking_serialized: List[Dict[str, Any]] = []
        try:
            from atlas_live.memory import live_integration
            live_integration.run_live_cycle(results)
            # Cabina del Piloto, Panel 2 en adelante: mismo ranking que ya se
            # usa para el snapshot dinámico del Prediction Journal, servido
            # también para la interfaz -- ninguna llamada ni cálculo nuevo.
            memory_ranking_serialized = [
                live_integration.serialize_ranked_candidate(c)
                for c in live_integration.build_live_ranking(results)
            ]
        except Exception:
            pass

        # Trazabilidad de precio (2026-08-02): registra en el Timeline de
        # Mission Control el `marketState` que Yahoo Finance reportó en
        # este ciclo, solo si cambió respecto al último ciclo -- mismo
        # criterio que heartbeat.py ("solo transiciones reales, no cada
        # repetición del mismo estado"). Nunca debe tumbar el escaneo.
        detected_market_state = next(
            (r["explosive"]["metrics"].get("market_state") for r in results if r.get("explosive")), None,
        )
        if detected_market_state and detected_market_state != STATE.last_market_state:
            try:
                timeline.record_event(
                    run_id="SCAN_WORKER", process_type="scan_worker", label="Escaneo en vivo",
                    event_type="state_changed", severity="INFO",
                    message=f"Sesión de mercado detectada por Yahoo Finance: {detected_market_state}",
                    metadata={"market_state": detected_market_state, "previous_market_state": STATE.last_market_state},
                )
            except Exception:
                pass
            STATE.update(last_market_state=detected_market_state)

        STATE.update(
            context=context_payload,
            ranking=results[:TOP_N],
            generated_at=datetime.now(timezone.utc).isoformat(),
            scan_duration_seconds=round(time.monotonic() - start, 1),
            symbols_scanned=len(assets),
            symbols_ok=len(results),
            errors=errors,
            scanning=False,
            explosive_diagnostics=diagnostics,
            memory_ranking=memory_ranking_serialized,
            memory_ranking_generated_at=datetime.now(timezone.utc).isoformat() if memory_ranking_serialized else STATE.memory_ranking_generated_at,
        )
    except Exception as exc:
        STATE.update(scanning=False, last_error=f"{exc}\n{traceback.format_exc()}")


def get_symbol_detail(symbol: str) -> Dict[str, Any]:
    """Calcula el detalle completo de un símbolo bajo demanda (no cacheado)."""
    from atlas.engine.atlas_score import calculate_atlas_score as _atlas_score
    from atlas.engine.money_flow_engine import MoneyFlowEngine as _MoneyFlowEngine
    from atlas.knowledge.pattern_store import PatternStore

    collector = DataCollector(get_default_provider())
    quote = collector.get_quote(symbol)
    atlas_score = _atlas_score(symbol, collector)
    momentum_result = calculate_momentum_score(symbol, collector)

    # Reutiliza el Money Flow Engine del último escaneo en vez de repetir un
    # escaneo completo del universo (~200 símbolos) por cada clic: solo se
    # recalcula desde cero si todavía no hay ningún escaneo en caché.
    money_flow_engine = STATE.money_flow_engine
    if money_flow_engine is None:
        watchlist = _build_watchlist(include_dynamic_movers=False)
        money_flow_engine = _MoneyFlowEngine(collector=collector, universe_provider=lambda: watchlist)
        money_flow_engine.scan()

    context = MarketContextEngine(collector=collector).get_context(sector=quote.sector, money_flow_engine=money_flow_engine)
    decision_engine = DecisionEngine(collector=collector, money_flow_engine=money_flow_engine)
    decision_result = decision_engine.decide(symbol)

    atr_component = atlas_score.component("atr")
    risk_level = _risk_level(atr_component.score if atr_component else None, context.vix_price)

    knowledge = KnowledgeEngine()
    pattern_store = PatternStore(knowledge.events)
    similar_events = []
    try:
        recent_events = knowledge.events.get_events(ticker=symbol, limit=1)
        if recent_events:
            for event, similarity in pattern_store.find_similar(recent_events[0], top_n=5):
                similar_events.append({
                    "ticker": event.ticker, "event_type": event.event_type,
                    "similarity": similarity, "date": event.date,
                })
    except Exception:
        pass
    knowledge.close()

    return {
        "symbol": symbol,
        "name": quote.name,
        "price": quote.last_price,
        "change_pct": quote.change_percent,
        "sector": quote.sector,
        "industry": quote.industry,
        "atlas_score": {
            "total": atlas_score.total,
            "components": [
                {"name": c.name, "score": c.score, "weight": c.weight,
                 "weighted_score": c.weighted_score, "explanation": c.explanation}
                for c in atlas_score.components
            ],
        },
        "momentum": {
            "total": momentum_result.momentum_score,
            "components": [
                {"name": c.name, "score": c.score, "weight": c.weight,
                 "weighted_score": c.weighted_score, "explanation": c.explanation}
                for c in momentum_result.components
            ],
        },
        "decision": {
            "decision": decision_result.decision,
            "confidence": decision_result.confidence,
            "mode": decision_result.mode,
            "met_conditions": decision_result.met_conditions,
            "missing_conditions": decision_result.missing_conditions,
            "next_events": decision_result.next_events,
            "unavailable_conditions": decision_result.unavailable_conditions,
        },
        "display_decision": _display_decision(decision_result.decision, decision_result.confidence),
        "risk_level": risk_level,
        "context_used": {
            "spy_price": context.spy_price, "spy_change_percent": context.spy_change_percent,
            "qqq_price": context.qqq_price, "vix_price": context.vix_price,
            "btc_price": context.btc_price, "sector_etf_symbol": context.sector_etf_symbol,
            "leading_sector": context.leading_sector, "leading_industry": context.leading_industry,
            "sector_money_flow_score": context.sector_money_flow_score,
            "day_of_week": context.day_of_week, "earnings_season": context.earnings_season,
        },
        "similar_patterns": similar_events,
        "antipatterns_note": (
            "Research Lab todavía no tiene lógica de descubrimiento de antipatrones "
            "implementada (interfaz declarada, sin datos suficientes acumulados todavía)."
        ),
    }


def get_explosive_diagnostics() -> Optional[Dict[str, Any]]:
    """Diagnóstico del último escaneo completo, o None si todavía no corrió ninguno."""
    return STATE.diagnostics_snapshot()


def get_memory_ranking() -> Dict[str, Any]:
    """Ranking del Memory Engine (Ranking Score) del último ciclo -- Cabina
    del Piloto, Panel 2 en adelante. `candidates` queda vacío hasta que
    corra el primer ciclo completo."""
    return STATE.memory_ranking_snapshot()


_background_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


def _refresh_loop() -> None:
    while not _stop_event.is_set():
        run_scan_once()
        # `wait()` en vez de `sleep()`: si se pide detener a mitad de la
        # espera, el hilo despierta enseguida en vez de tardar hasta
        # REFRESH_INTERVAL_SECONDS -- necesario para que el cierre
        # prolijo (`request_stop` + `wait_until_stopped`) sea rápido.
        _stop_event.wait(REFRESH_INTERVAL_SECONDS)


def start_background_refresh() -> None:
    """Arranca el hilo de refresco periódico (una sola vez por proceso).

    Modo Interactivo Continuo (decisión de arquitectura, 2026-08-02): Atlas
    V1 escanea, actualiza el Ranking, el Memory Engine y el Prediction
    Journal de forma continua mientras el proceso esté vivo -- no es un
    servicio 24/7 (eso queda fuera de alcance, versión futura); es un hilo
    de fondo que vive y muere con la aplicación, con un mecanismo explícito
    para terminar de forma segura (`request_stop`/`wait_until_stopped`) en
    vez de que un cierre lo corte a mitad de una escritura."""
    global _background_thread
    if _background_thread is not None:
        return
    _stop_event.clear()
    _background_thread = threading.Thread(target=_refresh_loop, daemon=True)
    _background_thread.start()


def request_stop() -> None:
    """Pide que el ciclo de refresco termine después del cycle en curso --
    no lo interrumpe a mitad de una escritura."""
    _stop_event.set()


def wait_until_stopped(timeout: Optional[float] = None) -> bool:
    """Espera a que el hilo de fondo termine su ciclo actual y salga.
    Devuelve True si terminó dentro del timeout, False si no (en ese caso,
    el hilo sigue siendo daemon -- el proceso puede cerrar igual, pero sin
    la garantía de que el último ciclo terminó de escribir)."""
    if _background_thread is None:
        return True
    _background_thread.join(timeout)
    return not _background_thread.is_alive()
