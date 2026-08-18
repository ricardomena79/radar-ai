"""Tests reales (sin red) de `_apply_stale_fallback_guard` -- Fase 8.

Función pura: no requiere mockear ningún ciclo de escaneo. Cubre los 3
casos del plan aprobado: stale + elegible, stale + ya inelegible por otro
motivo (no se pisa el motivo original), y no-stale (sin cambios).

Extendido 2026-08-18 (Tradier como fuente operativa única, caso real
SBLK): `_apply_non_tradier_source_guard`, `compute_price_age_seconds`,
`is_price_stale`, y las 2 funciones de "recalcular en cada request"
(`apply_serving_freshness_to_ranking_row`/`_to_memory_candidate`).
"""

from datetime import datetime, timezone

from atlas.data.models.quote import Quote
from atlas_live.explosive_engine import ExplosiveResult
from atlas_live.scan_worker import (
    NO_TRADIER_SOURCE_DISPLAY_DECISION,
    NO_TRADIER_SOURCE_EXCLUDED_REASON,
    STALE_PRICE_DISPLAY_DECISION,
    STALE_PRICE_EXCLUDED_REASON,
    STALE_SESSION_DISPLAY_DECISION,
    STALE_SESSION_EXCLUDED_REASON,
    _apply_non_tradier_source_guard,
    _apply_stale_fallback_guard,
    apply_serving_freshness_to_memory_candidate,
    apply_serving_freshness_to_ranking_row,
    compute_price_age_seconds,
    is_change_pct_coherent,
    is_price_stale,
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


# --- Tradier como fuente operativa única (2026-08-18, caso real SBLK) ---

def _quote_source(source: str) -> Quote:
    return Quote(
        symbol="SBLK", name="Star Bulk Carriers", last_price=30.02, change_percent=3.3,
        volume=500_000, open=29.5, high=30.2, low=29.4, previous_close=29.06, source=source,
    )


def test_fuente_no_tradier_pasa_a_inelegible_con_datos_no_disponibles():
    quote = _quote_source("yahoo_finance")
    explosive_result = ExplosiveResult(eligible=True, score=90.0, reasons=["rvol_alto"])
    display_decision = {"code": "COMPRAR", "emoji": "✅", "label": "Comprar"}

    result, display = _apply_non_tradier_source_guard(quote, explosive_result, display_decision)

    assert result.eligible is False
    assert result.excluded_reason == NO_TRADIER_SOURCE_EXCLUDED_REASON
    assert display == NO_TRADIER_SOURCE_DISPLAY_DECISION


def test_fuente_finnhub_tambien_pasa_a_inelegible():
    quote = _quote_source("finnhub")
    explosive_result = ExplosiveResult(eligible=True, score=50.0, reasons=[])
    display_decision = {"code": "COMPRAR", "emoji": "✅", "label": "Comprar"}

    result, display = _apply_non_tradier_source_guard(quote, explosive_result, display_decision)

    assert result.eligible is False
    assert display == NO_TRADIER_SOURCE_DISPLAY_DECISION


def test_fuente_no_tradier_ya_inelegible_por_otro_motivo_no_pisa_el_motivo():
    quote = _quote_source("yahoo_finance")
    explosive_result = ExplosiveResult(
        eligible=False, score=None, reasons=[], excluded_reason="price: fuera de rango",
    )
    display_decision = {"code": "DESCARTAR", "emoji": "❌", "label": "Descartar"}

    result, display = _apply_non_tradier_source_guard(quote, explosive_result, display_decision)

    assert result.excluded_reason == "price: fuera de rango"
    assert display == NO_TRADIER_SOURCE_DISPLAY_DECISION


def test_fuente_tradier_no_cambia_nada():
    quote = _quote_source("tradier")
    explosive_result = ExplosiveResult(eligible=True, score=90.0, reasons=["rvol_alto"])
    display_decision = {"code": "COMPRAR", "emoji": "✅", "label": "Comprar"}

    result, display = _apply_non_tradier_source_guard(quote, explosive_result, display_decision)

    assert result is explosive_result
    assert display is display_decision


def test_quote_sin_source_atributo_no_rompe():
    class _SinSource:
        pass

    explosive_result = ExplosiveResult(eligible=True, score=1.0, reasons=[])
    display_decision = {"code": "X"}
    result, display = _apply_non_tradier_source_guard(_SinSource(), explosive_result, display_decision)
    assert result.eligible is False
    assert display == NO_TRADIER_SOURCE_DISPLAY_DECISION


# --- Antigüedad recalculada al servir el request (2026-08-18, punto 4) ---

_NOW = datetime(2026, 8, 18, 11, 0, 0, tzinfo=timezone.utc)


def test_compute_price_age_seconds_calcula_diferencia_real():
    age = compute_price_age_seconds("2026-08-18T10:57:00+00:00", now=_NOW)
    assert age == 180.0


def test_compute_price_age_seconds_sin_timestamp_es_none():
    assert compute_price_age_seconds(None, now=_NOW) is None
    assert compute_price_age_seconds("", now=_NOW) is None


def test_compute_price_age_seconds_timestamp_invalido_es_none():
    assert compute_price_age_seconds("no-es-una-fecha", now=_NOW) is None


def test_is_price_stale_dentro_del_umbral_no_es_stale():
    assert is_price_stale("2026-08-18T10:59:00+00:00", now=_NOW, max_age_seconds=180) is False


def test_is_price_stale_supera_el_umbral_es_stale():
    # SBLK real: price_as_of 10:01:10, "ahora" 10:49:04 -> ~48 min de atraso.
    assert is_price_stale("2026-08-18T10:01:10+00:00", now=_NOW, max_age_seconds=180) is True


def test_is_price_stale_sin_timestamp_siempre_es_stale():
    assert is_price_stale(None, now=_NOW) is True


def test_apply_serving_freshness_a_ranking_row_stale_fuerza_no_recomendar():
    row = {
        "symbol": "SBLK",
        "display_decision": {"code": "COMPRAR", "emoji": "✅", "label": "Comprar"},
        "explosive": {"eligible": True, "excluded_reason": None, "metrics": {"price_as_of": "2026-08-18T10:01:10+00:00"}},
    }
    out = apply_serving_freshness_to_ranking_row(row, now=_NOW)

    assert out["price_age_seconds"] == (_NOW - datetime(2026, 8, 18, 10, 1, 10, tzinfo=timezone.utc)).total_seconds()
    assert out["display_decision"] == STALE_PRICE_DISPLAY_DECISION
    assert out["explosive"]["eligible"] is False
    assert out["explosive"]["excluded_reason"] == STALE_PRICE_EXCLUDED_REASON
    # nunca muta el dict original (el mismo objeto sigue cacheado en STATE)
    assert row["explosive"]["eligible"] is True
    assert row["display_decision"]["code"] == "COMPRAR"


def test_apply_serving_freshness_a_ranking_row_fresco_no_cambia_display():
    row = {
        "symbol": "AAPL",
        "display_decision": {"code": "COMPRAR", "emoji": "✅", "label": "Comprar"},
        "explosive": {"eligible": True, "excluded_reason": None, "metrics": {"price_as_of": "2026-08-18T10:59:00+00:00"}},
    }
    out = apply_serving_freshness_to_ranking_row(row, now=_NOW)

    assert out["price_age_seconds"] == 60.0
    assert out["display_decision"]["code"] == "COMPRAR"
    assert out["explosive"]["eligible"] is True


def test_apply_serving_freshness_a_memory_candidate_stale_fuerza_no_recomendar():
    candidate = {
        "symbol": "SBLK", "price_as_of": "2026-08-18T10:01:10+00:00",
        "eligible_radar": True, "semaforo": "verde", "radar_excluded_reason": None,
    }
    out = apply_serving_freshness_to_memory_candidate(candidate, now=_NOW)

    assert out["eligible_radar"] is False
    assert out["semaforo"] == "rojo"
    assert out["radar_excluded_reason"] == STALE_PRICE_EXCLUDED_REASON
    assert candidate["eligible_radar"] is True  # no muta el original


def test_apply_serving_freshness_a_memory_candidate_fresco_no_cambia_nada():
    candidate = {
        "symbol": "AAPL", "price_as_of": "2026-08-18T10:59:00+00:00",
        "eligible_radar": True, "semaforo": "verde", "radar_excluded_reason": None,
    }
    out = apply_serving_freshness_to_memory_candidate(candidate, now=_NOW)

    assert out["eligible_radar"] is True
    assert out["semaforo"] == "verde"


# --- Coherencia del % de cambio (2026-08-18, caso E, punto 8) ---
# Tolerancia de VALIDACIÓN DE DATOS (redondeo), no un umbral de trading.

def test_is_change_pct_coherent_caso_coherente():
    # (30.02 - 29.06) / 29.06 * 100 = 3.303...% -- Tradier reporta 3.30%
    assert is_change_pct_coherent(30.02, 29.06, 3.30) is True


def test_is_change_pct_coherent_caso_e_incoherente():
    # Tradier reporta +3.3% pero last_price/previous_close implican -8.7%
    # -- diferencia de ~12 puntos porcentuales, muy por encima de la
    # tolerancia de redondeo (1.0 pp).
    assert is_change_pct_coherent(30.02, 32.9, 3.3) is False


def test_is_change_pct_coherent_dentro_de_la_tolerancia_de_redondeo():
    # (30.02 - 29.06) / 29.06 * 100 = 3.303...% -- Tradier reporta 3.9%
    # (0.6pp de diferencia, dentro de la tolerancia de 1.0pp).
    assert is_change_pct_coherent(30.02, 29.06, 3.9) is True


def test_is_change_pct_coherent_sin_previous_close_no_se_puede_verificar():
    # Nunca se inventa un fallo por falta de evidencia.
    assert is_change_pct_coherent(30.02, None, 3.3) is True
    assert is_change_pct_coherent(30.02, 0, 3.3) is True


def test_is_change_pct_coherent_sin_change_percent_no_se_puede_verificar():
    assert is_change_pct_coherent(30.02, 29.06, None) is True
