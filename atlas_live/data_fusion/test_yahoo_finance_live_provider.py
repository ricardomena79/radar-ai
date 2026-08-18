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
