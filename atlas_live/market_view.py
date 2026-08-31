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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

from atlas.data.providers.tradier_provider import TRADIER_CHUNK_SIZE, TradierProvider
from atlas.data.providers.tradier_symbol_map import normalize
from atlas.data.universe import load_universe
from atlas_live.data_fusion.universe_quotes import build_tradier_provider
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


def _run_cycle_body() -> float:
    t0 = time.time()
    tradier_provider = build_tradier_provider()
    if tradier_provider is None:
        raise RuntimeError("TRADIER_API_TOKEN no configurado -- Mercado no puede operar sin Tradier")

    # Universo COMPLETO de Racional (EQUITY+ETF+ETN, 2.577 reales) -- sin
    # filtro nuevo, sin agregar ni quitar ningún símbolo. `load_universe()`
    # ya es la fuente completa de `Universe`, no se toca ese módulo.
    instruments = list(load_universe().values())
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

    # El universo (1.646 EQUITY) SIEMPRE se representa completo -- una
    # acción nunca desaparece del ranking solo porque su batch falló este
    # ciclo. Tres estados por símbolo, nunca inventando `change_percent`:
    #   FRESCO   -- Tradier respondió este ciclo con `last_price` real.
    #   STALE    -- no hubo respuesta este ciclo; se conserva el último
    #               dato conocido (cache aislado de este módulo), marcado
    #               con su antigüedad real.
    #   SIN_DATO -- nunca hubo ningún dato para este símbolo -- precio y
    #               `change_pct` quedan `None`, nunca inventados.
    now_utc = datetime.now(timezone.utc)
    rows: List[Dict[str, Any]] = []
    for original, query_symbol in normalized.items():
        q = quotes_by_query.get(query_symbol)
        if q is not None and q.last_price is not None:
            with _sparkline_lock:
                dq = _sparkline_by_symbol.setdefault(original, deque(maxlen=SPARKLINE_MAX_POINTS))
                dq.append(q.last_price)
                sparkline = list(dq)
            data_age_seconds = None
            if q.timestamp is not None:
                ts = q.timestamp if q.timestamp.tzinfo else q.timestamp.replace(tzinfo=timezone.utc)
                data_age_seconds = round((now_utc - ts).total_seconds(), 1)
            price_is_stale = bool(getattr(q, "price_is_stale", False))
            # Cambio $ = precio actual - cierre anterior real de Tradier
            # (`previous_close`, ya poblado por `TradierProvider`). Nunca
            # se deriva de `change_pct` (evita inventar un número por
            # redondeo inverso) -- si falta `previous_close`, queda `None`.
            previous_close = getattr(q, "previous_close", None)
            change_abs = (
                round(q.last_price - previous_close, 4)
                if previous_close is not None else None
            )
            row = {
                "symbol": original,
                "name": name_by_original.get(original, original),
                "price": q.last_price,
                "change_abs": change_abs,
                "change_pct": q.change_percent,
                "price_is_stale": price_is_stale,
                "data_age_seconds": data_age_seconds,
                "sparkline": sparkline,
                "data_status": "FRESCO",
            }
            with _last_known_lock:
                _last_known_by_symbol[original] = {
                    "price": q.last_price,
                    "change_abs": change_abs,
                    "change_pct": q.change_percent,
                    "price_is_stale": price_is_stale,
                    "cached_at": now_utc,
                }
            rows.append(row)
            continue

        with _last_known_lock:
            cached = _last_known_by_symbol.get(original)
        if cached is None:
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
            })
            continue

        with _sparkline_lock:
            sparkline = list(_sparkline_by_symbol.get(original, []))
        rows.append({
            "symbol": original,
            "name": name_by_original.get(original, original),
            "price": cached["price"],
            "change_abs": cached.get("change_abs"),
            "change_pct": cached["change_pct"],
            "price_is_stale": cached["price_is_stale"],
            "data_age_seconds": round((now_utc - cached["cached_at"]).total_seconds(), 1),
            "sparkline": sparkline,
            "data_status": "STALE",
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
    for i, r in enumerate(rows, start=1):
        r["rank"] = i

    frescos = sum(1 for r in rows if r["data_status"] == "FRESCO")
    stale_cache = sum(1 for r in rows if r["data_status"] == "STALE")
    sin_datos = sum(1 for r in rows if r["data_status"] == "SIN_DATO")

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
        _snapshot["ultimo_error"] = None
        _snapshot["cycles_total"] += 1
        _snapshot["cycles_ok"] += 1
    return duration


def run_market_cycle_once() -> Optional[float]:
    """Corre UN ciclo completo (no reentrante, mismo criterio que
    `radar_worker.run_sweep_once()`). Devuelve la duración en segundos, o
    `None` si no corrió (lock ocupado, sin token, o cualquier excepción --
    nunca propaga hacia el llamador)."""
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
