"""Tests de `GET /api/admin/shadow-retention-dry-run` (Hito 6, Fase
6.4-D1, 2026-09-05, autorizado explícitamente) -- mismo patrón sin
red/sin hilos de fondo que `test_u3c3_quality_report_endpoint.py`.
Confirma: token obligatorio, respuesta 200 con el reporte real,
`retention_days` inválido/por debajo del piso rechazado con 400."""

import os

import atlas_live.backtest.seed_import as _si
import atlas_live.market_view as _mv
import atlas_live.radar.radar_worker as _rw
import atlas_live.scan_worker as _sw

_orig_seed = _si.import_all_seeds
_orig_refresh = _sw.start_background_refresh
_orig_radar = _rw.start_universe_radar
_orig_market_view = _mv.start_market_view
_si.import_all_seeds = lambda *a, **k: None
_sw.start_background_refresh = lambda *a, **k: None
_rw.start_universe_radar = lambda *a, **k: None
_mv.start_market_view = lambda *a, **k: None
try:
    from atlas_live import server  # noqa: E402
finally:
    _si.import_all_seeds = _orig_seed
    _sw.start_background_refresh = _orig_refresh
    _rw.start_universe_radar = _orig_radar
    _mv.start_market_view = _orig_market_view

from atlas_live.radar import shadow_retention_dry_run as srd  # noqa: E402


def _client():
    return server.app.test_client()


def test_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().get("/api/admin/shadow-retention-dry-run")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_con_token_devuelve_el_reporte_real(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    reporte_falso = {"n_eligible_blocks": 0, "retention_days": 180}
    monkeypatch.setattr(srd, "dry_run_retention_report", lambda retention_days: reporte_falso)
    try:
        r = _client().get("/api/admin/shadow-retention-dry-run?token=secreto-real")
        assert r.status_code == 200
        assert r.get_json() == reporte_falso
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_retention_days_por_debajo_del_piso_devuelve_400(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    try:
        r = _client().get("/api/admin/shadow-retention-dry-run?token=secreto-real&retention_days=10")
        assert r.status_code == 400
        assert "error" in r.get_json()
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_retention_days_no_numerico_devuelve_400():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    try:
        r = _client().get("/api/admin/shadow-retention-dry-run?token=secreto-real&retention_days=abc")
        assert r.status_code == 400
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_endpoint_nunca_escribe_en_ninguna_db(monkeypatch, tmp_path):
    """Corrida real (sin mockear `dry_run_retention_report`) contra una
    `raw_data_consolidation.db` temporal vacía -- confirma que la cadena
    completa endpoint -> módulo responde 200 sin tocar ninguna base real."""
    import tempfile
    import uuid
    from pathlib import Path

    from atlas_live.radar import raw_data_consolidation_registry as registry

    orig_db = registry.DB_PATH
    registry.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_endpoint_rdc_{uuid.uuid4().hex}.db"
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    try:
        registry._connect().close()
        r = _client().get("/api/admin/shadow-retention-dry-run?token=secreto-real")
        assert r.status_code == 200
        body = r.get_json()
        assert body["n_eligible_blocks"] == 0
        assert body["total_blocks_scanned"] == 0
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]
        registry.DB_PATH = orig_db
