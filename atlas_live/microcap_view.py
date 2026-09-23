"""Vista de Microcap -- ranking en vivo, exclusivo, de las acciones de
Racional que cotizan bajo $5 USD (2026-09-23, autorizado explícitamente:
"quiero tener en el menú una sección que diga microcap... todas las que
cuesten menos de 5 us. sin tocar el mercado ni los etf").

Módulo HERMANO de `market_view.py`/`etf_normal_view.py`, mismo patrón de
arquitectura (hilo de fondo propio, cadencia auto-ajustada, multi-fuente
Tradier->Yahoo, circuit breaker) pero con estado 100% propio (`_snapshot`,
`_lock`, `_thread`, `_sparkline_by_symbol`, `_last_known_by_symbol`) --
nunca lee ni escribe el snapshot de `market_view.py`/`etf_normal_view.py`,
nunca corre en el mismo hilo. Las piezas puras (`_fetch_with_circuit_breaker`,
`_fetch_chunk`, `_fetch_yahoo_chunk`) se IMPORTAN de `market_view.py` tal
cual -- nunca se copian.

COMPLETAMENTE AISLADO, igual que `market_view.py`/`etf_normal_view.py`: no
importa ni es importado por `atlas_decision_core.py`, `candidate_gates.py`,
`priority_classifier.py`, `radar_worker.py`, `scan_worker.py`, ni ningún
módulo de `atlas_live/core/`, `atlas_live/learning/` o `atlas_live/memory/`
(salvo `market_hours.get_session()`, de solo lectura). Nunca escribe en
`racional_universe.json` -- solo lee. No agrega símbolos al universo
operativo del radar.

Universo de BARRIDO: todas las acciones (`type=="EQUITY"`) de
`atlas.data.universe.load_universe()` (~1.751 símbolos hoy) -- el precio
no se conoce hasta consultarlo, así que no se puede filtrar antes.
Universo de PRESENTACIÓN: de ese barrido, se muestran únicamente las filas
cuyo `price` resuelto es `< MICROCAP_MAX_PRICE_USD` (default 5.0, mismo
patrón de corte de presentación que `MERCADO_TOP_N` en `market_view.py` --
nunca cambia qué se resuelve/cachea, solo qué se sirve en el snapshot
final). Una acción que cruza el umbral de un día a otro entra/sale sola,
sin ningún ajuste manual.

Sin Finnhub (mismo criterio que `etf_normal_view.py`, para no tocar
`finnhub_shared_budget.py`, compartido con hot_quote/catalyst_worker/
market_view): Tradier + Yahoo alcanzan.
"""

import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

from atlas.data.providers.tradier_provider import TRADIER_CHUNK_SIZE
from atlas.data.providers.tradier_symbol_map import normalize
from atlas.data.universe import load_universe
from atlas_live.data_fusion.multi_source_resolver import (
    es_fresco_independiente,
    es_tradier_fresco,
    resolver_mejor_precio,
)
from atlas_live.data_fusion.universe_quotes import build_tradier_provider
from atlas_live.market_view import _fetch_chunk, _fetch_with_circuit_breaker, _fetch_yahoo_chunk
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


MICROCAP_VIEW_ENABLED = _env_bool("ATLAS_MICROCAP_VIEW_ENABLED", True)
CYCLE_FLOOR_SECONDS = _env_float("ATLAS_MICROCAP_VIEW_FLOOR_SECONDS", 10.0)
CYCLE_CEILING_SECONDS = _env_float("ATLAS_MICROCAP_VIEW_CEILING_SECONDS", 90.0)
CYCLE_SAFETY_MARGIN = _env_float("ATLAS_MICROCAP_VIEW_SAFETY_MARGIN", 3.0)
IDLE_RECHECK_SECONDS = _env_float("ATLAS_MICROCAP_VIEW_IDLE_RECHECK_SECONDS", 90.0)
MAX_WORKERS = int(_env_float("ATLAS_MICROCAP_VIEW_MAX_WORKERS", 6))
SPARKLINE_MAX_POINTS = int(_env_float("ATLAS_MICROCAP_VIEW_SPARKLINE_POINTS", 60))

