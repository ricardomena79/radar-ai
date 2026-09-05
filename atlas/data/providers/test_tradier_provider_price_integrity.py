"""Hito 6, Fase 6.2 (2026-09-04, autorizado explícitamente): tests
end-to-end de PRICE_INTEGRITY a través de `_to_quote()` real -- confirma
que `Quote.possible_split_flag`/`Quote.possible_split_ratio` quedan
correctamente conectados desde el dato crudo de Tradier hasta el `Quote`
final, vía `_resolve_current_price()` (sin tocarla) +
`price_integrity.classify_possible_split()` (ver ese módulo para el
diseño completo)."""

from datetime import datetime, timedelta, timezone

from atlas.data.price_integrity import POSSIBLE_SPLIT_FLAG
from atlas.data.providers.tradier_provider import _to_quote

NOW = datetime(2026, 9, 4, 14, 0, 0, tzinfo=timezone.utc)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _base(**overrides):
    # `trade_date` fresco a propósito (Caso A, `price_basis="tradier_last"`)
    # -- este archivo prueba PRICE_INTEGRITY, no la resolución de precio en
    # sí (eso ya lo cubre `test_tradier_provider_premarket_price.py`).
    data = {
        "symbol": "TEST", "last": 100.0, "prevclose": 100.0, "change_percentage": 0.0,
        "trade_date": _ms(NOW - timedelta(seconds=5)),
        "bid": 99.9, "ask": 100.1,
        "bid_date": _ms(NOW - timedelta(seconds=2)),
        "ask_date": _ms(NOW - timedelta(seconds=1)),
        "volume": 1000, "average_volume": 500,
    }
    data.update(overrides)
    return data


def test_split_sintetico_2_a_1_queda_marcado_en_el_quote_final():
    data = _base(last=51.0, prevclose=100.0, change_percentage=-49.0)
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_basis == "tradier_last"  # confirma que pasó por Caso A, sin tocar esa lógica
    assert q.possible_split_flag == POSSIBLE_SPLIT_FLAG
    assert abs(q.possible_split_ratio - 0.51) < 1e-9


def test_caso_normal_no_queda_marcado_en_el_quote_final():
    data = _base(last=104.0, prevclose=100.0, change_percentage=4.0)
    q = _to_quote(data, "TEST", now=NOW)
    assert q.possible_split_flag is None
    assert q.possible_split_ratio is None


def test_caso_stale_c_nunca_se_marca_aunque_el_last_viejo_luzca_como_split():
    # Fuerza Caso C (STALE_REGULAR_CLOSE): trade_date vencido, bid/ask
    # también vencidos/inválidos -- price_is_stale=True, change_percent
    # descartado a None por _resolve_current_price() (sin tocarla). El
    # guard de price_is_stale en classify_possible_split() debe ganar.
    data = _base(
        last=50.0, prevclose=100.0, change_percentage=-50.0,
        trade_date=_ms(NOW - timedelta(hours=20)),
        bid=None, ask=None,
    )
    q = _to_quote(data, "TEST", now=NOW)
    assert q.price_is_stale is True
    assert q.change_percent is None
    assert q.possible_split_flag is None
    assert q.possible_split_ratio is None
