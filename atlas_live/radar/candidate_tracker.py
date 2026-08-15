"""Orquestador de un barrido: Quotes -> historial -> puertas -> registro (2026-08-14).

Punto de unión entre Hilo A (`radar_worker.py`, que produce un
`UniverseQuotesResult` por barrido) y la persistencia (`candidate_registry`).
No hace red, no decide cadencia -- solo procesa UN barrido ya obtenido.
"""

import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

from atlas.data.models.quote import Quote
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


def _quote_to_snapshot(sweep_id: str, observed_at: str, quote: Optional[Quote]) -> SweepSnapshot:
    if quote is None:
        return SweepSnapshot(sweep_id, observed_at, None, None, None, None, None, None)
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
    )


def _tag_phase_at_detection(symbol: str, market_date: str, change_pct, gates_fired_payload: list, session: str) -> None:
    """Calcula y guarda `phase_tag`/`direction_at_detection` (Reinicio
    2026-08-15) en el momento de la primera detección. Import perezoso de
    `reference_registry` (paquete distinto) para no imponer un orden de
    import entre `atlas_live.radar` y `atlas_live.reference`. Si el símbolo
    todavía no tiene historial de referencia, el timing queda con el
    criterio más pobre documentado en `phase_classifier` (nunca se inventa)."""
    try:
        from atlas_live.reference import reference_registry as ref_reg

        percentile_90 = ref_reg.percentile_change_pct(symbol, 0.9)
    except Exception:
        percentile_90 = None
    gate_names = [g["name"] for g in gates_fired_payload]
    tag = pc.from_live_detection(change_pct, gate_names, percentile_90, session)
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
        current = _quote_to_snapshot(sweep_id, observed_at, quote)

        results = gates.evaluate_all_gates(current, prior_history, session)
        disparadas = gates.fired_gates(results)
        for g in disparadas:
            dispersion[g.name] = dispersion.get(g.name, 0) + 1

        if disparadas:
            gates_fired_payload = [{"name": g.name, "reason": g.reason, "value": g.value} for g in disparadas]
            es_nueva = reg.record_detection(
                symbol, market_date, session, observed_at, sweep_id,
                current.price, current.change_pct, current.volume, current.average_volume,
                current.relative_volume, current.dollar_volume, gates_fired_payload,
            )
            if es_nueva:
                nuevas.append(symbol)
                _tag_phase_at_detection(symbol, market_date, current.change_pct, gates_fired_payload, session)
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
            n_obs += 1

        history.push(symbol, current)

    return SweepProcessResult(
        sweep_id=sweep_id, n_evaluados=len(quotes), n_nuevas_detecciones=nuevas,
        n_observaciones_registradas=n_obs, gates_dispersion=dispersion,
    )
