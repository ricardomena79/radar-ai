"""Tests de GET /api/catalyst-events y GET /api/admin/catalyst-worker-status
(2026-08-23) -- mismo patrón que test_radar_oportunidades_endpoint.py: sin
red, sin arrancar ningún hilo de fondo real, DB temporal para
catalyst_registry."""

import os
import tempfile
import uuid as _uuid
from pathlib import Path

import atlas_live.backtest.seed_import as _si
import atlas_live.catalyst.catalyst_worker as _cw
import atlas_live.radar.radar_worker as _rw
import atlas_live.scan_worker as _sw

_orig_seed = _si.import_all_seeds
_orig_refresh = _sw.start_background_refresh
_orig_radar = _rw.start_universe_radar
_orig_catalyst = _cw.start_catalyst_worker
_si.import_all_seeds = lambda *a, **k: None
_sw.start_background_refresh = lambda *a, **k: None
_rw.start_universe_radar = lambda *a, **k: None
_cw.start_catalyst_worker = lambda *a, **k: None
try:
    from atlas_live import server  # noqa: E402
finally:
    _si.import_all_seeds = _orig_seed
    _sw.start_background_refresh = _orig_refresh
    _rw.start_universe_radar = _orig_radar
    _cw.start_catalyst_worker = _orig_catalyst

from atlas_live.catalyst import catalyst_registry as creg  # noqa: E402

_ORIG_DB = creg.DB_PATH


def _fresh_db():
    creg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_catalyst_endpoint_{_uuid.uuid4().hex}.db"
    creg._schema_ready_for = None


def _restore_db():
    creg.DB_PATH = _ORIG_DB


def _client():
    return server.app.test_client()


def test_catalyst_events_vacio_devuelve_200_nunca_error():
    _fresh_db()
    try:
        resp = _client().get("/api/catalyst-events")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["catalizadores"] == []
        assert data["noticias_recientes"] == []
        assert data["provider_health"]["status"] == "SIN_CONFIGURAR"
    finally:
        _restore_db()


def test_catalyst_events_incluye_noticias_y_catalizadores_reales():
    _fresh_db()
    try:
        creg.upsert_catalyst_event(
            "ZYME", "FDA_PDUFA", "ZYME PDUFA date approaches", "finnhub_earnings_calendar",
            importance="alta", direction="NEUTRAL", confidence=1.0, source_id="1",
            event_date="2026-09-01",
        )
        creg.upsert_catalyst_event(
            "AAPL", "EARNINGS", "AAPL reports strong earnings", "finnhub_company_news",
            importance="media", direction="ALCISTA", confidence=1.0, source_id="news-1",
        )
        resp = _client().get("/api/catalyst-events")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["catalizadores"]) == 1
        assert data["catalizadores"][0]["ticker"] == "ZYME"
        assert len(data["noticias_recientes"]) == 2
    finally:
        _restore_db()


def test_admin_catalyst_worker_status_sin_token_rechaza():
    resp = _client().get("/api/admin/catalyst-worker-status")
    assert resp.status_code == 403


def test_admin_catalyst_worker_status_con_token_devuelve_diagnostico(monkeypatch):
    monkeypatch.setenv("ATLAS_ADMIN_TOKEN", "token-de-prueba")
    _fresh_db()
    try:
        resp = _client().get("/api/admin/catalyst-worker-status?token=token-de-prueba")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "provider_health" in data
        assert "worker_enabled" in data
        assert "tier3_cursor" in data
    finally:
        _restore_db()


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            if fn.__code__.co_argcount:
                class _FakeMonkeypatch:
                    def setenv(self, key, value):
                        os.environ[key] = value
                fn(_FakeMonkeypatch())
            else:
                fn()
            print("PASS", fn.__name__)
            p += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
            traceback.print_exc()
            f += 1
    print(f"--- {p} passed, {f} failed ---")
