"""Vista de Horario extendido -- ranking en vivo de los instrumentos de
Racional habilitados para operar fuera del horario regular (listado
público de Racional, ver `atlas/data/universe/extended_hours.py`).

La sección solo trabaja entre el cierre y la apertura: premarket,
after-hours y overnight (y el fin de semana, con el último dato). Durante
la sesión regular el hilo queda en reposo y el snapshot informa
`activa=False` para que la Cabina lo avise en vez de mostrar datos del
día.

Módulo HERMANO de `market_view.py` / `etf_normal_view.py` /
`microcap_view.py`: mismo patrón (hilo de fondo propio, cadencia
auto-ajustada, Tradier -> Yahoo con circuit breaker) y estado 100% propio.
Las piezas puras de red se importan de `market_view.py`, nunca se copian.

Completamente aislado del radar, del motor de decisión y del aprendizaje:
no importa ni es importado por `atlas_decision_core.py`,
`candidate_gates.py`, `radar_worker.py`, `scan_worker.py` ni por ningún
módulo de `atlas_live/core/`, `learning/` o `memory/` (salvo
`market_hours.get_session()`, de solo lectura). Nunca escribe en el
universo. Sin Finnhub, para no tocar el presupuesto compartido.
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
from atlas.data.universe.extended_hours import get_extended_hours_assets
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


EXTENDED_HOURS_VIEW_ENABLED = _env_bool("ATLAS_EXTENDED_HOURS_VIEW_ENABLED", True)
CYCLE_FLOOR_SECONDS = _env_float("ATLAS_EXTENDED_HOURS_VIEW_FLOOR_SECONDS", 10.0)
CYCLE_CEILING_SECONDS = _env_float("ATLAS_EXTENDED_HOURS_VIEW_CEILING_SECONDS", 90.0)
CYCLE_SAFETY_MARGIN = _env_float("ATLAS_EXTENDED_HOURS_VIEW_SAFETY_MARGIN", 3.0)
IDLE_RECHECK_SECONDS = _env_float("ATLAS_EXTENDED_HOURS_VIEW_IDLE_RECHECK_SECONDS", 60.0)
MAX_WORKERS = int(_env_float("ATLAS_EXTENDED_HOURS_VIEW_MAX_WORKERS", 4))
SPARKLINE_MAX_POINTS = int(_env_float("ATLAS_EXTENDED_HOURS_VIEW_SPARKLINE_POINTS", 60))

MULTI_SOURCE_ENABLED = _env_bool("ATLAS_EXTENDED_HOURS_MULTI_SOURCE_ENABLED", True)
YAHOO_BATCH_SIZE = int(_env_float("ATLAS_EXTENDED_HOURS_YAHOO_BATCH_SIZE", 15))
YAHOO_BATCH_WORKERS = int(_env_float("ATLAS_EXTENDED_HOURS_YAHOO_BATCH_WORKERS", 3))
CIRCUIT_MIN_SAMPLE = int(_env_float("ATLAS_EXTENDED_HOURS_CIRCUIT_MIN_SAMPLE", 20))
CIRCUIT_ERROR_RATE = _env_float("ATLAS_EXTENDED_HOURS_CIRCUIT_ERROR_RATE", 0.85)

# Sesiones en las que el hilo consulta datos. "closed" (fin de semana) no
# consulta: queda el último dato del viernes, salvo que todavía no haya
# ninguno (ver `_should_run`).
ACTIVE_SESSIONS = ("premarket", "afterhours", "overnight")

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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_interval(last_duration: Optional[float]) -> float:
    if not last_duration:
        return CYCLE_FLOOR_SECONDS
    target = CYCLE_SAFETY_MARGIN * last_duration
    return max(CYCLE_FLOOR_SECONDS, min(CYCLE_CEILING_SECONDS, target))


def _is_active_session(session: Optional[str]) -> bool:
    return session in ACTIVE_SESSIONS


def _should_run(session: Optional[str], has_rows: bool) -> bool:
    """Corre en las sesiones extendidas; con el mercado cerrado solo si
    todavía no hay ningún dato (por ejemplo, un reinicio en fin de semana)."""
    if _is_active_session(session):
        return True
    return session == "closed" and not has_rows


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

    instruments = get_extended_hours_assets()
    originals = [a.symbol for a in instruments]
    name_by_original = {a.symbol: a.name for a in instruments}
    type_by_original = {a.symbol: a.type for a in instruments}

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
            raise RuntimeError("TRADIER_API_TOKEN no configurado -- Horario extendido no puede operar sin Tradier")

        query_symbols = list(query_to_originals.keys())
        chunks = [
            query_symbols[i:i + TRADIER_CHUNK_SIZE]
            for i in range(0, len(query_symbols), TRADIER_CHUNK_SIZE)
        ]

        with ThreadPoolExecutor(max_workers=max(1, min(MAX_WORKERS, len(chunks) or 1))) as executor:
            futures = {executor.submit(_fetch_chunk, tradier_provider, chunk): chunk for chunk in chunks}
            for future in as_completed(futures):
                try:
                    for q in future.result():
                        quotes_by_query[q.symbol] = q
                except Exception:
                    chunk_errors += 1

    now_utc = datetime.now(timezone.utc)

    tradier_stale_originals: List[str] = []
    tradier_fresh_count = 0
    tradier_stale_count = 0
    for original, query_symbol in normalized.items():
        if es_tradier_fresco(quotes_by_query.get(query_symbol)):
            tradier_fresh_count += 1
        else:
            tradier_stale_count += 1
            tradier_stale_originals.append(original)

    yahoo_by_original: Dict[str, Any] = {}
    yahoo_stats = {"attempted": 0, "success": 0, "errors": 0, "aborted": 0, "budget_rejected": 0}
    if MULTI_SOURCE_ENABLED and tradier_stale_originals and session_now != "closed":
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
            continue
        if resuelto.source == "cache":
            cache_used_count += 1

        prevclose_confiable = True
        if resuelto.previous_close is not None and cached and cached.get("previous_close"):
            prev_cached = cached["previous_close"]
            if abs(resuelto.previous_close - prev_cached) / prev_cached * 100 > 2.0:
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

        rows.append({
            "symbol": original,
            "name": name_by_original.get(original, original),
            "tipo": type_by_original.get(original),
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


def run_extended_hours_cycle_once() -> Optional[float]:
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
        with _state_lock:
            has_rows = bool(_snapshot["rows"])
        if _should_run(session, has_rows):
            duration = run_extended_hours_cycle_once()
            interval = _next_interval(duration)
        else:
            interval = IDLE_RECHECK_SECONDS
        if _stop.wait(interval):
            break


def start_extended_hours_view() -> None:
    """Arranca el hilo una sola vez por proceso, con estado propio."""
    global _thread
    if not EXTENDED_HOURS_VIEW_ENABLED:
        return
    if _thread is not None:
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="extended_hours_view")
    _thread.start()


def request_stop() -> None:
    _stop.set()


def get_extended_hours_snapshot() -> Dict[str, Any]:
    """Solo lectura -- nunca dispara una consulta nueva. `activa` es False
    durante la sesión regular: la sección solo funciona entre el cierre y
    la apertura."""
    try:
        session = market_hours.get_session()
    except Exception:
        session = None
    with _state_lock:
        snap = dict(_snapshot)
    snap["session_actual"] = session
    snap["activa"] = session is not None and session != "regular"
    return snap
