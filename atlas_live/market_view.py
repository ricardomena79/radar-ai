"""Vista de Mercado -- ranking en vivo del universo negociable de Racional
(2026-08-29, autorizado explícitamente; ampliado a EQUITY+ETF+ETN el
2026-08-30, mismo criterio de autorización explícita).

Módulo COMPLETAMENTE AISLADO: solo lee precios vía Tradier y los presenta
ordenados por variación del día -- nunca participa en ninguna decisión de
Atlas. No importa ni es importado por `atlas_decision_core.py`,
`current_top_opportunity.py`, `top_opportunity_stability.py`,
`priority_classifier.py`, `decision_engine.py`, `scan_worker.py`,
`radar_worker.py`, ni ningún módulo de `atlas_live/memory/` o
`atlas_live/learning/`. Hilo de fondo propio, independiente de los ya
existentes.

Universo: `atlas.data.universe.load_universe()` -- TODOS los instrumentos
del snapshot de Racional (2.577 reales: 1.646 EQUITY + 929 ETF + 2 ETN,
contados directamente del loader), nunca solo EQUITY. Sin filtro de tipo
nuevo -- se toma el universo completo tal cual lo expone `Universe`, sin
tocar ese módulo. No hay cripto en este loader (Racional lo maneja aparte),
así que la exclusión de cripto pedida ya queda satisfecha por los datos
mismos, sin ningún filtro adicional.

Paralelización (pedido explícito, autorizado): los chunks de 250 símbolos
(mismo `TRADIER_CHUNK_SIZE` que ya usa `TradierProvider.get_quotes()`) se
piden en paralelo vía `ThreadPoolExecutor` -- ENCAPSULADO acá. Nunca
modifica `TradierProvider.get_quotes()`/`fetch_universe_quotes()`, que
siguen secuenciales para `radar_worker`/`catalyst_worker`, sin ningún
cambio de comportamiento para esos sistemas. Un chunk que falla no
descarta los demás -- el snapshot se sirve con lo que sí llegó, y el
conteo de fallos queda expuesto para diagnóstico.

Cadencia auto-ajustada (mismo mecanismo que `radar_worker._next_interval()`,
replicado acá de forma independiente -- sin importar ese módulo, aislamiento
total): margen de seguridad 3x sobre la duración REAL del último ciclo, con
piso/techo configurables. Nunca una cifra fija sin medir -- el primer ciclo
real determina el ritmo, igual que ya se hace en `radar_worker`.
"""

import os
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from atlas.data.providers.base import RateLimitError
from atlas.data.providers.tradier_provider import TRADIER_CHUNK_SIZE, TradierProvider
from atlas.data.providers.tradier_symbol_map import normalize
from atlas.data.universe import load_universe
from atlas_live.data_fusion.finnhub_provider import FinnhubProvider
from atlas_live.data_fusion.multi_source_resolver import (
    es_fresco_independiente,
    es_tradier_fresco,
    resolver_mejor_precio,
)
from atlas_live.data_fusion.universe_quotes import build_tradier_provider
from atlas_live.data_fusion.yahoo_finance_live_provider import YahooFinanceLiveProvider
from atlas_live.memory import market_hours


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "si", "sí")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


MARKET_VIEW_ENABLED = _env_bool("ATLAS_MARKET_VIEW_ENABLED", True)
# Piso/techo/margen: mismo criterio y mismos nombres de concepto que
# `radar_worker.py` (SWEEP_FLOOR_SECONDS/CEILING/SAFETY_MARGIN), pero
# variables y valores propios -- nunca se importa ni se comparte estado
# con ese módulo.
CYCLE_FLOOR_SECONDS = _env_float("ATLAS_MARKET_VIEW_FLOOR_SECONDS", 10.0)
CYCLE_CEILING_SECONDS = _env_float("ATLAS_MARKET_VIEW_CEILING_SECONDS", 90.0)
CYCLE_SAFETY_MARGIN = _env_float("ATLAS_MARKET_VIEW_SAFETY_MARGIN", 3.0)
IDLE_RECHECK_SECONDS = _env_float("ATLAS_MARKET_VIEW_IDLE_RECHECK_SECONDS", 90.0)
MAX_WORKERS = int(_env_float("ATLAS_MARKET_VIEW_MAX_WORKERS", 8))
SPARKLINE_MAX_POINTS = int(_env_float("ATLAS_MARKET_VIEW_SPARKLINE_POINTS", 60))
# Mercado muestra únicamente el TOP N por movimiento (pedido explícito
# 2026-08-31) -- corte de PRESENTACIÓN sobre el ranking ya ordenado
# FRESCO>STALE>SIN_DATO + change_pct DESC, nunca un filtro nuevo de qué
# se calcula (el universo completo se sigue resolviendo, cacheando y
# auditando tal cual).
MERCADO_TOP_N = int(_env_float("ATLAS_MERCADO_TOP_N", 100))

