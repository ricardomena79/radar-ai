"""Eje de dirección en Radar Explosivo (2026-08-18, pedido explícito del
usuario, información sin tocar arquitectura ni gates).

`explosive_engine.py` marca "eligible" cualquier movimiento fuerte en
CUALQUIER dirección (`abs(gap_pct or change_pct)`, ver evidencia real:
investigación de 208 señales resueltas del 2026-08-17 -- 48% ya estaban
cayendo al detectarse, 94% de esas nunca volvió a terreno positivo). Este
test confirma que `metrics["direction"]` expone esa dirección real como
información adicional, SIN cambiar en absoluto qué queda `eligible` --
mismo `ExplosiveResult`/gates de siempre, solo un campo más en `metrics`,
igual que `stale_session_fallback`/`price_basis`."""

from atlas.data.models.quote import Quote
from atlas.engine.momentum_engine import MomentumResult, ScoredComponent
from atlas_live import explosive_engine as ee


def _momentum(volatility_score: float = 80.0) -> MomentumResult:
    def _c(name):
        return ScoredComponent(name=name, score=volatility_score, weight=1.0,
                                weighted_score=volatility_score, explanation="")
    return MomentumResult(
        symbol="XYZ", momentum_score=volatility_score,
        components=[_c("atr"), _c("rsi"), _c("vwap_distance")],
    )


def _eligible_quote(change_percent) -> Quote:
    """Quote que pasa todos los gates (price/liquidez/rvol/movimiento/
    volatilidad/tamaño) sin importar la dirección -- así el test aísla el
    campo `direction`, no la elegibilidad."""
    return Quote(
        symbol="XYZ", name="XYZ Corp", last_price=10.0, change_percent=change_percent,
        volume=2_000_000, average_volume=500_000, open=10.5, high=11.0, low=9.5,
        previous_close=9.8, market_cap=100_000_000,
    )


def test_direction_alcista_no_cambia_elegibilidad():
    result = ee.evaluate(_eligible_quote(6.0), _momentum(), None)
    assert result.eligible is True
    assert result.metrics["direction"] == "ALCISTA"


def test_direction_bajista_sigue_siendo_eligible_igual_que_antes():
    """Caso real (2026-08-17): candidatas cayendo -6% también quedaban
    'eligible' -- este test confirma que el nuevo campo es puramente
    informativo, la elegibilidad NO cambia."""
    result = ee.evaluate(_eligible_quote(-6.0), _momentum(), None)
    assert result.eligible is True  # sin cambios -- mismo comportamiento de siempre
    assert result.metrics["direction"] == "BAJISTA"


def test_direction_neutral_dentro_de_la_banda():
    result = ee.evaluate(_eligible_quote(0.5), _momentum(), None)
    assert result.metrics["direction"] == "NEUTRAL"


def test_direction_indefinida_cuando_no_hay_change_percent():
    """`change_percent=None` -> 'INDEFINIDA', nunca un falso 'NEUTRAL' --
    usa el valor crudo de Quote, no el `change_pct` ya coercido a 0.0 que
    usan los gates de arriba."""
    result = ee.evaluate(_eligible_quote(None), _momentum(), None)
    assert result.metrics["direction"] == "INDEFINIDA"


def test_direction_tambien_se_expone_para_candidatos_no_eligibles():
    """El modo Diagnóstico ya calcula metrics para descartados -- direction
    debe seguir esa misma convención."""
    quote = Quote(symbol="ZZZ", name="ZZZ", last_price=0.50, change_percent=-8.0,
                   volume=1000, average_volume=500, open=0.5, high=0.5, low=0.5,
                   previous_close=0.54)  # precio bajo el mínimo -> falla en "price"
    result = ee.evaluate(quote, _momentum(), None)
    assert result.eligible is False
    assert result.failed_stage == "price"
    assert result.metrics["direction"] == "BAJISTA"


# ---------------------------------------------------------------------------
# Fase 1E (2026-08-24) -- cierre de la presentación BID_ONLY en Explosivas/
# Momentum/Oportunidad del Día: `executable_price`/`bid_only_reason` deben
# propagarse a `metrics` con el MISMO pass-through puro que `price_basis`
# (ya existente) -- sin tocar ningún gate/umbral de este archivo.
# ---------------------------------------------------------------------------

def test_fase1e_executable_price_y_bid_only_reason_se_exponen_en_metrics():
    """Caso NSSC reconstruido: BID_ONLY -- `metrics["price"]` sigue siendo
    la señal ($39.00, sin cambios), `executable_price` queda `None`,
    `bid_only_reason` documenta por qué."""
    quote = Quote(
        symbol="NSSC", name="NSSC", last_price=39.00, change_percent=2.39,
        volume=458, average_volume=372451, open=39.0, high=39.5, low=38.5,
        previous_close=38.09, price_basis="tradier_bid_only",
        bid_only_reason="ask_vencido", executable_price=None,
    )
    result = ee.evaluate(quote, _momentum(), None)
    assert result.metrics["price"] == 39.00  # señal, sin cambios
    assert result.metrics["price_basis"] == "tradier_bid_only"
    assert result.metrics["executable_price"] is None
    assert result.metrics["bid_only_reason"] == "ask_vencido"


def test_fase1e_bidaskmid_executable_price_conserva_comportamiento_actual():
    """Caso MSTU-shaped: `tradier_bid_ask_mid` -- `executable_price` debe
    coincidir con `price`, exactamente igual que antes de Fase 1E."""
    quote = Quote(
        symbol="MSTU", name="MSTU", last_price=27.31, change_percent=0.037,
        volume=6150, average_volume=50000, open=27.3, high=27.4, low=27.2,
        previous_close=27.30, price_basis="tradier_bid_ask_mid", executable_price=27.31,
    )
    result = ee.evaluate(quote, _momentum(), None)
    assert result.metrics["price_basis"] == "tradier_bid_ask_mid"
    assert result.metrics["executable_price"] == result.metrics["price"] == 27.31
    assert result.metrics["bid_only_reason"] is None


def test_fase1e_tradier_last_normal_sin_cambios():
    """`tradier_last` (sesión normal): `executable_price` espeja `price`."""
    quote = Quote(
        symbol="XYZ", name="XYZ", last_price=10.0, change_percent=1.5,
        volume=2_000_000, average_volume=500_000, open=10.5, high=11.0, low=9.5,
        previous_close=9.8, price_basis="tradier_last", executable_price=10.0,
    )
    result = ee.evaluate(quote, _momentum(), None)
    assert result.metrics["executable_price"] == result.metrics["price"] == 10.0


def test_fase1e_otro_proveedor_sin_price_basis_no_rompe():
    """Un Quote de un proveedor que no sea Tradier (sin `price_basis`
    explícito) debe seguir funcionando -- `executable_price` espeja
    `last_price` por el default de `Quote.__post_init__`, `price_basis`/
    `bid_only_reason` quedan `None`/default, nunca rompe."""
    quote = _eligible_quote(6.0)  # Quote genérico, sin price_basis explícito
    result = ee.evaluate(quote, _momentum(), None)
    assert result.metrics["price_basis"] is None
    assert result.metrics["executable_price"] == result.metrics["price"]
    assert result.metrics["bid_only_reason"] is None
