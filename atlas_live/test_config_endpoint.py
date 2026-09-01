"""Test del endpoint /api/config -- valores REALES del backend, no MOCK
(limpieza MOCK 2026-08-07, ver DECISION_LOG.md).

Neutraliza el arranque pesado (seed import + refresco en segundo plano)
ANTES de importar el servidor, para correr offline y sin red.
"""

import atlas_live.backtest.seed_import as _si
import atlas_live.market_view as _mv
import atlas_live.scan_worker as _sw

# Neutralizar el arranque pesado SOLO durante el import del servidor (que
# corre seed import + refresco en segundo plano a nivel de módulo), y
# RESTAURAR los originales enseguida -- si no, otros tests (ej.
# test_seed_sync) recibirían estos stubs y fallarían.
_orig_seed = _si.import_all_seeds
_orig_refresh = _sw.start_background_refresh
_orig_market_view = _mv.start_market_view
_si.import_all_seeds = lambda *a, **k: None
_sw.start_background_refresh = lambda *a, **k: None
_mv.start_market_view = lambda *a, **k: None
try:
    from atlas_live import server  # noqa: E402  (import tras neutralizar el arranque)
finally:
    _si.import_all_seeds = _orig_seed
    _sw.start_background_refresh = _orig_refresh
    _mv.start_market_view = _orig_market_view
from atlas_live.explosive_config import load_config as _load_exp
from atlas_live.memory.classifier import load_config as _load_cls


def _get():
    return server.app.test_client().get("/api/config").get_json()


def test_config_reads_real_scan_interval():
    assert _get()["refresh_interval_seconds"] == _sw.REFRESH_INTERVAL_SECONDS == 300


def test_config_reads_real_classifier_thresholds():
    c = _get()
    cls = _load_cls()
    assert c["explosion_threshold_pct"] == cls["explosion_threshold_pct"]
    assert c["false_breakout_ceiling_pct"] == cls["false_breakout_ceiling_pct"]
    assert c["loser_threshold_pct"] == cls["loser_threshold_pct"]


def test_config_reads_real_microcap_ceiling():
    c = _get()
    exp = _load_exp()
    assert c["microcap_ceiling_usd"] == exp["size_factor"]["small_cap_reference"]
    assert c["min_price_usd"] == exp["gates"]["min_price"]
    assert c["top_n"] == exp["top_n"]


def test_config_market_hours_and_seal_window_present():
    c = _get()
    mh = c["market_hours"]
    assert mh["regular"] == "09:30-16:00"
    assert mh["timezone"] == "America/New_York"
    assert c["seal_window"] == "09:25 - 09:30 ET"


def test_config_has_no_hardcoded_mock_marker():
    # El endpoint no debe inventar valores: todos salen de módulos reales.
    # (Sanidad: la respuesta no trae ninguna marca de ejemplo/simulado.)
    import json
    blob = json.dumps(_get()).lower()
    for bad in ("mock", "ejemplo", "simulad"):
        assert bad not in blob
