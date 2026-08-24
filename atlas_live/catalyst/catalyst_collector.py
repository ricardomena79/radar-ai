"""Collector -- une provider crudo + clasificador + scorer + registro por
ticker/lote (2026-08-23, Motor de Catalizadores). Cada función acá recibe
los datos crudos ya obtenidos (noticia de Finnhub, fila de calendario) y
el precio en vivo ya en memoria -- **nunca llama a un proveedor
directamente** (eso es responsabilidad de `catalyst_worker.py`), lo que
mantiene esta capa testeable con fixtures sintéticas, sin red ni mocks de
HTTP, mismo criterio que separa `universe_quotes.py` (fetch crudo) de
`Quote` (normalización)."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from atlas_live.catalyst import catalyst_classifier as ccl
from atlas_live.catalyst import catalyst_registry as reg
from atlas_live.catalyst import catalyst_score as csc
from atlas_live.memory import market_hours

logger = logging.getLogger(__name__)

# Piso de relevancia para congelar CATALYST_SCORE/MRNA_SIMILARITY_SCORE
# (Fase 6 del plan): "importance != baja" y "lifecycle_state != EXTENDIDA"
# -- un catalizador de baja importancia o que ya corrió del todo no amerita
# congelar un score para calificarlo después.
_SCORE_RELEVANT_IMPORTANCE = {"alta", "media"}
_SCORE_IRRELEVANT_LIFECYCLE = {"EXTENDIDA"}

_HOUR_LABEL = {"bmo": "BMO", "amc": "AMC", "dmh": "TBD"}


def _racional_available(ticker: str) -> Optional[bool]:
    """Mismo patrón exacto que `candidate_registry.live_opportunities()`
    -- se recalcula en vivo, nunca se cachea desde otra fila, `None` si
    `atlas.data.universe` no está disponible por algún motivo."""
    try:
        from atlas.data.universe import is_available
        return is_available(ticker)
    except Exception:
        return None


def _parse_epoch(epoch: Optional[int]) -> Optional[datetime]:
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def _price_change_since_published(price_now: Optional[float], price_then: Optional[float]) -> Optional[float]:
    """`None` explícito (nunca inventado) si falta cualquiera de los dos
    precios -- la clasificación de ciclo de vida ya sabe caer a las ramas
    basadas solo en fecha cuando esto es `None`."""
    if price_now is None or price_then is None or price_then == 0:
        return None
    return (price_now - price_then) / price_then * 100.0


def _maybe_freeze_score(
    ticker: str, catalyst_id: int, now: datetime, catalyst_type: str, importance: str,
    lifecycle_state: str, direction: str, gates_fired_count: int,
    relative_volume_at_detection: Optional[float], change_pct_at_detection: Optional[float],
    price_change_since_published_pct: Optional[float], relative_volume_hoy_peak: Optional[float],
    retroceso_desde_maximo_pct: Optional[float],
) -> None:
    if importance not in _SCORE_RELEVANT_IMPORTANCE or lifecycle_state in _SCORE_IRRELEVANT_LIFECYCLE:
        return
    score = csc.catalyst_score(
        catalyst_type=catalyst_type, importance=importance, lifecycle_state=lifecycle_state,
        direction=direction, gates_fired_count=gates_fired_count,
        relative_volume_at_detection=relative_volume_at_detection,
        change_pct_at_detection=change_pct_at_detection,
        price_change_since_published_pct=price_change_since_published_pct,
    )
    similarity = csc.mrna_similarity_score(
        catalyst_type=catalyst_type, gates_fired_count=gates_fired_count,
        relative_volume_at_detection=relative_volume_at_detection,
        relative_volume_hoy_peak=relative_volume_hoy_peak, direction=direction,
        retroceso_desde_maximo_pct=retroceso_desde_maximo_pct,
    )
    reg.record_score_snapshot(
        ticker=ticker, market_date=market_hours.market_date(now), frozen_at=now.isoformat(),
        catalyst_score=score, mrna_similarity_score=similarity, catalyst_id=catalyst_id,
        score_components={
            "importance": importance, "lifecycle_state": lifecycle_state, "direction": direction,
            "gates_fired_count": gates_fired_count,
            "relative_volume_at_detection": relative_volume_at_detection,
            "change_pct_at_detection": change_pct_at_detection,
            "price_change_since_published_pct": price_change_since_published_pct,
        },
    )


def process_news_item(
    ticker: str, item: Dict[str, Any], now: datetime,
    price_now: Optional[float] = None, price_at_detection: Optional[float] = None,
    gates_fired_count: int = 0, relative_volume_at_detection: Optional[float] = None,
    change_pct_at_detection: Optional[float] = None, relative_volume_hoy_peak: Optional[float] = None,
    retroceso_desde_maximo_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """Normaliza+clasifica+persiste UNA noticia cruda de
    `FinnhubProvider.get_company_news()`. `price_now`/`price_at_detection`
    y el resto de los parámetros técnicos vienen de
    `radar_worker.get_last_quotes()`/`candidate_registry.get_detection()`/
    `alert_stage_history_for_ticker()` -- todos datos que el radar YA
    calcula, cero llamadas nuevas de precio/volumen (llamado por
    `catalyst_worker.py`, que sí tiene acceso a esas fuentes)."""
    headline = item.get("headline") or ""
    summary = item.get("summary")
    clasificado = ccl.classify_catalyst_type(headline, summary)

    published_at = _parse_epoch(item.get("datetime"))
    price_change = _price_change_since_published(price_now, price_at_detection)

    lifecycle = ccl.classify_catalyst_lifecycle(
        event_date=None, published_at=published_at, now=now,
        price_change_since_published_pct=price_change,
    )

    catalyst_id = reg.upsert_catalyst_event(
        ticker=ticker, catalyst_type=clasificado.catalyst_type, headline=headline,
        source="finnhub_company_news", importance=clasificado.importance,
        direction=clasificado.direction, confidence=clasificado.confidence,
        summary=summary, source_id=(str(item["id"]) if item.get("id") is not None else None),
        url=item.get("url"), published_at=(published_at.isoformat() if published_at else None),
        racional_available=_racional_available(ticker),
    )
    reg.record_lifecycle_transition(
        catalyst_id, ticker, now.isoformat(), lifecycle,
        price_change_since_published_pct=price_change,
    )
    _maybe_freeze_score(
        ticker, catalyst_id, now, clasificado.catalyst_type, clasificado.importance, lifecycle,
        clasificado.direction, gates_fired_count, relative_volume_at_detection,
        change_pct_at_detection, price_change, relative_volume_hoy_peak, retroceso_desde_maximo_pct,
    )
    return {
        "catalyst_id": catalyst_id, "ticker": ticker, "catalyst_type": clasificado.catalyst_type,
        "direction": clasificado.direction, "importance": clasificado.importance,
        "lifecycle_state": lifecycle, "price_change_since_published_pct": price_change,
    }


def process_earnings_calendar_item(item: Dict[str, Any], now: datetime) -> Optional[Dict[str, Any]]:
    """Normaliza+clasifica+persiste UNA fila cruda de
    `FinnhubProvider.get_earnings_calendar()`. Dedup por `source_id`
    sintético `"{ticker}:{event_date}"` (determinístico) -- así el mismo
    upsert write-once que ya usan las noticias evita duplicar la fila
    entre sondeos horarios del Tier 2, sin depender del caso especial de
    NULL en el UNIQUE de SQLite. `None` si la fila no trae `symbol`/`date`
    (dato incompleto, se descarta explícitamente, nunca se inventa)."""
    ticker = (item.get("symbol") or "").upper().strip()
    event_date = item.get("date")
    if not ticker or not event_date:
        return None

    importance = ccl.default_importance("EARNINGS")
    event_time = _HOUR_LABEL.get((item.get("hour") or "").lower(), "TBD")
    headline = f"{ticker}: resultados programados para {event_date} ({event_time})"

    lifecycle = ccl.classify_catalyst_lifecycle(
        event_date=event_date, published_at=None, now=now,
        price_change_since_published_pct=None,
    )

    catalyst_id = reg.upsert_catalyst_event(
        ticker=ticker, catalyst_type="EARNINGS", headline=headline,
        source="finnhub_earnings_calendar", importance=importance, direction="NEUTRAL",
        confidence=1.0, source_id=f"{ticker}:{event_date}", event_date=event_date, event_time=event_time,
        racional_available=_racional_available(ticker),
    )
    reg.record_lifecycle_transition(catalyst_id, ticker, now.isoformat(), lifecycle)
    return {
        "catalyst_id": catalyst_id, "ticker": ticker, "catalyst_type": "EARNINGS",
        "lifecycle_state": lifecycle, "event_date": event_date, "event_time": event_time,
    }
