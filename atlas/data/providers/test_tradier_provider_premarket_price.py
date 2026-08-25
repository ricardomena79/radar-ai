"""Tests del precio de premarket vía bid/ask (2026-08-18, autorizado tras
auditoría con evidencia real -- ver `tradier_provider._resolve_current_price()`).

Caso real: `last`/`trade_date` de Tradier llegan CONGELADOS en el cierre de
la sesión regular anterior durante todo el premarket, mientras `bid`/`ask`
se actualizan en tiempo real (confirmado cruzando contra Yahoo Finance:
NVDA bid=220.05 vs Yahoo premarket oficial $220.06; XOS bid/ask=4.59-4.60
vs Yahoo premarket oficial $4.585, +119.38% -- ambos casos coinciden).

Reglas exactas pedidas por el usuario:
  A. `last` fresco -> se usa `last` (`price_basis="tradier_last"`, `LIVE_TRADE`).
  B. `last` vencido, bid/ask frescos y confiables -> punto medio bid/ask
     (`price_basis="tradier_bid_ask_mid"`, `BID_ASK_MID`).
  C. Ninguno confiable -> se conserva `last`/`trade_date` (vencidos) tal
     cual en `last_price` (nunca se inventa un precio), PERO (2026-08-24,
     Fase 1 -- corrección de datos premarket, caso real NSSC: $38.09/0%
     congelado ~46 min seguidos) ahora queda marcado explícitamente
     `price_basis="tradier_regular_close_stale"` (`STALE_REGULAR_CLOSE`),
     `price_is_stale=True`, y `change_percent=None` -- Tradier lo
     calculaba contra ese mismo `last` vencido, así que sería un "0%"
     engañoso, no un dato real. La cadena de confiabilidad ya existente
     (`scan_worker.compute_price_age_seconds`/`is_price_stale`/
     `estado_validacion=VENCIDO`) sigue intacta, sin cambios.
"""

from datetime import datetime, timedelta, timezone

