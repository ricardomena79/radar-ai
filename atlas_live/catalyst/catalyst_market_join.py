"""Cross-join catalizador + mercado (2026-08-24, Segunda Fase del Motor de
Catalizadores). Pura -- sin DB, sin red -- recibe datos YA obtenidos por el
caller (`radar_worker.get_last_quotes()`, `candidate_registry.live_opportunities()`)
y solo los combina. Nunca llama a un proveedor: reutiliza exactamente lo
que Tradier ya trae en memoria, mismo criterio "cero llamadas nuevas" que
`api_radar_oportunidades()` ya aplica.

Dos niveles de cobertura, siempre declarados explícitamente, nunca
mezclados en silencio:
  1. `market`: de la última cotización de Tradier (`Quote`) -- precio,
     cambio, volumen, volumen promedio, RVOL, gap (derivado de
     `open`/`previous_close`, ya en el Quote). Disponible para CUALQUIER
     ticker que el barrido de Tradier haya cubierto.
  2. `technical`: de `candidate_registry.live_opportunities()` -- gates,
     dirección, "extensión" (`retroceso_desde_maximo_pct`), timing,
     etapa de alerta. Disponible SOLO si el ticker es TAMBIÉN una
     candidata técnica detectada por el radar HOY -- nunca inventado
     para el resto.

`resistencia_soporte` queda siempre `{"disponible": False}` -- ningún
módulo de Atlas calcula niveles de soporte/resistencia hoy (confirmado
por auditoría de código antes de escribir este archivo). Se declara así
en vez de omitirse, para que quede visible que es una limitación real,
no un olvido (pedido explícito del usuario: "no inventar, pero tampoco
dejar el sistema inútil -- mostrar SIN DATOS")."""

from typing import Any, Dict, Optional

from atlas_live.catalyst import catalyst_score as csc
from atlas_live.catalyst import catalyst_status as cst
from atlas_live.radar import priority_classifier
from atlas_live.radar.alert_stage import VOLUME_ELEVATED_THRESHOLD
from atlas_live.radar.phase_classifier import MOVEMENT_FLOOR_PCT


def _gap_pct(quote: Any) -> Optional[float]:
    open_price = getattr(quote, "open", None)
    previous_close = getattr(quote, "previous_close", None)
    if open_price is None or previous_close in (None, 0):
        return None
    return (open_price - previous_close) / previous_close * 100.0


