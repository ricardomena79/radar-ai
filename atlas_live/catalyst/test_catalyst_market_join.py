"""Tests de catalyst_market_join.py (2026-08-24). Puro, sin DB/red."""

from datetime import datetime, timezone
from types import SimpleNamespace

from atlas_live.catalyst import catalyst_market_join as mj


def _quote(**kwargs):
    base = dict(
        last_price=38.09, change_percent=0.85, volume=349940, average_volume=125000,
        relative_volume=2.8, open=37.0, previous_close=36.8,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_join_con_quote_completa_da_todos_los_campos():
    r = mj.join_market_data("NSSC", {"NSSC": _quote()})
    assert r["market"]["market_data_status"] == "OK"
    assert r["market"]["price"] == 38.09
    assert r["market"]["relative_volume"] == 2.8
    assert round(r["market"]["gap_pct"], 4) == round((37.0 - 36.8) / 36.8 * 100, 4)


def test_join_sin_quote_da_sin_datos_nunca_inventa():
    r = mj.join_market_data("NOEXISTE", {})
    assert r["market"]["market_data_status"] == "SIN_DATOS"
    assert r["market"]["price"] is None
    assert r["market"]["gap_pct"] is None
    assert r["market"]["price_basis"] is None
    assert r["market"]["price_is_stale"] is None
    assert r["market"]["bid_only_reason"] is None


def test_join_expone_price_basis_timestamp_y_stale_cuando_el_quote_los_trae():
    """Fase 1 (2026-08-24) -- corrección de datos premarket: caso real
    NSSC, precio congelado. Sin estos 3 campos, un precio stale era
    indistinguible de uno vivo desde la API."""
    ts = datetime(2026, 8, 24, 8, 7, 26, tzinfo=timezone.utc)
    q = _quote(price_basis="tradier_regular_close_stale", price_is_stale=True, timestamp=ts, change_percent=None)
    r = mj.join_market_data("NSSC", {"NSSC": q})
    assert r["market"]["price_basis"] == "tradier_regular_close_stale"
    assert r["market"]["price_is_stale"] is True
    assert r["market"]["price_timestamp"] == ts.isoformat()
    assert r["market"]["change_pct"] is None


def test_join_expone_bid_only_reason_cuando_el_quote_lo_trae():
    """Fase 1C (2026-08-24) -- fallback BID_ONLY: caso real NSSC, bid
    rescatado con un ask roto. `bid_only_reason` debe quedar expuesto tal
    cual para trazabilidad (por qué se descartó el ask)."""
    ts = datetime(2026, 8, 24, 9, 17, 58, tzinfo=timezone.utc)
    q = _quote(
        price_basis="tradier_bid_only", price_is_stale=False, timestamp=ts,
        bid_only_reason="ask_vencido", change_percent=2.39,
    )
    r = mj.join_market_data("NSSC", {"NSSC": q})
    assert r["market"]["price_basis"] == "tradier_bid_only"
    assert r["market"]["bid_only_reason"] == "ask_vencido"
    assert r["market"]["price_is_stale"] is False


def test_join_expone_executable_price_nssc_bidonly_da_none():
    """Fase 1D (2026-08-24) -- caso J del pedido: NSSC debe representarse
    con `price=39.00`/`change_pct≈2.39`/`price_basis="tradier_bid_only"`/
    `executable_price=None` -- separación señal/ejecutable expuesta en el
    endpoint de catalizadores."""
    q = _quote(
        last_price=39.00, change_percent=2.39, price_basis="tradier_bid_only",
        price_is_stale=False, bid_only_reason="ask_vencido", executable_price=None,
    )
    r = mj.join_market_data("NSSC", {"NSSC": q})
    assert r["market"]["price"] == 39.00
    assert round(r["market"]["change_pct"], 2) == 2.39
    assert r["market"]["price_basis"] == "tradier_bid_only"
    assert r["market"]["executable_price"] is None


def test_join_expone_executable_price_bidaskmid_conserva_valor():
    """`executable_price` para `tradier_bid_ask_mid` debe coincidir con
    `price` -- sin cambios respecto al comportamiento actual."""
    q = _quote(price_basis="tradier_bid_ask_mid", executable_price=27.31, last_price=27.31)
    r = mj.join_market_data("MSTU", {"MSTU": q})
    assert r["market"]["executable_price"] == r["market"]["price"] == 27.31


def test_join_sin_quote_executable_price_none_no_inventado():
    r = mj.join_market_data("NOEXISTE", {})
    assert r["market"]["executable_price"] is None


def test_fase1d_F_executable_price_no_afecta_catalyst_opportunity_score():
    """Caso F del pedido -- el scoring de catalizadores (`catalyst_score.py`)
    nunca consume `executable_price`/`price`/`change_pct` directamente
    (solo `relative_volume`/`gap_pct`/etc, ver auditoría de Fase 1D) -- dos
    llamadas idénticas salvo por `executable_price` deben dar el MISMO
    `catalyst_opportunity_score`."""
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    radar_row = {
        "gates_fired": [{"gate": "acceleration"}], "direction": "ALCISTA",
        "retroceso_desde_maximo_pct": 4.0, "timing_deteccion_hoy": "al_comienzo", "stage": "INICIO",
        "relative_volume_at_detection": 2.8, "change_pct_at_detection": 5.0,
        "relative_volume_hoy": 3.5, "change_pct_confiable": True,
    }
    row = _catalyst_row(ticker="NSSC")

    r_ejecutable = mj.enrich_catalyst_row(
        row, lifecycle_state="INMINENTE",
        last_quotes={"NSSC": _quote(relative_volume=2.8, open=39.0, previous_close=37.7, executable_price=39.0)},
        radar_row=radar_row, now=now,
    )
    r_no_ejecutable = mj.enrich_catalyst_row(
        row, lifecycle_state="INMINENTE",
        last_quotes={"NSSC": _quote(relative_volume=2.8, open=39.0, previous_close=37.7, executable_price=None)},
        radar_row=radar_row, now=now,
    )
    assert r_ejecutable["catalyst_opportunity_score"] == r_no_ejecutable["catalyst_opportunity_score"]
    assert r_ejecutable["catalyst_score"] == r_no_ejecutable["catalyst_score"]


def test_join_price_basis_ausente_en_quote_no_inventado_default_none():
    """Un Quote de un proveedor que no sea Tradier (sin estos atributos)
    no debe romper -- `getattr` con default `None`, ya probado arriba en
    los tests existentes que usan `_quote()` sin estos campos."""
    q = _quote()  # SimpleNamespace sin price_basis/price_is_stale/timestamp
    r = mj.join_market_data("NSSC", {"NSSC": q})
    assert r["market"]["price_basis"] is None
    assert r["market"]["price_is_stale"] is None
    assert r["market"]["price_timestamp"] is None
    assert r["market"]["bid_only_reason"] is None


def test_join_gap_pct_none_si_falta_open_o_previous_close():
    r = mj.join_market_data("X", {"X": _quote(open=None)})
    assert r["market"]["gap_pct"] is None
    r2 = mj.join_market_data("X", {"X": _quote(previous_close=0)})
    assert r2["market"]["gap_pct"] is None


def test_join_con_radar_row_incluye_tecnico():
    radar_row = {
        "gates_fired": [{"gate": "acceleration"}, {"gate": "wakeup"}],
        "direction": "ALCISTA", "retroceso_desde_maximo_pct": 5.0,
        "timing_deteccion_hoy": "al_comienzo", "stage": "ALERTA_FUERTE",
    }
    r = mj.join_market_data("NSSC", {"NSSC": _quote()}, radar_row=radar_row)
    assert r["technical"]["disponible"] is True
    assert r["technical"]["gates_fired_count"] == 2
    assert r["technical"]["direction"] == "ALCISTA"
    assert r["technical"]["alert_stage"] == "ALERTA_FUERTE"


def test_join_sin_radar_row_no_inventa_tecnico():
    r = mj.join_market_data("ZYME", {"ZYME": _quote()})
    assert r["technical"]["disponible"] is False
    assert r["technical"]["gates_fired_count"] == 0
    assert r["technical"]["direction"] is None


def test_join_resistencia_soporte_siempre_no_disponible():
    r = mj.join_market_data("NSSC", {"NSSC": _quote()})
    assert r["resistencia_soporte"] == {"disponible": False}


# ---------------------------------------------------------------------------
# enrich_catalyst_row -- composición completa
# ---------------------------------------------------------------------------

def _catalyst_row(**kwargs):
    base = dict(
        ticker="NSSC", catalyst_type="EARNINGS", importance="media", direction="NEUTRAL",
        event_date="2026-08-25", event_time="BMO", racional_available=True,
    )
    base.update(kwargs)
    return base


def test_enrich_caso_tipo_nssc_con_deteccion_tecnica_hoy():
    """NSSC-shaped: earnings mañana, ES candidata técnica hoy (RVOL/gap
    fuertes, gates disparadas) -- debe dar catalyst_score/opportunity_score
    altos, mrna_similarity numérica (no EN_EVALUACION), trading_status vía
    priority_classifier real."""
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    radar_row = {
        "gates_fired": [{"gate": "acceleration"}, {"gate": "wakeup"}, {"gate": "behavior_change"}],
        "direction": "ALCISTA", "retroceso_desde_maximo_pct": 4.0,
        "timing_deteccion_hoy": "al_comienzo", "stage": "INICIO",
        "relative_volume_at_detection": 2.8, "change_pct_at_detection": 5.0,
        "relative_volume_hoy": 3.5, "change_pct_confiable": True,
    }
    r = mj.enrich_catalyst_row(
        _catalyst_row(), lifecycle_state="INMINENTE",
        last_quotes={"NSSC": _quote(relative_volume=2.8, open=39.0, previous_close=37.7)},
        radar_row=radar_row, now=now,
    )
    assert r["dias_al_evento"] == 1
    assert r["event_status"] == "MANANA"
    assert r["mrna_similarity_status"] == "OK"
    assert r["mrna_similarity_score"] is not None
    assert r["catalyst_opportunity_score"] > 50.0
    assert r["trading_status"] == "OPORTUNIDAD_PRIORITARIA"  # INICIO + ALCISTA, vía priority_classifier real


def test_enrich_caso_tipo_zyme_sin_deteccion_tecnica_hoy_da_en_evaluacion():
    """ZYME-shaped: catalizador de calendario puro, sin detección técnica
    de hoy -- mrna_similarity_status debe ser EN_EVALUACION (nunca un 0.0
    que parezca 'no se parece a MRNA'), trading_status por la rama nueva
    (catalyst_trading_status), nunca priority_classifier."""
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    r = mj.enrich_catalyst_row(
        _catalyst_row(ticker="ZYME", importance="alta", catalyst_type="FDA_PDUFA"),
        lifecycle_state="INMINENTE", last_quotes={}, radar_row=None, now=now,
    )
    assert r["technical"]["disponible"] is False
    assert r["mrna_similarity_status"] == "EN_EVALUACION"
    assert r["mrna_similarity_score"] is None
    assert r["trading_status"] in mj.cst.TRADING_STATUS_STATES
    # catalyst_score sigue siendo calculable aunque no haya mercado.
    assert r["catalyst_score"] > 0.0


def test_enrich_caso_tipo_mrna_extendida():
    """Caso real MRNA ya corrido -- event_status debe dar EXTENDIDA
    (gana sobre dias_al_evento), catalyst_score bajo por lifecycle."""
    now = datetime(2026, 8, 19, 20, 0, tzinfo=timezone.utc)
    r = mj.enrich_catalyst_row(
        _catalyst_row(ticker="MRNA", catalyst_type="FDA_PDUFA", event_date=None, importance="alta"),
        lifecycle_state="EXTENDIDA", last_quotes={"MRNA": _quote()}, radar_row=None, now=now,
    )
    assert r["event_status"] == "EXTENDIDA"
    assert r["catalyst_score"] < 40.0  # lifecycle_score(EXTENDIDA)=5, domina la suma


def test_is_relevant_for_ranking_excluye_no_racional():
    r = mj.enrich_catalyst_row(
        _catalyst_row(racional_available=False), lifecycle_state="INMINENTE",
        last_quotes={"NSSC": _quote(relative_volume=5.0)}, radar_row=None, now=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    assert mj.is_relevant_for_ranking(r) is False


def test_is_relevant_for_ranking_excluye_ruido_sin_senal():
    """Empresa grande, RVOL normal, sin gap, sin detección técnica --
    ejemplo explícito del usuario de lo que NO debe entrar al ranking."""
    r = mj.enrich_catalyst_row(
        _catalyst_row(importance="media"), lifecycle_state="FUTURO",
        last_quotes={"NSSC": _quote(relative_volume=0.9, open=38.0, previous_close=37.9)},
        radar_row=None, now=datetime(2026, 8, 10, tzinfo=timezone.utc),  # evento lejos
    )
    assert mj.is_relevant_for_ranking(r) is False


def test_is_relevant_for_ranking_incluye_rvol_fuerte():
    r = mj.enrich_catalyst_row(
        _catalyst_row(), lifecycle_state="INMINENTE",
        last_quotes={"NSSC": _quote(relative_volume=2.8)}, radar_row=None, now=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    assert mj.is_relevant_for_ranking(r) is True


def test_is_relevant_for_ranking_acepta_racional_available_como_int_de_sqlite():
    """SQLite devuelve 0/1 (int), no True/False (bool de Python) -- `1 is
    not True` en Python, así que una comparación con `is True` excluiría
    SIEMPRE las filas reales de la base. Regresión directa de ese bug."""
    r = mj.enrich_catalyst_row(
        _catalyst_row(racional_available=1), lifecycle_state="INMINENTE",
        last_quotes={"NSSC": _quote(relative_volume=2.8)}, radar_row=None, now=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    assert mj.is_relevant_for_ranking(r) is True

    r2 = mj.enrich_catalyst_row(
        _catalyst_row(racional_available=0), lifecycle_state="INMINENTE",
        last_quotes={"NSSC": _quote(relative_volume=2.8)}, radar_row=None, now=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    assert mj.is_relevant_for_ranking(r2) is False


def test_is_relevant_for_ranking_incluye_deteccion_tecnica_hoy_sin_importar_rvol():
    radar_row = {"gates_fired": [{"gate": "x"}], "direction": "ALCISTA", "stage": "PREPARACION"}
    r = mj.enrich_catalyst_row(
        _catalyst_row(), lifecycle_state="FUTURO", last_quotes={"NSSC": _quote(relative_volume=0.5)},
        radar_row=radar_row, now=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    assert mj.is_relevant_for_ranking(r) is True


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
            p += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
            traceback.print_exc()
            f += 1
    print(f"--- {p} passed, {f} failed ---")
