"""Detector Unificado -- MODO SHADOW (2026-08-26, U3-C2, autorizado
explícitamente, con condición fundamental: esto es una etapa TRANSITORIA,
nunca un segundo sistema permanente).

Objetivo de esta fase: demostrar con datos reales si un único detector
(`candidate_gates.evaluate_all_gates()`, SIN modificar, la única lógica de
detección que existe en todo Atlas) puede eventualmente reemplazar la
duplicación actual (`candidate_gates.py` vía Tradier + las Stage-A gates
propias de `explosive_engine.py` vía Yahoo) sin perder candidatos válidos.

AISLAMIENTO TOTAL (condición explícita del usuario, verificado por tests
estructurales en `test_unified_detector.py`):
- `SweepHistory` propia (`_history` de este módulo) -- nunca la de
  `radar_worker.py` (`_history` global, exclusiva de `candidate_tracker.py`).
- Persistencia propia (`shadow_detector_registry.py`, DB propia) -- nunca
  escribe en `candidate_detection`/`candidate_registry.py`.
- Nunca alimenta `atlas_decision_core`, ningún score, ningún ranking, ninguna
  UI. `apply_recalibration` no participa acá -- este módulo no decide nada,
  solo observa y registra.
- Reutiliza `candidate_gates.evaluate_all_gates()` TAL CUAL (import directo,
  cero copia de lógica) -- confirmado por test AST que ninguna función de
  puertas se reimplementa acá.

COSTO DE RED (verificado antes de implementar, autorizado explícitamente
tras la verificación -- ver informe U3-C2):
- Premarket/regular: CERO llamadas nuevas -- reutiliza
  `radar_worker.get_last_quotes()` (ya en memoria, mismo patrón que
  `catalyst_market_join.py` usa toda la sesión). El universo cubierto acá
  es exactamente el que el radar real ya barrió (~5.500 Tradier broad) --
  el objetivo en esta ventana es comparar METODOLOGÍA sobre terreno ya
  conocido, no sumar cobertura.
- Afterhours (la cobertura genuinamente NUEVA -- nada más consulta Tradier
  en esa ventana hoy): barrido propio, pero SOLO sobre el universo Racional
  (~2.577, no los 5.500 de Tradier broad -- ampliar más allá de eso es
  trabajo de una fase posterior, no de esta), con cadencia mínima de
  `AFTERHOURS_MIN_INTERVAL_SECONDS` (300s = 5 min) entre barridos reales --
  nunca la cadencia de 30-120s del radar real.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from atlas.data.universe import universe as racional_universe
from atlas_live.data_fusion.registry import get_default_provider
from atlas_live.data_fusion.universe_quotes import build_tradier_provider, fetch_universe_quotes
from atlas_live.memory import market_hours
from atlas_live.radar import candidate_gates as gates
from atlas_live.radar import radar_worker
from atlas_live.radar import shadow_detector_registry as registry
from atlas_live.radar.sweep_history import SweepHistory, SweepSnapshot

SHADOW_LOOP_INTERVAL_SECONDS = 60.0
AFTERHOURS_MIN_INTERVAL_SECONDS = 300.0

_lock = threading.Lock()
_stop = threading.Event()
_thread: Optional[threading.Thread] = None
_history = SweepHistory()
_last_afterhours_sweep_at: Optional[datetime] = None

# Observabilidad de errores (2026-08-26, U3-C3 -- pedido explícito: "no
# quiero errores silenciosos durante una auditoría que decidirá qué
# detector queda"). Se registra el error para poder diagnosticarlo, pero
# NUNCA se relanza hacia el radar principal -- sigue siendo el mismo
# criterio de aislamiento total de U3-C2, solo que ahora el fallo queda
# visible en vez de perderse.
_last_error: Optional[str] = None
_last_error_at: Optional[str] = None
_last_error_session: Optional[str] = None
_error_count: int = 0
_last_successful_sweep_at: Optional[str] = None


def _dedup_universe() -> List[str]:
    """Universo Racional deduplicado (equities+ETFs) -- SOLO para el
    barrido nuevo de afterhours. `set()` sobre ambas listas: cada símbolo
    aparece una sola vez sin importar si está clasificado como equity o
    ETF, evitando pedir la misma cotización dos veces."""
    equities = racional_universe.get_equities()
    etfs = racional_universe.get_etfs()
    return sorted({a.symbol for a in equities + etfs})


def _quote_to_snapshot(quote: Any, session: str) -> SweepSnapshot:
    """Conversión propia -- deliberadamente NO importada de
    `candidate_tracker.py` (protegido, función privada) para no acoplar
    este módulo shadow a un archivo protegido. Mismos campos que
    `SweepSnapshot` ya espera, ninguno calculado con red adicional (todo
    ya viene en el `Quote`)."""
    price = getattr(quote, "last_price", None)
    volume = getattr(quote, "volume", None)
    dollar_volume = price * volume if price is not None and volume is not None else None
    return SweepSnapshot(
        sweep_id="",
        observed_at=datetime.now(timezone.utc).isoformat(),
        price=price,
        change_pct=getattr(quote, "change_percent", None),
        volume=volume,
        average_volume=getattr(quote, "average_volume", None),
        relative_volume=getattr(quote, "relative_volume", None),
        dollar_volume=dollar_volume,
        session=session,
    )


def run_shadow_sweep_once() -> Optional[Dict[str, Any]]:
    """Corre UN barrido shadow si no hay otro en curso (no-reentrante,
    mismo criterio que `radar_worker.run_sweep_once()`). Devuelve un
    resumen del barrido, o `None` si no corrió (lock ocupado, sesión
    `closed`, sin token de Tradier, o afterhours todavía dentro del piso
    de cadencia)."""
    if not _lock.acquire(blocking=False):
        return None
    try:
        session = market_hours.get_session()
        if session not in ("premarket", "regular", "afterhours"):
            return None

        market_date = market_hours.market_date()

        if session == "afterhours":
            global _last_afterhours_sweep_at
            now = datetime.now(timezone.utc)
            if (
                _last_afterhours_sweep_at is not None
                and (now - _last_afterhours_sweep_at).total_seconds() < AFTERHOURS_MIN_INTERVAL_SECONDS
            ):
                return None
            tradier_provider = build_tradier_provider()
            if tradier_provider is None:
                return None
            fallback_provider = get_default_provider()
            universe = _dedup_universe()
            result = fetch_universe_quotes(universe, tradier_provider=tradier_provider, fallback_provider=fallback_provider)
            quotes = result.quotes
            universe_source = "afterhours_fresh_sweep"
            _last_afterhours_sweep_at = now
        else:
            # Piggyback -- CERO llamadas nuevas a ningún proveedor. Mismo
            # universo que el radar real ya barrió este ciclo.
            quotes = radar_worker.get_last_quotes()
            universe_source = "piggyback_radar"

        if _history.current_market_date != market_date:
            _history.reset_for_new_day(market_date)

        detecciones = 0
        for ticker, quote in quotes.items():
            snapshot = _quote_to_snapshot(quote, session)
            history = _history.get(ticker)
            results = gates.evaluate_all_gates(snapshot, history, session)
            _history.push(ticker, snapshot)
            fired = gates.fired_gates(results)
            if fired:
                registry.record_shadow_detection(
                    ticker=ticker,
                    market_date=market_date,
                    session=session,
                    price=snapshot.price,
                    change_pct=snapshot.change_pct,
                    volume=snapshot.volume,
                    average_volume=snapshot.average_volume,
                    relative_volume=snapshot.relative_volume,
                    dollar_volume=snapshot.dollar_volume,
                    price_source=getattr(quote, "source", None),
                    price_basis=getattr(quote, "price_basis", None),
                    price_is_stale=getattr(quote, "price_is_stale", None),
                    universe_source=universe_source,
                    gates_fired=[{"gate": g.name, "reason": g.reason, "value": g.value} for g in fired],
                    snapshot=dataclasses.asdict(snapshot),
                )
                detecciones += 1

        return {
            "session": session, "universe_source": universe_source,
            "universe_size": len(quotes), "detecciones": detecciones,
        }
    finally:
        _lock.release()


def _loop() -> None:
    global _last_error, _last_error_at, _last_error_session, _error_count, _last_successful_sweep_at
    while not _stop.is_set():
        try:
            resultado = run_shadow_sweep_once()
            if resultado is not None:
                _last_successful_sweep_at = datetime.now(timezone.utc).isoformat()
        except Exception as exc:
            # El detector shadow NUNCA puede afectar producción -- la
            # excepción NUNCA se relanza hacia el radar principal -- pero
            # sí queda registrada (mensaje, timestamp, sesión conocida al
            # momento del fallo, contador acumulado) para que una auditoría
            # como U3-C3 pueda diagnosticar fallos en vez de asumir
            # silenciosamente "cero detecciones = todo OK".
            _last_error = f"{type(exc).__name__}: {exc}"
            _last_error_at = datetime.now(timezone.utc).isoformat()
            try:
                _last_error_session = market_hours.get_session()
            except Exception:
                _last_error_session = None
            _error_count += 1
        if _stop.wait(SHADOW_LOOP_INTERVAL_SECONDS):
            break


def start_shadow_detector() -> None:
    """Arranca el hilo de fondo del detector shadow (una sola vez por
    proceso) -- mismo patrón que `radar_worker.start_universe_radar()`/
    `catalyst_worker.start_catalyst_worker()`."""
    global _thread
    if _thread is not None:
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="unified-detector-shadow", daemon=True)
    _thread.start()


def request_stop() -> None:
    _stop.set()
