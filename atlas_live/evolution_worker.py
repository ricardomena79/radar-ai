"""Motor de Evolución de Atlas Live.

Revisita cada evento EXPLOSION (una recomendación COMPRAR real) en los
checkpoints de su ciclo de vida (+5m, +15m, +30m, +60m, cierre de sesión,
día siguiente), mide con datos reales de mercado qué pasó, y clasifica el
estado del movimiento (Motor de Agotamiento). Cuando se completa un
checkpoint final (cierre o día siguiente), cierra el ciclo del evento
(`EventStore.update_event_outcome`) y del patrón que lo originó
(`PatternRegistry.record_outcome`), y dispara el informe diario de esa
fecha si todavía no existe.

Se llama al final de cada `run_scan_once()` -- misma cadencia de 5 minutos,
mismo hilo, sin infraestructura nueva. No decide nada, no cambia pesos de
ningún motor: solo observa, mide y registra evidencia real.

Limitaciones conocidas, documentadas a propósito (no son bugs escondidos):
- La granularidad de precio entre checkpoints es de barras de 15 minutos
  (yfinance intradía), no tick a tick.
- El cierre de sesión y el día siguiente se calculan sobre el horario
  regular de Wall Street (16:00 hora de Nueva York) sin calendario de
  feriados: un feriado corre el checkpoint "día siguiente" un día, no lo
  invalida (el dato que trae sigue siendo real, solo que de un día en que
  el mercado no operó).
"""

import traceback
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.providers.yahoo_finance import YahooFinanceProvider
from atlas.engine.exhaustion_engine import classify_movement_state
from atlas.engine.momentum_engine import calculate_momentum_score
from atlas.knowledge import (
    CHECKPOINT_EOD,
    CHECKPOINT_NEXT_DAY,
    CHECKPOINTS_IN_ORDER,
    EXPLOSION,
    FINAL_CHECKPOINTS,
    DailyReport,
    DailyReportStore,
    EventObservation,
    EventObservationStore,
    EventStore,
    MarketEvent,
    PatternRegistry,
)

NY_TZ = ZoneInfo("America/New_York")
MARKET_CLOSE_HOUR_NY = 16

CHECKPOINT_OFFSET_MINUTES = {
    "+5m": 5,
    "+15m": 15,
    "+30m": 30,
    "+60m": 60,
}

# Cuántos días hacia atrás se buscan eventos con checkpoints todavía
# pendientes. 3 días cubre de sobra el checkpoint "día siguiente" incluso
# cruzando un fin de semana.
LOOKBACK_DAYS = 3

# Cuántas barras de historia intradía se piden para reconstruir el máximo,
# el mínimo y el momento del máximo entre el evento y el checkpoint.
HISTORY_PERIOD = "5d"
HISTORY_INTERVAL = "15m"


