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

import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.providers.base import ProviderError
from atlas.data.universe import Asset, get_equities, get_etfs
from atlas.decision_journal import DecisionJournal
from atlas.decision_recorder import DecisionRecorder
from atlas.engine.atlas_score import calculate_atlas_score
from atlas.engine.decision_engine import COMPRAR, DESCARTAR, VIGILAR, DecisionEngine
from atlas.engine.market_context_engine import MarketContextEngine
from atlas.engine.momentum_engine import calculate_momentum_score
from atlas.engine.money_flow_engine import MoneyFlowEngine
from atlas.knowledge import NORMAL, KnowledgeEngine
from atlas.scanners.global_radar import GlobalRadar
from atlas_live.coverage_tracker import CoverageTracker

# --- Configuración, pensada para poder ajustarse sin tocar el resto del archivo ---
# Etapa 1 (Radar Global): recorre el Universo Racional completo (~2.577
# activos) en paralelo, sin calcular Atlas Score. GLOBAL_RADAR_MAX_WORKERS
# es el mayor de los dos cuellos de botella medidos -- subirlo mucho más
# no acelera demasiado (probado: 25 vs 40 workers da apenas 12% de mejora)
# y sube el riesgo de contención con Yahoo Finance.
GLOBAL_RADAR_MAX_WORKERS = 30
GLOBAL_RADAR_TOP_N_PER_CATEGORY = 60

# Etapa 2 (Atlas Core, sin cambios): Atlas Score, Momentum, Money Flow,
# Decision Engine, Decision Recorder sobre los candidatos del radar.
STAGE2_MAX_WORKERS = 20
MONEY_FLOW_MAX_WORKERS = 20

REQUIRED_SYMBOLS = ["AAPL", "NVDA", "PLTR", "SOXL"]
# REQUIRED_SYMBOLS ya no es una ventaja permanente: un símbolo requerido
# solo se fuerza a entrar a la Etapa 2 si GlobalRadar no lo seleccionó Y
# hace más de esta ventana que no se analiza. Si ya está cubierto, compite
# en igualdad de condiciones con el resto del universo, como cualquier otro.
REQUIRED_SYMBOLS_MIN_COVERAGE_HOURS = 24.0

# Techo objetivo de símbolos que llegan a la Etapa 2 por ciclo. Los
# candidatos de GlobalRadar (actividad real) tienen prioridad; si sobra
# lugar hasta este techo, se completa con cobertura rotativa: los
# símbolos del universo que hace más tiempo no se analizan, para que
# ninguno quede afuera para siempre.
STAGE2_TARGET_SIZE = 300

REFRESH_INTERVAL_SECONDS = 300  # 5 minutos
TOP_N = 20

# --- Persistencia del último escaneo válido en disco ---
# El usuario nunca debe ver una pantalla vacía: mientras se recalcula, el
# ranking anterior ya se conserva en memoria (STATE no se limpia hasta que
# el nuevo escaneo termina). Este archivo cubre el otro caso -- un
# reinicio del proceso -- para que el dashboard arranque con el último
# dato válido en vez de "esperando primer análisis".
CACHE_FILE = Path(__file__).parent / "last_scan_cache.json"
_CACHE_KEYS = (
    "context", "ranking", "generated_at", "scan_duration_seconds",
    "symbols_scanned", "symbols_ok", "errors", "timeout_errors", "market_session",
    "stage1_duration_seconds", "stage2_duration_seconds",
    "symbols_reviewed_stage1", "candidates_sent_stage2",
    "radar_candidates_count", "required_symbols_added", "rotation_added",
)


def _save_cache(snapshot: Dict[str, Any]) -> None:
    try:
        CACHE_FILE.write_text(
            json.dumps({k: snapshot.get(k) for k in _CACHE_KEYS}),
            encoding="utf-8",
        )
    except OSError:
        pass  # el cache es una comodidad, nunca debe tumbar un escaneo


def _load_cache() -> Dict[str, Any]:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

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


# --- Sesión de mercado ---
# `quote.session` viene de Atlas Core (Data Collector/YahooFinanceProvider)
# ya normalizado. Esto solo traduce ese valor a lo que se muestra en
# pantalla; ningún motor usa esta etiqueta para calcular nada.
SESSION_DISPLAY = {
    "PREMARKET": {"code": "PREMARKET", "emoji": "🟡", "label": "PREMARKET"},
    "REGULAR": {"code": "REGULAR", "emoji": "🟢", "label": "MERCADO ABIERTO"},
    "AFTERHOURS": {"code": "AFTERHOURS", "emoji": "🔵", "label": "AFTER HOURS"},
    "CLOSED": {"code": "CLOSED", "emoji": "⚪", "label": "MERCADO CERRADO"},
}


