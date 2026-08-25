"""Fase 1E (2026-08-24) -- cierre de la presentación BID_ONLY en Explosivas/
Momentum/Oportunidad del Día. Confirma que `price_basis`/`executable_price`/
`bid_only_reason` viajan intactos desde `row["explosive"]["metrics"]`
(la misma forma que ya produce `explosive_engine.py`) hasta el dict final
que sirve la Cabina (`live_integration.serialize_ranked_candidate()`),
pasando por `demo_ranking.build_ranked_candidate()` -- sin tocar ningún
cálculo de Ranking Score/Radar Explosivo. Puro, sin DB/red.
"""

from atlas_live.memory import demo_ranking as dr
from atlas_live.memory import live_integration as li


def _row(symbol, **metrics):
    defaults = dict(
        price=10.0, gap_pct=0.0, change_pct=0.0, relative_volume=0.5,
        dollar_volume=3_000_000, volatility_score=50.0, market_cap=500_000_000,
        price_basis=None, executable_price=10.0, bid_only_reason=None,
    )
    defaults.update(metrics)
    return {"symbol": symbol, "explosive": {"eligible": True, "score": 60.0, "metrics": defaults, "excluded_reason": None}}


def _build(row):
    candidate = dr.build_ranked_candidate(row, proposals=[], condition_value_cache={}, baseline=0.05)
    return li.serialize_ranked_candidate(candidate)


def test_bidonly_nssc_propaga_hasta_el_dict_final_de_la_cabina():
    """Caso NSSC reconstruido: `price` sigue siendo señal ($39.00) --
    `executable_price` llega `None` hasta el JSON final que consume
    Explosivas/Momentum/Oportunidad del Día."""
    row = _row(
        "NSSC", price=39.00, change_pct=2.39, price_basis="tradier_bid_only",
        executable_price=None, bid_only_reason="ask_vencido",
    )
    out = _build(row)
    assert out["price"] == 39.00
    assert round(out["change_pct"], 2) == 2.39
    assert out["price_basis"] == "tradier_bid_only"
    assert out["executable_price"] is None
    assert out["bid_only_reason"] == "ask_vencido"


def test_bidaskmid_mstu_sin_cambios():
    """Caso MSTU: `tradier_bid_ask_mid` -- `executable_price` debe
    coincidir con `price` en el dict final, comportamiento actual intacto."""
    row = _row("MSTU", price=27.31, change_pct=0.037, price_basis="tradier_bid_ask_mid", executable_price=27.31)
    out = _build(row)
    assert out["price_basis"] == "tradier_bid_ask_mid"
    assert out["executable_price"] == out["price"] == 27.31
    assert out["bid_only_reason"] is None


def test_tradier_last_normal_sin_cambios():
    row = _row("XYZ", price=10.0, price_basis="tradier_last", executable_price=10.0)
    out = _build(row)
    assert out["executable_price"] == out["price"] == 10.0


def test_otro_proveedor_sin_price_basis_no_rompe():
    """Filas históricas/de otros proveedores sin estos 3 campos (archivos
    `results_v1/*.json` anteriores a Fase 1E) no deben romper -- `.get()`
    con default `None` ya lo garantiza."""
    row = {"symbol": "OLD", "explosive": {
        "eligible": True, "score": 50.0, "excluded_reason": None,
        "metrics": {"price": 5.0, "gap_pct": 0.0, "change_pct": 0.0, "relative_volume": 1.0,
                    "dollar_volume": 1_000_000, "volatility_score": 40.0, "market_cap": 100_000_000},
    }}
    out = _build(row)
    assert out["price_basis"] is None
    assert out["executable_price"] is None
    assert out["bid_only_reason"] is None


def test_precio_ejecutable_no_afecta_score_ni_semaforo():
    """El scoring/Ranking Score/semáforo (Radar Explosivo + Memory Engine)
    no debe cambiar por `executable_price`/`price_basis` -- son puramente
    de presentación, mismo criterio que ya vale para `direction`/
    `stale_session_fallback`."""
    row_ejecutable = _row("A", price_basis="tradier_last", executable_price=10.0)
    row_no_ejecutable = _row("A", price_basis="tradier_bid_only", executable_price=None, bid_only_reason="ask_roto")
    out_a = _build(row_ejecutable)
    out_b = _build(row_no_ejecutable)
    assert out_a["score"] == out_b["score"]
    assert out_a["eligible_radar"] == out_b["eligible_radar"]
    assert out_a["semaforo"] == out_b["semaforo"]
    assert out_a["probability_pct"] == out_b["probability_pct"]
