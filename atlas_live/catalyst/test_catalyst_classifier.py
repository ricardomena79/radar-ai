"""Tests de catalyst_classifier.py (2026-08-23). Puros, sin DB/red --
headlines sintéticos + el caso real de MRNA como regresión directa."""

from datetime import datetime, timedelta, timezone

from atlas_live.catalyst import catalyst_classifier as cc

# ---------------------------------------------------------------------------
# 1. Taxonomía de catalyst_type -- una fila por tipo de la tabla del plan
# ---------------------------------------------------------------------------

def test_fda_pdufa():
    r = cc.classify_catalyst_type("FDA Grants Priority Review for Company X's New Drug Application")
    assert r.catalyst_type == "FDA_PDUFA"
    assert r.direction == "ALCISTA"
    assert r.confidence == 1.0
    assert r.importance == "alta"


def test_fda_rechazo_da_bajista():
    r = cc.classify_catalyst_type("FDA Issues Complete Response Letter for Company Y's Application")
    assert r.catalyst_type == "FDA_PDUFA"
    assert r.direction == "BAJISTA"


def test_clinical_trial_positivo():
    r = cc.classify_catalyst_type("Company X Announces Positive Phase 3 Topline Results, Meets Primary Endpoint")
    assert r.catalyst_type == "CLINICAL_TRIAL"
    assert r.direction == "ALCISTA"
    assert r.importance == "alta"


def test_clinical_trial_fallido():
    r = cc.classify_catalyst_type("Company X Phase 2 Trial Fails to Meet Endpoint")
    assert r.catalyst_type == "CLINICAL_TRIAL"
    assert r.direction == "BAJISTA"


def test_ma_acquisition():
    r = cc.classify_catalyst_type("Company X to be Acquired by Company Y in $2B Merger")
    assert r.catalyst_type == "MA_ACQUISITION"
    assert r.direction == "ALCISTA"


def test_financing_dilution_siempre_bajista():
    r = cc.classify_catalyst_type("Company Y Prices $50M Registered Direct Offering")
    assert r.catalyst_type == "FINANCING_DILUTION"
    assert r.direction == "BAJISTA"


def test_contract_award():
    r = cc.classify_catalyst_type("Company Z Awarded $10M Contract by Department of Defense")
    assert r.catalyst_type == "CONTRACT_AWARD"
    assert r.direction == "ALCISTA"


def test_guidance_raises():
    r = cc.classify_catalyst_type("Company X Raises Full-Year Guidance")
    assert r.catalyst_type == "GUIDANCE"
    assert r.direction == "ALCISTA"


def test_guidance_cuts():
    r = cc.classify_catalyst_type("Company X Cuts Outlook for Fiscal Year")
    assert r.catalyst_type == "GUIDANCE"
    assert r.direction == "BAJISTA"


def test_analyst_action_upgrade():
    r = cc.classify_catalyst_type("Analyst Upgrades Company X to Buy, Raises Price Target")
    assert r.catalyst_type == "ANALYST_ACTION"
    assert r.direction == "ALCISTA"
    assert r.importance == "baja"


def test_partnership():
    r = cc.classify_catalyst_type("Company X Announces Strategic Partnership with Company Y")
    assert r.catalyst_type == "PARTNERSHIP"
    assert r.direction == "ALCISTA"


def test_product_launch():
    r = cc.classify_catalyst_type("Company X Unveils New Product Line")
    assert r.catalyst_type == "PRODUCT_LAUNCH"


def test_earnings_beat():
    r = cc.classify_catalyst_type("Company X Q2 2026 Results: Earnings Beat Estimates")
    assert r.catalyst_type == "EARNINGS"
    assert r.direction == "ALCISTA"


def test_earnings_miss():
    r = cc.classify_catalyst_type("Company X Reports Earnings, Misses Estimates")
    assert r.catalyst_type == "EARNINGS"
    assert r.direction == "BAJISTA"


def test_fallback_other_material():
    r = cc.classify_catalyst_type("Company X Announces New CEO Appointment")
    assert r.catalyst_type == "OTHER_MATERIAL"
    assert r.confidence == 0.3


