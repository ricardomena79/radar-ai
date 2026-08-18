"""Orquestador de un barrido: Quotes -> historial -> puertas -> registro (2026-08-14).

Punto de unión entre Hilo A (`radar_worker.py`, que produce un
`UniverseQuotesResult` por barrido) y la persistencia (`candidate_registry`).
No hace red, no decide cadencia -- solo procesa UN barrido ya obtenido.
"""

import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

from atlas.data.models.quote import Quote
from atlas_live.radar import alert_stage as als
from atlas_live.radar import candidate_gates as gates
from atlas_live.radar import candidate_registry as reg
from atlas_live.radar import phase_classifier as pc
from atlas_live.radar.sweep_history import SweepHistory, SweepSnapshot


@dataclass
class SweepProcessResult:
    sweep_id: str
    n_evaluados: int
    n_nuevas_detecciones: List[str]
    n_observaciones_registradas: int
    gates_dispersion: Dict[str, int]  # nombre de puerta -> cuántos símbolos la dispararon este barrido


def _quote_to_snapshot(sweep_id: str, observed_at: str, quote: Optional[Quote], session: Optional[str] = None) -> SweepSnapshot:
    if quote is None:
        return SweepSnapshot(sweep_id, observed_at, None, None, None, None, None, None, session=session)
    dollar_volume = None
    if quote.last_price is not None and quote.volume is not None:
        dollar_volume = quote.last_price * quote.volume
    return SweepSnapshot(
        sweep_id=sweep_id,
        observed_at=observed_at,
        price=quote.last_price,
        change_pct=quote.change_percent,
        volume=quote.volume,
        average_volume=quote.average_volume,
        relative_volume=quote.relative_volume,
        dollar_volume=dollar_volume,
        session=session,
    )


def _tag_phase_at_detection(
    symbol: str, market_date: str, change_pct, gates_fired_payload: list, session: str,
    relative_volume: Optional[float] = None,
) -> None:
    """Calcula y guarda `phase_tag`/`direction_at_detection` (Reinicio
    2026-08-15) en el momento de la primera detección. Import perezoso de
    `reference_registry` (paquete distinto) para no imponer un orden de
    import entre `atlas_live.radar` y `atlas_live.reference`. Si el símbolo
    todavía no tiene historial de referencia, el timing queda con el
    criterio más pobre documentado en `phase_classifier` (nunca se inventa).

    `relative_volume` (Fase 7, 2026-08-18) permite a `from_live_detection`
    distinguir un `change_pct=0.0` real de uno no confiable por falta de
    operaciones -- ver `phase_classifier.CHANGE_PCT_MIN_RVOL_TO_TRUST_ZERO`."""
    try:
        from atlas_live.reference import reference_registry as ref_reg

        percentile_90 = ref_reg.percentile_change_pct(symbol, 0.9)
    except Exception:
        percentile_90 = None
    gate_names = [g["name"] for g in gates_fired_payload]
    tag = pc.from_live_detection(change_pct, gate_names, percentile_90, session, relative_volume=relative_volume)
    reg.set_phase_tag(symbol, market_date, tag.timing_deteccion, direction_at_detection=tag.direction)


def _tag_experimental_signals_at_detection(symbol: str, market_date: str, quote: Optional[Quote]) -> None:
    """Experimentos A/C (2026-08-16, `PROPUESTA PRIORIZADA DE EXPERIMENTOS`
    aprobada) -- guarda `volatility_14d_pct`/`daily_range_pct` como
    DIAGNÓSTICO puro en `candidate_detection`. Nunca se lee desde
    `candidate_gates.py`/`phase_classifier.py`, no afecta qué se detecta ni
    el orden en que se muestra -- solo permite comparar después, con datos
    reales en vivo, si estas señales mejoran algo frente al baseline actual.

    `daily_range_pct` sale del propio Quote de este barrido (High/Low del
    día hasta este momento, sin red adicional). `volatility_14d_pct` usa la
    última lectura ya guardada en la Base Histórica de Referencia para este
    símbolo (siempre de un día ANTERIOR a hoy, nunca del día en curso) --
    aproximación de costo cero documentada: no se pide historial diario
    fresco por cada candidata, a cambio de no estar actualizada al minuto."""
    daily_range_pct = None
    if quote is not None and quote.high is not None and quote.low is not None and quote.last_price:
        daily_range_pct = round(100 * (quote.high - quote.low) / quote.last_price, 3)

    volatility_14d_pct = None
    try:
        from atlas_live.reference import reference_registry as ref_reg

        volatility_14d_pct = ref_reg.latest_volatility_14d_pct(symbol)
    except Exception:
        volatility_14d_pct = None

    reg.set_experimental_signals(
        symbol, market_date,
        volatility_14d_pct=volatility_14d_pct, daily_range_pct=daily_range_pct,
    )