def _session_display(session: str) -> Dict[str, str]:
    return SESSION_DISPLAY.get(session, SESSION_DISPLAY["REGULAR"])


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


# "Riesgo alto" junto a "Sí compraría" lee como un mensaje contradictorio
# para alguien sin conocimientos de trading. La pantalla principal no
# muestra el nivel de riesgo: muestra el mismo dato traducido a "tipo de
# oportunidad", que describe el carácter del movimiento sin sonar a
# advertencia. El nivel de riesgo literal (BAJO/MEDIO/ALTO) se sigue
# calculando igual y queda disponible en el detalle técnico ("¿Por qué?").
OPPORTUNITY_TYPE_LABELS = {
    "BAJO": "Conservadora",
    "MEDIO": "Moderada",
    "ALTO": "Agresiva",
}


def _opportunity_type(risk_level: str) -> str:
    return OPPORTUNITY_TYPE_LABELS.get(risk_level, "Moderada")


def _full_universe() -> Dict[str, Asset]:
    assets = get_equities() + get_etfs()
    return {asset.symbol: asset for asset in assets}


def _is_stale(last_analyzed_at: Optional[str], min_hours: float) -> bool:
    """True si nunca se analizó, o si hace más de `min_hours` que se analizó."""
    if last_analyzed_at is None:
        return True
    try:
        last = datetime.fromisoformat(last_analyzed_at)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600.0
    return age_hours >= min_hours


def _apply_required_symbols_safety_net(
    watchlist: Dict[str, Asset], universe: Dict[str, Asset], tracker: CoverageTracker
) -> int:
    """Red de seguridad, no ventaja permanente: un símbolo de REQUIRED_SYMBOLS
    solo se fuerza a entrar si GlobalRadar no lo seleccionó Y hace tiempo
    que no se analiza. Si ya está cubierto, no se toca -- compite en
    igualdad de condiciones con el resto del universo."""
    added = 0
    for symbol in REQUIRED_SYMBOLS:
        if symbol in watchlist:
            continue
        asset = universe.get(symbol)
        if asset is None:
            continue
        if _is_stale(tracker.last_analyzed_at(symbol), REQUIRED_SYMBOLS_MIN_COVERAGE_HOURS):
            watchlist[symbol] = asset
            added += 1
    return added


def _fill_rotating_coverage(
    watchlist: Dict[str, Asset], universe: Dict[str, Asset], tracker: CoverageTracker, target_size: int
) -> int:
    """Completa hasta `target_size` con los símbolos del universo que hace
    más tiempo no se analizan (o nunca), para que ninguno quede afuera
    para siempre solo por no tener actividad destacada hoy."""
    remaining_slots = target_size - len(watchlist)
    if remaining_slots <= 0:
        return 0
    pool = [symbol for symbol in universe if symbol not in watchlist]
    chosen = tracker.rank_by_staleness(pool)[:remaining_slots]
    for symbol in chosen:
        watchlist[symbol] = universe[symbol]
    return len(chosen)


