"""Tests de catalyst_status.py (2026-08-24) -- Event Status vs Trading
Status. Puros, sin DB/red."""

from datetime import datetime, timezone

from atlas_live.catalyst import catalyst_status as cst

# ---------------------------------------------------------------------------
# days_to_event -- misma fórmula que classify_catalyst_lifecycle()
# ---------------------------------------------------------------------------

def test_days_to_event_replica_formula_del_clasificador():
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    assert cst.days_to_event("2026-08-25", now) == 1
    assert cst.days_to_event("2026-08-24", now) == 0
    assert cst.days_to_event(None, now) is None
    assert cst.days_to_event("fecha-invalida", now) is None


# ---------------------------------------------------------------------------
# event_status -- 7 buckets
# ---------------------------------------------------------------------------

def test_event_status_hoy_manana_ventanas():
    assert cst.event_status(0, "INMINENTE") == "HOY"
    assert cst.event_status(1, "INMINENTE") == "MANANA"
    assert cst.event_status(2, "INMINENTE") == "DOS_A_TRES_DIAS"
    assert cst.event_status(3, "INMINENTE") == "DOS_A_TRES_DIAS"
    assert cst.event_status(5, "FUTURO") == "CUATRO_A_SIETE_DIAS"
    assert cst.event_status(7, "FUTURO") == "CUATRO_A_SIETE_DIAS"
    assert cst.event_status(8, "FUTURO") == "FUTURO"
    assert cst.event_status(None, "FUTURO") == "FUTURO"


def test_event_status_lifecycle_state_none_cae_a_dias_al_evento():
    """Regresión (2026-08-24, encontrado en verificación de UI): un
    catalizador SIN transición de ciclo de vida registrada todavía
    (`lifecycle_state=None`, nunca forzado a "OCURRIDO") con un evento a
    1 día debe seguir dando MANANA, no OCURRIDO."""
    assert cst.event_status(dias_al_evento=1, lifecycle_state=None) == "MANANA"
    assert cst.event_status(dias_al_evento=None, lifecycle_state=None) == "FUTURO"


def test_event_status_ocurrido_y_extendida_ganan_sobre_dias():
    # MRNA real: ya extendida, sin importar cuántos "días" queden calculados.
    assert cst.event_status(dias_al_evento=None, lifecycle_state="EXTENDIDA") == "EXTENDIDA"
    assert cst.event_status(dias_al_evento=3, lifecycle_state="OCURRIDO") == "OCURRIDO"


# ---------------------------------------------------------------------------
# catalyst_trading_status -- solo para tickers SIN detección técnica hoy
# ---------------------------------------------------------------------------

def test_trading_status_preparar_evento_manana_con_rvol_fuerte():
    estado = cst.catalyst_trading_status(
        opportunity_score=50.0, dias_al_evento=1, relative_volume=2.8, gap_pct=None,
    )
    assert estado == "PREPARAR"


def test_trading_status_vigilar_evento_lejano_score_alto():
    estado = cst.catalyst_trading_status(
        opportunity_score=75.0, dias_al_evento=5, relative_volume=None, gap_pct=None,
    )
    assert estado == "VIGILAR"


def test_trading_status_calendario_sin_senal_ni_proximidad():
    estado = cst.catalyst_trading_status(
        opportunity_score=20.0, dias_al_evento=10, relative_volume=0.5, gap_pct=0.2,
    )
    assert estado == "CALENDARIO"


def test_trading_status_evento_manana_sin_senal_da_vigilar_no_preparar():
    """Evento inminente pero SIN RVOL/gap real ni score alto -- VIGILAR,
    nunca PREPARAR sin evidencia."""
    estado = cst.catalyst_trading_status(
        opportunity_score=30.0, dias_al_evento=1, relative_volume=None, gap_pct=None,
    )
    assert estado == "VIGILAR"


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