def _tag_alert_stage(
    symbol: str, market_date: str, observed_at: str, quote: Optional[Quote],
    gates_fired_payload: list, session: str,
) -> None:
    """Capa OBSERVACIONAL de ALERTA TEMPRANA (Fase 4, 2026-08-17) -- calcula
    y registra la ventana actual (`alert_stage.classify_alert_stage`) en
    CADA sweep de una candidata ya vista (no solo la primera detección, a
    diferencia de `_tag_phase_at_detection`), para que el panel en vivo
    muestre el estado real mientras la candidata sigue activa.

    Puramente aditiva: solo lee `reference_registry` (histórico, ya
    construido) y `Quote` (ya disponible en este sweep), y solo ESCRIBE en
    `alert_stage_log` (tabla nueva, propia). Nunca toca `gates_fired`, el
    resultado de `evaluate_all_gates()`, `candidate_detection` ni ninguna
    columna que lea el score en vivo o `DecisionEngine`."""
    try:
        from atlas_live.reference import reference_registry as ref_reg

        percentile_90 = ref_reg.percentile_change_pct(symbol, 0.9)
        recent = ref_reg.recent_daily_features(symbol, n=5)
        volatility_14d_pct = ref_reg.latest_volatility_14d_pct(symbol)
    except Exception:
        percentile_90 = None
        recent = []
        volatility_14d_pct = None

    change_pct = quote.change_percent if quote is not None else None
    relative_volume_hoy = quote.relative_volume if quote is not None else None
    gate_names = [g["name"] for g in gates_fired_payload]
    tag = pc.from_live_detection(change_pct, gate_names, percentile_90, session, relative_volume=relative_volume_hoy)

    # Retroceso desde máximo intradía (2026-08-18, pedido explícito del
    # usuario, caso real YYAI): `reg.record_observation()` de ESTE mismo
    # barrido ya se ejecutó antes de llamar acá (ver process_sweep), así
    # que `max_price_today()` ya incluye el precio actual -- si es un
    # máximo nuevo, el retroceso da 0/None correctamente, nunca negativo.
    retroceso_desde_maximo_pct = None
    if quote is not None and quote.last_price:
        try:
            peak = reg.max_price_today(symbol, market_date)
        except Exception:
            peak = None
        if peak and peak > 0 and quote.last_price < peak:
            retroceso_desde_maximo_pct = round((peak - quote.last_price) / peak * 100, 3)

    dias_volumen_elevado = sum(
        1 for r in recent if (r.get("relative_volume") or 0) >= als.VOLUME_ELEVATED_THRESHOLD
    )
    aceleracion_volumen = None
    if len(recent) >= 2:
        mas_reciente = recent[0].get("relative_volume")
        mas_antiguo = recent[-1].get("relative_volume")
        if mas_reciente is not None and mas_antiguo is not None:
            aceleracion_volumen = round(mas_reciente - mas_antiguo, 3)

    stage = als.classify_alert_stage(
        relative_volume_hoy=relative_volume_hoy, dias_volumen_elevado=dias_volumen_elevado,
        aceleracion_volumen=aceleracion_volumen, volatility_14d_pct=volatility_14d_pct,
        timing_deteccion_hoy=tag.timing_deteccion, direction=tag.direction,
        retroceso_desde_maximo_pct=retroceso_desde_maximo_pct,
    )
    if stage is None:
        return

    racional_available = None
    try:
        from atlas.data.universe import is_available

        racional_available = is_available(symbol)
    except Exception:
        racional_available = None

    reg.record_alert_stage(
        symbol, market_date, observed_at, stage,
        relative_volume_hoy=relative_volume_hoy, volatility_14d_pct=volatility_14d_pct,
        dias_volumen_elevado=dias_volumen_elevado, aceleracion_volumen=aceleracion_volumen,
        timing_deteccion_hoy=tag.timing_deteccion, racional_available=racional_available,
        direction=tag.direction, change_pct_confiable=tag.change_pct_confiable,
        retroceso_desde_maximo_pct=retroceso_desde_maximo_pct,
    )