def join_market_data(
    ticker: str,
    last_quotes: Optional[Dict[str, Any]],
    radar_row: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    quote = last_quotes.get(ticker) if last_quotes else None

    if quote is None:
        market = {
            "market_data_status": "SIN_DATOS",
            "price": None, "change_pct": None, "volume": None,
            "average_volume": None, "relative_volume": None, "gap_pct": None,
        }
    else:
        market = {
            "market_data_status": "OK",
            "price": quote.last_price,
            "change_pct": quote.change_percent,
            "volume": quote.volume,
            "average_volume": quote.average_volume,
            "relative_volume": quote.relative_volume,
            "gap_pct": _gap_pct(quote),
        }

    if radar_row is not None:
        technical = {
            "disponible": True,
            "gates_fired_count": len(radar_row.get("gates_fired") or []),
            "direction": radar_row.get("direction"),
            "retroceso_desde_maximo_pct": radar_row.get("retroceso_desde_maximo_pct"),
            "timing_deteccion_hoy": radar_row.get("timing_deteccion_hoy"),
            "alert_stage": radar_row.get("stage"),
        }
    else:
        technical = {
            "disponible": False,
            "gates_fired_count": 0,
            "direction": None,
            "retroceso_desde_maximo_pct": None,
            "timing_deteccion_hoy": None,
            "alert_stage": None,
        }

    return {
        "market": market,
        "technical": technical,
        "resistencia_soporte": {"disponible": False},
    }


def enrich_catalyst_row(
    catalyst_row: Dict[str, Any],
    lifecycle_state: str,
    last_quotes: Optional[Dict[str, Any]],
    radar_row: Optional[Dict[str, Any]],
    now: Any,
) -> Dict[str, Any]:
    """Une TODO -- mercado (`join_market_data`), scoring (`catalyst_score.py`,
    reutilizado tal cual) y estado (`catalyst_status.py`) -- para UNA fila
    de catalizador. Único punto donde se compone la Segunda Fase completa;
    `server.py` solo junta las fuentes de datos (quotes, radar de hoy) y
    llama acá por cada fila, mismo patrón que ya usa `api_radar_oportunidades()`."""
    ticker = catalyst_row["ticker"]
    catalyst_type = catalyst_row["catalyst_type"]
    importance = catalyst_row["importance"]

    joined = join_market_data(ticker, last_quotes, radar_row)
    market, technical = joined["market"], joined["technical"]

    dias_al_evento = cst.days_to_event(catalyst_row.get("event_date"), now)
    event_status_val = cst.event_status(dias_al_evento, lifecycle_state)

    rvol_deteccion = radar_row.get("relative_volume_at_detection") if radar_row else None
    cambio_deteccion = radar_row.get("change_pct_at_detection") if radar_row else None
    direction = (radar_row.get("direction") if radar_row else None) or catalyst_row.get("direction")

    score = csc.catalyst_score(
        catalyst_type=catalyst_type, importance=importance, lifecycle_state=lifecycle_state,
        direction=catalyst_row.get("direction"),
        gates_fired_count=technical["gates_fired_count"],
        relative_volume_at_detection=rvol_deteccion, change_pct_at_detection=cambio_deteccion,
    )
    technical_confirmation = csc.technical_confirmation_score(
        technical["gates_fired_count"], rvol_deteccion, cambio_deteccion,
    )

    # "EN_EVALUACION" (punto 6 del usuario) en vez de un 0.0 que parecería
    # "estructuralmente distinto de MRNA" cuando en realidad no hay NINGÚN
    # dato técnico propio para juzgar (ticker sin detección de hoy).
    tiene_evidencia_tecnica = (
        technical["gates_fired_count"] > 0
        or rvol_deteccion is not None
        or technical["retroceso_desde_maximo_pct"] is not None
    )
    if tiene_evidencia_tecnica:
        mrna_similarity = csc.mrna_similarity_score(
            catalyst_type=catalyst_type, gates_fired_count=technical["gates_fired_count"],
            relative_volume_at_detection=rvol_deteccion,
            relative_volume_hoy_peak=radar_row.get("relative_volume_hoy") if radar_row else None,
            direction=direction, retroceso_desde_maximo_pct=technical["retroceso_desde_maximo_pct"],
        )
        mrna_similarity_status = "OK"
    else:
        mrna_similarity = None
        mrna_similarity_status = "EN_EVALUACION"

    opportunity_score = csc.catalyst_opportunity_score(
        catalyst_score=score, technical_confirmation=technical_confirmation, catalyst_type=catalyst_type,
        relative_volume=market["relative_volume"], gap_pct=market["gap_pct"],
        dias_al_evento=dias_al_evento, mrna_similarity=mrna_similarity,
        retroceso_desde_maximo_pct=technical["retroceso_desde_maximo_pct"],
    )

    if technical["disponible"]:
        trading_status, trading_status_motivo = priority_classifier.classify_final_priority(
            stage=technical["alert_stage"], direction=technical["direction"],
            change_pct_confiable=radar_row.get("change_pct_confiable") if radar_row else None,
            tiene_precio_actual=(market["market_data_status"] == "OK"),
        )
    else:
        trading_status = cst.catalyst_trading_status(
            opportunity_score, dias_al_evento, market["relative_volume"], market["gap_pct"],
        )
        trading_status_motivo = None

    out = dict(catalyst_row)
    out.update({
        "market": market, "technical": technical, "resistencia_soporte": joined["resistencia_soporte"],
        "lifecycle_state": lifecycle_state, "dias_al_evento": dias_al_evento, "event_status": event_status_val,
        "catalyst_score": score, "technical_confirmation_score": technical_confirmation,
        "mrna_similarity_score": mrna_similarity, "mrna_similarity_status": mrna_similarity_status,
        "catalyst_opportunity_score": opportunity_score,
        "trading_status": trading_status, "trading_status_motivo": trading_status_motivo,
    })
    return out


# Piso de relevancia para entrar al ranking "TOP CATALYST OPPORTUNITIES"
# (punto 10 del usuario -- "un earnings no es automáticamente una
# oportunidad"). Reutiliza umbrales YA existentes en el proyecto
# (`VOLUME_ELEVATED_THRESHOLD`/`MOVEMENT_FLOOR_PCT`, importados arriba),
# nunca inventa uno nuevo.
EVENT_PROXIMITY_ALTA_IMPORTANCIA_DIAS = 3


def is_relevant_for_ranking(enriched_row: Dict[str, Any]) -> bool:
    # `racional_available` viene de SQLite como int (0/1/NULL) en producción
    # y como bool en los tests -- `bool(...)` normaliza ambos casos, nunca
    # comparar con `is True` (1 is not True en Python, aunque 1 == True).
    if not enriched_row.get("racional_available"):
        return False
    market, technical = enriched_row["market"], enriched_row["technical"]
    if technical["disponible"]:
        return True
    rvol = market.get("relative_volume")
    if rvol is not None and rvol >= VOLUME_ELEVATED_THRESHOLD:
        return True
    gap = market.get("gap_pct")
    if gap is not None and abs(gap) >= MOVEMENT_FLOOR_PCT:
        return True
    dias = enriched_row.get("dias_al_evento")
    if enriched_row.get("importance") == "alta" and dias is not None and 0 <= dias <= EVENT_PROXIMITY_ALTA_IMPORTANCIA_DIAS:
        return True
    return False
