"""Tests de `POST /api/admin/raw-data-consolidation/consolidate` y
`GET /api/admin/raw-data-consolidation/status` (2026-09-02, autorizado
explícitamente, Hito 2) -- mismo patrón sin red/sin hilos de fondo que
`test_u3c3_exclusive_diagnostics_endpoint.py`.

Nota TEST-ONLY (2026-09-02, sin relación con la lógica productiva de
Hito 2): además de los 4 workers que ya neutralizaba
`test_u3c3_exclusive_diagnostics_endpoint.py`, este archivo neutraliza
también `study_worker.start_study_worker`/`catalyst_worker.start_catalyst_worker`
-- ambos se arrancan a nivel de módulo en `server.py` y, sin neutralizar,
`catalyst_worker._loop()` dispara su Tier 1 de inmediato en un hilo de
fondo, que llama a `candidate_registry.list_candidates_for_date()` --
esto puede correr en paralelo con `_fresh()` (de
`test_raw_data_consolidation.py`) reasignando
`candidate_registry.DB_PATH`/`_schema_ready_for` en el hilo principal,
produciendo el `sqlite3.OperationalError: no such table:
candidate_observation` observado de forma determinista quando ambos
archivos corren en cierto orden. Confirmado por lectura de código
(`catalyst_worker.py:104,154,283-292,317-338`), no supuesto. Nunca fue
causado por `raw_data_consolidation.py`/`_pipeline.py`/`_registry.py`
(ninguno de los 3 llama a `candidate_registry._connect()` ni toca
`_schema_ready_for`)."""

import os

import atlas_live.backtest.seed_import as _si
import atlas_live.catalyst.catalyst_worker as _cw
import atlas_live.market_study.study_worker as _stw
import atlas_live.market_view as _mv
import atlas_live.radar.radar_worker as _rw
import atlas_live.scan_worker as _sw

_orig_seed = _si.import_all_seeds
_orig_refresh = _sw.start_background_refresh
_orig_radar = _rw.start_universe_radar
_orig_market_view = _mv.start_market_view
_orig_study = _stw.start_study_worker
_orig_catalyst = _cw.start_catalyst_worker
_si.import_all_seeds = lambda *a, **k: None
_sw.start_background_refresh = lambda *a, **k: None
_rw.start_universe_radar = lambda *a, **k: None
_mv.start_market_view = lambda *a, **k: None
_stw.start_study_worker = lambda *a, **k: None
_cw.start_catalyst_worker = lambda *a, **k: None
try:
    from atlas_live import server  # noqa: E402
finally:
    _si.import_all_seeds = _orig_seed
    _sw.start_background_refresh = _orig_refresh
    _rw.start_universe_radar = _orig_radar
    _mv.start_market_view = _orig_market_view
    _stw.start_study_worker = _orig_study
    _cw.start_catalyst_worker = _orig_catalyst

from atlas_live.radar import raw_data_consolidation_pipeline as pipeline  # noqa: E402
from atlas_live.radar import raw_data_consolidation_registry as registry  # noqa: E402


def _client():
    return server.app.test_client()


# --- /consolidate ------------------------------------------------------

def test_consolidate_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().post("/api/admin/raw-data-consolidation/consolidate")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_consolidate_con_token_source_table_invalida_da_400(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    try:
        r = _client().post(
            "/api/admin/raw-data-consolidation/consolidate"
            "?token=secreto-real&source_table=candidate_detection&ticker=AAA&market_date=2026-08-15"
        )
        assert r.status_code == 400
        assert "valores_permitidos" in r.get_json()
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_consolidate_sin_ticker_ni_market_date_da_400():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    try:
        r = _client().post(
            "/api/admin/raw-data-consolidation/consolidate"
            "?token=secreto-real&source_table=candidate_observation"
        )
        assert r.status_code == 400
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_consolidate_con_token_y_parametros_validos_delega_en_el_pipeline(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    capturado = {}

    def _fake(source_table, ticker, market_date):
        capturado["args"] = (source_table, ticker, market_date)
        return {"ok": True, "status": "verified", "row_count_covered": 5, "error": None}

    monkeypatch.setattr(pipeline, "consolidate_block", _fake)
    try:
        r = _client().post(
            "/api/admin/raw-data-consolidation/consolidate"
            "?token=secreto-real&source_table=candidate_observation&ticker=AAA&market_date=2026-08-15"
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["status"] == "verified"
        assert capturado["args"] == ("candidate_observation", "AAA", "2026-08-15")
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_consolidate_ok_false_devuelve_500(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    monkeypatch.setattr(
        pipeline, "consolidate_block",
        lambda source_table, ticker, market_date: {"ok": False, "error": "fallo sintetico"},
    )
    try:
        r = _client().post(
            "/api/admin/raw-data-consolidation/consolidate"
            "?token=secreto-real&source_table=candidate_observation&ticker=AAA&market_date=2026-08-15"
        )
        assert r.status_code == 500
        assert r.get_json()["ok"] is False
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


# --- /status -------------------------------------------------------------

def test_status_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().get("/api/admin/raw-data-consolidation/status")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_status_con_token_devuelve_bloques(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    monkeypatch.setattr(registry, "list_blocks", lambda source_table=None: [{"block_key": "AAA|2026-08-15"}])
    try:
        r = _client().get("/api/admin/raw-data-consolidation/status?token=secreto-real")
        assert r.status_code == 200
        body = r.get_json()
        assert body["n_bloques"] == 1
        assert body["bloques"] == [{"block_key": "AAA|2026-08-15"}]
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_status_con_source_table_invalida_da_400():
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    try:
        r = _client().get("/api/admin/raw-data-consolidation/status?token=secreto-real&source_table=otra_cosa")
        assert r.status_code == 400
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]
