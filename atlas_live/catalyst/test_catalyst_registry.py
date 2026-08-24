"""Tests del registro de catalizadores (2026-08-23). DB temporal, sin red."""

import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.catalyst import catalyst_registry as reg

_ORIG = reg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_catalyst_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None


def _restore():
    reg.DB_PATH = _ORIG


def test_upsert_catalyst_event_es_write_once_por_ticker_source_source_id():
    _fresh()
    try:
        id1 = reg.upsert_catalyst_event(
            "ZYME", "FDA_PDUFA", "ZYME PDUFA date approaches", "finnhub_company_news",
            importance="alta", direction="NEUTRAL", confidence=1.0, source_id="news-123",
            event_date="2026-08-25",
        )
        # segunda vez, mismo (ticker, source, source_id) -- actualiza, no duplica.
        id2 = reg.upsert_catalyst_event(
            "ZYME", "FDA_PDUFA", "ZYME PDUFA date approaches (updated)", "finnhub_company_news",
            importance="alta", direction="ALCISTA", confidence=1.0, source_id="news-123",
            event_date="2026-08-25",
        )
        assert id1 == id2
        eventos = reg.get_events_for_ticker("ZYME")
        assert len(eventos) == 1
        assert eventos[0]["direction"] == "ALCISTA"  # se actualizó
        assert eventos[0]["headline"] == "ZYME PDUFA date approaches (updated)"
    finally:
        _restore()


def test_upsert_catalyst_event_distintos_source_id_no_se_pisan():
    _fresh()
    try:
        reg.upsert_catalyst_event("ZYME", "FDA_PDUFA", "Noticia A", "finnhub_company_news",
                                   importance="alta", direction="NEUTRAL", confidence=1.0, source_id="a")
        reg.upsert_catalyst_event("ZYME", "FDA_PDUFA", "Noticia B", "finnhub_company_news",
                                   importance="alta", direction="NEUTRAL", confidence=1.0, source_id="b")
        eventos = reg.get_events_for_ticker("ZYME")
        assert len(eventos) == 2
    finally:
        _restore()


def test_list_recent_events_mas_reciente_primero():
    _fresh()
    try:
        reg.upsert_catalyst_event("AAA", "EARNINGS", "AAA earnings", "finnhub_earnings_calendar",
                                   importance="media", direction="NEUTRAL", confidence=1.0, source_id="1")
        reg.upsert_catalyst_event("BBB", "FDA_PDUFA", "BBB FDA news", "finnhub_company_news",
                                   importance="alta", direction="ALCISTA", confidence=1.0, source_id="2")
        recientes = reg.list_recent_events(limit=10)
        assert [r["ticker"] for r in recientes] == ["BBB", "AAA"]
    finally:
        _restore()


def test_list_upcoming_events_filtra_por_ventana_de_fecha():
    _fresh()
    try:
        reg.upsert_catalyst_event("CERCA", "FDA_PDUFA", "evento cerca", "finnhub_company_news",
                                   importance="alta", direction="NEUTRAL", confidence=1.0,
                                   source_id="1", event_date="2026-08-25")
        reg.upsert_catalyst_event("LEJOS", "FDA_PDUFA", "evento lejos", "finnhub_company_news",
                                   importance="alta", direction="NEUTRAL", confidence=1.0,
                                   source_id="2", event_date="2026-12-01")
        proximos = reg.list_upcoming_events(days_ahead=7, reference_date="2026-08-21")
        assert [p["ticker"] for p in proximos] == ["CERCA"]
    finally:
        _restore()


def test_record_lifecycle_transition_solo_inserta_si_cambia():
    _fresh()
    try:
        cid = reg.upsert_catalyst_event("ZYME", "FDA_PDUFA", "ZYME PDUFA", "finnhub_company_news",
                                         importance="alta", direction="NEUTRAL", confidence=1.0, source_id="1")
        primero = reg.record_lifecycle_transition(cid, "ZYME", "2026-08-21T10:00:00Z", "INMINENTE", days_to_event=2.0)
        segundo = reg.record_lifecycle_transition(cid, "ZYME", "2026-08-21T10:05:00Z", "INMINENTE", days_to_event=1.9)
        tercero = reg.record_lifecycle_transition(cid, "ZYME", "2026-08-22T10:00:00Z", "EN_ANTICIPACION", days_to_event=1.0)
        assert primero is True
        assert segundo is False  # mismo estado, no duplica
        assert tercero is True   # cambió de verdad
        assert reg.latest_lifecycle_state(cid) == "EN_ANTICIPACION"
    finally:
        _restore()


def test_record_score_snapshot_es_write_once():
    _fresh()
    try:
        primero = reg.record_score_snapshot("MRNA", "2026-08-19", "2026-08-19T10:45:00Z",
                                             catalyst_score=85.0, mrna_similarity_score=95.0,
                                             score_components={"importance": 100, "lifecycle": 60})
        segundo = reg.record_score_snapshot("MRNA", "2026-08-19", "2026-08-19T14:00:00Z",
                                             catalyst_score=10.0, mrna_similarity_score=10.0,
                                             score_components={})
        assert primero is True
        assert segundo is False
        snap = reg.get_score_snapshot("MRNA", "2026-08-19")
        assert snap["catalyst_score"] == 85.0  # nunca se pisó
    finally:
        _restore()


def test_poll_state_y_provider_health_summary():
    _fresh()
    try:
        assert reg.provider_health_summary()["status"] == "SIN_CONFIGURAR"

        reg.set_poll_state("AAPL", ok=True, n_events=3)
        salud = reg.provider_health_summary()
        assert salud["status"] == "OK"
        assert salud["last_successful_poll_at"] is not None

        estado = reg.get_poll_state("AAPL")
        assert estado["last_poll_ok"] == 1
        assert estado["n_events_found"] == 3
    finally:
        _restore()


def test_provider_health_summary_offline_si_nada_reciente_salio_ok():
    _fresh()
    try:
        reg.set_poll_state("AAPL", ok=False, error="ProviderError: fallo de red")
        salud = reg.provider_health_summary(stale_after_minutes=30)
        assert salud["status"] == "OFFLINE"
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