def _run_global_radar(collector: DataCollector) -> "tuple[Dict[str, Asset], Dict[str, Any]]":
    """Etapa 1 (Radar Global): recorre el Universo Racional completo y arma el watchlist de candidatos.

    No calcula Atlas Score -- eso sigue siendo exclusivo de la Etapa 2. El
    objetivo no es analizar más símbolos: es no dejar ninguna oportunidad
    fuera por un muestreo fijo, sea una microcap, una empresa grande o un
    ETF apalancado -- y que ningún símbolo quede permanentemente afuera
    del aprendizaje (REQUIRED_SYMBOLS ya no es una ventaja fija; cobertura
    rotativa completa lo que sobra hasta STAGE2_TARGET_SIZE).
    """
    start = time.monotonic()
    radar = GlobalRadar(
        collector=collector,
        max_workers=GLOBAL_RADAR_MAX_WORKERS,
        top_n_per_category=GLOBAL_RADAR_TOP_N_PER_CATEGORY,
    )
    candidates = radar.scan()

    watchlist: Dict[str, Asset] = {
        c.symbol: Asset(symbol=c.symbol, name=c.name or c.symbol, type=c.asset_type)
        for c in candidates
    }
    radar_candidates_count = len(watchlist)

    universe = _full_universe()
    tracker = CoverageTracker()
    try:
        required_added = _apply_required_symbols_safety_net(watchlist, universe, tracker)
        rotation_added = _fill_rotating_coverage(watchlist, universe, tracker, STAGE2_TARGET_SIZE)
    finally:
        tracker.close()

    stats = {
        "stage1_duration_seconds": round(time.monotonic() - start, 1),
        "symbols_reviewed_stage1": radar.last_scan_symbols_reviewed,
        "candidates_sent_stage2": len(watchlist),
        "radar_candidates_count": radar_candidates_count,
        "required_symbols_added": required_added,
        "rotation_added": rotation_added,
    }
    return watchlist, stats


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
        # Cuántos de esos errores fueron específicamente por timeout de red
        # (Yahoo Finance sin responder a tiempo), no por otra causa. Se
        # reinicia al comenzar cada escaneo.
        self.timeout_errors: int = 0
        # Métricas del radar de dos etapas: Etapa 1 (Radar Global, sin Atlas
        # Score, sobre el Universo Racional completo) y Etapa 2 (pipeline
        # actual de Atlas Core, sin cambios, sobre los candidatos).
        self.stage1_duration_seconds: Optional[float] = None
        self.stage2_duration_seconds: Optional[float] = None
        self.symbols_reviewed_stage1: int = 0
        self.candidates_sent_stage2: int = 0
        # Desglose de candidates_sent_stage2: cuántos vinieron de GlobalRadar
        # por actividad real, cuántos por la red de seguridad de
        # REQUIRED_SYMBOLS, y cuántos por cobertura rotativa.
        self.radar_candidates_count: int = 0
        self.required_symbols_added: int = 0
        self.rotation_added: int = 0
        self.scanning: bool = False
        self.last_error: Optional[str] = None
        # Sesión de mercado del último escaneo (derivada de las cotizaciones
        # ya obtenidas, sin ninguna llamada extra): todos los símbolos de EE.UU.
        # comparten la misma sesión de mercado en un momento dado.
        self.market_session: Dict[str, Any] = SESSION_DISPLAY["REGULAR"]
        # Cache interna (no forma parte del snapshot JSON): el Money Flow
        # Engine ya escaneado, para que get_symbol_detail() lo reutilice.
        self.money_flow_engine: Optional[MoneyFlowEngine] = None

        # Precarga el último escaneo válido guardado en disco, para que un
        # reinicio del proceso no muestre una pantalla vacía.
        cached = _load_cache()
        for key, value in cached.items():
            if value is not None:
                setattr(self, key, value)

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
                "timeout_errors": self.timeout_errors,
                "stage1_duration_seconds": self.stage1_duration_seconds,
                "stage2_duration_seconds": self.stage2_duration_seconds,
                "symbols_reviewed_stage1": self.symbols_reviewed_stage1,
                "candidates_sent_stage2": self.candidates_sent_stage2,
                "radar_candidates_count": self.radar_candidates_count,
                "required_symbols_added": self.required_symbols_added,
                "rotation_added": self.rotation_added,
                "scanning": self.scanning,
                "last_error": self.last_error,
                "market_session": self.market_session,
            }

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            for key, value in kwargs.items():
                setattr(self, key, value)

    def increment_timeout_errors(self) -> None:
        with self._lock:
            self.timeout_errors += 1


STATE = _State()


def _score_symbol(asset: Asset, collector: DataCollector, money_flow_engine: MoneyFlowEngine,
                   recorder: DecisionRecorder, context) -> Optional[Dict[str, Any]]:
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
        risk_level = _risk_level(atr_score, context.vix_price if context else None)

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
            "risk_level": risk_level,
            "opportunity_type": _opportunity_type(risk_level),
            "session": quote.session,
            "session_display": _session_display(quote.session),
            "is_preliminary": quote.session == "PREMARKET",
        }
    except ProviderError as exc:
        if "Tiempo de espera agotado" in str(exc):
            STATE.increment_timeout_errors()
        return None
    except Exception:
        return None


