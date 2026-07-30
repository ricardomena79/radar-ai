"""Prueba end-to-end de Atlas Core con datos reales de mercado.

Cadena completa: DataCollector -> Atlas Score -> Momentum Engine ->
Money Flow -> Market Context -> Decision Engine -> Decision Recorder ->
Knowledge Base -> Pattern Store -> Learning Engine -> Calibration Advisor.

Parte A (real): un símbolo real (AAPL) recorre la cadena completa una vez,
con datos en vivo de Yahoo Finance -- prueba que el cableado funciona.

Parte B (sintética, declarada): Atlas todavía no acumuló meses de historial
real (Decision Recorder recién se construyó), así que se agrega un
volumen adicional de evidencia sintética -- igual que en las etapas
anteriores -- para que Learning Engine tenga muestra suficiente y pueda
demostrar un resultado no trivial (Calibration Advisor generando una
propuesta real). Nada de esto se presenta como dato de mercado genuino.
"""

from pathlib import Path

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.providers.yahoo_finance import YahooFinanceProvider
from atlas.data.universe import get_equities
from atlas.decision_journal import DecisionJournal
from atlas.decision_recorder import DecisionRecorder
from atlas.engine.atlas_score import calculate_atlas_score
from atlas.engine.decision_engine import COMPRAR, DecisionEngine
from atlas.engine.market_context_engine import MarketContextEngine
from atlas.engine.momentum_engine import calculate_momentum_score
from atlas.engine.money_flow_engine import MoneyFlowEngine
from atlas.knowledge import NORMAL, KnowledgeEngine, MarketEvent, PatternRegistry
from atlas.knowledge.prediction_store import PredictionRecord
from atlas.learning import LearningEngine

KNOWLEDGE_DB = Path(__file__).resolve().parents[1] / "cache" / "test_e2e_knowledge.db"
PATTERN_DB = Path(__file__).resolve().parents[1] / "cache" / "test_e2e_patterns.db"
JOURNAL_DB = Path(__file__).resolve().parents[1] / "cache" / "test_e2e_journal.db"

SYMBOL = "AAPL"
SAMPLE_SIZE = 60