# Multi-fuente (2026-08-31, autorizado explícitamente): Tradier sigue
# siendo la fuente principal para el universo completo -- Yahoo/Finnhub
# SOLO se consultan para los símbolos que Tradier devolvió stale/sin dato
# este ciclo, nunca para las 2.577 acciones. Paralelización propia
# (ThreadPoolExecutor, igual patrón que Tradier), encapsulada acá, sin
# tocar `fetch_universe_quotes()`/`radar_worker.py`/`scan_worker.py`.
MULTI_SOURCE_ENABLED = _env_bool("ATLAS_MERCADO_MULTI_SOURCE_ENABLED", True)
# Tope de seguridad opcional (0 = sin tope, pedido explícito: "no
# optimizar prematuramente" -- se deja el knob listo para calibrar con
# la medición real, sin necesitar un cambio de código después).
FALLBACK_MAX_SYMBOLS_PER_CYCLE = int(_env_float("ATLAS_MERCADO_FALLBACK_MAX_SYMBOLS", 0))

# Circuit breaker + batching real (2026-09-01, autorizado explícitamente
# tras medir 421s/2.577 llamadas Yahoo/1.687 errores con `mercado cerrado`
# -- ver informe previo). `YahooFinanceProvider.get_quotes()` YA EXISTE
# (atlas/data/providers/yahoo_finance.py, reutilizado por
# `YahooFinanceLiveProvider` sin cambios) y comparte UNA sesión HTTP
# (`yf.Tickers`) por lote en vez de una sesión nueva por símbolo -- se
# reutiliza tal cual, nunca se reimplementa. Los lotes se envían de a
# poco (`YAHOO_BATCH_WORKERS` a la vez, nunca los 2.577 de una sola vez):
# el circuit breaker puede frenar ANTES de encolar el resto.
YAHOO_BATCH_SIZE = int(_env_float("ATLAS_MERCADO_YAHOO_BATCH_SIZE", 15))
YAHOO_BATCH_WORKERS = int(_env_float("ATLAS_MERCADO_YAHOO_BATCH_WORKERS", 4))
# Finnhub (free tier) no tiene endpoint de lote real (ver
# `FinnhubProvider.get_quotes()`, ya documentado) -- mismo circuit
# breaker, unidad = 1 símbolo en vez de un chunk.
FINNHUB_BATCH_WORKERS = int(_env_float("ATLAS_MERCADO_FINNHUB_BATCH_WORKERS", 4))
# Circuit breaker compartido entre Yahoo/Finnhub (mismo criterio: ambos
# son fallback de mejor esfuerzo). Un `RateLimitError` explícito abre el
# circuito de inmediato, sin esperar la muestra mínima -- cualquier otra
# excepción cuenta para la tasa de error, que abre el circuito recién
# tras `CIRCUIT_MIN_SAMPLE` intentos reales (nunca por 1-2 fallos
# aislados, ej. tickers delisted/OTC que Yahoo simplemente no reconoce).
CIRCUIT_MIN_SAMPLE = int(_env_float("ATLAS_MERCADO_CIRCUIT_MIN_SAMPLE", 20))
CIRCUIT_ERROR_RATE = _env_float("ATLAS_MERCADO_CIRCUIT_ERROR_RATE", 0.85)