def process_sweep(
    quotes: Dict[str, Quote],
    history: SweepHistory,
    market_date: str,
    session: str,
    observed_at: str,
    sweep_id: Optional[str] = None,
) -> SweepProcessResult:
    """Procesa UN barrido completo: para cada símbolo con Quote, evalúa las
    8 puertas contra su historial de HOY (antes de empujar el snapshot
    actual -- nunca se compara un barrido consigo mismo), registra primera
    detección (idempotente) y observación continua si alguna puerta disparó,
    y actualiza el historial en memoria para el próximo barrido."""
    sweep_id = sweep_id or uuid.uuid4().hex[:12]

    if history.current_market_date != market_date:
        history.reset_for_new_day(market_date)

    nuevas: List[str] = []
    n_obs = 0
    dispersion: Dict[str, int] = {}

    for symbol, quote in quotes.items():
        prior_history = history.get(symbol)
        current = _quote_to_snapshot(sweep_id, observed_at, quote, session)

        results = gates.evaluate_all_gates(current, prior_history, session)
        disparadas = gates.fired_gates(results)
        for g in disparadas:
            dispersion[g.name] = dispersion.get(g.name, 0) + 1

        if disparadas:
            gates_fired_payload = [{"name": g.name, "reason": g.reason, "value": g.value} for g in disparadas]
            spread_pct_at_detection = None
            if quote.bid is not None and quote.ask is not None:
                mid = (quote.bid + quote.ask) / 2
                spread_pct_at_detection = round((quote.ask - quote.bid) / mid * 100, 4) if mid else None
            es_nueva = reg.record_detection(
                symbol, market_date, session, observed_at, sweep_id,
                current.price, current.change_pct, current.volume, current.average_volume,
                current.relative_volume, current.dollar_volume, gates_fired_payload,
                price_basis_at_detection=quote.price_basis, bid_at_detection=quote.bid,
                ask_at_detection=quote.ask, spread_pct_at_detection=spread_pct_at_detection,
            )
            if es_nueva:
                nuevas.append(symbol)
                _tag_phase_at_detection(symbol, market_date, current.change_pct, gates_fired_payload, session,
                                         relative_volume=current.relative_volume)
                _tag_experimental_signals_at_detection(symbol, market_date, quote)
            else:
                # No es la primera vez que se ve -- pasa a "señal" (Reinicio
                # 2026-08-15, decisión explícita: candidata = 1+ puerta en
                # UN barrido; señal = sigue activa en un barrido posterior,
                # no fue un parpadeo de un solo tick).
                reg.mark_as_signal(symbol, market_date)
            reg.record_observation(
                symbol, market_date, observed_at, sweep_id,
                current.price, current.change_pct, current.volume, current.relative_volume,
                gates_fired_payload,
            )
            _tag_alert_stage(symbol, market_date, observed_at, quote, gates_fired_payload, session)
            reg.compute_interim_outcome(symbol, market_date)
            n_obs += 1
        elif reg.is_detected(symbol, market_date):
            # Ya es candidata de un barrido anterior -- sigue con seguimiento
            # aunque ESTE barrido puntual no haya disparado ninguna puerta
            # (pedido explícito: "no debe desaparecer" del seguimiento).
            # Por la misma razón que arriba, este es al menos el 2do barrido
            # en que se la ve -> también cuenta como señal.
            reg.mark_as_signal(symbol, market_date)
            reg.record_observation(
                symbol, market_date, observed_at, sweep_id,
                current.price, current.change_pct, current.volume, current.relative_volume,
                [],
            )
            _tag_alert_stage(symbol, market_date, observed_at, quote, [], session)
            reg.compute_interim_outcome(symbol, market_date)
            n_obs += 1

        history.push(symbol, current)

    return SweepProcessResult(
        sweep_id=sweep_id, n_evaluados=len(quotes), n_nuevas_detecciones=nuevas,
        n_observaciones_registradas=n_obs, gates_dispersion=dispersion,
    )
