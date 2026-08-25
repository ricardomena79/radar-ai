"""Fase 1D (2026-08-24) -- caso G del pedido: verificar que `DecisionEngine`
nunca presenta un precio (BID_ONLY o cualquier otro) como si fuera
ejecutable. `quote.last_price` SÍ alimenta el cálculo interno de scores
(VWAP distance/ATR/liquidez/ruptura -- uso de SEÑAL/momentum, explícitamente
permitido), pero `DecisionResult` -- lo único que se expone hacia afuera --
nunca contiene un campo de precio en absoluto: la decisión es puramente
categórica (COMPRAR/VIGILAR/DESCARTAR) + explicación textual, nunca un
"comprá a $X". Test estructural, sin red -- no requiere mockear todo el
pipeline de `DecisionEngine.decide()`.
"""

from dataclasses import fields

from atlas.engine.decision_engine import DecisionResult

_PRICE_LIKE_NAMES = {"price", "entry_price", "last_price", "executable_price", "signal_price", "buy_price"}


def test_decision_result_nunca_expone_un_campo_de_precio():
    campos = {f.name for f in fields(DecisionResult)}
    assert campos.isdisjoint(_PRICE_LIKE_NAMES), (
        f"DecisionResult expone un campo de precio ({campos & _PRICE_LIKE_NAMES}) -- "
        "riesgo de que BID_ONLY (u otro precio de señal) se presente como ejecutable."
    )


def test_decision_result_es_puramente_categorico():
    campos = {f.name for f in fields(DecisionResult)}
    # La decisión es SIEMPRE categórica (COMPRAR/VIGILAR/DESCARTAR) + scores
    # (0-100) + explicación textual -- nunca un valor en dólares.
    assert "decision" in campos
    assert campos == {
        "symbol", "mode", "decision", "confidence", "atlas_score", "momentum_score",
        "money_flow_score", "met_conditions", "missing_conditions", "next_events",
        "unavailable_conditions",
    }
