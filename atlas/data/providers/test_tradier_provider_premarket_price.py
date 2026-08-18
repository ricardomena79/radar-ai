"""Tests del precio de premarket vía bid/ask (2026-08-18, autorizado tras
auditoría con evidencia real -- ver `tradier_provider._resolve_current_price()`).

Caso real: `last`/`trade_date` de Tradier llegan CONGELADOS en el cierre de
la sesión regular anterior durante todo el premarket, mientras `bid`/`ask`
se actualizan en tiempo real (confirmado cruzando contra Yahoo Finance:
NVDA bid=220.05 vs Yahoo premarket oficial $220.06; XOS bid/ask=4.59-4.60
vs Yahoo premarket oficial $4.585, +119.38% -- ambos casos coinciden).

Reglas exactas pedidas por el usuario:
  A. `last` fresco -> se usa `last`.
  B. `last` vencido, bid/ask frescos y confiables -> punto medio bid/ask.
  C. Ninguno confiable -> se conserva `last`/`trade_date` (vencidos) tal
     cual, para que la cadena de confiabilidad YA EXISTENTE los marque
     NO_TOCAR -- nunca se inventa un precio.
"""

from datetime import datetime, timedelta, timezone

from atlas.data.providers.tradier_provider import (
    BID_ASK_MAX_AGE_SECONDS,
    MAX_MIDPOINT_SPREAD_PCT,
    _to_quote,
)

NOW = datetime(2026, 8, 18, 13, 16, 53, tzinfo=timezone.utc)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _base(**overrides):
    data = {
        "symbol": "TEST", "last": 100.0, "prevclose": 99.0, "change_percentage": 1.01,
        "trade_date": _ms(NOW - timedelta(hours=17, minutes=17)),  # vencido, caso real
        "bid": 98.0, "ask": 98.1,
        "bid_date": _ms(NOW - timedelta(seconds=2)),
        "ask_date": _ms(NOW - timedelta(seconds=1)),
        "volume": 1000, "average_volume": 500,
    }
    data.update(overrides)
    return data


def test_caso_a_last_fresco_se_usa_tal_cual():
    data = _base(trade_date=_ms(NOW - timedelta(seconds=30)))
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_last"
    assert q.last_price == 100.0
    assert q.change_percent == 1.01
    assert q.timestamp == NOW - timedelta(seconds=30)
    # bid/ask quedan expuestos igual, para trazabilidad, aunque no se usen
    assert q.bid == 98.0 and q.ask == 98.1


def test_caso_b_last_vencido_bid_ask_frescos_usa_midpoint():
    data = _base()  # trade_date vencido por defecto, bid/ask frescos
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_bid_ask_mid"
    assert q.last_price == (98.0 + 98.1) / 2
    # % de cambio recalculado contra prevclose, NUNCA el change_percentage congelado de Tradier
    esperado = (q.last_price - 99.0) / 99.0 * 100
    assert abs(q.change_percent - esperado) < 1e-9
    # timestamp = el más reciente entre bid_date/ask_date
    assert q.timestamp == NOW - timedelta(seconds=1)


def test_caso_b_real_nvda_coincide_con_yahoo_premarket():
    # Datos crudos REALES de Tradier (2026-08-18T13:16:53 UTC) -- ver
    # auditoría de producción. Yahoo Finance mostró Pre-Market $220.06.
    data = {
        "last": 225.01, "prevclose": 225.01, "change_percentage": 0.0,
        "trade_date": 1786996800168, "bid": 220.05, "ask": 220.10,
        "bid_date": 1787059012000, "ask_date": 1787059013000,
        "volume": 2625944, "average_volume": 145128235,
    }
    now = datetime.fromtimestamp(1787059013.697438, tz=timezone.utc)
    q = _to_quote(data, "NVDA", now=now)
    assert q.price_basis == "tradier_bid_ask_mid"
    assert q.last_price == (220.05 + 220.10) / 2  # 220.075, vs Yahoo real $220.06 -- diferencia < 2 centavos
    assert q.bid == 220.05 and q.ask == 220.10


def test_caso_b_real_xos_captura_el_salto_de_119_por_ciento():
    # XOS: Tradier `last`=2.09 (cierre de ayer) pero Yahoo Pre-Market real
    # fue $4.585 (+119.38%) -- confirmado independientemente en producción.
    data = {
        "last": 2.09, "prevclose": 2.0950, "change_percentage": 0.0,
        "trade_date": 1786996800201, "bid": 4.59, "ask": 4.60,
        "bid_date": 1787059012000, "ask_date": 1787059009000,
        "volume": 54352509, "average_volume": 2754900,
    }
    now = datetime.fromtimestamp(1787059013.697438, tz=timezone.utc)
    q = _to_quote(data, "XOS", now=now)
    assert q.price_basis == "tradier_bid_ask_mid"
    assert round(q.last_price, 3) == 4.595  # (4.59+4.60)/2 -- vs Yahoo real $4.585, coincide
    cambio_pct = (q.last_price - 2.0950) / 2.0950 * 100
    assert cambio_pct > 100  # el movimiento real de +119% se refleja, no un 0.0% congelado