_lock = threading.Lock()
_stop = threading.Event()
_thread: Optional[threading.Thread] = None

_state_lock = threading.Lock()
_snapshot: Dict[str, Any] = {
    "generated_at": None,
    "cycle_duration_s": None,
    "session_at_generation": None,
    "rows": [],
    "total_universe": 0,
    "resueltos": 0,
    "frescos": 0,
    "stale_cache": 0,
    "sin_datos": 0,
    "chunks_total": 0,
    "chunks_error": 0,
    # Auditoría multi-fuente (2026-08-31) -- para poder confirmar si el
    # fallback realmente se activa y cuánto cuesta, mañana con datos reales.
    "tradier_fresh": 0,
    "tradier_stale": 0,
    "yahoo_checked": 0,
    "yahoo_attempted": 0,
    "yahoo_fresh": 0,
    "yahoo_success": 0,
    "yahoo_errors": 0,
    "yahoo_aborted": 0,
    "finnhub_checked": 0,
    "finnhub_attempted": 0,
    "finnhub_fresh": 0,
    "finnhub_success": 0,
    "finnhub_errors": 0,
    "finnhub_aborted": 0,
    "cache_used": 0,
    "sin_dato_final": 0,
    "cycles_total": 0,
    "cycles_ok": 0,
    "cycles_error": 0,
    "ultimo_error": None,
}

_sparkline_lock = threading.Lock()
_sparkline_by_symbol: Dict[str, Deque[float]] = {}

# Último dato conocido por símbolo -- sobrevive entre ciclos, aislado acá
# (nunca en TradierProvider/fetch_universe_quotes/universe.py). Un batch
# roto nunca borra esto: solo se actualiza cuando llega un dato fresco.
_last_known_lock = threading.Lock()
_last_known_by_symbol: Dict[str, Dict[str, Any]] = {}

# El ranking en vivo corre en premarket/regular/afterhours -- misma fuente
# de verdad que el resto de Atlas (`market_hours.get_session()`), nunca un
# horario propio. Fuera de esas 3 sesiones (cerrado) no se consulta Tradier.
ACTIVE_SESSIONS = ("premarket", "regular", "afterhours")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_interval(last_duration: Optional[float]) -> float:
    """Auto-ajuste de cadencia -- mismo criterio ya probado en
    `radar_worker._next_interval()`: margen de seguridad sobre la
    duración REAL del último ciclo, nunca una cifra inventada."""
    if not last_duration:
        return CYCLE_FLOOR_SECONDS
    target = CYCLE_SAFETY_MARGIN * last_duration
    return max(CYCLE_FLOOR_SECONDS, min(CYCLE_CEILING_SECONDS, target))


def _is_active_session(session: Optional[str]) -> bool:
    """Premarket/regular/afterhours -- nunca depende de 'regular' a solas.
    'closed' (incluye fin de semana) es la única sesión que detiene las
    consultas a Tradier."""
    return session in ACTIVE_SESSIONS


def _fetch_chunk(tradier_provider: TradierProvider, chunk_query_symbols: List[str]):
    """Un chunk = una llamada batch real a Tradier (<=250 símbolos, mismo
    `TRADIER_CHUNK_SIZE` que el resto del proyecto). `get_quotes()` con
    <=250 símbolos hace exactamente 1 request HTTP -- nunca re-chunkea
    acá, la paralelización vive exclusivamente en `_run_cycle_body()`."""
    return tradier_provider.get_quotes(chunk_query_symbols)


def _build_finnhub_provider() -> Optional[FinnhubProvider]:
    """Mismo patrón exacto que `build_tradier_provider()` -- `None`
    (degradación segura) si no hay `FINNHUB_API_KEY` configurada. Nunca
    imprime ni loguea la key."""
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        return None
    try:
        return FinnhubProvider(key)
    except Exception:
        return None


def _empty_circuit_stats() -> Dict[str, int]:
    return {"attempted": 0, "success": 0, "errors": 0, "aborted": 0}


