"""Decision Recorder: único escritor autorizado de Knowledge Base y Decision Journal.

Centraliza el registro de todo evento relevante -- decisiones del Decision
Engine, eventos de mercado ya clasificados (explosión, colapso, falsa
ruptura, normal), y operaciones reales del operador -- para que ningún otro
motor tenga que ensamblar a mano un `PredictionRecord`, un `MarketEvent` o
un `Trade` (que es exactamente lo que hacían los scripts de prueba hasta
ahora, repitiendo el mismo trabajo cada vez).

No analiza, no decide, no filtra: solo junta las piezas que ya calcularon
otros módulos (Quote, AtlasScore, MomentumResult, DecisionResult,
MarketContext) y las guarda con la trazabilidad completa (fuente, hora de
captura, estado del dato, versión de los motores).

Escribe en dos destinos, que se mantienen separados según el principio ya
aprobado:
  - Knowledge Base (conocimiento del mercado) -- vía KnowledgeEngine.
  - Decision Journal (conocimiento del operador) -- vía DecisionJournal.
Nunca mezcla uno con otro: cada método escribe exclusivamente en su
destino correspondiente.
"""

import dataclasses
from datetime import datetime, timezone
from typing import Optional

from atlas.data.models.quote import Quote
from atlas.decision_journal.decision_journal import DecisionJournal, Trade
from atlas.engine.atlas_score import AtlasScore
from atlas.engine.decision_engine import DecisionResult
from atlas.engine.market_context_engine import MarketContext
from atlas.engine.momentum_engine import MomentumResult
from atlas.indicators import gap_percent as calculate_gap_percent
from atlas.knowledge.engine_versions import current_versions_json
from atlas.knowledge.event_store import EVENT_TYPES, SOURCE_CALCULATED, STATUS_OK, MarketEvent
from atlas.knowledge.knowledge_engine import KnowledgeEngine
from atlas.knowledge.prediction_store import PredictionRecord

_CONTEXT_FIELDS = [f.name for f in dataclasses.fields(MarketContext)]


def _now_date_time() -> "tuple[str, str]":
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S")


def _context_kwargs(context: Optional[MarketContext]) -> dict:
    """Convierte un MarketContext en kwargs listos para MarketEvent/PredictionRecord
    (ambos usan exactamente los mismos nombres de campo para el contexto)."""
    if context is None:
        return {}
    return dataclasses.asdict(context)


class DecisionRecorder:
    """Único punto de escritura hacia Knowledge Base y Decision Journal."""

    def __init__(self, knowledge_engine: KnowledgeEngine, decision_journal: DecisionJournal) -> None:
        self._knowledge = knowledge_engine
        self._journal = decision_journal

    def record_decision(
        self,
        quote: Quote,
        decision_result: DecisionResult,
        context: Optional[MarketContext] = None,
        rank_in_scan: Optional[int] = None,
        scan_size: Optional[int] = None,
        data_status: str = STATUS_OK,
        date: Optional[str] = None,
        time: Optional[str] = None,
    ) -> int:
        """Registra en Knowledge Base la decisión que produjo Decision Engine para un símbolo."""
        record_date, record_time = (date, time) if date and time else _now_date_time()

        prediction = PredictionRecord(
            date=record_date,
            time=record_time,
            ticker=quote.symbol,
            mode=decision_result.mode,
            decision=decision_result.decision,
            confidence=decision_result.confidence,
            atlas_score=decision_result.atlas_score,
            momentum_score=decision_result.momentum_score,
            money_flow_score=decision_result.money_flow_score,
            data_source=SOURCE_CALCULATED,
            captured_at=datetime.now(timezone.utc).isoformat(),
            data_status=data_status,
            engine_versions=current_versions_json(),
            rank_in_scan=rank_in_scan,
            scan_size=scan_size,
            **_context_kwargs(context),
        )
        return self._knowledge.record_prediction(prediction)

    def record_market_event(
        self,
        quote: Quote,
        event_type: str,
        atlas_score: Optional[AtlasScore] = None,
        momentum_result: Optional[MomentumResult] = None,
        money_flow_score: Optional[float] = None,
        decision_result: Optional[DecisionResult] = None,
        context: Optional[MarketContext] = None,
        max_result_percent: Optional[float] = None,
        close_result_percent: Optional[float] = None,
        rank_in_scan: Optional[int] = None,
        scan_size: Optional[int] = None,
        data_status: str = STATUS_OK,
        date: Optional[str] = None,
        time: Optional[str] = None,
    ) -> int:
        """Registra en Knowledge Base un evento de mercado ya clasificado
        (EXPLOSION, COLLAPSE, FALSE_BREAKOUT o NORMAL)."""
        if event_type not in EVENT_TYPES:
            raise ValueError(f"event_type inválido: '{event_type}'. Válidos: {sorted(EVENT_TYPES)}")

        record_date, record_time = (date, time) if date and time else _now_date_time()

        gap_percent = (
            calculate_gap_percent(quote.open, quote.previous_close)
            if quote.open is not None and quote.previous_close
            else None
        )

        event = MarketEvent(
            date=record_date,
            time=record_time,
            ticker=quote.symbol,
            price=quote.last_price,
            event_type=event_type,
            sector=quote.sector,
            industry=quote.industry,
            gap_percent=gap_percent,
            rvol=quote.relative_volume,
            volume=quote.volume,
            float_shares=quote.float_shares,
            market_cap=quote.market_cap,
            atlas_score=atlas_score.total if atlas_score else None,
            momentum_score=momentum_result.momentum_score if momentum_result else None,
            money_flow_score=money_flow_score,
            decision=decision_result.decision if decision_result else None,
            max_result_percent=max_result_percent,
            close_result_percent=close_result_percent,
            data_source=SOURCE_CALCULATED,
            captured_at=datetime.now(timezone.utc).isoformat(),
            data_status=data_status,
            engine_versions=current_versions_json(),
            rank_in_scan=rank_in_scan,
            scan_size=scan_size,
            **_context_kwargs(context),
        )
        return self._knowledge.record_event(event)

    def record_trade(self, trade: Trade) -> int:
        """Registra en Decision Journal una operación real del operador."""
        return self._journal.record_trade(trade)

    def close(self) -> None:
        """Cierra Knowledge Base y Decision Journal."""
        self._knowledge.close()
        self._journal.close()
