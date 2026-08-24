"""Hilo del Motor de Catalizadores -- cadencia por 3 niveles (2026-08-23,
Fase Worker del plan aprobado, ver ethereal-mixing-anchor.md).

Corre en un hilo daemon COMPLETAMENTE APARTE del radar técnico
(`radar_worker.py`) -- nunca se llama desde `run_sweep_once()`, nunca
bloquea ni depende de él. Degradación segura: si `FINNHUB_API_KEY` no está
configurada, el hilo igual arranca pero cada ciclo se salta sin romper
nada (`provider_health_summary()` queda en `SIN_CONFIGURAR`, la tabla de
sondeos nunca se pobló).

Cadencia declarada honestamente (NO cobertura en tiempo real de todo el
universo -- ver el análisis de límites en el plan aprobado, Finnhub free
tier = 60 llamadas/min):
  - Tier 1 (candidatas activas de HOY, `candidate_registry.list_candidates_for_date`):
    noticias por ticker, cada 5 min.
  - Tier 2 (calendario de earnings, 1 sola llamada por rango de fechas
    para TODO el universo): cada 1 hora.
  - Tier 3 (barrido lento round-robin del resto de Racional, 20 símbolos
    cada 5 min -- cobertura completa ~10h, declarado, no prometido como
    tiempo real): cada 5 min.
Total combinado ~14 llamadas/min -- margen real sobre el límite gratuito.

Cada llamada de red está envuelta en try/except POR TICKER (mismo patrón
que `eod_report.py`) -- un símbolo roto nunca tumba el resto del lote ni
el hilo."""

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from atlas.data.providers.base import ProviderError
from atlas.data.universe import get_equities
from atlas_live.catalyst import catalyst_collector as coll
from atlas_live.catalyst import catalyst_provider as prov
from atlas_live.catalyst import catalyst_registry as reg
from atlas_live.memory import market_hours
from atlas_live.radar import candidate_registry as candreg
from atlas_live.radar import radar_worker

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "si", "sí")


CATALYST_WORKER_ENABLED = _env_bool("ATLAS_CATALYST_WORKER_ENABLED", True)
TIER1_INTERVAL_SECONDS = 300.0
TIER2_INTERVAL_SECONDS = 3600.0
TIER3_INTERVAL_SECONDS = 300.0
TIER3_BATCH_SIZE = 20
LOOP_CHECK_SECONDS = 30.0
TIER2_DAYS_AHEAD = 7
NEWS_LOOKBACK_DAYS = 2
TIER2_POLL_STATE_KEY = "__TIER2_EARNINGS_CALENDAR__"

# Piso de espera ENTRE llamadas sucesivas a Finnhub dentro de un mismo lote
# (2026-08-23, hallazgo real de verificación local): el diseño original de
# ~14 llamadas/min PROMEDIO no bastaba -- un lote de 20 símbolos de Tier 3
# sin ninguna pausa dispara ~10 requests/seg en ráfaga, muy por encima del
# límite por segundo real de Finnhub aunque el promedio por minuto esté
# bien. Verificado en vivo: la key configurada quedó devolviendo HTTP 401
# "Invalid API key" en TODOS los endpoints (incluso /quote, que ya
# funcionaba en producción) después de una corrida sin este piso -- mismo
# patrón "delay_ms" ya usado por scripts/build_historical_reference.py
# para el mismo problema con Tradier.
INTER_CALL_DELAY_SECONDS = 0.35

_stop = threading.Event()
_thread: Optional[threading.Thread] = None
_tier1_last_run = 0.0
_tier2_last_run = 0.0
_tier3_last_run = 0.0
_tier3_cursor = 0


def _price_now(last_quotes: Optional[Dict[str, Any]], ticker: str) -> Optional[float]:
    if not last_quotes:
        return None
    quote = last_quotes.get(ticker)
    return getattr(quote, "last_price", None) if quote is not None else None


def run_tier1_once(
    provider, market_date: str, now: datetime, last_quotes: Optional[Dict[str, Any]] = None,
    inter_call_delay_seconds: float = INTER_CALL_DELAY_SECONDS,
) -> Dict[str, Any]:
    """Noticias de los últimos `NEWS_LOOKBACK_DAYS` días para cada
    candidata YA detectada hoy por el radar técnico -- foco en "qué
    detonó" cada una, cruzadas con el precio de detección real
    (`candidate_detection`) y el precio en vivo (`radar_worker.get_last_quotes()`).
    `inter_call_delay_seconds`: pausa real entre tickers -- ver
    `INTER_CALL_DELAY_SECONDS` (0.0 en tests, para no ralentizarlos)."""
    candidatas = candreg.list_candidates_for_date(market_date)
    desde = (now - timedelta(days=NEWS_LOOKBACK_DAYS)).date().isoformat()
    hasta = now.date().isoformat()
    procesados = 0
    errores = 0
    for i, c in enumerate(candidatas):
        if i > 0 and inter_call_delay_seconds > 0:
            time.sleep(inter_call_delay_seconds)
        ticker = c["ticker"]
        try:
            noticias = provider.get_company_news(ticker, desde, hasta)
        except ProviderError as exc:
            reg.set_poll_state(ticker, ok=False, error=str(exc))
            errores += 1
            continue
        historial_stage = candreg.alert_stage_history_for_ticker(ticker, market_date)
        ultimo_stage = historial_stage[-1] if historial_stage else {}
        for item in noticias:
            coll.process_news_item(
                ticker, item, now, price_now=_price_now(last_quotes, ticker),
                price_at_detection=c.get("price_at_detection"),
                gates_fired_count=len(c.get("gates_fired") or []),
                relative_volume_at_detection=c.get("relative_volume_at_detection"),
                change_pct_at_detection=c.get("change_pct_at_detection"),
                relative_volume_hoy_peak=ultimo_stage.get("relative_volume_hoy"),
                retroceso_desde_maximo_pct=ultimo_stage.get("retroceso_desde_maximo_pct"),
            )
            procesados += 1
        reg.set_poll_state(ticker, ok=True, n_events=len(noticias))
    return {"candidatas": len(candidatas), "eventos_procesados": procesados, "errores": errores}