def _fetch_with_circuit_breaker(
    units: List[Any],
    fetch_one: Callable[[Any], Dict[str, Any]],
    unit_len: Callable[[Any], int],
    max_workers: int,
    min_sample: int,
    error_rate_threshold: float,
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """Motor genérico de fallback con circuit breaker -- reutilizado por
    Yahoo (unidad = un chunk de símbolos, sesión HTTP compartida) y
    Finnhub (unidad = 1 símbolo, sin lote real en el free tier).

    Las unidades se encolan de a `max_workers` por vez -- NUNCA todas de
    una -- para que el circuit breaker pueda frenar antes de intentar el
    resto. Un `RateLimitError` explícito abre el circuito de inmediato
    (el proveedor ya está diciendo "parame"); cualquier otra tasa de
    error solo abre el circuito después de `min_sample` intentos reales
    (nunca por un puñado de símbolos delisted/OTC que el proveedor no
    reconoce -- eso es ruido normal, no una señal de saturación). Lo que
    queda sin siquiera intentarse se cuenta como `aborted`, nunca como
    `errors` -- la distinción importa para el informe (punto 4 del
    pedido: "no quiero que Yahoo pueda mantener bloqueado Mercado")."""
    stats = _empty_circuit_stats()
    result: Dict[str, Any] = {}
    if not units:
        return result, stats

    circuit_open = False
    next_idx = 0

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(units)))) as executor:
        pending: Dict[Any, Any] = {}

        def _submit_next() -> None:
            nonlocal next_idx
            if next_idx < len(units):
                unit = units[next_idx]
                next_idx += 1
                pending[executor.submit(fetch_one, unit)] = unit

        for _ in range(min(max_workers, len(units))):
            _submit_next()

        while pending:
            done, _ = wait(list(pending.keys()), return_when=FIRST_COMPLETED)
            for future in done:
                unit = pending.pop(future)
                n = unit_len(unit)
                stats["attempted"] += n
                try:
                    by_symbol = future.result()
                    result.update(by_symbol)
                    got = len(by_symbol)
                    stats["success"] += got
                    stats["errors"] += max(0, n - got)
                except RateLimitError:
                    stats["errors"] += n
                    circuit_open = True
                except Exception:
                    stats["errors"] += n

                if not circuit_open and stats["attempted"] >= min_sample:
                    if stats["errors"] / stats["attempted"] >= error_rate_threshold:
                        circuit_open = True

                if not circuit_open:
                    _submit_next()

    if circuit_open:
        stats["aborted"] = sum(unit_len(u) for u in units[next_idx:])
    return result, stats


def _fetch_yahoo_chunk(symbols: List[str]) -> Dict[str, Any]:
    """Un chunk = UNA sesión HTTP compartida (`yf.Tickers`, ya
    implementado en `YahooFinanceProvider.get_quotes()`, heredado sin
    cambios por `YahooFinanceLiveProvider`) -- nunca una sesión nueva por
    símbolo. Si Yahoo devuelve rate-limit, `get_quotes()` ya lo convierte
    en `RateLimitError` y lo relanza de inmediato (comportamiento
    preexistente, reutilizado tal cual) -- se propaga para que el
    circuit breaker del ciclo lo cuente y frene el resto de los chunks."""
    provider = YahooFinanceLiveProvider()
    quotes = provider.get_quotes(symbols)
    return {q.symbol: q for q in quotes}


def _fetch_yahoo_batch(symbols: List[str]) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """Fallback SOLO para los símbolos ya identificados como stale en
    Tradier este ciclo (nunca para el universo completo, y nunca -- bajo
    ninguna circunstancia -- con la sesión cerrada, ver `_run_cycle_body`).
    Se agrupa en lotes de `YAHOO_BATCH_SIZE` (sesión HTTP compartida por
    lote, en vez de una sesión nueva por símbolo) y se envían de a
    `YAHOO_BATCH_WORKERS` a la vez -- el circuit breaker puede frenar
    antes de agotar la lista completa."""
    if not symbols:
        return {}, _empty_circuit_stats()
    chunks = [symbols[i:i + YAHOO_BATCH_SIZE] for i in range(0, len(symbols), YAHOO_BATCH_SIZE)]
    return _fetch_with_circuit_breaker(
        chunks, _fetch_yahoo_chunk, len, YAHOO_BATCH_WORKERS, CIRCUIT_MIN_SAMPLE, CIRCUIT_ERROR_RATE,
    )


