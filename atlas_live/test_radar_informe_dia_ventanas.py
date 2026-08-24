"""Test de GET /api/radar-informe-dia -- campos nuevos de rigor
estadístico (2026-08-24): `precision_de_magnitud_ventanas`/`_racional` y
los 3 campos nuevos dentro de `precision_de_magnitud_acumulada`. Mismo
patrón que `test_catalyst_events_endpoint.py`: sin red, sin arrancar
hilos de fondo reales, DB temporal."""

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

from atlas_live.radar import candidate_registry as reg  # noqa: E402

_ORIG_DB = reg.DB_PATH


def _fresh_db():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_informe_dia_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None


def _restore_db():
    reg.DB_PATH = _ORIG_DB


def _client():
    return server.app.test_client()


def test_radar_informe_dia_incluye_ventanas_vacias_sin_romper():
    _fresh_db()
    try:
        resp = _client().get("/api/radar-informe-dia")
        assert resp.status_code == 200
        data = resp.get_json()
        ventanas = data["precision_de_magnitud_ventanas"]
        for clave in ("ultimas_50", "ultimas_100", "ultimas_250", "ultimas_500"):
            assert clave in ventanas
            assert ventanas[clave]["datos_suficientes"] is False
            assert ventanas[clave]["precision_pct"] is None
        assert "precision_de_magnitud_ventanas_racional" in data

        acum = data["precision_de_magnitud_acumulada"]
        assert acum["validation_state"] == "MUESTRA_INSUFICIENTE"
        assert acum["meta_confirmada"] is False
        assert acum["wilson_ci"] is None
    finally:
        _restore_db()


def test_radar_informe_dia_ventana_con_datos_reales():
    _fresh_db()
    try:
        for i in range(3):
            ticker = f"T{i}"
            fecha = f"2026-08-1{i}"
            reg.record_magnitud_prediction(ticker, fecha, f"{fecha}T10:00:00Z", 10.0)
            reg.record_outcome(
                ticker, fecha, run_up_before_detection_pct=None, max_price_after_detection=None,
                max_return_after_detection_pct=20.0, minutes_to_max=None, reached_20=False,
                reached_50=False, reached_100=False, category="mejor_oportunidad", is_final=True,
                confiable_para_aprendizaje=True, close_return_after_detection_pct=20.0,
            )
        resp = _client().get("/api/radar-informe-dia")
        assert resp.status_code == 200
        data = resp.get_json()
        ventana_3_no_pedida = data["precision_de_magnitud_ventanas"]["ultimas_50"]
        assert ventana_3_no_pedida["n_evaluables"] == 3
        assert ventana_3_no_pedida["datos_suficientes"] is False  # 3 < 50, sigue sin alcanzar
        assert data["precision_de_magnitud_acumulada"]["n_evaluables"] == 3
        assert data["precision_de_magnitud_acumulada"]["precision_pct"] == 100.0
    finally:
        _restore_db()


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
