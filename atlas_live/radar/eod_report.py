"""Evaluación de cierre del radar (2026-08-14).

Al cerrar el mercado regular, para cada candidata detectada ese día se
calcula qué pasó DESPUÉS del momento exacto de la detección -- no el
resultado del día completo. Esta es la métrica fundamental pedida
explícitamente: si una acción terminó +100% pero Atlas la detectó cuando
ya llevaba +85%, el recorrido real capturable era +15%, no +100%.

`run_up_before_detection_pct` = `change_pct_at_detection` (ya es exactamente
eso: cuánto se había movido ANTES de que el radar la viera, respecto al
cierre anterior -- no hace falta recalcularlo).

`max_return_after_detection_pct` se calcula con las velas de 1 minuto de
Tradier del día completo, tomando solo las velas POSTERIORES a
`detected_at` -- separación anti-leakage estricta con la detección misma.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from atlas.data.models.quote import Quote
from atlas.data.providers.base import ProviderError, QuoteNotFoundError
from atlas.data.providers.tradier_provider import TradierProvider
from atlas.data.providers.tradier_symbol_map import normalize as normalize_symbol
from atlas_live.radar import candidate_registry as reg

# Categorías descriptivas, no un veredicto predictivo -- ver docstring del
# módulo. Los pisos son deliberadamente simples y quedan documentados acá.
FALSA_SENAL_CEILING_PCT = 3.0        # el movimiento posterior no llegó ni a esto
DETECCION_TARDIA_RATIO = 0.3          # si lo que quedaba es menos del 30% de lo que ya había corrido
MEJOR_OPORTUNIDAD_FLOOR_PCT = 50.0
BUENA_OPORTUNIDAD_FLOOR_PCT = 20.0

# Para el chequeo de "oportunidades no detectadas": piso de cambio % del día
# para que un símbolo NO-candidata se considere digno de mención -- mismo
# piso modesto que gate_price_change, no uno más estricto.
MISSED_OPPORTUNITY_MIN_CHANGE_PCT = 10.0


@dataclass
class CandidateOutcome:
    ticker: str
    run_up_before_detection_pct: Optional[float]
    max_price_after_detection: Optional[float]
    max_return_after_detection_pct: Optional[float]
    minutes_to_max: Optional[float]
    reached_20: bool
    reached_50: bool
    reached_100: bool
    category: str
    notes: Optional[str] = None


def _categorize(run_up_before: Optional[float], max_after: Optional[float]) -> str:
    if max_after is None:
        return "sin_datos_posteriores"
    if max_after < FALSA_SENAL_CEILING_PCT:
        return "falsa_senal"
    if run_up_before is not None and run_up_before > 0 and max_after < run_up_before * DETECCION_TARDIA_RATIO:
        return "deteccion_tardia"
    if max_after >= MEJOR_OPORTUNIDAD_FLOOR_PCT:
        return "mejor_oportunidad"
    if max_after >= BUENA_OPORTUNIDAD_FLOOR_PCT:
        return "buena_oportunidad"
    return "oportunidad_moderada"


def evaluate_candidate_outcome(
    ticker: str, detected_at: str, price_at_detection: Optional[float],
    change_pct_at_detection: Optional[float], tradier_provider: TradierProvider,
    query_symbol: Optional[str] = None,
) -> CandidateOutcome:
    """Calcula el resultado real posterior de UNA candidata, con las velas
    de 1 minuto del día completo. No escribe nada -- función pura sobre un
    fetch ya hecho acá adentro (única función de este módulo con red)."""
    symbol = query_symbol or ticker
    try:
        df = tradier_provider.get_intraday_timesales(symbol, interval="1min", session_filter="all")
    except (QuoteNotFoundError, ProviderError) as exc:
        return CandidateOutcome(ticker, change_pct_at_detection, None, None, None, False, False, False,
                                 "sin_datos_posteriores", notes=f"timesales no disponible: {exc}")

    if df is None or df.empty or price_at_detection is None:
        return CandidateOutcome(ticker, change_pct_at_detection, None, None, None, False, False, False,
                                 "sin_datos_posteriores", notes="sin velas o sin precio de detección")

    try:
        detected_ts = pd.Timestamp(detected_at)
        if detected_ts.tzinfo is None:
            detected_ts = detected_ts.tz_localize("UTC")
        else:
            detected_ts = detected_ts.tz_convert("UTC")
    except Exception:
        return CandidateOutcome(ticker, change_pct_at_detection, None, None, None, False, False, False,
                                 "sin_datos_posteriores", notes="detected_at inválido")

    posteriores = df[df.index > detected_ts]
    if posteriores.empty:
        return CandidateOutcome(ticker, change_pct_at_detection, None, 0.0, 0.0, False, False, False,
                                 "falsa_senal", notes="sin velas posteriores a la detección (día ya terminado o detección al cierre)")

    max_row_idx = posteriores["High"].idxmax()
    max_price = float(posteriores.loc[max_row_idx, "High"])
    max_return_pct = round(100 * (max_price - price_at_detection) / price_at_detection, 3) if price_at_detection else None
    minutes_to_max = round((max_row_idx - detected_ts).total_seconds() / 60.0, 1)

    reached_20 = max_return_pct is not None and max_return_pct >= 20
    reached_50 = max_return_pct is not None and max_return_pct >= 50
    reached_100 = max_return_pct is not None and max_return_pct >= 100
    category = _categorize(change_pct_at_detection, max_return_pct)

    return CandidateOutcome(
        ticker=ticker, run_up_before_detection_pct=change_pct_at_detection,
        max_price_after_detection=max_price, max_return_after_detection_pct=max_return_pct,
        minutes_to_max=minutes_to_max, reached_20=reached_20, reached_50=reached_50,
        reached_100=reached_100, category=category,
    )


@dataclass
class EODReport:
    market_date: str
    n_candidatas: int
    n_evaluadas: int
    n_reached_20: int
    n_reached_50: int
    n_reached_100: int
    n_falsas_senales: int
    n_deteccion_tardia: int
    mejores_oportunidades: List[Dict[str, Any]]
    posibles_no_detectadas: List[Dict[str, Any]] = field(default_factory=list)
    errores: List[str] = field(default_factory=list)


def run_eod_evaluation(
    market_date: str, tradier_provider: TradierProvider,
    symbol_query_map: Optional[Dict[str, str]] = None,
    last_sweep_quotes: Optional[Dict[str, Quote]] = None,
) -> EODReport:
    """Corre la evaluación de cierre para TODAS las candidatas del día que
    todavía no tengan `candidate_outcome` (idempotente -- si se corre dos
    veces, no recalcula ni duplica). `symbol_query_map` es el mismo mapeo
    de normalización usado en el barrido (ticker original -> símbolo real
    consultado en Tradier), para pedir el timesales correcto."""
    candidatas = reg.list_candidates_for_date(market_date)
    n_reached_20 = n_reached_50 = n_reached_100 = 0
    n_falsas = n_tardias = 0
    mejores: List[Dict[str, Any]] = []
    errores: List[str] = []
    n_evaluadas = 0

    for c in candidatas:
        ticker = c["ticker"]
        if reg.has_outcome(ticker, market_date):
            continue
        query_symbol = (symbol_query_map or {}).get(ticker) or normalize_symbol(ticker).query_symbol
        outcome = evaluate_candidate_outcome(
            ticker, c["detected_at"], c["price_at_detection"], c["change_pct_at_detection"],
            tradier_provider, query_symbol=query_symbol,
        )
        reg.record_outcome(
            ticker, market_date, outcome.run_up_before_detection_pct, outcome.max_price_after_detection,
            outcome.max_return_after_detection_pct, outcome.minutes_to_max, outcome.reached_20,
            outcome.reached_50, outcome.reached_100, outcome.category, outcome.notes,
        )
        n_evaluadas += 1
        if outcome.reached_20:
            n_reached_20 += 1
        if outcome.reached_50:
            n_reached_50 += 1
        if outcome.reached_100:
            n_reached_100 += 1
        if outcome.category == "falsa_senal":
            n_falsas += 1
        if outcome.category == "deteccion_tardia":
            n_tardias += 1
        if outcome.category in ("mejor_oportunidad", "buena_oportunidad"):
            mejores.append({
                "ticker": ticker, "detected_at": c["detected_at"],
                "price_at_detection": c["price_at_detection"],
                "run_up_before_pct": outcome.run_up_before_detection_pct,
                "max_return_after_pct": outcome.max_return_after_detection_pct,
                "minutes_to_max": outcome.minutes_to_max, "category": outcome.category,
            })
        if outcome.notes and "no disponible" in (outcome.notes or ""):
            errores.append(f"{ticker}: {outcome.notes}")

    mejores.sort(key=lambda x: (x["max_return_after_pct"] or 0), reverse=True)

    posibles_no_detectadas: List[Dict[str, Any]] = []
    if last_sweep_quotes:
        detectados = {c["ticker"] for c in candidatas}
        for symbol, q in last_sweep_quotes.items():
            if symbol in detectados:
                continue
            if q.change_percent is not None and abs(q.change_percent) >= MISSED_OPPORTUNITY_MIN_CHANGE_PCT:
                posibles_no_detectadas.append({"ticker": symbol, "change_pct_final": q.change_percent})
        posibles_no_detectadas.sort(key=lambda x: abs(x["change_pct_final"] or 0), reverse=True)

    reg.set_meta(eod_ejecutado_para=market_date)

    return EODReport(
        market_date=market_date, n_candidatas=len(candidatas), n_evaluadas=n_evaluadas,
        n_reached_20=n_reached_20, n_reached_50=n_reached_50, n_reached_100=n_reached_100,
        n_falsas_senales=n_falsas, n_deteccion_tardia=n_tardias,
        mejores_oportunidades=mejores[:20], posibles_no_detectadas=posibles_no_detectadas[:20],
        errores=errores[:20],
    )