def _fetch_one_finnhub_unit(provider: FinnhubProvider, symbol: str) -> Dict[str, Any]:
    try:
        return {symbol: provider.get_quote(symbol)}
    except Exception:
        return {}


def _fetch_finnhub_batch(symbols: List[str], provider: Optional[FinnhubProvider]) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """Fallback de último nivel -- SOLO para símbolos que ni Tradier ni
    Yahoo pudieron resolver frescos este ciclo. `provider=None` (sin
    `FINNHUB_API_KEY`) devuelve vacío de inmediato, sin intentar red --
    "mejor esfuerzo", nunca bloquea Mercado esperando Finnhub. Mismo
    circuit breaker que Yahoo (unidad = 1 símbolo -- Finnhub no tiene
    lote real en el free tier, ya documentado en `finnhub_provider.py`)."""
    if not symbols or provider is None:
        return {}, _empty_circuit_stats()
    return _fetch_with_circuit_breaker(
        symbols, lambda s: _fetch_one_finnhub_unit(provider, s), lambda _s: 1,
        FINNHUB_BATCH_WORKERS, CIRCUIT_MIN_SAMPLE, CIRCUIT_ERROR_RATE,
    )


def _run_cycle_body(symbols_override: Optional[List[str]] = None) -> float:
    t0 = time.time()
    tradier_provider = build_tradier_provider()
    if tradier_provider is None:
        raise RuntimeError("TRADIER_API_TOKEN no configurado -- Mercado no puede operar sin Tradier")

    # Universo COMPLETO de Racional (EQUITY+ETF+ETN, 2.577 reales) -- sin
    # filtro nuevo, sin agregar ni quitar ningún símbolo. `load_universe()`
    # ya es la fuente completa de `Universe`, no se toca ese módulo.
    # `symbols_override` (opcional, nunca usado por el hilo de fondo) es
    # solo para pruebas/diagnóstico reales de alcance acotado -- corre el
    # ciclo real completo (Tradier+fallback) sobre un subconjunto chico,
    # sin tocar el comportamiento por defecto (`None` = universo completo).
    instruments = list(load_universe().values())
    if symbols_override:
        override_set = set(symbols_override)
        instruments = [a for a in instruments if a.symbol in override_set]
    originals = [a.symbol for a in instruments]
    name_by_original = {a.symbol: a.name for a in instruments}

    # Normalización -- MISMA función pura ya usada por
    # `atlas_live/data_fusion/universe_quotes.py`, sin modificarla.
    # Uno-a-muchos a propósito (ej. "PBR.A"/"PBRA" -> mismo query_symbol
    # real de Tradier) -- mismo criterio ya validado en ese módulo.
    normalized: Dict[str, str] = {}
    query_to_originals: Dict[str, List[str]] = {}
    for sym in originals:
        n = normalize(sym)
        normalized[sym] = n.query_symbol
        query_to_originals.setdefault(n.query_symbol, []).append(sym)

    query_symbols = list(query_to_originals.keys())
    chunks = [
        query_symbols[i:i + TRADIER_CHUNK_SIZE]
        for i in range(0, len(query_symbols), TRADIER_CHUNK_SIZE)
    ]

    quotes_by_query: Dict[str, Any] = {}
    chunk_errors = 0
    # Paralelización -- el único cambio real frente al patrón secuencial
    # ya usado en `fetch_universe_quotes()`. Un chunk roto no descarta
    # los demás: se captura por-future, se cuenta, se sigue.
    with ThreadPoolExecutor(max_workers=max(1, min(MAX_WORKERS, len(chunks) or 1))) as executor:
        futures = {executor.submit(_fetch_chunk, tradier_provider, chunk): chunk for chunk in chunks}
        for future in as_completed(futures):
            try:
                quotes = future.result()
                for q in quotes:
                    quotes_by_query[q.symbol] = q
            except Exception:
                chunk_errors += 1

    try:
        session_now = market_hours.get_session()
    except Exception:
        session_now = None

    now_utc = datetime.now(timezone.utc)

    # --- Multi-fuente (2026-08-31, autorizado explícitamente) ---------------
    # Tradier ya resolvió arriba (quotes_by_query). Acá se identifican los
    # símbolos cuyo dato de Tradier NO es fresco este ciclo (ausente o
    # `price_is_stale=True`) -- SOLO esos pasan a Yahoo, y solo los que
    # Yahoo tampoco pudo refrescar pasan a Finnhub. Nunca se consulta
    # Yahoo/Finnhub para el universo completo.
    tradier_stale_originals: List[str] = []
    tradier_fresh_count = 0
    tradier_stale_count = 0
    for original, query_symbol in normalized.items():
        q = quotes_by_query.get(query_symbol)
        if es_tradier_fresco(q):
            tradier_fresh_count += 1
        else:
            tradier_stale_count += 1
            tradier_stale_originals.append(original)

    fallback_candidates = tradier_stale_originals
    if FALLBACK_MAX_SYMBOLS_PER_CYCLE and len(fallback_candidates) > FALLBACK_MAX_SYMBOLS_PER_CYCLE:
        fallback_candidates = fallback_candidates[:FALLBACK_MAX_SYMBOLS_PER_CYCLE]

    yahoo_by_original: Dict[str, Any] = {}
    yahoo_stats = _empty_circuit_stats()
    finnhub_by_original: Dict[str, Any] = {}
    finnhub_stats = _empty_circuit_stats()

    # CERRADO -- el fallback NUNCA se activa, sin importar cuántos
    # símbolos de Tradier estén stale (2026-09-01, autorizado
    # explícitamente tras medir 2.577 llamadas Yahoo/421s con el mercado
    # cerrado). Defensa en profundidad: `_loop()` ya evita llamar a
    # `run_market_cycle_once()` fuera de sesión activa, pero este guard
    # protege igual una invocación directa/admin/diagnóstico -- Mercado
    # nunca genera una tormenta de requests solo porque el mercado está
    # cerrado, sin importar quién dispare el ciclo.
    fallback_allowed = MULTI_SOURCE_ENABLED and _is_active_session(session_now) and bool(fallback_candidates)

    if fallback_allowed:
        yahoo_by_original, yahoo_stats = _fetch_yahoo_batch(fallback_candidates)

        still_stale = [
            o for o in fallback_candidates
            if not es_fresco_independiente(yahoo_by_original.get(o), now_utc)
        ]
        if still_stale:
            finnhub_provider = _build_finnhub_provider()
            finnhub_by_original, finnhub_stats = _fetch_finnhub_batch(still_stale, finnhub_provider)

    yahoo_fresh_count = sum(1 for o in fallback_candidates if es_fresco_independiente(yahoo_by_original.get(o), now_utc))
    finnhub_fresh_count = sum(1 for o in finnhub_by_original if es_fresco_independiente(finnhub_by_original.get(o), now_utc))

    # El universo COMPLETO (2.577) SIEMPRE se representa -- un instrumento
    # nunca desaparece del ranking solo porque su batch de Tradier falló
    # este ciclo. `resolver_mejor_precio()` decide, por símbolo, cuál de
    # las 3 fuentes (o el cache) es el mejor dato disponible -- FRESCO >
    # STALE, nunca al revés, nunca se inventa un precio (Caso E).
    rows: List[Dict[str, Any]] = []
    cache_used_count = 0
    sin_dato_count = 0
    for original, query_symbol in normalized.items():
        tradier_q = quotes_by_query.get(query_symbol)
        yahoo_q = yahoo_by_original.get(original)
        finnhub_q = finnhub_by_original.get(original)
        with _last_known_lock:
            cached = _last_known_by_symbol.get(original)

        resuelto = resolver_mejor_precio(original, tradier_q, yahoo_q, finnhub_q, now_utc, cached=cached)

        if resuelto.source == "sin_dato":
            sin_dato_count += 1
            rows.append({
                "symbol": original,
                "name": name_by_original.get(original, original),
                "price": None,
                "change_abs": None,
                "change_pct": None,
                "price_is_stale": None,
                "data_age_seconds": None,
                "sparkline": [],
                "data_status": "SIN_DATO",
                "source": "sin_dato",
            })
            continue

        if resuelto.source == "cache":
            cache_used_count += 1

        change_abs = (
            round(resuelto.price - resuelto.previous_close, 4)
            if resuelto.price is not None and resuelto.previous_close is not None else None
        )
        data_age_seconds = None
        if resuelto.timestamp is not None:
            ts = resuelto.timestamp if resuelto.timestamp.tzinfo else resuelto.timestamp.replace(tzinfo=timezone.utc)
            data_age_seconds = round((now_utc - ts).total_seconds(), 1)

        # El sparkline solo acumula puntos genuinamente frescos (nunca
        # duplica el mismo precio de cache/stale en cada ciclo).
        if resuelto.is_stale:
            with _sparkline_lock:
                sparkline = list(_sparkline_by_symbol.get(original, []))
        else:
            with _sparkline_lock:
                dq = _sparkline_by_symbol.setdefault(original, deque(maxlen=SPARKLINE_MAX_POINTS))
                if resuelto.price is not None:
                    dq.append(resuelto.price)
                sparkline = list(dq)
            # Cache de último dato conocido -- SOLO se actualiza con un
            # resultado genuinamente fresco (nunca con "cache"/"sin_dato"),
            # para que no se retroalimente a sí mismo con datos viejos.
            with _last_known_lock:
                _last_known_by_symbol[original] = {
                    "price": resuelto.price,
                    "previous_close": resuelto.previous_close,
                    "change_pct": resuelto.change_pct,
                    "price_basis": resuelto.price_basis,
                    "source": resuelto.source,
                    "cached_at": now_utc,
                }

        rows.append({
            "symbol": original,
            "name": name_by_original.get(original, original),
            "price": resuelto.price,
            "change_abs": change_abs,
            "change_pct": resuelto.change_pct,
            "price_is_stale": resuelto.is_stale,
            "data_age_seconds": data_age_seconds,
            "sparkline": sparkline,
            "data_status": "STALE" if resuelto.is_stale else "FRESCO",
            "source": resuelto.source,
            "session_dato": resuelto.session,
            "overnight_disponible": resuelto.overnight_disponible,
        })

    # Ranking -- siempre descendente. Prioridad FRESCO > STALE > SIN_DATO
    # (puntos 1-2); dentro de FRESCO/STALE, `change_pct` descendente con
    # los `None` al final de su propio grupo (punto 3-4); SIN_DATO siempre
    # último (punto 5).
    status_order = {"FRESCO": 0, "STALE": 1, "SIN_DATO": 2}
    rows.sort(key=lambda r: (
        status_order.get(r["data_status"], 3),
        r["change_pct"] is None,
        -(r["change_pct"] or 0.0),
    ))

    # Conteos de auditoría -- SIEMPRE sobre el universo COMPLETO (nunca
    # solo el top mostrado), para que el diagnóstico de salud del
    # pipeline (cuántos frescos/stale/sin_dato de los 2.577) siga siendo
    # honesto sin importar cuántas filas se terminen mostrando.
    frescos = sum(1 for r in rows if r["data_status"] == "FRESCO")
    stale_cache = sum(1 for r in rows if r["data_status"] == "STALE")
    sin_datos = sum(1 for r in rows if r["data_status"] == "SIN_DATO")

    # Mercado muestra únicamente el TOP (pedido explícito 2026-08-31,
    # "simplificar Mercado a lo que realmente necesitamos") -- el orden ya
    # es exclusivamente FRESCO>STALE>SIN_DATO + change_pct DESC (arriba),
    # nunca volumen/predicción/score. El corte es solo de PRESENTACIÓN:
    # no cambia el fallback, el resolver, ni los conteos de auditoría.
    rows = rows[:MERCADO_TOP_N]
    for i, r in enumerate(rows, start=1):
        r["rank"] = i

    duration = round(time.time() - t0, 2)
    with _state_lock:
        _snapshot["generated_at"] = _now_iso()
        _snapshot["cycle_duration_s"] = duration
        _snapshot["session_at_generation"] = session_now
        _snapshot["rows"] = rows
        _snapshot["total_universe"] = len(originals)
        _snapshot["resueltos"] = frescos + stale_cache
        _snapshot["frescos"] = frescos
        _snapshot["stale_cache"] = stale_cache
        _snapshot["sin_datos"] = sin_datos
        _snapshot["chunks_total"] = len(chunks)
        _snapshot["chunks_error"] = chunk_errors
        _snapshot["tradier_fresh"] = tradier_fresh_count
        _snapshot["tradier_stale"] = tradier_stale_count
        _snapshot["yahoo_checked"] = yahoo_stats["attempted"]
        _snapshot["yahoo_attempted"] = yahoo_stats["attempted"]
        _snapshot["yahoo_fresh"] = yahoo_fresh_count
        _snapshot["yahoo_success"] = yahoo_stats["success"]
        _snapshot["yahoo_errors"] = yahoo_stats["errors"]
        _snapshot["yahoo_aborted"] = yahoo_stats["aborted"]
        _snapshot["finnhub_checked"] = finnhub_stats["attempted"]
        _snapshot["finnhub_attempted"] = finnhub_stats["attempted"]
        _snapshot["finnhub_fresh"] = finnhub_fresh_count
        _snapshot["finnhub_success"] = finnhub_stats["success"]
        _snapshot["finnhub_errors"] = finnhub_stats["errors"]
        _snapshot["finnhub_aborted"] = finnhub_stats["aborted"]
        _snapshot["cache_used"] = cache_used_count
        _snapshot["sin_dato_final"] = sin_dato_count
        _snapshot["ultimo_error"] = None
        _snapshot["cycles_total"] += 1
        _snapshot["cycles_ok"] += 1
    return duration


