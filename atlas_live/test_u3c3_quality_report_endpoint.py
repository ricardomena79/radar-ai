"""Tests de `GET /api/admin/u3c3-quality-report` (2026-09-02, autorizado
explícitamente) -- mismo patrón sin red/sin hilos de fondo que
`test_data_dir_diagnostics_endpoint.py`. Confirma: token obligatorio, cero
parámetros aceptados del cliente (query/body nunca alteran el rango de
fechas), rango acotado a [U3_DEPLOY_MARKET_DATE, hoy] calculado 100%
server-side, respuesta controlada sin datos, y que la cadena completa nunca
escribe nada."""

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

from atlas_live.memory import market_hours as mh  # noqa: E402
from atlas_live.radar import detector_comparison as dc  # noqa: E402
from atlas_live.radar import shadow_detector_registry as sreg  # noqa: E402


def _client():
    return server.app.test_client()


def test_sin_token_rechaza():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().get("/api/admin/u3c3-quality-report")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_con_token_permite_y_devuelve_reporte(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    monkeypatch.setattr(mh, "market_date", lambda now=None: "2026-09-02")
    monkeypatch.setattr(sreg, "list_shadow_market_dates", lambda: ["2026-08-27"])
    monkeypatch.setattr(dc, "quality_report_aggregated", lambda market_dates: {"muestra_total": 0, "market_dates": market_dates})
    try:
        r = _client().get("/api/admin/u3c3-quality-report?token=secreto-real")
        assert r.status_code == 200
        body = r.get_json()
        assert body["market_dates"] == ["2026-08-27"]
        assert body["u3c3_window"]["u3_deploy_date"] == "2026-08-26"
        assert body["u3c3_window"]["hoy"] == "2026-09-02"
        assert body["u3c3_window"]["market_dates_usados"] == ["2026-08-27"]
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_query_params_no_alteran_el_rango(monkeypatch):
    """Intentar inyectar fechas/SQL vía query string no tiene ningún
    efecto -- el endpoint nunca lee `request.args`."""
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    monkeypatch.setattr(mh, "market_date", lambda now=None: "2026-09-02")
    monkeypatch.setattr(sreg, "list_shadow_market_dates", lambda: ["2026-08-27"])
    capturado = {}

    def _fake_quality_report(market_dates):
        capturado["market_dates"] = market_dates
        return {"muestra_total": 0}

    monkeypatch.setattr(dc, "quality_report_aggregated", _fake_quality_report)
    try:
        r = _client().get(
            "/api/admin/u3c3-quality-report?token=secreto-real"
            "&market_date=2020-01-01&from=2000-01-01&to=2099-12-31"
            "&market_dates=2026-08-26,2026-08-27,2026-08-28&sql=DROP TABLE x"
        )
        assert r.status_code == 200
        assert capturado["market_dates"] == ["2026-08-27"]  # ignoró todo lo inyectado
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_body_no_altera_el_rango(monkeypatch):
    """El endpoint es GET y nunca lee el body -- confirmado enviando un
    JSON body de todos modos."""
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    monkeypatch.setattr(mh, "market_date", lambda now=None: "2026-09-02")
    monkeypatch.setattr(sreg, "list_shadow_market_dates", lambda: ["2026-08-27"])
    capturado = {}

    def _fake_quality_report(market_dates):
        capturado["market_dates"] = market_dates
        return {"muestra_total": 0}

    monkeypatch.setattr(dc, "quality_report_aggregated", _fake_quality_report)
    try:
        r = _client().get(
            "/api/admin/u3c3-quality-report?token=secreto-real",
            json={"market_dates": ["2000-01-01"], "start": "1999-01-01"},
        )
        assert r.status_code == 200
        assert capturado["market_dates"] == ["2026-08-27"]
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_rango_inferior_fijo_excluye_fechas_anteriores_a_u3(monkeypatch):
    monkeypatch.setattr(mh, "market_date", lambda now=None: "2026-09-02")
    # Fechas reales anteriores al deploy de U3 (imposibles en la práctica,
    # pero probadas igual para blindar el filtro) deben quedar excluidas.
    monkeypatch.setattr(
        sreg, "list_shadow_market_dates",
        lambda: ["2026-08-01", "2026-08-25", "2026-08-26", "2026-08-27"],
    )
    capturado = {}
    def _fake_quality_report(market_dates):
        capturado["md"] = market_dates
        return {}

    monkeypatch.setattr(dc, "quality_report_aggregated", _fake_quality_report)
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    try:
        r = _client().get("/api/admin/u3c3-quality-report?token=secreto-real")
        assert r.status_code == 200
        assert capturado["md"] == ["2026-08-26", "2026-08-27"]
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_fechas_posteriores_a_hoy_quedan_fuera(monkeypatch):
    monkeypatch.setattr(mh, "market_date", lambda now=None: "2026-08-28")
    monkeypatch.setattr(
        sreg, "list_shadow_market_dates",
        lambda: ["2026-08-27", "2026-08-28", "2026-08-29", "2026-09-05"],
    )
    capturado = {}
    def _fake_quality_report(market_dates):
        capturado["md"] = market_dates
        return {}

    monkeypatch.setattr(dc, "quality_report_aggregated", _fake_quality_report)
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    try:
        r = _client().get("/api/admin/u3c3-quality-report?token=secreto-real")
        assert r.status_code == 200
        assert capturado["md"] == ["2026-08-27", "2026-08-28"]
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_sin_fechas_shadow_en_el_rango_respuesta_controlada(monkeypatch):
    monkeypatch.setattr(mh, "market_date", lambda now=None: "2026-09-02")
    monkeypatch.setattr(sreg, "list_shadow_market_dates", lambda: [])
    llamado = []
    monkeypatch.setattr(dc, "quality_report_aggregated", lambda market_dates: llamado.append(market_dates))
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    try:
        r = _client().get("/api/admin/u3c3-quality-report?token=secreto-real")
        assert r.status_code == 200
        body = r.get_json()
        assert body["error"] == "sin datos shadow en el rango U3-C3"
        assert body["u3_deploy_date"] == "2026-08-26"
        assert llamado == []  # quality_report() NUNCA se llama sin fechas reales
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_endpoint_completo_no_escribe_nada_en_ninguna_db(monkeypatch, tmp_path):
    """Extremo a extremo, con las funciones REALES (no mockeadas) de
    `shadow_detector_registry`/`detector_comparison`/`candidate_registry`,
    sobre bases temporales -- confirma que ni un solo byte cambia en
    ninguno de los 2 archivos tras llamar al endpoint completo."""
    import tempfile
    import uuid as _uuid
    from pathlib import Path

    from atlas_live.radar import candidate_registry as reg

    orig_sreg_db, orig_reg_db = sreg.DB_PATH, reg.DB_PATH
    sreg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_u3c3_shadow_{_uuid.uuid4().hex}.db"
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_u3c3_reg_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None
    monkeypatch.setattr(mh, "market_date", lambda now=None: "2026-08-27")
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    try:
        sreg.record_shadow_detection(
            ticker="AAA", market_date="2026-08-26", session="regular",
            price=10.0, change_pct=5.0, volume=1000, average_volume=200,
            relative_volume=5.0, dollar_volume=10000.0,
            price_source="tradier", price_basis="tradier_last", price_is_stale=False,
            universe_source="piggyback_radar",
            gates_fired=[{"gate": "price_change", "reason": "x", "value": 5.0}],
            snapshot={"price": 10.0},
        )
        # Fuerza la creación única del esquema de radar_candidates.db ANTES
        # de medir "antes" -- replica el estado real de producción, donde
        # el archivo ya existe con su esquema completo (nunca un archivo
        # recién creado desde cero).
        reg._connect().close()
        size_shadow_antes = sreg.DB_PATH.stat().st_size
        size_reg_antes = reg.DB_PATH.stat().st_size

        r = _client().get("/api/admin/u3c3-quality-report?token=secreto-real")
        assert r.status_code == 200

        assert sreg.DB_PATH.stat().st_size == size_shadow_antes
        assert reg.DB_PATH.stat().st_size == size_reg_antes
        # La detección shadow original sigue exactamente igual.
        assert sreg.count_shadow_detections("2026-08-26") == 1
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]
        sreg.DB_PATH = orig_sreg_db
        reg.DB_PATH = orig_reg_db