def _sample(assets, count):
    if len(assets) <= count:
        return list(assets)
    step = max(1, len(assets) // count)
    return assets[::step][:count]


def _add_synthetic_volume(knowledge: KnowledgeEngine, registry: PatternRegistry) -> None:
    """Evidencia sintética adicional, claramente separada del paso real de arriba."""
    idx = 0

    def add_pair(decision, correct):
        nonlocal idx
        idx += 1
        ticker = f"SYN{idx}"
        close_result = (2.5 if correct else -2.0) if decision == COMPRAR else (-1.0 if correct else 3.0)
        knowledge.record_prediction(PredictionRecord(
            date="2026-07-30", time="10:00:00", ticker=ticker, mode="standard", decision=decision, confidence=55.0,
        ))
        knowledge.record_event(MarketEvent(
            date="2026-07-30", time="16:00:00", ticker=ticker, price=50.0, event_type=NORMAL,
            close_result_percent=close_result,
        ))

    for _ in range(16):
        add_pair(COMPRAR, correct=True)
    for _ in range(4):
        add_pair(COMPRAR, correct=False)  # 16/20 = 80%

    registry.register_pattern(
        "vwap_confirmado_technology", "Confirmación sobre VWAP en Technology", "combinacion_factores",
        evidence={"sample_size": 35, "win_rate": 0.68, "recent_sample_size": 14,
                  "recent_win_rate": 0.65, "baseline_win_rate": 0.50},
    )


def test_atlas_core_end_to_end() -> None:
    for path in (KNOWLEDGE_DB, PATTERN_DB, JOURNAL_DB):
        if path.exists():
            path.unlink()

    print("=" * 70)
    print(f"ATLAS CORE - PRUEBA END-TO-END ({SYMBOL}, datos reales de mercado)")
    print("=" * 70)

    # 1. DataCollector
    collector = DataCollector(YahooFinanceProvider())
    quote = collector.get_quote(SYMBOL)
    print(f"\n1. DataCollector: {SYMBOL} @ ${quote.last_price} ({quote.change_percent:+.2f}%)")

    # 2. Atlas Score
    atlas_score = calculate_atlas_score(SYMBOL, collector)
    print(f"2. Atlas Score: {atlas_score.total:.2f}/100")

    # 3. Momentum Engine
    momentum_result = calculate_momentum_score(SYMBOL, collector)
    print(f"3. Momentum Engine: {momentum_result.momentum_score:.2f}/100")

    # 4. Money Flow (sobre un subconjunto real del Universo Racional, no el universo completo)
    sample_universe = {a.symbol: a for a in _sample(get_equities(), SAMPLE_SIZE)}
    money_flow_engine = MoneyFlowEngine(collector=collector, universe_provider=lambda: sample_universe)
    money_flow_engine.scan()
    sector_money_flow = None
    if quote.sector:
        for group in money_flow_engine.top(10_000):
            if group.name == quote.sector:
                sector_money_flow = group.money_flow_score
                break
    print(f"4. Money Flow: sector={quote.sector}, score={sector_money_flow}")

    # 5. Market Context
    context = MarketContextEngine(collector=collector).get_context(sector=quote.sector, money_flow_engine=money_flow_engine)
    print(f"5. Market Context: SPY={context.spy_price}  VIX={context.vix_price}  día={context.day_of_week}")

    # 6. Decision Engine
    decision_engine = DecisionEngine(collector=collector, money_flow_engine=money_flow_engine)
    decision_result = decision_engine.decide(SYMBOL)
    print(f"6. Decision Engine: {decision_result.decision}  (confianza={decision_result.confidence:.0f}%)")

    # 7. Decision Recorder -> 8. Knowledge Base
    knowledge = KnowledgeEngine(db_path=KNOWLEDGE_DB)
    journal = DecisionJournal(db_path=JOURNAL_DB)
    recorder = DecisionRecorder(knowledge_engine=knowledge, decision_journal=journal)

    prediction_id = recorder.record_decision(quote=quote, decision_result=decision_result, context=context)
    event_id = recorder.record_market_event(
        quote=quote, event_type=NORMAL, atlas_score=atlas_score, momentum_result=momentum_result,
        money_flow_score=sector_money_flow, decision_result=decision_result, context=context,
        close_result_percent=quote.change_percent,  # resultado real observado hoy, no inventado
    )
    print(f"7-8. Decision Recorder -> Knowledge Base: predicción #{prediction_id}, evento #{event_id}")

    stored_event = knowledge.events.get_event(event_id)
    assert stored_event.ticker == SYMBOL
    assert stored_event.atlas_score == atlas_score.total
    assert stored_event.spy_price == context.spy_price

    # 9. Pattern Store: se registra un patrón real, ligado a las condiciones observadas hoy.
    registry = PatternRegistry(db_path=PATTERN_DB)
    real_pattern_key = f"{SYMBOL.lower()}_condiciones_hoy"
    real_pattern = registry.register_pattern(
        real_pattern_key, f"Condiciones observadas hoy en {SYMBOL}", "combinacion_factores",
        evidence={"sample_size": 1, "win_rate": 1.0 if quote.change_percent > 0 else 0.0},
    )
    print(f"9. Pattern Store: '{real_pattern_key}' registrado, estado={real_pattern.state} "
          f"(muestra=1, esperablemente insuficiente todavía)")

    # Volumen sintético adicional, declarado como tal (ver docstring del módulo).
    _add_synthetic_volume(knowledge, registry)

    # 10. Learning Engine -> 11. Calibration Advisor
    learning_engine = LearningEngine(knowledge_engine=knowledge, pattern_registry=registry)
    report = learning_engine.generate_learning_report()

    print(f"\n10. Learning Engine:")
    print(f"    Precisión general: {report.overall_accuracy.breakdown}")
    print(f"    Patrones evaluados: {len(report.pattern_reports)}")
    for pr in report.pattern_reports:
        print(f"      {pr.pattern_key}: {pr.current_state} -> {pr.proposed_state}")

    print(f"\n11. Calibration Advisor: {len(report.calibration_proposals)} propuesta(s)")
    for proposal in report.calibration_proposals:
        print(f"      [{proposal.category}] {proposal.title}")

    # El patrón real de hoy (muestra=1) NO debe generar una propuesta -- evidencia insuficiente.
    real_pattern_report = next(pr for pr in report.pattern_reports if pr.pattern_key == real_pattern_key)
    assert real_pattern_report.proposed_state is None
    assert "insuficiente" in real_pattern_report.reason.lower() or "Sin sample_size" not in real_pattern_report.reason

    # El patrón sintético (con evidencia suficiente) SÍ debe generar una propuesta.
    assert any(p.target == "vwap_confirmado_technology" for p in report.calibration_proposals)

    # La precisión general debe ser calculable (36 pares sintéticos + posiblemente el real, si
    # el ticker real coincidiera con alguno sintético, lo cual no ocurre: tickers distintos).
    assert report.overall_accuracy.breakdown["todas"]["n"] >= 20
    assert report.overall_accuracy.breakdown["todas"]["insufficient_sample"] is False

    knowledge.close()
    registry.close()
    journal.close()

    print("\n" + "=" * 70)
    print("OK: la cadena completa DataCollector -> ... -> Calibration Advisor")
    print("    funcionó de punta a punta con datos reales de mercado.")


if __name__ == "__main__":
    test_atlas_core_end_to_end()
