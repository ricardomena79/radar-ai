"""Tests de multi_source_resolver.py (2026-08-31, autorizado explícitamente).
Puro, sin red, sin DB -- Quotes sintéticos vía SimpleNamespace, mismos
campos reales que atlas/data/models/quote.py."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from atlas_live.data_fusion import multi_source_resolver as res

NOW = datetime(2026, 9, 1, 14, 0, 0, tzinfo=timezone.utc)  # martes 10:00 ET (regular)


def _q(last_price=100.0, previous_close=95.0, change_percent=5.26, timestamp=None,
       price_is_stale=False, price_basis="tradier_last", price_overnight=None):
    return SimpleNamespace(
        last_price=last_price, previous_close=previous_close, change_percent=change_percent,
        timestamp=timestamp or NOW, price_is_stale=price_is_stale, price_basis=price_basis,
        price_overnight=price_overnight,
    )


# --- A: Tradier fresco -> gana Tradier ---

def test_A_tradier_fresco_gana():
    tq = _q(last_price=101.0, price_is_stale=False)
    r = res.resolver_mejor_precio("AAA", tq, None, None, NOW)
    assert r.source == "tradier"
    assert r.price == 101.0
    assert r.is_stale is False


# --- B: Tradier stale + Yahoo fresco -> gana Yahoo ---

def test_B_tradier_stale_yahoo_fresco_gana_yahoo():
    tq = _q(last_price=101.0, price_is_stale=True, price_basis="tradier_regular_close_stale")
    yq = _q(last_price=102.5, timestamp=NOW - timedelta(seconds=30))
    r = res.resolver_mejor_precio("AAA", tq, yq, None, NOW)
    assert r.source == "yahoo"
    assert r.price == 102.5
    assert r.is_stale is False


# --- C: Tradier stale + Yahoo stale + Finnhub fresco -> gana Finnhub ---

def test_C_tradier_yahoo_stale_finnhub_fresco_gana_finnhub():
    tq = _q(last_price=101.0, price_is_stale=True)
    yq = _q(last_price=99.0, timestamp=NOW - timedelta(hours=3))  # viejo, aunque Yahoo diga stale=False
    fq = _q(last_price=103.2, timestamp=NOW - timedelta(seconds=10))
    r = res.resolver_mejor_precio("AAA", tq, yq, fq, NOW)
    assert r.source == "finnhub"
    assert r.price == 103.2
    assert r.is_stale is False


# --- D: las 3 stale + cache -> gana cache ---

def test_D_las_tres_stale_usa_cache():
    tq = _q(last_price=101.0, price_is_stale=True)
    yq = _q(last_price=99.0, timestamp=NOW - timedelta(hours=3))
    fq = _q(last_price=98.5, timestamp=NOW - timedelta(hours=5))
    cached = {"price": 100.0, "previous_close": 95.0, "change_pct": 5.26,
              "cached_at": NOW - timedelta(minutes=10), "price_basis": "tradier_last"}
    r = res.resolver_mejor_precio("AAA", tq, yq, fq, NOW, cached=cached)
    assert r.source == "cache"
    assert r.price == 100.0
    assert r.is_stale is True


# --- E: las 3 sin dato (y sin cache) -> SIN_DATO ---

def test_E_sin_ninguna_fuente_ni_cache_da_sin_dato():
    r = res.resolver_mejor_precio("AAA", None, None, None, NOW, cached=None)
    assert r.source == "sin_dato"
    assert r.price is None
    assert r.session == "SIN_DATO"


# --- F: Yahoo con flag stale incorrecto (price_is_stale=False) pero timestamp viejo ---

def test_F_yahoo_flag_incorrecto_pero_timestamp_viejo_se_detecta_stale():
    tq = _q(last_price=101.0, price_is_stale=True)
    yq = _q(last_price=99.0, timestamp=NOW - timedelta(hours=54), price_is_stale=False)  # flag miente
    r = res.resolver_mejor_precio("AAA", tq, yq, None, NOW)
    # Yahoo NO debe ganar -- su timestamp real (54h) esta fuera del umbral,
    # sin importar que su propio flag diga False.
    assert r.source != "yahoo"


# --- G: timestamp de viernes recibido durante "lunes" (NOW) -> stale ---

def test_G_timestamp_viejo_de_otro_dia_queda_stale():
    viernes = NOW - timedelta(days=3)
    tq = _q(last_price=101.0, price_is_stale=True, timestamp=viernes)
    yq = _q(last_price=99.0, timestamp=viernes, price_is_stale=False)
    r = res.resolver_mejor_precio("AAA", tq, yq, None, NOW, cached=None)
    assert r.source != "yahoo"  # el timestamp de Yahoo tambien es de viernes -- stale real
    # sin cache, cae al ultimo stale disponible (D-bis) -- nunca None si hay un numero real
    assert r.price is not None
    assert r.is_stale is True


# --- H/I/J: sesion se determina por el timestamp real del dato ganador ---

def test_H_precio_fresco_premarket_conserva_premarket():
    ts_premarket = datetime(2026, 9, 1, 9, 0, 0, tzinfo=timezone.utc)  # 05:00 ET martes
    tq = _q(last_price=101.0, price_is_stale=False, timestamp=ts_premarket)
    r = res.resolver_mejor_precio("AAA", tq, None, None, ts_premarket)
    assert r.session == "PREMARKET"


def test_I_precio_fresco_regular_da_regular():
    ts_regular = datetime(2026, 9, 1, 15, 0, 0, tzinfo=timezone.utc)  # 11:00 ET martes
    tq = _q(last_price=101.0, price_is_stale=False, timestamp=ts_regular)
    r = res.resolver_mejor_precio("AAA", tq, None, None, ts_regular)
    assert r.session == "REGULAR"


def test_J_precio_fresco_afterhours_da_afterhours():
    ts_ah = datetime(2026, 9, 1, 21, 0, 0, tzinfo=timezone.utc)  # 17:00 ET martes
    tq = _q(last_price=101.0, price_is_stale=False, timestamp=ts_ah)
    r = res.resolver_mejor_precio("AAA", tq, None, None, ts_ah)
    assert r.session == "AFTERHOURS"


def test_J2_precio_fresco_en_ventana_overnight_da_overnight():
    """2026-09-01, autorizado explícitamente: `market_hours.get_session()`
    ahora clasifica 20:00-04:00 ET como 'overnight' (antes 'closed', que
    `_clasificar_sesion()` mapeaba a CLOSED_UNKNOWN) -- confirma que la
    nueva llave del mapeo (`multi_source_resolver.py`) se usa de verdad,
    vía el camino NORMAL (Caso A/Tradier fresco), no el mecanismo aparte
    de `price_overnight` (ver K2 más abajo, que sigue siendo un caso
    distinto)."""
    ts_overnight = datetime(2026, 9, 2, 2, 0, 0, tzinfo=timezone.utc)  # 22:00 ET martes
    tq = _q(last_price=101.0, price_is_stale=False, timestamp=ts_overnight)
    r = res.resolver_mejor_precio("AAA", tq, None, None, ts_overnight)
    assert r.session == "OVERNIGHT"
    assert r.overnight_disponible is False  # mecanismo distinto -- no se activa acá


# --- K: overnight sin proveedor -> NUNCA inventado ---

def test_K_overnight_sin_proveedor_nunca_se_inventa():
    tq = _q(last_price=101.0, price_is_stale=True)
    yq = _q(last_price=99.0, timestamp=NOW - timedelta(hours=54), price_overnight=None)
    r = res.resolver_mejor_precio("AAA", tq, yq, None, NOW)
    assert r.overnight_disponible is False
    assert r.session != "OVERNIGHT"


def test_K2_overnight_explicito_de_una_fuente_se_usa_y_se_marca():
    """Caso futuro (hoy inerte, 2026-08-31 comprobado siempre None): si
    Yahoo alguna vez completa `price_overnight`, el resolver ya sabe
    usarlo sin tocar market_view.py."""
    tq = _q(last_price=101.0, price_is_stale=True)
    yq = _q(last_price=99.0, timestamp=NOW - timedelta(hours=54), price_overnight=97.5)
    r = res.resolver_mejor_precio("AAA", tq, yq, None, NOW)
    assert r.overnight_disponible is True
    assert r.session == "OVERNIGHT"
    assert r.price == 97.5
    assert r.source == "yahoo"


# --- L: Tradier fresco con bid/ask vs Yahoo fresco sin bid/ask -> gana Tradier ---

def test_L_tradier_fresco_gana_aunque_yahoo_tambien_este_fresco():
    tq = _q(last_price=101.0, price_is_stale=False)  # Tradier trae bid/ask en la practica
    yq = _q(last_price=102.0, timestamp=NOW - timedelta(seconds=5))  # Yahoo sin bid/ask, tambien fresco
    r = res.resolver_mejor_precio("AAA", tq, yq, None, NOW)
    assert r.source == "tradier"
    assert r.price == 101.0


# --- M: Caso D-bis -- change_pct calculado con datos reales aunque STALE
# (2026-08-31, fix real -- antes se descartaba un % perfectamente
# calculable solo por estar el dato marcado STALE) ---

def test_M1_tradier_stale_con_previous_close_calcula_change_pct_y_sigue_stale():
    tq = _q(last_price=127.31, previous_close=137.4, price_is_stale=True,
            timestamp=NOW - timedelta(hours=56))
    r = res.resolver_mejor_precio("MSTR", tq, None, None, NOW)
    assert r.source == "tradier"
    assert r.is_stale is True
    assert r.change_pct == pytest.approx(-7.3435, abs=0.001)


def test_M2_yahoo_stale_con_previous_close_calcula_change_pct_y_sigue_stale():
    tq = _q(last_price=None, price_is_stale=True)  # Tradier sin dato utilizable
    yq = _q(last_price=178.64, previous_close=190.72, timestamp=NOW - timedelta(hours=56))
    r = res.resolver_mejor_precio("COIN", tq, yq, None, NOW)
    assert r.source == "yahoo"
    assert r.is_stale is True
    assert r.change_pct == pytest.approx(-6.3339, abs=0.001)


def test_M3_sin_previous_close_change_pct_none():
    tq = _q(last_price=100.0, previous_close=None, price_is_stale=True,
            timestamp=NOW - timedelta(hours=56))
    r = res.resolver_mejor_precio("AAA", tq, None, None, NOW)
    assert r.is_stale is True
    assert r.change_pct is None  # nunca inventado sin previous_close real


def test_M4_previous_close_cero_change_pct_none():
    tq = _q(last_price=100.0, previous_close=0.0, price_is_stale=True,
            timestamp=NOW - timedelta(hours=56))
    r = res.resolver_mejor_precio("AAA", tq, None, None, NOW)
    assert r.is_stale is True
    assert r.change_pct is None  # división inválida, nunca se inventa


def test_M5_ranking_puede_ordenar_por_el_change_pct_calculado_en_stale():
    """El ranking (armado en market_view.py, no acá) depende de que
    `change_pct` no sea None cuando es calculable -- confirma que el
    resolver entrega el valor real usable para ordenar."""
    tq_sube = _q(last_price=110.0, previous_close=100.0, price_is_stale=True,
                 timestamp=NOW - timedelta(hours=56))
    tq_baja = _q(last_price=80.0, previous_close=100.0, price_is_stale=True,
                 timestamp=NOW - timedelta(hours=56))
    r_sube = res.resolver_mejor_precio("SUBE", tq_sube, None, None, NOW)
    r_baja = res.resolver_mejor_precio("BAJA", tq_baja, None, None, NOW)
    assert r_sube.change_pct == pytest.approx(10.0)
    assert r_baja.change_pct == pytest.approx(-20.0)
    assert sorted([r_sube, r_baja], key=lambda r: r.change_pct, reverse=True) == [r_sube, r_baja]