def _event_datetime(event: MarketEvent) -> datetime:
    """Reconstruye el instante (UTC) en que se registró un evento, a partir
    de sus columnas date+time (ambas ya en UTC: ver decision_recorder.py)."""
    return datetime.strptime(f"{event.date} {event.time}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _market_close_utc(local_date_ny) -> datetime:
    """16:00 hora de Nueva York de una fecha dada, convertido a UTC."""
    close_ny = datetime.combine(local_date_ny, datetime.min.time(), tzinfo=NY_TZ).replace(
        hour=MARKET_CLOSE_HOUR_NY
    )
    return close_ny.astimezone(timezone.utc)


def _next_weekday(local_date_ny):
    """Siguiente día calendario, saltando sábado y domingo (no maneja
    feriados de mercado: ver limitaciones en el docstring del módulo)."""
    next_day = local_date_ny + timedelta(days=1)
    while next_day.weekday() >= 5:  # 5=sábado, 6=domingo
        next_day += timedelta(days=1)
    return next_day


def checkpoint_target_time(event_dt: datetime, checkpoint: str) -> datetime:
    """Instante (UTC) en el que un checkpoint queda "vencido" para un evento."""
    if checkpoint in CHECKPOINT_OFFSET_MINUTES:
        return event_dt + timedelta(minutes=CHECKPOINT_OFFSET_MINUTES[checkpoint])

    event_date_ny = event_dt.astimezone(NY_TZ).date()
    eod_utc = _market_close_utc(event_date_ny)
    if checkpoint == CHECKPOINT_EOD:
        # Si el evento ya ocurrió después del cierre (fuera de horario),
        # el checkpoint de cierre queda vencido de inmediato.
        return max(eod_utc, event_dt)
    if checkpoint == CHECKPOINT_NEXT_DAY:
        next_day_ny = _next_weekday(event_date_ny)
        return _market_close_utc(next_day_ny)
    raise ValueError(f"Checkpoint desconocido: '{checkpoint}'")


def _due_checkpoints(event: MarketEvent, completed: set, now: datetime) -> List[str]:
    """Checkpoints de un evento que todavía no se registraron y ya vencieron."""
    event_dt = _event_datetime(event)
    due = []
    for checkpoint in CHECKPOINTS_IN_ORDER:
        if checkpoint in completed:
            continue
        if checkpoint_target_time(event_dt, checkpoint) <= now:
            due.append(checkpoint)
    return due


def _price_stats_since_event(collector: DataCollector, ticker: str, event_dt: datetime, observed_at: datetime):
    """Máximo, mínimo y momento (minutos desde el evento) del máximo, usando
    barras intradía reales entre el evento y el checkpoint. Devuelve
    (max_price, min_price, minutes_to_max) -- cualquiera puede ser None si
    no hay barras en esa ventana (evento muy reciente, sin datos todavía)."""
    try:
        history = collector.get_history(ticker, period=HISTORY_PERIOD, interval=HISTORY_INTERVAL)
    except Exception:
        return None, None, None

    if history is None or history.empty:
        return None, None, None

    index = history.index
    if index.tz is None:
        index = index.tz_localize("UTC")
    else:
        index = index.tz_convert("UTC")
    history = history.copy()
    history.index = index

    window = history[(history.index >= event_dt) & (history.index <= observed_at)]
    if window.empty:
        return None, None, None

    max_price = float(window["High"].max())
    min_price = float(window["Low"].min())
    max_at = window["High"].idxmax()
    minutes_to_max = int((max_at - event_dt).total_seconds() // 60)
    return max_price, min_price, minutes_to_max


def _build_observation(
    collector: DataCollector,
    event: MarketEvent,
    checkpoint: str,
    money_flow_score: Optional[float],
    previous_momentum_score: Optional[float],
) -> Optional[EventObservation]:
    """Calcula una revisita con datos reales de mercado. None si el dato
    todavía no está disponible (ej. ticker deslistado, error de proveedor)."""
    event_dt = _event_datetime(event)
    observed_at = datetime.now(timezone.utc)

    try:
        quote = collector.get_quote(event.ticker)
    except Exception:
        return None

    max_price, min_price, minutes_to_max = _price_stats_since_event(collector, event.ticker, event_dt, observed_at)
    current_price = quote.last_price
    max_price = current_price if max_price is None else max(max_price, current_price)
    min_price = current_price if min_price is None else min(min_price, current_price)

    if not event.price:
        return None
    return_percent = (current_price - event.price) / event.price * 100
    max_return_percent = (max_price - event.price) / event.price * 100

    try:
        momentum_result = calculate_momentum_score(event.ticker, collector)
        momentum_score = momentum_result.momentum_score
        rvol_component = momentum_result.component("relative_volume")
        rvol_score = rvol_component.score if rvol_component else None
    except Exception:
        momentum_score = None
        rvol_score = None

    movement_state = classify_movement_state(
        return_percent=return_percent,
        max_return_percent=max_return_percent,
        momentum_score=momentum_score,
        rvol_score=rvol_score,
        previous_momentum_score=previous_momentum_score,
    )

    return EventObservation(
        event_id=event.id,
        ticker=event.ticker,
        checkpoint=checkpoint,
        observed_at=observed_at.isoformat(),
        price=current_price,
        max_price_since_event=max_price,
        min_price_since_event=min_price,
        max_return_percent=round(max_return_percent, 2),
        return_percent=round(return_percent, 2),
        minutes_to_max=minutes_to_max,
        volume=quote.volume,
        momentum_score=momentum_score,
        money_flow_score=money_flow_score,
        movement_state=movement_state,
        data_status="OK",
    )


def _money_flow_score_for(event: MarketEvent) -> Optional[float]:
    """Reutiliza el Money Flow Engine ya cacheado del último escaneo (igual
    que get_symbol_detail en scan_worker.py) en vez de volver a escanear
    todo el universo solo para una revisita."""
    from atlas_live.scan_worker import STATE

    money_flow_engine = STATE.money_flow_engine
    if money_flow_engine is None or not event.sector:
        return None
    for group in money_flow_engine.top(10_000):
        if group.name == event.sector:
            return group.money_flow_score
    return None


def process_due_evolutions() -> None:
    """Punto de entrada: revisa todos los eventos EXPLOSION recientes con
    checkpoints vencidos, los registra con datos reales, cierra el ciclo de
    los que llegan a un checkpoint final, y genera el informe diario si
    corresponde. Nunca lanza: cada evento se procesa en su propio try/except
    para que un error puntual no le impida seguir al resto."""
    now = datetime.now(timezone.utc)
    today = now.date()
    start_date = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()

    event_store = EventStore()
    observation_store = EventObservationStore()
    pattern_registry = PatternRegistry()
    collector = DataCollector(YahooFinanceProvider())

    try:
        pending_events = event_store.get_events(
            event_type=EXPLOSION, start_date=start_date, end_date=today.isoformat(), limit=1000
        )

        dates_touched = set()
        for event in pending_events:
            try:
                completed = observation_store.completed_checkpoints(event.id)
                due = _due_checkpoints(event, completed, now)
                if not due:
                    continue

                observations_so_far = observation_store.get_observations_for_event(event.id)
                previous_momentum_score = observations_so_far[-1].momentum_score if observations_so_far else None
                money_flow_score = _money_flow_score_for(event)

                for checkpoint in due:
                    observation = _build_observation(
                        collector, event, checkpoint, money_flow_score, previous_momentum_score
                    )
                    if observation is None:
                        continue
                    observation_store.record_observation(observation)
                    previous_momentum_score = observation.momentum_score
                    dates_touched.add(event.date)

                    if checkpoint in FINAL_CHECKPOINTS:
                        _close_event_cycle(event_store, pattern_registry, event, observation, checkpoint)
            except Exception:
                continue  # un evento con error no debe frenar al resto

        for date in dates_touched:
            try:
                _maybe_generate_daily_report(event_store, observation_store, pattern_registry, date)
            except Exception:
                continue
    finally:
        event_store.close()
        observation_store.close()
        pattern_registry.close()


def _close_event_cycle(
    event_store: EventStore,
    pattern_registry: PatternRegistry,
    event: MarketEvent,
    final_observation: EventObservation,
    checkpoint: str,
) -> None:
    """Completa el resultado del evento y, si tenía un patrón asociado y este
    es su checkpoint final de verdad, acumula el resultado en la evidencia
    del patrón.

    Un evento pasa por dos checkpoints "finales" (eod y next_day) -- el
    resultado del evento (`update_event_outcome`) se actualiza en los dos,
    porque next_day siempre extiende/afina la medición de eod y no hay
    nada que perder al sobrescribir. Pero el resultado del PATRÓN
    (`record_outcome`) solo puede contarse una vez por evento, o un mismo
    evento inflaría wins/losses dos veces (confirmado con un evento de
    prueba: sin esta guarda, un evento cuyos dos checkpoints finales caen
    en el mismo ciclo de revisión duplicaba el conteo). CHECKPOINT_NEXT_DAY
    es siempre el último en ocurrir, así que es el único punto seguro para
    cerrar el patrón sin importar en qué orden o agrupación lleguen los
    checkpoints.
    """
    event_store.update_event_outcome(
        event_id=event.id,
        max_result_percent=final_observation.max_return_percent,
        close_result_percent=final_observation.return_percent,
    )
    if event.pattern_key and checkpoint == CHECKPOINT_NEXT_DAY:
        won = final_observation.return_percent is not None and final_observation.return_percent > 0
        pattern_registry.record_outcome(
            pattern_key=event.pattern_key, won=won, return_percent=final_observation.return_percent or 0.0
        )


def _maybe_generate_daily_report(
    event_store: EventStore,
    observation_store: EventObservationStore,
    pattern_registry: PatternRegistry,
    date: str,
) -> None:
    """Genera el informe diario de una fecha si todavía no existe y ya hay
    al menos una recomendación de ese día con checkpoint de cierre
    completado (si ninguna llegó a "eod" todavía, esperar al próximo tick)."""
    report_store = DailyReportStore()
    try:
        if report_store.exists(date):
            return

        day_events = event_store.get_events(event_type=EXPLOSION, start_date=date, end_date=date, limit=1000)
        if not day_events:
            return

        evaluated = []
        for event in day_events:
            observations = observation_store.get_observations_for_event(event.id)
            eod_obs = next((o for o in observations if o.checkpoint == CHECKPOINT_EOD), None)
            if eod_obs is not None and eod_obs.return_percent is not None:
                evaluated.append((event, eod_obs))

        if not evaluated:
            return  # ninguna recomendación de hoy llegó todavía a cierre de sesión

        all_day_events = event_store.get_events(start_date=date, end_date=date, limit=100_000)
        total_tickers_analyzed = len({e.ticker for e in all_day_events})

        wins = sum(1 for _, obs in evaluated if obs.return_percent > 0)
        losses = len(evaluated) - wins
        win_rate = round(wins / len(evaluated) * 100, 1)
        avg_return = round(sum(obs.return_percent for _, obs in evaluated) / len(evaluated), 2)

        best_event, best_obs = max(evaluated, key=lambda pair: pair[1].return_percent)
        worst_event, worst_obs = min(evaluated, key=lambda pair: pair[1].return_percent)

        pattern_stats: dict = {}
        for event, obs in evaluated:
            if not event.pattern_key:
                continue
            stats = pattern_stats.setdefault(event.pattern_key, {"wins": 0, "count": 0, "total_return": 0.0})
            stats["count"] += 1
            stats["total_return"] += obs.return_percent
            if obs.return_percent > 0:
                stats["wins"] += 1

        pattern_rows = [
            {
                "pattern_key": key,
                "win_rate": round(s["wins"] / s["count"] * 100, 1),
                "avg_return_percent": round(s["total_return"] / s["count"], 2),
                "count": s["count"],
            }
            for key, s in pattern_stats.items()
        ]
        pattern_rows.sort(key=lambda r: r["win_rate"], reverse=True)
        top_patterns = pattern_rows[:3]
        bottom_patterns = list(reversed(pattern_rows[-3:])) if len(pattern_rows) > 3 else []

        self_assessment = _build_self_assessment(
            win_rate=win_rate,
            avg_return=avg_return,
            top_patterns=top_patterns,
            bottom_patterns=bottom_patterns,
            evaluated_count=len(evaluated),
            total_recommendations=len(day_events),
        )

        report = DailyReport(
            date=date,
            total_tickers_analyzed=total_tickers_analyzed,
            total_recommendations=len(day_events),
            wins=wins,
            losses=losses,
            win_rate=win_rate,
            avg_return_percent=avg_return,
            best_ticker=best_event.ticker,
            best_return_percent=best_obs.return_percent,
            worst_ticker=worst_event.ticker,
            worst_return_percent=worst_obs.return_percent,
            top_patterns=top_patterns,
            bottom_patterns=bottom_patterns,
            self_assessment=self_assessment,
        )
        report_store.save(report)
    finally:
        report_store.close()


def _build_self_assessment(
    win_rate: float,
    avg_return: float,
    top_patterns: list,
    bottom_patterns: list,
    evaluated_count: int,
    total_recommendations: int,
) -> dict:
    """Arma las 5 respuestas de autoevaluación como texto generado a partir
    de los números reales de arriba -- ninguna frase inventa un dato que no
    esté en `top_patterns`/`bottom_patterns`/`win_rate`/`avg_return`."""
    best = f"'{top_patterns[0]['pattern_key']}' ({top_patterns[0]['win_rate']}% de acierto)" if top_patterns else "ninguno con suficiente evidencia todavía"
    worst = f"'{bottom_patterns[0]['pattern_key']}' ({bottom_patterns[0]['win_rate']}% de acierto)" if bottom_patterns else "ninguno con suficiente evidencia todavía"

    return {
        "que_hizo_bien": (
            f"De {evaluated_count} recomendaciones evaluadas hoy, {win_rate}% resultaron positivas "
            f"al cierre, con un retorno promedio de {avg_return:+.2f}%."
        ),
        "que_hizo_mal": (
            f"{round(100 - win_rate, 1)}% de las recomendaciones evaluadas hoy no resultaron positivas al cierre."
            if win_rate < 100
            else "Todas las recomendaciones evaluadas hoy resultaron positivas al cierre."
        ),
        "patrones_que_funcionaron_mejor": f"El patrón que mejor funcionó hoy fue {best}.",
        "patrones_que_dejaron_de_funcionar": f"El patrón con peor resultado hoy fue {worst}.",
        "que_aprendio_hoy": (
            f"Se evaluaron {evaluated_count} de {total_recommendations} recomendaciones emitidas hoy "
            f"(el resto todavía no llegó a su checkpoint de cierre)."
        ),
    }
