"""Tests reales (sin red) de `_apply_stale_fallback_guard` -- Fase 8.

Función pura: no requiere mockear ningún ciclo de escaneo. Cubre los 3
casos del plan aprobado: stale + elegible, stale + ya inelegible por otro
motivo (no se pisa el motivo original), y no-stale (sin cambios).
"""

from atlas.data.models.quote import Quote
from atlas_live.explosive_engine import ExplosiveResult
from atlas_live.scan_worker import (
    STALE_SESSION_DISPLAY_DECISION,
    STALE_SESSION_EXCLUDED_REASON,
    _apply_stale_fallback_guard,
)


def _quote(stale_session_fallback: bool) -> Quote:
    return Quote(
        symbol="PTEN",
        name="Patterson-UTI Energy",
        last_price=12.21,
        change_percent=1.3,
        volume=500_000,
        open=12.10,
        high=12.30,
        low=12.05,
        previous_close=12.05,
        stale_session_fallback=stale_session_fallback,
    )


def test_stale_y_elegible_pasa_a_inelegible_con_motivo_y_display_datos_antiguos():
    quote = _quote(stale_session_fallback=True)
    explosive_result = ExplosiveResult(eligible=True, score=87.5, reasons=["rvol_alto"])
    display_decision = {"code": "COMPRAR", "emoji": "✅", "label": "Comprar"}

    result, display = _apply_stale_fallback_guard(quote, explosive_result, display_decision)

    assert result.eligible is False
    assert result.excluded_reason == STALE_SESSION_EXCLUDED_REASON
    assert display == STALE_SESSION_DISPLAY_DECISION


def test_stale_y_ya_inelegible_por_otro_motivo_no_pisa_el_motivo_original():
    quote = _quote(stale_session_fallback=True)
    explosive_result = ExplosiveResult(
        eligible=False, score=None, reasons=[], excluded_reason="price: fuera de rango",
    )
    display_decision = {"code": "DESCARTAR", "emoji": "❌", "label": "Descartar"}

    result, display = _apply_stale_fallback_guard(quote, explosive_result, display_decision)

    assert result.eligible is False
    assert result.excluded_reason == "price: fuera de rango"
    assert display == STALE_SESSION_DISPLAY_DECISION


def test_no_stale_no_cambia_nada():
    quote = _quote(stale_session_fallback=False)
    explosive_result = ExplosiveResult(eligible=True, score=87.5, reasons=["rvol_alto"])
    display_decision = {"code": "COMPRAR", "emoji": "✅", "label": "Comprar"}

    result, display = _apply_stale_fallback_guard(quote, explosive_result, display_decision)

    assert result is explosive_result
    assert display is display_decision
