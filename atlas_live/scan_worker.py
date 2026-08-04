"""Motor de refresco de Atlas Live.

Recorre un watchlist tomado del Universo Racional, calcula el ranking
completo usando exclusivamente Atlas Core (Data Collector, Atlas Score,
Momentum Engine, Money Flow Engine, Market Context Engine, Decision
Engine), registra cada decisión, evento de mercado y patrón de
comportamiento real en la Knowledge Base vía Decision Recorder / Pattern
Registry, y cachea el resultado en memoria para que `server.py` lo sirva.

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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.models.quote import Quote
from atlas.data.providers.yahoo_finance import YahooFinanceProvider
from atlas.data.universe import Asset, get_equities, get_etfs
from atlas.decision_journal import DecisionJournal
from atlas.decision_recorder import DecisionRecorder
from atlas.engine.atlas_score import AtlasScore, calculate_atlas_score
from atlas.engine.decision_engine import COMPRAR, DESCARTAR, VIGILAR, DecisionEngine, DecisionResult
from atlas.engine.market_context_engine import MarketContextEngine
from atlas.engine.momentum_engine import MomentumResult, calculate_momentum_score
from atlas.engine.money_flow_engine import MoneyFlowEngine
from atlas.knowledge import EXPLOSION, NORMAL, KnowledgeEngine, PatternRegistry

# --- Configuración, pensada para poder ajustarse sin tocar el resto del archivo ---
WATCHLIST_EQUITIES = 150
WATCHLIST_ETFS = 50
REQUIRED_SYMBOLS = ["AAPL", "NVDA", "PLTR", "SOXL"]
MAX_WORKERS = 10
REFRESH_INTERVAL_SECONDS = 300  # 5 minutos
TOP_N = 20

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


def _build_watchlist() -> Dict[str, Asset]:
    equities = _stratified_sample(get_equities(), WATCHLIST_EQUITIES)
    etfs = _stratified_sample(get_etfs(), WATCHLIST_ETFS)
    watchlist = {asset.symbol: asset for asset in equities + etfs}
    for symbol in REQUIRED_SYMBOLS:
        if symbol not in watchlist:
            for asset in get_equities():
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


STATE = _State()


@dataclass
class _ScoredSymbol:
    """Resultado de puntuar un símbolo: la fila lista para servir por la API,
    más los objetos crudos que el registro de conocimiento necesita después.

    Separado del dict de la fila a propósito: la fila se serializa a JSON tal
    cual (no puede llevar objetos de Atlas Core adentro), y el registro de
    conocimiento pasa a un loop secuencial fuera del ThreadPoolExecutor (ver
    `run_scan_once`), así que estos objetos viajan aparte hasta ese punto.
    """

    row: Dict[str, Any]
    quote: Quote
    atlas_score: AtlasScore
    momentum_result: MomentumResult
    money_flow_score: Optional[float]
    decision_result: DecisionResult


def _score_symbol(asset: Asset, collector: DataCollector, money_flow_engine: MoneyFlowEngine,
                   context) -> Optional[_ScoredSymbol]:
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

        atr_component = atlas_score.component("atr")
        atr_score = atr_component.score if atr_component else None

        row = {
            "symbol": asset.symbol,
            "name": asset.name,
            "asset_type": asset.type,
            "price": quote.last_price,
            "change_pct": quote.change_percent,
            "relative_volume": quote.relative_volume,
            "atlas_score": atlas_score.total,
            "momentum_score": momentum_result.momentum_score,
            "money_flow_score": money_flow_score,
            "decision": decision_result.decision,
            "confidence": decision_result.confidence,
            "display_decision": _display_decision(decision_result.decision, decision_result.confidence),
            "risk_level": _risk_level(atr_score, context.vix_price if context else None),
            # Motivo corto para la fila del ranking sin tener que seleccionar el
            # símbolo: la misma condición ya calculada por Decision Engine, no
            # una nueva explicación generada aparte.
            "reason": decision_result.met_conditions[0] if decision_result.met_conditions else None,
        }
        return _ScoredSymbol(
            row=row,
            quote=quote,
            atlas_score=atlas_score,
            momentum_result=momentum_result,
            money_flow_score=money_flow_score,
            decision_result=decision_result,
        )
    except Exception:
        return None


def _event_type_for(decision: str) -> str:
    """Clasifica el evento a registrar a partir, exclusivamente, de la propia
    decisión de Decision Engine (que ya integra momentum, RVOL, gap, VWAP,
    liquidez, Atlas Score, money flow y ruptura intradía en una sola
    confianza). COMPRAR es la señal alcista de mayor convicción que Atlas ya
    calcula, así que se registra como EXPLOSION. El resto se registra como
    NORMAL: clasificar colapsos o falsas rupturas requeriría una señal de
    agotamiento que todavía no existe (motor de agotamiento, pendiente) --
    no hay que inventar un umbral nuevo para eso acá.
    """
    return EXPLOSION if decision == COMPRAR else NORMAL


def _pattern_identity(decision_result: DecisionResult) -> Optional[Tuple[str, str, str]]:
    """Deriva la identidad de un patrón de comportamiento a partir de las
    condiciones que Decision Engine ya evaluó como cumplidas para este
    símbolo en este escaneo. La combinación ordenada de condiciones (no el
    ticker) es la identidad del patrón: la misma combinación en dos símbolos
    distintos es, por diseño, el mismo patrón -- es lo que permite que una
    microcap y una acción de Racional enriquezcan el mismo comportamiento.
    Devuelve None si no hay ninguna condición destacada (nada distintivo que
    registrar).
    """
    met = sorted(decision_result.met_conditions)
    if not met:
        return None
    pattern_key = "|".join(met)
    name = " + ".join(met)
    category = decision_result.decision
    return pattern_key, name, category


def run_scan_once() -> None:
    """Ejecuta un ciclo completo de escaneo y actualiza el estado cacheado."""
    STATE.update(scanning=True, last_error=None)
    start = time.monotonic()

    try:
        collector = DataCollector(YahooFinanceProvider())
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
        pattern_registry = PatternRegistry()

        results: List[Dict[str, Any]] = []
        scored_symbols: List[_ScoredSymbol] = []
        errors = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [
                executor.submit(_score_symbol, asset, collector, money_flow_engine, market_context)
                for asset in assets
            ]
            for future in as_completed(futures):
                scored = future.result()
                if scored is None:
                    errors += 1
                else:
                    scored_symbols.append(scored)
                    results.append(scored.row)

        # Registro de conocimiento: secuencial y fuera del ThreadPoolExecutor
        # a propósito. sqlite3 no permite compartir una misma conexión entre
        # hilos (el registro dentro del pool fallaba en silencio por esto:
        # cada llamada disparaba un error tragado por el try/except de más
        # abajo, así que en la práctica nunca se guardaba nada). Lo que pesa
        # en el tiempo de escaneo son las llamadas de red de arriba, que
        # siguen 100% paralelas; este loop son solo inserts locales rápidos.
        observed_at = datetime.now(timezone.utc).isoformat()
        for scored in scored_symbols:
            try:
                recorder.record_decision(
                    quote=scored.quote, decision_result=scored.decision_result, context=market_context
                )
                recorder.record_market_event(
                    quote=scored.quote,
                    event_type=_event_type_for(scored.decision_result.decision),
                    atlas_score=scored.atlas_score,
                    momentum_result=scored.momentum_result,
                    money_flow_score=scored.money_flow_score,
                    decision_result=scored.decision_result,
                    context=market_context,
                )
                identity = _pattern_identity(scored.decision_result)
                if identity is not None:
                    pattern_key, name, category = identity
                    pattern_registry.register_pattern(pattern_key=pattern_key, name=name, category=category)
                    pattern_registry.record_observation(
                        pattern_key=pattern_key, ticker=scored.quote.symbol, observed_at=observed_at
                    )
            except Exception:
                pass  # el registro nunca debe tumbar el escaneo

        recorder.close()
        pattern_registry.close()

        # La "mejor oportunidad disponible" es la de mayor confianza
        # acumulada (el número que Decision Engine ya diseñó para resumir
        # los 8 factores), con el Atlas Score como desempate.
        results.sort(key=lambda r: (r["confidence"], r["atlas_score"]), reverse=True)
        for rank, row in enumerate(results, start=1):
            row["rank"] = rank
            row["is_top_pick"] = rank == 1

        STATE.update(
            context=context_payload,
            ranking=results[:TOP_N],
            generated_at=datetime.now(timezone.utc).isoformat(),
            scan_duration_seconds=round(time.monotonic() - start, 1),
            symbols_scanned=len(assets),
            symbols_ok=len(results),
            errors=errors,
            scanning=False,
        )
    except Exception as exc:
        STATE.update(scanning=False, last_error=f"{exc}\n{traceback.format_exc()}")


def get_symbol_detail(symbol: str) -> Dict[str, Any]:
    """Calcula el detalle completo de un símbolo bajo demanda (no cacheado)."""
    from atlas.engine.atlas_score import calculate_atlas_score as _atlas_score
    from atlas.engine.money_flow_engine import MoneyFlowEngine as _MoneyFlowEngine
    from atlas.knowledge.pattern_store import PatternStore

    collector = DataCollector(YahooFinanceProvider())
    quote = collector.get_quote(symbol)
    atlas_score = _atlas_score(symbol, collector)
    momentum_result = calculate_momentum_score(symbol, collector)

    # Reutiliza el Money Flow Engine del último escaneo en vez de repetir un
    # escaneo completo del universo (~200 símbolos) por cada clic: solo se
    # recalcula desde cero si todavía no hay ningún escaneo en caché.
    money_flow_engine = STATE.money_flow_engine
    if money_flow_engine is None:
        watchlist = _build_watchlist()
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
        "relative_volume": quote.relative_volume,
        "volume": quote.volume,
        # Niveles reales del día tal como los reporta el proveedor de datos,
        # sin ningún cálculo propio de soporte/resistencia.
        "levels": {
            "open": quote.open,
            "high": quote.high,
            "low": quote.low,
            "previous_close": quote.previous_close,
        },
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


_background_thread: Optional[threading.Thread] = None
_background_thread_lock = threading.Lock()


def _refresh_loop() -> None:
    while True:
        run_scan_once()
        time.sleep(REFRESH_INTERVAL_SECONDS)


def start_background_refresh() -> None:
    """Arranca el hilo de refresco periódico (una sola vez por proceso).

    Protegido con lock porque `atlas_live.server` lo llama a nivel de
    módulo: el import de un módulo ya es atómico dentro de un mismo
    proceso, pero el lock evita una doble arrancada si en el futuro se
    invoca también desde otro punto (p. ej. un endpoint). Cada proceso
    worker de gunicorn ejecuta esto una sola vez de todas formas: por
    diseño se despliega con `--workers 1`, porque `STATE` es un caché en
    memoria de un solo proceso (con más de un worker cada uno escanearía
    por separado y las respuestas serían inconsistentes entre requests).
    """
    global _background_thread
    with _background_thread_lock:
        if _background_thread is not None:
            return
        _background_thread = threading.Thread(target=_refresh_loop, daemon=True)
        _background_thread.start()
