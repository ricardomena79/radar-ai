"""Test aislado (sin red) del fix de 2026-08-18 -- caso real "0 ciclos con
datos" en scan_worker.py durante premarket.

Causa raíz: `calculate_momentum_score()` llamaba `score_gap_pct(quote.open,
quote.previous_close)` sin proteger contra `quote.open is None` -- Tradier
no reporta apertura de la sesión REGULAR mientras esa sesión no abrió hoy
(premarket), así que `gap_percent()` lanzaba `ValueError` sin capturar,
tumbando el símbolo completo en `_score_symbol()` (atlas_live/scan_worker.py).
Este test reproduce exactamente ese caso con un stub, sin red."""

import pandas as pd
import pytest

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.models.quote import Quote
from atlas.data.providers.base import DataProvider
from atlas.engine.momentum_engine import calculate_momentum_score


def _daily_history():
    return pd.DataFrame({
        "Open": [10.0] * 30, "High": [11.0] * 30, "Low": [9.0] * 30,
        "Close": [10.5] * 30, "Volume": [100000] * 30,
    })


class _StubProvider(DataProvider):
    def __init__(self, quote: Quote):
        self._quote = quote

    def get_quote(self, symbol):
        return self._quote

    def get_quotes(self, symbols):
        return [self._quote]

    def get_history(self, symbol, period="6mo", interval="1d"):
        return _daily_history()


def _quote(open_price):
    return Quote(
        symbol="COIN", name="Coinbase", last_price=250.0, change_percent=1.5,
        volume=500_000, open=open_price, high=255.0, low=248.0, previous_close=246.5,
        average_volume=400_000,
    )


def test_gap_pct_degrada_a_neutro_sin_lanzar_cuando_open_es_none():
    """Caso real: Tradier en premarket, `quote.open=None` -- antes esto
    lanzaba ValueError sin capturar; ahora debe degradar a un componente
    neutro (score=50), como ya hace vwap_distance para el mismo tipo de
    ausencia de dato."""
    collector = DataCollector(_StubProvider(_quote(open_price=None)))
    result = calculate_momentum_score("COIN", collector)

    gap_component = next(c for c in result.components if c.name == "gap_pct")
    assert gap_component.score == 50.0
    assert "no disponible" in gap_component.explanation.lower()
    # el resto del cálculo sigue funcionando -- no se pierde el símbolo entero
    assert 0 <= result.momentum_score <= 100


def test_gap_pct_se_calcula_normalmente_cuando_open_esta_presente():
    """Con `quote.open` real, el comportamiento es EXACTAMENTE el mismo
    de siempre -- este fix no cambia el cálculo, solo el caso de ausencia."""
    collector = DataCollector(_StubProvider(_quote(open_price=248.0)))
    result = calculate_momentum_score("COIN", collector)

    gap_component = next(c for c in result.components if c.name == "gap_pct")
    # gap = (248.0 - 246.5) / 246.5 * 100 = 0.6085...% -> score = 50 + gap*5
    assert gap_component.score == pytest.approx(50 + ((248.0 - 246.5) / 246.5 * 100) * 5, abs=0.01)
    assert "no disponible" not in gap_component.explanation.lower()