def run_scan_once() -> None:
    """Ejecuta un ciclo completo de escaneo (Radar Global + Atlas Core) y actualiza el estado cacheado."""
    STATE.update(scanning=True, last_error=None, timeout_errors=0)
    cycle_start = time.monotonic()

    try:
        collector = DataCollector()

        # Etapa 1: Radar Global sobre el Universo Racional completo. Ya deja
        # las cotizaciones de cada candidato en la caché de `collector`, así
        # que la Etapa 2 no necesita repetir ese fetch.
        watchlist, radar_stats = _run_global_radar(collector)
        assets = list(watchlist.values())

        stage2_start = time.monotonic()

        # Etapa 2: pipeline de Atlas Core sin cambios, sobre los candidatos.
        money_flow_engine = MoneyFlowEngine(
            collector=collector, universe_provider=lambda: watchlist, max_workers=MONEY_FLOW_MAX_WORKERS
        )
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

        results: List[Dict[str, Any]] = []
        errors = 0
        with ThreadPoolExecutor(max_workers=STAGE2_MAX_WORKERS) as executor:
            futures = [
                executor.submit(_score_symbol, asset, collector, money_flow_engine, recorder, market_context)
                for asset in assets
            ]
            for future in as_completed(futures):
                row = future.result()
                if row is None:
                    errors += 1
                else:
                    results.append(row)

        recorder.close()

        # Registra en la cobertura rotativa qué símbolos se analizaron
        # recién ahora, para que la próxima Etapa 1 sepa que están al día.
        tracker = CoverageTracker()
        try:
            tracker.mark_analyzed([row["symbol"] for row in results])
        finally:
            tracker.close()

        # La "mejor oportunidad disponible" es la de mayor confianza
        # acumulada (el número que Decision Engine ya diseñó para resumir
        # los 8 factores), con el Atlas Score como desempate.
        results.sort(key=lambda r: (r["confidence"], r["atlas_score"]), reverse=True)
        for rank, row in enumerate(results, start=1):
            row["rank"] = rank
            row["is_top_pick"] = rank == 1

        market_session = _session_display(results[0]["session"]) if results else SESSION_DISPLAY["REGULAR"]

        stage2_duration = round(time.monotonic() - stage2_start, 1)

        STATE.update(
            context=context_payload,
            ranking=results[:TOP_N],
            generated_at=datetime.now(timezone.utc).isoformat(),
            scan_duration_seconds=round(time.monotonic() - cycle_start, 1),
            stage1_duration_seconds=radar_stats["stage1_duration_seconds"],
            stage2_duration_seconds=stage2_duration,
            symbols_reviewed_stage1=radar_stats["symbols_reviewed_stage1"],
            candidates_sent_stage2=radar_stats["candidates_sent_stage2"],
            radar_candidates_count=radar_stats["radar_candidates_count"],
            required_symbols_added=radar_stats["required_symbols_added"],
            rotation_added=radar_stats["rotation_added"],
            symbols_scanned=len(assets),
            symbols_ok=len(results),
            errors=errors,
            market_session=market_session,
            scanning=False,
        )
        _save_cache(STATE.snapshot())
    except Exception as exc:
        STATE.update(scanning=False, last_error=f"{exc}\n{traceback.format_exc()}")


def get_symbol_detail(symbol: str) -> Dict[str, Any]:
    """Calcula el detalle completo de un símbolo bajo demanda (no cacheado)."""
    from atlas.engine.atlas_score import calculate_atlas_score as _atlas_score
    from atlas.engine.money_flow_engine import MoneyFlowEngine as _MoneyFlowEngine
    from atlas.knowledge.pattern_store import PatternStore

    collector = DataCollector()
    quote = collector.get_quote(symbol)
    atlas_score = _atlas_score(symbol, collector)
    momentum_result = calculate_momentum_score(symbol, collector)

    # Reutiliza el Money Flow Engine del último escaneo en vez de repetir un
    # escaneo completo del universo (~200 símbolos) por cada clic: solo se
    # recalcula desde cero si todavía no hay ningún escaneo en caché.
    money_flow_engine = STATE.money_flow_engine
    if money_flow_engine is None:
        watchlist, _ = _run_global_radar(collector)
        money_flow_engine = _MoneyFlowEngine(
            collector=collector, universe_provider=lambda: watchlist, max_workers=MONEY_FLOW_MAX_WORKERS
        )
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
        "opportunity_type": _opportunity_type(risk_level),
        "session": quote.session,
        "session_display": _session_display(quote.session),
        "is_preliminary": quote.session == "PREMARKET",
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


def _refresh_loop() -> None:
    while True:
        run_scan_once()
        time.sleep(REFRESH_INTERVAL_SECONDS)


def start_background_refresh() -> None:
    """Arranca el hilo de refresco periódico (una sola vez por proceso)."""
    global _background_thread
    if _background_thread is not None:
        return
    _background_thread = threading.Thread(target=_refresh_loop, daemon=True)
    _background_thread.start()