def test_match_solo_en_summary_da_confianza_menor():
    r = cc.classify_catalyst_type("Company X Provides Update", summary="The FDA has granted priority review.")
    assert r.catalyst_type == "FDA_PDUFA"
    assert r.confidence == 0.6


# ---------------------------------------------------------------------------
# 2. Ciclo de vida -- el fix directo del caso MRNA
# ---------------------------------------------------------------------------

def test_lifecycle_evento_futuro_lejos():
    now = datetime(2026, 8, 21, tzinfo=timezone.utc)
    estado = cc.classify_catalyst_lifecycle(
        event_date="2026-09-05", published_at=None, now=now, price_change_since_published_pct=None,
    )
    assert estado == "FUTURO"


def test_lifecycle_evento_inminente_caso_zyme_sintetico():
    """Caso "tipo ZYME": evento en 2 días, sin movimiento previo todavía --
    debe dar INMINENTE, nunca EXTENDIDA/OCURRIDO."""
    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    estado = cc.classify_catalyst_lifecycle(
        event_date="2026-08-25", published_at=now - timedelta(hours=6), now=now,
        price_change_since_published_pct=2.5,
    )
    assert estado == "INMINENTE"


def test_lifecycle_en_anticipacion_gana_sobre_inminente():
    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    estado = cc.classify_catalyst_lifecycle(
        event_date="2026-08-25", published_at=now - timedelta(days=2), now=now,
        price_change_since_published_pct=12.0,  # >= 8.0 -- el mercado ya se posicionó
    )
    assert estado == "EN_ANTICIPACION"


def test_lifecycle_caso_real_mrna_da_extendida():
    """Caso REAL de MRNA (2026-08-19, ya en la base de esta sesión):
    detectada 06:45 ET, sin event_date propio (la noticia SE PUBLICÓ, no
    hay una fecha de evento futura separada), total_day_change_pct=49.91%
    (cierre real) -- muy por encima del piso de 40%. Debe dar EXTENDIDA,
    el fix literal de "vendí temprano porque no sabía que un movimiento
    por noticia sigue todo el día... pero cuando lo vi ya se había
    movido casi todo"."""
    now = datetime(2026, 8, 19, 20, 0, tzinfo=timezone.utc)  # cierre del mismo día
    published_at = datetime(2026, 8, 19, 10, 45, 36, tzinfo=timezone.utc)  # detección real
    estado = cc.classify_catalyst_lifecycle(
        event_date=None, published_at=published_at, now=now,
        price_change_since_published_pct=49.91,  # total_day_change_pct real
    )
    assert estado == "EXTENDIDA"


def test_lifecycle_movimiento_grande_mismo_dia_sin_tiempo_pasado_no_es_extendida_si_hay_fecha():
    """Si published_at es de HACE MUY POCO (menos de 1 día) Y tenemos esa
    fecha, se espera a que pase al menos una sesión completa antes de
    llamarla EXTENDIDA -- da OCURRIDO mientras tanto."""
    now = datetime(2026, 8, 19, 11, 0, tzinfo=timezone.utc)  # 15 min después de publicada
    published_at = datetime(2026, 8, 19, 10, 45, 36, tzinfo=timezone.utc)
    estado = cc.classify_catalyst_lifecycle(
        event_date=None, published_at=published_at, now=now,
        price_change_since_published_pct=45.0,
    )
    assert estado == "OCURRIDO"


def test_lifecycle_ocurrido_sin_movimiento_grande():
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    estado = cc.classify_catalyst_lifecycle(
        event_date=None, published_at=now - timedelta(days=2), now=now,
        price_change_since_published_pct=5.0,
    )
    assert estado == "OCURRIDO"


def test_lifecycle_sin_ninguna_fecha_da_ocurrido_conservador():
    now = datetime(2026, 8, 21, tzinfo=timezone.utc)
    estado = cc.classify_catalyst_lifecycle(
        event_date=None, published_at=None, now=now, price_change_since_published_pct=None,
    )
    assert estado == "OCURRIDO"  # nunca FUTURO sin evidencia


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