MULTI_SOURCE_ENABLED = _env_bool("ATLAS_MICROCAP_MULTI_SOURCE_ENABLED", True)
YAHOO_BATCH_SIZE = int(_env_float("ATLAS_MICROCAP_YAHOO_BATCH_SIZE", 15))
YAHOO_BATCH_WORKERS = int(_env_float("ATLAS_MICROCAP_YAHOO_BATCH_WORKERS", 4))
CIRCUIT_MIN_SAMPLE = int(_env_float("ATLAS_MICROCAP_CIRCUIT_MIN_SAMPLE", 20))
CIRCUIT_ERROR_RATE = _env_float("ATLAS_MICROCAP_CIRCUIT_ERROR_RATE", 0.85)

# Corte de PRESENTACIÓN (pedido explícito: "menos de 5 us") -- nunca
# cambia qué se resuelve/cachea, mismo criterio que MERCADO_TOP_N.
MICROCAP_MAX_PRICE_USD = _env_float("ATLAS_MICROCAP_MAX_PRICE_USD", 5.0)

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
    "total_bajo_umbral": 0,
    "resueltos": 0,
    "frescos": 0,
    "stale_cache": 0,
    "sin_datos": 0,
    "chunks_total": 0,
    "chunks_error": 0,
    "tradier_fresh": 0,
    "tradier_stale": 0,
    "yahoo_attempted": 0,
    "yahoo_fresh": 0,
    "yahoo_success": 0,
    "yahoo_errors": 0,
    "yahoo_aborted": 0,
    "cache_used": 0,
    "sin_dato_final": 0,
    "cycles_total": 0,
    "cycles_ok": 0,
    "cycles_error": 0,
    "ultimo_error": None,
}

_sparkline_lock = threading.Lock()
_sparkline_by_symbol: Dict[str, Deque[float]] = {}

_last_known_lock = threading.Lock()
_last_known_by_symbol: Dict[str, Dict[str, Any]] = {}

ACTIVE_SESSIONS = ("premarket", "regular", "afterhours", "overnight")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_interval(last_duration: Optional[float]) -> float:
    if not last_duration:
        return CYCLE_FLOOR_SECONDS
    target = CYCLE_SAFETY_MARGIN * last_duration
    return max(CYCLE_FLOOR_SECONDS, min(CYCLE_CEILING_SECONDS, target))


def _is_active_session(session: Optional[str]) -> bool:
    return session in ACTIVE_SESSIONS


def microcap_symbols() -> List[Any]:
    """Universo de BARRIDO (nunca filtrado por precio) -- todas las
    acciones (`type=="EQUITY"`) de Racional. El filtro de precio se
    aplica después, sobre el resultado ya resuelto (ver `_run_cycle_body`)."""
    universo = load_universe()
    return [asset for asset in universo.values() if asset.type == "EQUITY"]


def _fetch_yahoo_batch(symbols: List[str]):
    if not symbols:
        return {}, {"attempted": 0, "success": 0, "errors": 0, "aborted": 0, "budget_rejected": 0}
    chunks = [symbols[i:i + YAHOO_BATCH_SIZE] for i in range(0, len(symbols), YAHOO_BATCH_SIZE)]
    return _fetch_with_circuit_breaker(
        chunks, _fetch_yahoo_chunk, len, YAHOO_BATCH_WORKERS, CIRCUIT_MIN_SAMPLE, CIRCUIT_ERROR_RATE,
    )


