"""Tests de catalyst_collector.py (2026-08-23). DB temporal, sin red --
noticias/calendario sintéticos con la misma forma cruda que devuelve
FinnhubProvider."""

import tempfile
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

from atlas_live.catalyst import catalyst_collector as coll
from atlas_live.catalyst import catalyst_registry as reg

_ORIG = reg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_catalyst_coll_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None


def _restore():
    reg.DB_PATH = _ORIG


def test_process_news_item_clasifica_y_persiste():
    _fresh()
    try:
        now = datetime(2026, 8, 23, 14, 0, tzinfo=timezone.utc)
        item = {
            "id": 555, "headline": "Company X Announces Positive Phase 3 Topline Results",
            "summary": "Meets primary endpoint.", "url": "https://example.com/n/555",
            "datetime": int(now.timestamp()) - 3600,
        }
        resultado = coll.process_news_item("ZYME", item, now)
        assert resultado["catalyst_type"] == "CLINICAL_TRIAL"
        assert resultado["direction"] == "ALCISTA"

        eventos = reg.get_events_for_ticker("ZYME")
        assert len(eventos) == 1
        assert eventos[0]["source"] == "finnhub_company_news"
        assert eventos[0]["source_id"] == "555"

        # se registró la transición de ciclo de vida real
        assert reg.latest_lifecycle_state(resultado["catalyst_id"]) == resultado["lifecycle_state"]
    finally:
        _restore()


def test_process_news_item_caso_mrna_temprano_congela_score_por_relevancia():
    """Noticia de alta importancia (FDA), evidencia técnica fuerte, TEMPRANO
    en el movimiento (20 min después de publicada, +8% -- todavía no cruza
    el piso de EXTENDIDA) -- debe cruzar el piso de relevancia y congelar
    un snapshot de score. (El caso del cierre completo de MRNA, 49.9%
    varias horas después, clasifica EXTENDIDA a propósito -- ver
    test_lifecycle_caso_real_mrna_da_extendida en test_catalyst_classifier.py
    -- y EXTENDIDA nunca congela score, por diseño.)"""
    _fresh()
    try:
        publicada = datetime(2026, 8, 19, 10, 45, 36, tzinfo=timezone.utc)
        now = datetime(2026, 8, 19, 11, 5, tzinfo=timezone.utc)  # 20 min después
        item = {
            "id": 1, "headline": "FDA Grants Priority Review for MRNA's New Application",
            "summary": None, "url": "https://example.com/n/1",
            "datetime": int(publicada.timestamp()),
        }
        resultado = coll.process_news_item(
            "MRNA", item, now, price_now=70.85, price_at_detection=65.605,  # +8.0%
            gates_fired_count=4, relative_volume_at_detection=0.0071,
            change_pct_at_detection=4.2, relative_volume_hoy_peak=24.0,
            retroceso_desde_maximo_pct=8.0,
        )
        assert resultado["importance"] == "alta"
        assert resultado["lifecycle_state"] != "EXTENDIDA"
        snap = reg.get_score_snapshot("MRNA", "2026-08-19")
        assert snap is not None
        assert snap["catalyst_score"] > 0
        assert snap["mrna_similarity_score"] > 0
    finally:
        _restore()


def test_process_news_item_baja_importancia_no_congela_score():
    _fresh()
    try:
        now = datetime(2026, 8, 23, 14, 0, tzinfo=timezone.utc)
        item = {
            "id": 2, "headline": "Analyst Initiates Coverage on Company X",
            "summary": None, "url": None, "datetime": int(now.timestamp()),
        }
        resultado = coll.process_news_item("XYZ", item, now)
        assert resultado["importance"] == "baja"
        assert reg.get_score_snapshot("XYZ", "2026-08-23") is None
    finally:
        _restore()


def test_process_news_item_sin_precios_da_price_change_none():
    _fresh()
    try:
        now = datetime(2026, 8, 23, 14, 0, tzinfo=timezone.utc)
        item = {"id": 3, "headline": "Company Y Reports Earnings", "datetime": int(now.timestamp())}
        resultado = coll.process_news_item("YYY", item, now)
        assert resultado["price_change_since_published_pct"] is None
    finally:
        _restore()


def test_process_earnings_calendar_item_persiste_con_dedup_deterministico():
    _fresh()
    try:
        now = datetime(2026, 8, 21, tzinfo=timezone.utc)
        item = {"symbol": "zyme", "date": "2026-08-25", "hour": "bmo"}
        r1 = coll.process_earnings_calendar_item(item, now)
        r2 = coll.process_earnings_calendar_item(item, now)  # segundo sondeo, misma fila
        assert r1["ticker"] == "ZYME"
        assert r1["catalyst_id"] == r2["catalyst_id"]  # dedup, no duplica
        eventos = reg.get_events_for_ticker("ZYME")
        assert len(eventos) == 1
        assert eventos[0]["event_time"] == "BMO"
    finally:
        _restore()


def test_process_earnings_calendar_item_sin_symbol_o_date_devuelve_none():
    _fresh()
    try:
        now = datetime(2026, 8, 21, tzinfo=timezone.utc)
        assert coll.process_earnings_calendar_item({"symbol": "", "date": "2026-08-25"}, now) is None
        assert coll.process_earnings_calendar_item({"symbol": "ZYME", "date": None}, now) is None
    finally:
        _restore()


def test_process_news_item_calcula_racional_available_real(monkeypatch):
    _fresh()
    try:
        monkeypatch.setattr("atlas.data.universe.is_available", lambda t: t == "ZYME")
        now = datetime(2026, 8, 23, 14, 0, tzinfo=timezone.utc)
        item = {"id": 1, "headline": "ZYME Reports Earnings", "datetime": int(now.timestamp())}
        coll.process_news_item("ZYME", item, now)
        eventos = reg.get_events_for_ticker("ZYME")
        assert eventos[0]["racional_available"] == 1
    finally:
        _restore()


def test_process_earnings_calendar_item_calcula_racional_available_real(monkeypatch):
    _fresh()
    try:
        monkeypatch.setattr("atlas.data.universe.is_available", lambda t: t == "ZYME")
        now = datetime(2026, 8, 21, tzinfo=timezone.utc)
        coll.process_earnings_calendar_item({"symbol": "ZYME", "date": "2026-08-25", "hour": "bmo"}, now)
        coll.process_earnings_calendar_item({"symbol": "OTCJUNK", "date": "2026-08-25", "hour": "bmo"}, now)
        assert reg.get_events_for_ticker("ZYME")[0]["racional_available"] == 1
        assert reg.get_events_for_ticker("OTCJUNK")[0]["racional_available"] == 0
    finally:
        _restore()


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