from atlas.data.providers.tradier_provider import (
    BID_ASK_MAX_AGE_SECONDS,
    BID_ONLY_MAX_AGE_SECONDS,
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
    assert q.price_is_stale is False
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
    assert q.price_is_stale is False


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
    assert q.price_basis == "tradier_regular_close_stale"
    assert q.price_is_stale is True
    assert q.last_price == 100.0  # el `last` vencido, sin inventar nada nuevo
    assert q.change_percent is None  # nunca el "1.01%" calculado sobre un last vencido
    assert q.timestamp == NOW - timedelta(hours=17, minutes=17)


def test_caso_c_ask_falta_ahora_rescata_bid_only_fase_1c():
    """Actualizado en Fase 1C (2026-08-24): antes de este fallback, un ask
    ausente perdía también el bid (aunque fuera bueno) y caía al Caso C --
    ahora, con el bid fresco y válido, se rescata vía BID_ONLY en vez de
    descartarlo (ver `_classify_ask()` -> `"ausente"`)."""
    data = _base(ask=None)
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_bid_only"
    assert q.price_is_stale is False
    assert q.bid_only_reason == "ask_ausente"
    assert q.last_price == 98.0


def test_caso_c_bid_cero_o_negativo_conserva_last_vencido():
    for bid_malo in (0.0, -1.0):
        data = _base(bid=bid_malo)
        q = _to_quote(data, "TEST", now=NOW)
        assert q.price_basis == "tradier_regular_close_stale"
        assert q.price_is_stale is True
        assert q.change_percent is None


def test_caso_c_mercado_cruzado_ahora_rescata_bid_only_fase_1c():
    """Actualizado en Fase 1C (2026-08-24): un ask cruzado (`ask < bid`)
    es inválido POR SÍ MISMO -- antes descartaba también el bid, ahora se
    rescata el bid solo (ver `_classify_ask()` -> `"invalido"`)."""
    data = _base(bid=50.0, ask=40.0)
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_bid_only"
    assert q.price_is_stale is False
    assert q.bid_only_reason == "ask_invalido"
    assert q.last_price == 50.0


def test_caso_c_bid_vencido_aunque_ask_este_fresco_conserva_last():
    # Caso real SEZL: ask fresco (2.5min) pero bid vencido (7.7min > 180s) --
    # un solo lado fresco NO alcanza, deben estar los dos.
    data = _base(
        bid_date=_ms(NOW - timedelta(seconds=BID_ASK_MAX_AGE_SECONDS + 100)),
        ask_date=_ms(NOW - timedelta(seconds=60)),
    )
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_regular_close_stale"
    assert q.price_is_stale is True
    assert q.change_percent is None


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
    assert q.price_basis == "tradier_regular_close_stale"
    assert q.price_is_stale is True
    assert q.change_percent is None  # ya no el 0.0% congelado de Tradier
    assert q.last_price == 122.18  # se conserva el last vencido -- la cadena de
    # confiabilidad existente (compute_price_age_seconds/is_price_stale) es
    # la que debe marcarlo VENCIDO/NO_TOCAR a partir de acá, no este código.


def test_caso_c_spread_excesivo_no_representativo():
    data = _base(bid=1.0, ask=1.0 + (MAX_MIDPOINT_SPREAD_PCT / 100 * 1.05) * 1.0)
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_regular_close_stale"
    assert q.price_is_stale is True
    assert q.change_percent is None


def test_spread_dentro_del_umbral_si_usa_midpoint():
    data = _base(bid=1.0, ask=1.0 + (MAX_MIDPOINT_SPREAD_PCT / 100 * 0.5) * 1.0)
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_bid_ask_mid"


def test_midpoint_sin_prevclose_no_fabrica_change_pct():
    data = _base(prevclose=None)
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_bid_ask_mid"
    assert q.change_percent is None  # nunca se inventa un % sin referencia real


# ---------------------------------------------------------------------------
# Fase 1 (2026-08-24) -- casos de regresión pedidos explícitamente por el
# usuario (A-F), sobre el fix del Caso C de arriba.
# ---------------------------------------------------------------------------

def test_A_nssc_reconstruido_precio_regular_stale_mas_bid_ask_premarket_real():
    """Caso A del pedido: NSSC con precio regular stale (Caso C, como se
    observó en producción: $38.09/0% congelado ~46 min) PERO, a diferencia
    de lo que Atlas realmente vio, con un bid/ask premarket real disponible
    (que no se pudo confirmar en producción, ver auditoría) -- si el
    fallback SÍ hubiera tenido un bid/ask válido, debe producir un
    change_pct correcto en vez de perderse en el Caso C."""
    data = {
        "last": 38.09, "prevclose": 38.09, "change_percentage": 0.0,
        "trade_date": _ms(NOW - timedelta(hours=17)),  # last vencido, mismo patrón real
        "bid": 40.90, "ask": 41.10,  # bid/ask premarket real hacia ~$41 (dato del usuario)
        "bid_date": _ms(NOW - timedelta(seconds=5)),
        "ask_date": _ms(NOW - timedelta(seconds=3)),
        "volume": 458, "average_volume": 372451,
    }
    q = _to_quote(data, "NSSC", now=NOW)
    assert q.price_basis == "tradier_bid_ask_mid"
    assert q.price_is_stale is False
    assert q.last_price == (40.90 + 41.10) / 2  # 41.0
    esperado_pct = (41.0 - 38.09) / 38.09 * 100
    assert abs(q.change_percent - esperado_pct) < 1e-6
    assert q.change_percent > 6.0  # captura el movimiento real, no un 0% congelado


def test_B_precio_stale_se_identifica_como_stale_regular_close_no_vivo():
    """Caso B del pedido -- reconstrucción EXACTA de lo que Atlas vio
    realmente para NSSC en producción (precio regular sin bid/ask
    rescatable): debe quedar STALE_REGULAR_CLOSE, nunca como si fuera un
    precio vivo."""
    data = {
        "last": 38.09, "prevclose": 38.09, "change_percentage": 0.0,
        "trade_date": _ms(NOW - timedelta(hours=17)),
        "bid": None, "ask": None,  # sin bid/ask rescatable, igual que el caso real observado
        "volume": 458, "average_volume": 372451,
    }
    q = _to_quote(data, "NSSC", now=NOW)
    assert q.price_basis == "tradier_regular_close_stale"
    assert q.price_is_stale is True
    assert q.change_percent is None
    assert q.last_price == 38.09  # se conserva como referencia, nunca oculto


def test_C_sin_datos_produce_no_data():
    """Caso C del pedido -- símbolo sin `last` en absoluto (Tradier no lo
    devuelve): debe seguir señalizando NO_DATA vía `QuoteNotFoundError`,
    comportamiento YA existente, confirmado sin cambios."""
    import pytest

    from atlas.data.providers.base import QuoteNotFoundError

    data = {"last": None, "prevclose": None, "volume": None, "average_volume": None}
    with pytest.raises(QuoteNotFoundError):
        _to_quote(data, "NOEXISTE", now=NOW)


def test_D_bid_ask_valido_sin_trade_usa_bid_ask_mid():
    """Caso D del pedido -- ya cubierto por los tests de Caso B existentes
    (NVDA/XOS/TSLA/AMD reales), se agrega una confirmación explícita con
    el nombre pedido."""
    data = _base()  # trade_date vencido por defecto, bid/ask frescos
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_bid_ask_mid"
    assert q.price_is_stale is False


def test_E_sesion_regular_comportamiento_sin_cambios():
    """Caso E del pedido -- fuera de premarket (last fresco, caso normal
    de sesión regular), el resultado debe ser IDÉNTICO a antes del fix:
    mismo `price_basis`, mismo `last_price`, mismo `change_percent`."""
    data = _base(trade_date=_ms(NOW - timedelta(seconds=10)))  # last fresco, sesión regular típica
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_last"
    assert q.last_price == 100.0
    assert q.change_percent == 1.01  # el change_percentage crudo de Tradier, sin tocar
    assert q.price_is_stale is False


def test_F_mstu_no_cambia_con_el_fix():
    """Caso F del pedido -- reconstrucción con los datos REALES de
    detección de MSTU en producción (candidate_full_history, 2026-08-24):
    price_at_detection=$27.31, price_basis="tradier_bid_ask_mid",
    change_pct_at_detection=+0.037% (NO +879% -- Tradier ya entregaba
    prevclose ajustado). Con el fix del Caso C, el Caso B de MSTU no se
    toca en absoluto -- mismo resultado numérico exacto."""
    prevclose = 27.30  # tal que (bid+ask)/2 contra este prevclose dé ~+0.037%
    bid, ask = 27.30, 27.32
    data = {
        "last": 2.73, "prevclose": prevclose, "change_percentage": 879.85,  # el `last`/`change_percentage` SIN ajustar, si Tradier lo diera así
        "trade_date": _ms(NOW - timedelta(hours=17)),  # last vencido -- fuerza el fallback
        "bid": bid, "ask": ask,
        "bid_date": _ms(NOW - timedelta(seconds=5)),
        "ask_date": _ms(NOW - timedelta(seconds=3)),
        "volume": 6150, "average_volume": 50000,
    }
    q = _to_quote(data, "MSTU", now=NOW)
    assert q.price_basis == "tradier_bid_ask_mid"
    assert q.price_is_stale is False
    assert q.last_price == (bid + ask) / 2  # 27.31, coincide con el dato real de producción
    esperado_pct = ((bid + ask) / 2 - prevclose) / prevclose * 100
    assert abs(q.change_percent - esperado_pct) < 1e-6
    assert q.change_percent < 1.0  # NUNCA +879% -- el fix no introduce ese artefacto


def test_bid_ask_quedan_expuestos_para_trazabilidad_en_ambos_casos():
    data = _base()
    q = _to_quote(data, "TEST", now=NOW)
    assert q.bid == 98.0
    assert q.ask == 98.1
    assert q.bid_timestamp == NOW - timedelta(seconds=2)
    assert q.ask_timestamp == NOW - timedelta(seconds=1)


# ---------------------------------------------------------------------------
# Fase 1C (2026-08-24) -- fallback BID_ONLY: caso real NSSC, bid=$39.00
# fresco (11.3 min, dentro de BID_ONLY_MAX_AGE_SECONDS) descartado junto con
# un ask=$61.76 roto (64.4 min vencido, spread=45.18%) por la regla
# todo-o-nada del Caso B. Los 10 casos pedidos explícitamente por el
# usuario -- ver diseño de `_classify_ask()` en tradier_provider.py.
# ---------------------------------------------------------------------------

def test_bidonly_1_nssc_real_bid_fresco_ask_vencido_y_roto():
    """Caso 1 -- reconstrucción EXACTA de los números reales de NSSC
    (2026-08-24): bid=$39.00 (11.3 min) descartado junto con un
    ask=$61.76 (64.4 min de antigüedad -- vencido bajo el umbral de 180s,
    y además con spread 45.18%). Debe rescatar el bid en vez de perderlo
    junto con el ask."""
    data = {
        "last": 38.09, "prevclose": 38.09, "change_percentage": 0.0,
        "trade_date": _ms(NOW - timedelta(hours=61, minutes=30)),  # ~61.5h, como el caso real
        "bid": 39.00, "ask": 61.76,
        "bid_date": _ms(NOW - timedelta(seconds=679)),   # 11.3 min
        "ask_date": _ms(NOW - timedelta(seconds=3864)),  # 64.4 min
        "volume": 458, "average_volume": 372451,
    }
    q = _to_quote(data, "NSSC", now=NOW)
    assert q.price_basis == "tradier_bid_only"
    assert q.price_is_stale is False
    assert q.last_price == 39.00
    assert q.bid_only_reason == "ask_vencido"
    esperado_pct = (39.00 - 38.09) / 38.09 * 100
    assert abs(q.change_percent - esperado_pct) < 1e-6
    assert round(q.change_percent, 2) == 2.39  # 39.00/38.09 - 1 ≈ +2.39%, verificado a mano


def test_bidonly_1b_ask_fresco_pero_con_spread_y_ratio_rotos():
    """Variante del Caso 1 -- ask FRESCO (nunca vencido) pero con
    spread/ratio extremos, para cubrir específicamente la rama de
    `_classify_ask()` que devuelve `"roto"` (no `"vencido"`)."""
    data = _base(bid=39.00, ask=61.76, ask_date=_ms(NOW - timedelta(seconds=5)))
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_bid_only"
    assert q.bid_only_reason == "ask_roto"
    assert q.last_price == 39.00


def test_bidonly_2_bid_fresco_ask_normal_sigue_bid_ask_mid():
    """Caso 2 -- ask sano (spread angosto) NUNCA debe activar bid-only,
    sigue el Caso B existente sin cambios."""
    data = _base()  # bid=98.0, ask=98.1, ambos frescos, spread ~0.1%
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_bid_ask_mid"
    assert q.bid_only_reason is None


def test_bidonly_3_bid_fresco_ask_ausente():
    """Caso 3 -- sin ask en absoluto (`None`) -- el bid solo debe rescatarse."""
    data = _base(ask=None, ask_date=None)
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_bid_only"
    assert q.bid_only_reason == "ask_ausente"
    assert q.last_price == 98.0


def test_bidonly_4_bid_vencido_ask_roto_no_inventa_precio():
    """Caso 4 -- ni el bid es rescatable (vencido bajo
    `BID_ONLY_MAX_AGE_SECONDS`) ni el ask -- debe caer al Caso C, nunca
    inventar un precio con un bid también dudoso."""
    data = _base(
        bid=39.00, ask=61.76,
        bid_date=_ms(NOW - timedelta(seconds=BID_ONLY_MAX_AGE_SECONDS + 100)),
        ask_date=_ms(NOW - timedelta(seconds=3864)),
    )
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_regular_close_stale"
    assert q.price_is_stale is True
    assert q.bid_only_reason is None


def test_bidonly_5_bid_vencido_ask_normal_no_inventa_precio():
    """Caso 5 -- de la tabla del pedido, 'bid stale + ask fresco -> NO
    inventar precio': un ask sano por sí solo NUNCA sustituye al bid,
    cae al Caso C."""
    data = _base(
        bid_date=_ms(NOW - timedelta(seconds=BID_ONLY_MAX_AGE_SECONDS + 100)),
        ask_date=_ms(NOW - timedelta(seconds=1)),
    )
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_regular_close_stale"
    assert q.price_is_stale is True


def test_bidonly_6_bid_invalido_ask_valido_no_activa_bidonly():
    """Caso 6 -- bid inválido (`<=0`) descarta el fallback aunque el ask
    sea perfectamente sano -- bid-only nunca se activa con un bid dudoso."""
    data = _base(bid=0.0)
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_regular_close_stale"
    assert q.price_is_stale is True
    assert q.bid_only_reason is None


def test_bidonly_7_bid_y_ask_ambos_invalidos():
    """Caso 7 -- ninguno de los dos lados es válido -- Caso C (el símbolo
    SÍ tiene `last`, así que no es NO_DATA -- ese caso es ausencia total
    de `last`, ya cubierto por `test_C_sin_datos_produce_no_data`)."""
    data = _base(bid=0.0, ask=None)
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_regular_close_stale"
    assert q.price_is_stale is True


def test_bidonly_8_sesion_regular_sin_cambios():
    """Caso 8 -- `last` fresco (sesión regular normal): el fallback
    bid-only nunca se evalúa siquiera, el Caso A retorna primero."""
    data = _base(trade_date=_ms(NOW - timedelta(seconds=10)))
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_last"
    assert q.bid_only_reason is None


def test_bidonly_9_mstu_no_cambia_con_bidonly():
    """Caso 9 -- reconstrucción real de MSTU: ask sano, el Caso B ya lo
    resuelve -- confirma que bid-only nunca compite ni cambia ese
    resultado."""
    prevclose = 27.30
    bid, ask = 27.30, 27.32
    data = {
        "last": 2.73, "prevclose": prevclose, "change_percentage": 879.85,
        "trade_date": _ms(NOW - timedelta(hours=17)),
        "bid": bid, "ask": ask,
        "bid_date": _ms(NOW - timedelta(seconds=5)),
        "ask_date": _ms(NOW - timedelta(seconds=3)),
        "volume": 6150, "average_volume": 50000,
    }
    q = _to_quote(data, "MSTU", now=NOW)
    assert q.price_basis == "tradier_bid_ask_mid"
    assert q.bid_only_reason is None
    assert q.change_percent < 1.0


def test_bidonly_10_spread_grande_pero_no_extremo_no_se_clasifica_como_roto():
    """Caso 10 -- spread mayor a `MAX_MIDPOINT_SPREAD_PCT` (8%) pero NO
    por encima de `ASK_BROKEN_SPREAD_PCT` (24%) -- evidencia insuficiente
    para llamarlo 'roto': NO debe activar bid-only, cae al Caso C (nunca
    inventa un precio con evidencia ambigua)."""
    bid = 10.0
    ask = 10.0 * 1.12  # spread ~11.3% -- por encima de 8%, muy por debajo de 24%
    data = _base(bid=bid, ask=ask)
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_regular_close_stale"
    assert q.price_is_stale is True
    assert q.bid_only_reason is None


# ---------------------------------------------------------------------------
# Fase 1D (2026-08-24) -- separación señal/ejecutable: `Quote.executable_price`
# nunca debe confundirse con `last_price` (precio de SEÑAL) cuando
# `price_basis` no tiene una contraparte de compra verificable. Casos A-D,
# I, J pedidos explícitamente por el usuario.
# ---------------------------------------------------------------------------

def test_fase1d_A_bidonly_signal_price_valido_executable_price_none():
    """Caso A -- BID_ONLY: signal_price (=`last_price`) SÍ se completa
    (alimenta detección/gates/momentum, uso permitido), pero
    `executable_price` queda `None` explícito -- no hay contraparte de
    compra verificable."""
    data = {
        "last": 38.09, "prevclose": 38.09, "change_percentage": 0.0,
        "trade_date": _ms(NOW - timedelta(hours=61, minutes=30)),
        "bid": 39.00, "ask": 61.76,
        "bid_date": _ms(NOW - timedelta(seconds=679)),
        "ask_date": _ms(NOW - timedelta(seconds=3864)),
        "volume": 458, "average_volume": 372451,
    }
    q = _to_quote(data, "NSSC", now=NOW)
    assert q.price_basis == "tradier_bid_only"
    assert q.last_price == 39.00  # signal_price -- sin cambios respecto a Fase 1C
    assert q.executable_price is None


def test_fase1d_B_bidaskmid_executable_price_conserva_comportamiento_actual():
    """Caso B -- BID_ASK_MID: `executable_price` debe seguir siendo el
    mismo punto medio que `last_price` -- CERO cambio de comportamiento
    respecto a antes de Fase 1D (pedido explícito del usuario)."""
    data = _base()  # bid=98.0, ask=98.1, ambos frescos, spread angosto
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_bid_ask_mid"
    assert q.executable_price == q.last_price == (98.0 + 98.1) / 2


def test_fase1d_C_tradier_last_normal_sin_cambios():
    """Caso C -- `last` fresco (sesión normal): `executable_price` espeja
    `last_price`, comportamiento idéntico al de antes de este campo."""
    data = _base(trade_date=_ms(NOW - timedelta(seconds=10)))
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_last"
    assert q.executable_price == q.last_price == 100.0


def test_fase1d_D_stale_regular_close_sin_precio_ejecutable():
    """Caso D -- STALE_REGULAR_CLOSE: aunque `last_price` conserva el
    cierre vencido como referencia, `executable_price` debe quedar `None`
    -- ese precio tampoco es comprable ahora mismo."""
    data = {
        "last": 38.09, "prevclose": 38.09, "change_percentage": 0.0,
        "trade_date": _ms(NOW - timedelta(hours=17)),
        "bid": None, "ask": None,
        "volume": 458, "average_volume": 372451,
    }
    q = _to_quote(data, "NSSC", now=NOW)
    assert q.price_basis == "tradier_regular_close_stale"
    assert q.last_price == 38.09  # se conserva como referencia, sin cambios
    assert q.executable_price is None


def test_fase1d_I_mstu_executable_price_sin_cambios():
    """Caso I -- MSTU: reconstrucción real, `price_basis="tradier_bid_ask_mid"`
    -- `executable_price` debe coincidir con `last_price` (27.31), el
    resultado NO cambia con Fase 1D."""
    prevclose = 27.30
    bid, ask = 27.30, 27.32
    data = {
        "last": 2.73, "prevclose": prevclose, "change_percentage": 879.85,
        "trade_date": _ms(NOW - timedelta(hours=17)),
        "bid": bid, "ask": ask,
        "bid_date": _ms(NOW - timedelta(seconds=5)),
        "ask_date": _ms(NOW - timedelta(seconds=3)),
        "volume": 6150, "average_volume": 50000,
    }
    q = _to_quote(data, "MSTU", now=NOW)
    assert q.price_basis == "tradier_bid_ask_mid"
    assert q.executable_price == q.last_price == (bid + ask) / 2
    assert q.change_percent < 1.0  # sigue sin ser +879%


def test_fase1d_J_nssc_representa_signal_y_no_ejecutable():
    """Caso J -- NSSC debe poder representarse exactamente como:
    $39.00 / +2.39% / BID_ONLY / NO EJECUTABLE."""
    data = {
        "last": 38.09, "prevclose": 38.09, "change_percentage": 0.0,
        "trade_date": _ms(NOW - timedelta(hours=61, minutes=30)),
        "bid": 39.00, "ask": 61.76,
        "bid_date": _ms(NOW - timedelta(seconds=679)),
        "ask_date": _ms(NOW - timedelta(seconds=3864)),
        "volume": 458, "average_volume": 372451,
    }
    q = _to_quote(data, "NSSC", now=NOW)
    assert q.last_price == 39.00
    assert round(q.change_percent, 2) == 2.39
    assert q.price_basis == "tradier_bid_only"
    assert q.executable_price is None  # "NO EJECUTABLE"


def test_fase1d_quote_generico_sin_tradier_executable_price_espeja_last_price():
    """Verifica que un `Quote` construido SIN pasar `executable_price`
    (como lo siguen haciendo Yahoo/Finnhub, sin tocar esos archivos) recibe
    por defecto `executable_price == last_price` -- comportamiento nuevo
    universal para cualquier proveedor no-Tradier, sin requerir cambios en
    ningún otro archivo de proveedor."""
    from atlas.data.models.quote import Quote

    q = Quote(
        symbol="AAPL", name="Apple", last_price=225.50, change_percent=1.2,
        volume=1000, open=224.0, high=226.0, low=223.0, previous_close=222.8,
    )
    assert q.executable_price == q.last_price == 225.50

    q_explicit_none = Quote(
        symbol="X", name=None, last_price=10.0, change_percent=0.0,
        volume=1, open=None, high=None, low=None, previous_close=None,
        executable_price=None,
    )
    assert q_explicit_none.executable_price is None  # explícito se respeta, no se pisa


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