def _run_cycle_body() -> float:
    t0 = time.time()

    try:
        session_now = market_hours.get_session()
    except Exception:
        session_now = None
    is_overnight = session_now == "overnight"

    instruments = microcap_symbols()
    originals = [a.symbol for a in instruments]
    name_by_original = {a.symbol: a.name for a in instruments}

    normalized: Dict[str, str] = {}
    query_to_originals: Dict[str, List[str]] = {}
    for sym in originals:
        n = normalize(sym)
        normalized[sym] = n.query_symbol
        query_to_originals.setdefault(n.query_symbol, []).append(sym)

    quotes_by_query: Dict[str, Any] = {}
    chunks: List[List[str]] = []
    chunk_errors = 0

    if not is_overnight:
        tradier_provider = build_tradier_provider()
        if tradier_provider is None:
            raise RuntimeError("TRADIER_API_TOKEN no configurado -- Microcap no puede operar sin Tradier")

        query_symbols = list(query_to_originals.keys())
        chunks = [
            query_symbols[i:i + TRADIER_CHUNK_SIZE]
            for i in range(0, len(query_symbols), TRADIER_CHUNK_SIZE)
        ]

        with ThreadPoolExecutor(max_workers=max(1, min(MAX_WORKERS, len(chunks) or 1))) as executor:
            futures = {executor.submit(_fetch_chunk, tradier_provider, chunk): chunk for chunk in chunks}
            for future in as_completed(futures):
                try:
                    quotes = future.result()
                    for q in quotes:
                        quotes_by_query[q.symbol] = q
                except Exception:
                    chunk_errors += 1

    now_utc = datetime.now(timezone.utc)

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

    yahoo_by_original: Dict[str, Any] = {}
    yahoo_stats = {"attempted": 0, "success": 0, "errors": 0, "aborted": 0, "budget_rejected": 0}

    fallback_allowed = MULTI_SOURCE_ENABLED and _is_active_session(session_now) and bool(tradier_stale_originals)
    if fallback_allowed:
        yahoo_by_original, yahoo_stats = _fetch_yahoo_batch(tradier_stale_originals)

    yahoo_fresh_count = sum(
        1 for o in tradier_stale_originals if es_fresco_independiente(yahoo_by_original.get(o), now_utc)
    )

    rows: List[Dict[str, Any]] = []
    cache_used_count = 0
    sin_dato_count = 0
    for original, query_symbol in normalized.items():
        tradier_q = quotes_by_query.get(query_symbol)
        yahoo_q = yahoo_by_original.get(original)
        with _last_known_lock:
            cached = _last_known_by_symbol.get(original)

        resuelto = resolver_mejor_precio(original, tradier_q, yahoo_q, None, now_utc, cached=cached)

        if resuelto.source == "sin_dato":
            sin_dato_count += 1
            continue  # nunca se muestra una fila SIN_DATO en Microcap -- no hay precio para saber si entra al umbral

        if resuelto.source == "cache":
            cache_used_count += 1

        # Plausibilidad de previous_close (mismo criterio que market_view.py,
        # caso real VWAV -- previous_close nunca debería cambiar de un
        # ciclo a otro dentro del mismo día).
        prevclose_confiable = True
        if resuelto.previous_close is not None and cached and cached.get("previous_close"):
            prev_cached = cached["previous_close"]
            diff_pct = abs(resuelto.previous_close - prev_cached) / prev_cached * 100
            if diff_pct > 2.0:
                prevclose_confiable = False

        change_abs = (
            round(resuelto.price - resuelto.previous_close, 4)
            if resuelto.price is not None and resuelto.previous_close is not None and prevclose_confiable else None
        )
        change_pct_final = resuelto.change_pct if prevclose_confiable else None

        data_age_seconds = None
        if resuelto.timestamp is not None:
            ts = resuelto.timestamp if resuelto.timestamp.tzinfo else resuelto.timestamp.replace(tzinfo=timezone.utc)
            data_age_seconds = round((now_utc - ts).total_seconds(), 1)

        if resuelto.is_stale:
            with _sparkline_lock:
                sparkline = list(_sparkline_by_symbol.get(original, []))
        else:
            with _sparkline_lock:
                dq = _sparkline_by_symbol.setdefault(original, deque(maxlen=SPARKLINE_MAX_POINTS))
                if resuelto.price is not None:
                    dq.append(resuelto.price)
                sparkline = list(dq)
            with _last_known_lock:
                _last_known_by_symbol[original] = {
                    "price": resuelto.price,
                    "previous_close": resuelto.previous_close if prevclose_confiable else (cached or {}).get("previous_close"),
                    "change_pct": change_pct_final,
                    "price_basis": resuelto.price_basis,
                    "source": resuelto.source,
                    "cached_at": now_utc,
                }

        # Corte de PRESENTACIÓN por precio -- nunca afecta lo ya resuelto/
        # cacheado arriba, solo qué fila termina en el snapshot final.
        if resuelto.price is None or resuelto.price >= MICROCAP_MAX_PRICE_USD:
            continue

        rows.append({
            "symbol": original,
            "name": name_by_original.get(original, original),
            "price": resuelto.price,
            "change_abs": change_abs,
            "change_pct": change_pct_final,
            "price_is_stale": resuelto.is_stale,
            "data_age_seconds": data_age_seconds,
            "sparkline": sparkline,
            "data_status": "STALE" if resuelto.is_stale else "FRESCO",
            "source": resuelto.source,
            "session_dato": resuelto.session,
            "overnight_disponible": resuelto.overnight_disponible,
            "prevclose_confiable": prevclose_confiable,
        })

    status_order = {"FRESCO": 0, "STALE": 1}
    rows.sort(key=lambda r: (
        status_order.get(r["data_status"], 2),
        r["change_pct"] is None,
        -(r["change_pct"] or 0.0),
    ))

    frescos = sum(1 for r in rows if r["data_status"] == "FRESCO")
    stale_cache = sum(1 for r in rows if r["data_status"] == "STALE")

    for i, r in enumerate(rows, start=1):
        r["rank"] = i

    duration = round(time.time() - t0, 2)
    with _state_lock:
        _snapshot["generated_at"] = _now_iso()
        _snapshot["cycle_duration_s"] = duration
        _snapshot["session_at_generation"] = session_now
        _snapshot["rows"] = rows
        _snapshot["total_universe"] = len(originals)
        _snapshot["total_bajo_umbral"] = len(rows)
        _snapshot["resueltos"] = frescos + stale_cache
        _snapshot["frescos"] = frescos
        _snapshot["stale_cache"] = stale_cache
        _snapshot["sin_datos"] = sin_dato_count
        _snapshot["chunks_total"] = len(chunks)
        _snapshot["chunks_error"] = chunk_errors
        _snapshot["tradier_fresh"] = tradier_fresh_count
        _snapshot["tradier_stale"] = tradier_stale_count
        _snapshot["yahoo_attempted"] = yahoo_stats["attempted"]
        _snapshot["yahoo_fresh"] = yahoo_fresh_count
        _snapshot["yahoo_success"] = yahoo_stats["success"]
        _snapshot["yahoo_errors"] = yahoo_stats["errors"]
        _snapshot["yahoo_aborted"] = yahoo_stats["aborted"]
        _snapshot["cache_used"] = cache_used_count
        _snapshot["sin_dato_final"] = sin_dato_count
        _snapshot["ultimo_error"] = None
        _snapshot["cycles_total"] += 1
        _snapshot["cycles_ok"] += 1
    return duration


def run_microcap_cycle_once() -> Optional[float]:
    """No-reentrante -- mismo criterio que `run_market_cycle_once()`."""
    if not _lock.acquire(blocking=False):
        return None
    try:
        return _run_cycle_body()
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
            duration = run_microcap_cycle_once()
            interval = _next_interval(duration)
        else:
            interval = IDLE_RECHECK_SECONDS
        if _stop.wait(interval):
            break


def start_microcap_view() -> None:
    """Arranca el hilo una sola vez por proceso -- propio, nunca comparte
    thread ni estado con `market_view.start_market_view()` ni con
    `etf_normal_view.start_etf_normal_view()`."""
    global _thread
    if not MICROCAP_VIEW_ENABLED:
        return
    if _thread is not None:
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="microcap_view")
    _thread.start()


def request_stop() -> None:
    _stop.set()


def get_microcap_snapshot() -> Dict[str, Any]:
    """Solo lectura -- nunca dispara una consulta nueva."""
    with _state_lock:
        return dict(_snapshot)
