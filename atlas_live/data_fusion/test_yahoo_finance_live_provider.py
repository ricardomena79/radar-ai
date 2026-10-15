"""Tests reales (sin red) de YahooFinanceLiveProvider -- caso STALE_SESSION_FALLBACK.

Llama directo a `_quote_from_info(symbol, info)` con diccionarios `info`
sintéticos que imitan exactamente la forma de `yf.Ticker(...).info` -- no
hace ninguna llamada de red. Cubre el caso real PTEN (2026-08-18): sesión
premarket esperada, pero Yahoo todavía sin `preMarketPrice`.
"""

from atlas_live.data_fusion.yahoo_finance_live_provider import YahooFinanceLiveProvider

BASE_INFO = {
    "regularMarketPrice": 12.21,
    "regularMarketPreviousClose": 12.05,
    "regularMarketTime": 1_700_000_000,
    "regularMarketVolume": 500_000,
    "averageVolume": 400_000,
    "longName": "Patterson-UTI Energy",
}


def _info(**overrides):
    info = dict(BASE_INFO)
    info.update(overrides)
    return info


def test_premarket_esperado_sin_premarket_price_marca_stale_fallback():
    info = _info(marketState="PRE", preMarketPrice=None)
    quote = YahooFinanceLiveProvider()._quote_from_info("PTEN", info)

    assert quote.price_type == "regular"
    assert quote.market_state == "PRE"
    assert quote.stale_session_fallback is True
    assert quote.last_price == 12.21


def test_premarket_con_premarket_price_presente_no_es_stale():
    info = _info(marketState="PRE", preMarketPrice=12.50, preMarketTime=1_700_003_000)
    quote = YahooFinanceLiveProvider()._quote_from_info("PTEN", info)

    assert quote.price_type == "premarket"
    assert quote.stale_session_fallback is False
    assert quote.last_price == 12.50


def test_sesion_regular_no_es_stale():
    info = _info(marketState="REGULAR")
    quote = YahooFinanceLiveProvider()._quote_from_info("PTEN", info)

    assert quote.price_type == "regular"
    assert quote.stale_session_fallback is False


def test_closed_no_es_stale_aunque_use_precio_regular():
    info = _info(marketState="CLOSED")
    quote = YahooFinanceLiveProvider()._quote_from_info("PTEN", info)

    assert quote.price_type == "regular"
    assert quote.stale_session_fallback is False


def test_afterhours_esperado_sin_postmarket_price_marca_stale_fallback():
    info = _info(marketState="POST", postMarketPrice=None)
    quote = YahooFinanceLiveProvider()._quote_from_info("PTEN", info)

    assert quote.price_type == "regular"
    assert quote.market_state == "POST"
    assert quote.stale_session_fallback is True


def test_afterhours_con_postmarket_price_presente_no_es_stale():
    info = _info(marketState="POST", postMarketPrice=11.90, postMarketTime=1_700_010_000)
    quote = YahooFinanceLiveProvider()._quote_from_info("PTEN", info)

    assert quote.price_type == "afterhours"
    assert quote.stale_session_fallback is False


# --- Base del cambio % por sesión (caso real AZN, 2026-10-15) -------------
# Valores reales de Yahoo (`yf.Ticker("AZN").info`) en premarket: último cierre
# regular = regularMarketPrice 162.03; regularMarketPreviousClose 155.82 es el
# cierre de DOS sesiones atrás; preMarketPrice 166.69. Yahoo reporta
# preMarketChangePercent ~ +2.86%. Atlas mostraba +6.98% (contra 155.82).
AZN_PRE = {
    "regularMarketPrice": 162.03,
    "regularMarketPreviousClose": 155.82,
    "regularMarketTime": 1_700_000_000,
    "preMarketPrice": 166.69,
    "preMarketTime": 1_700_003_000,
    "marketState": "PRE",
    "regularMarketVolume": 500_000,
    "averageVolume": 400_000,
}


def test_premarket_cambio_pct_se_mide_contra_ultimo_cierre_regular():
    quote = YahooFinanceLiveProvider()._quote_from_info("AZN", dict(AZN_PRE))

    assert quote.price_type == "premarket"
    assert quote.last_price == 166.69
    assert round(quote.change_percent, 4) == round((166.69 - 162.03) / 162.03 * 100, 4)  # +2.876%
    # consistente con el preMarketChangePercent que reportó Yahoo (+2.857%,
    # lectura apenas distinta en el tiempo) y NO con el valor erróneo anterior
    assert abs(quote.change_percent - 2.857) < 0.05
    assert abs(quote.change_percent - 6.98) > 3


def test_afterhours_cambio_pct_se_mide_contra_cierre_regular_de_hoy():
    info = _info(marketState="POST", postMarketPrice=11.90, postMarketTime=1_700_010_000)
    quote = YahooFinanceLiveProvider()._quote_from_info("PTEN", info)

    # base = regularMarketPrice (12.21), no regularMarketPreviousClose (12.05)
    assert quote.price_type == "afterhours"
    assert round(quote.change_percent, 4) == round((11.90 - 12.21) / 12.21 * 100, 4)


def test_sesion_regular_cambio_pct_sigue_contra_cierre_previo():
    info = _info(marketState="REGULAR")
    quote = YahooFinanceLiveProvider()._quote_from_info("PTEN", info)

    # comportamiento histórico intacto: (12.21 - 12.05) / 12.05
    assert round(quote.change_percent, 4) == round((12.21 - 12.05) / 12.05 * 100, 4)


def test_premarket_sin_precio_premarket_cae_a_regular_y_mantiene_cambio_historico():
    # Fallback "regular" (STALE_SESSION_FALLBACK): el precio usado es el
    # regular, así que su cambio es el de la sesión regular (vs. cierre previo).
    info = _info(marketState="PRE", preMarketPrice=None)
    quote = YahooFinanceLiveProvider()._quote_from_info("PTEN", info)

    assert quote.price_type == "regular"
    assert round(quote.change_percent, 4) == round((12.21 - 12.05) / 12.05 * 100, 4)