def run_tier2_once(provider, now: datetime, days_ahead: int = TIER2_DAYS_AHEAD) -> Dict[str, Any]:
    """UNA sola llamada al calendario de earnings para TODO el universo en
    el rango [hoy, hoy+days_ahead] -- así se cubre earnings-inminente sin
    sondear símbolo por símbolo (la pieza que permite descubrir
    catalizadores fuera del watchlist técnico del día)."""
    desde = now.date().isoformat()
    hasta = (now + timedelta(days=days_ahead)).date().isoformat()
    try:
        calendario = provider.get_earnings_calendar(desde, hasta)
    except ProviderError as exc:
        reg.set_poll_state(TIER2_POLL_STATE_KEY, ok=False, error=str(exc))
        return {"filas": 0, "procesadas": 0, "error": str(exc)}
    procesadas = 0
    for item in calendario:
        if coll.process_earnings_calendar_item(item, now):
            procesadas += 1
    reg.set_poll_state(TIER2_POLL_STATE_KEY, ok=True, n_events=procesadas)
    return {"filas": len(calendario), "procesadas": procesadas}


def _tier3_next_batch(symbols: List[str], batch_size: int) -> List[str]:
    global _tier3_cursor
    if not symbols:
        return []
    n = len(symbols)
    start = _tier3_cursor % n
    batch = [symbols[(start + i) % n] for i in range(min(batch_size, n))]
    _tier3_cursor = (start + len(batch)) % n
    return batch


def run_tier3_once(
    provider, now: datetime, symbols: Optional[List[str]] = None,
    batch_size: int = TIER3_BATCH_SIZE, last_quotes: Optional[Dict[str, Any]] = None,
    inter_call_delay_seconds: float = INTER_CALL_DELAY_SECONDS,
) -> Dict[str, Any]:
    """Barrido lento round-robin del universo Racional completo -- cursor
    persistente entre llamadas (`_tier3_cursor`), cobertura completa
    declarada en ~10h con la cadencia por defecto (20 símbolos/5 min sobre
    ~2.500+ símbolos), nunca prometida como tiempo real.
    `inter_call_delay_seconds`: pausa real entre tickers -- ver
    `INTER_CALL_DELAY_SECONDS` (0.0 en tests, para no ralentizarlos)."""
    if symbols is None:
        symbols = sorted({a.symbol for a in get_equities()})
    batch = _tier3_next_batch(symbols, batch_size)
    desde = (now - timedelta(days=NEWS_LOOKBACK_DAYS)).date().isoformat()
    hasta = now.date().isoformat()
    procesados = 0
    errores = 0
    for i, ticker in enumerate(batch):
        if i > 0 and inter_call_delay_seconds > 0:
            time.sleep(inter_call_delay_seconds)
        try:
            noticias = provider.get_company_news(ticker, desde, hasta)
        except ProviderError as exc:
            reg.set_poll_state(ticker, ok=False, error=str(exc))
            errores += 1
            continue
        for item in noticias:
            coll.process_news_item(ticker, item, now, price_now=_price_now(last_quotes, ticker))
            procesados += 1
        reg.set_poll_state(ticker, ok=True, n_events=len(noticias))
    return {"batch": batch, "eventos_procesados": procesados, "errores": errores}


def _run_cycle() -> None:
    global _tier1_last_run, _tier2_last_run, _tier3_last_run
    provider = prov.build_catalyst_provider()
    if provider is None:
        return  # SIN_CONFIGURAR -- provider_health_summary() lo refleja solo

    now = datetime.now(timezone.utc)
    market_date = market_hours.market_date(now)
    last_quotes = radar_worker.get_last_quotes()
    t = time.time()

    if t - _tier1_last_run >= TIER1_INTERVAL_SECONDS:
        try:
            run_tier1_once(provider, market_date, now, last_quotes)
        except Exception:
            logger.exception("catalyst_worker Tier 1 falló")
        _tier1_last_run = t

    if t - _tier2_last_run >= TIER2_INTERVAL_SECONDS:
        try:
            run_tier2_once(provider, now)
        except Exception:
            logger.exception("catalyst_worker Tier 2 falló")
        _tier2_last_run = t

    if t - _tier3_last_run >= TIER3_INTERVAL_SECONDS:
        try:
            run_tier3_once(provider, now, last_quotes=last_quotes)
        except Exception:
            logger.exception("catalyst_worker Tier 3 falló")
        _tier3_last_run = t


def _loop() -> None:
    while not _stop.is_set():
        try:
            _run_cycle()
        except Exception:
            logger.exception("catalyst_worker ciclo falló")
        if _stop.wait(LOOP_CHECK_SECONDS):
            break


def start_catalyst_worker() -> None:
    """Arranca el hilo una sola vez por proceso. No hace nada si está
    deshabilitado por entorno (`ATLAS_CATALYST_WORKER_ENABLED=false`) --
    el radar técnico sigue funcionando sin cambios en cualquier caso."""
    global _thread
    if not CATALYST_WORKER_ENABLED:
        return
    if _thread is not None:
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="catalyst_worker")
    _thread.start()


def request_stop() -> None:
    _stop.set()