def test_caso_b_real_tsla_coincide_con_yahoo_premarket():
    # Datos crudos REALES de Tradier (2026-08-18T13:16:53 UTC).
    data = {
        "last": 339.3, "prevclose": 339.3, "change_percentage": 0.0,
        "trade_date": 1786996800327, "bid": 334.55, "ask": 334.60,
        "bid_date": 1787059001000, "ask_date": 1787058994000,
        "volume": 800024, "average_volume": 17275304,
    }
    now = datetime.fromtimestamp(1787059013.697438, tz=timezone.utc)
    q = _to_quote(data, "TSLA", now=now)
    assert q.price_basis == "tradier_bid_ask_mid"
    assert q.last_price == (334.55 + 334.60) / 2  # 334.575
    cambio_pct = (q.last_price - 339.3) / 339.3 * 100
    assert -2.0 < cambio_pct < -1.0  # baja moderada real en premarket, no 0.0%


def test_caso_b_real_amd_coincide_con_yahoo_premarket():
    # Datos crudos REALES de Tradier (2026-08-18T13:16:53 UTC).
    data = {
        "last": 506.0, "prevclose": 506.0, "change_percentage": 0.0,
        "trade_date": 1786996800357, "bid": 487.07, "ask": 487.50,
        "bid_date": 1787058994000, "ask_date": 1787058996000,
        "volume": 607654, "average_volume": 55628230,
    }
    now = datetime.fromtimestamp(1787059013.697438, tz=timezone.utc)
    q = _to_quote(data, "AMD", now=now)
    assert q.price_basis == "tradier_bid_ask_mid"
    assert q.last_price == (487.07 + 487.50) / 2  # 487.285
    cambio_pct = (q.last_price - 506.0) / 506.0 * 100
    assert -4.0 < cambio_pct < -3.0  # baja moderada real en premarket, no 0.0%


def test_caso_c_bid_falta_conserva_last_vencido():
    data = _base(bid=None)
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_last"
    assert q.last_price == 100.0  # el `last` vencido, sin inventar nada nuevo
    assert q.timestamp == NOW - timedelta(hours=17, minutes=17)


def test_caso_c_ask_falta_conserva_last_vencido():
    data = _base(ask=None)
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_last"


def test_caso_c_bid_cero_o_negativo_conserva_last_vencido():
    for bid_malo in (0.0, -1.0):
        data = _base(bid=bid_malo)
        q = _to_quote(data, "TEST", now=NOW)
        assert q.price_basis == "tradier_last"


def test_caso_c_ask_menor_que_bid_mercado_cruzado_conserva_last_vencido():
    data = _base(bid=50.0, ask=40.0)
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_last"


def test_caso_c_bid_vencido_aunque_ask_este_fresco_conserva_last():
    # Caso real SEZL: ask fresco (2.5min) pero bid vencido (7.7min > 180s) --
    # un solo lado fresco NO alcanza, deben estar los dos.
    data = _base(
        bid_date=_ms(NOW - timedelta(seconds=BID_ASK_MAX_AGE_SECONDS + 100)),
        ask_date=_ms(NOW - timedelta(seconds=60)),
    )
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_last"


def test_caso_c_real_sezl_bid_vencido_no_usa_midpoint():
    # Datos crudos REALES de SEZL (2026-08-18T13:16:53 UTC): bid_date con
    # 459.7s de antigüedad (> 180s), ask_date con 149.7s (< 180s) -- el
    # lado bid vencido descarta el midpoint completo.
    data = {
        "last": 122.18, "prevclose": 122.18, "change_percentage": 0.0,
        "trade_date": 1786996800227, "bid": 119.0, "ask": 121.19,
        "bid_date": 1787058554000, "ask_date": 1787058864000,
        "volume": 22385, "average_volume": 19,
    }
    now = datetime.fromtimestamp(1787059013.697438, tz=timezone.utc)
    q = _to_quote(data, "SEZL", now=now)
    assert q.price_basis == "tradier_last"
    assert q.last_price == 122.18  # se conserva el last vencido -- la cadena de
    # confiabilidad existente (compute_price_age_seconds/is_price_stale) es
    # la que debe marcarlo VENCIDO/NO_TOCAR a partir de acá, no este código.


def test_caso_c_spread_excesivo_no_representativo():
    data = _base(bid=1.0, ask=1.0 + (MAX_MIDPOINT_SPREAD_PCT / 100 * 1.05) * 1.0)
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_last"


def test_spread_dentro_del_umbral_si_usa_midpoint():
    data = _base(bid=1.0, ask=1.0 + (MAX_MIDPOINT_SPREAD_PCT / 100 * 0.5) * 1.0)
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_bid_ask_mid"


def test_midpoint_sin_prevclose_no_fabrica_change_pct():
    data = _base(prevclose=None)
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_bid_ask_mid"
    assert q.change_percent is None  # nunca se inventa un % sin referencia real


def test_bid_ask_quedan_expuestos_para_trazabilidad_en_ambos_casos():
    data = _base()
    q = _to_quote(data, "TEST", now=NOW)
    assert q.bid == 98.0
    assert q.ask == 98.1
    assert q.bid_timestamp == NOW - timedelta(seconds=2)
    assert q.ask_timestamp == NOW - timedelta(seconds=1)


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__); p += 1
        except Exception as e:
            print("FAIL", fn.__name__, e); traceback.print_exc(); f += 1
    print(f"--- {p} passed, {f} failed ---")
