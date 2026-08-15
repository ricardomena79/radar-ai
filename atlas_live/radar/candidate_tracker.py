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