def run_market_cycle_once(symbols_override: Optional[List[str]] = None) -> Optional[float]:
    """Corre UN ciclo completo (no reentrante, mismo criterio que
    `radar_worker.run_sweep_once()`). Devuelve la duración en segundos, o
    `None` si no corrió (lock ocupado, sin token, o cualquier excepción --
    nunca propaga hacia el llamador). `symbols_override` es solo para
    pruebas/diagnóstico de alcance acotado -- el hilo de fondo (`_loop()`)
    nunca lo pasa."""
    if not _lock.acquire(blocking=False):
        return None
    try:
        return _run_cycle_body(symbols_override=symbols_override)
    except Exception as exc:
        with _state_lock:
            _snapshot["ultimo_error"] = f"{type(exc).__name__}: {exc}"
            _snapshot["cycles_total"] += 1
            _snapshot["cycles_error"] += 1
        return None
    finally:
        _lock.release()


def _loop() -> None:
    while not _stop.is_set():
        try:
            session = market_hours.get_session()
        except Exception:
            session = None
        if _is_active_session(session):
            duration = run_market_cycle_once()
            interval = _next_interval(duration)
        else:
            # Cerrado (incluye fin de semana): no consulta Tradier, solo
            # revisa cada rato si ya abrió -- el snapshot servido queda con
            # su antigüedad real, nunca se finge un dato nuevo.
            interval = IDLE_RECHECK_SECONDS
        if _stop.wait(interval):
            break


def start_market_view() -> None:
    """Arranca el hilo una sola vez por proceso. No hace nada si está
    deshabilitado por entorno (`ATLAS_MARKET_VIEW_ENABLED=false`)."""
    global _thread
    if not MARKET_VIEW_ENABLED:
        return
    if _thread is not None:
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="market_view")
    _thread.start()


def request_stop() -> None:
    _stop.set()


def get_market_snapshot() -> Dict[str, Any]:
    """Solo lectura -- copia superficial del snapshot cacheado. Nunca
    dispara una consulta nueva a Tradier; el endpoint público lee
    exactamente esto, tantas veces por segundo como pida el frontend, sin
    costo adicional."""
    with _state_lock:
        return dict(_snapshot)
