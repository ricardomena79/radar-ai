"""Tests de `detector_comparison.py` (2026-08-26, U3-C3). Puramente con
fixtures sintéticas -- los datos sintéticos sirven ÚNICAMENTE para probar
que el matching/agregación son correctos, nunca para declarar un resultado
de mercado real (ver docstring del módulo e informe de esta fase)."""

import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.radar import candidate_registry as reg
from atlas_live.radar import detector_comparison as dc
from atlas_live.radar import shadow_detector_registry as sreg

_ORIG_REG_DB = reg.DB_PATH
_ORIG_SHADOW_DB = sreg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_dc_reg_{_uuid.uuid4().hex}.db"
    sreg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_dc_shadow_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None


def _restore():
    reg.DB_PATH = _ORIG_REG_DB
    sreg.DB_PATH = _ORIG_SHADOW_DB


def _legacy_row(ticker="AAA", detected_at="2026-08-26T14:30:00+00:00", session="regular"):
    return {"ticker": ticker, "detected_at": detected_at, "session": session, "price_at_detection": 10.0,
            "change_pct_at_detection": 6.0, "volume_at_detection": 500_000, "average_volume_at_detection": 100_000,
            "relative_volume_at_detection": 5.0, "dollar_volume_at_detection": 5_000_000.0,
            "price_basis_at_detection": "tradier_last", "gates_fired": [{"gate": "cambio_de_precio"}]}


def _shadow_row(ticker="AAA", detected_at="2026-08-26T14:31:00+00:00", session="regular"):
    return {"ticker": ticker, "detected_at": detected_at, "session": session, "price": 10.05,
            "change_pct": 6.3, "volume": 505_000, "average_volume": 100_000, "relative_volume": 5.05,
            "dollar_volume": 5_075_250.0, "price_source": "tradier", "price_basis": "tradier_last",
            "price_is_stale": 0, "universe_source": "piggyback_radar",
            "gates_fired": [{"gate": "cambio_de_precio"}]}


# --- A: matching temporal correcto (dentro de la ventana) -------------------

def test_A_matching_temporal_dentro_de_la_ventana():
    legacy = [_legacy_row(detected_at="2026-08-26T14:30:00+00:00")]
    shadow = [_shadow_row(detected_at="2026-08-26T14:31:00+00:00")]  # 60s de diferencia, ventana=180s
    r = dc.match_detections(shadow, legacy)
    assert len(r["matched"]) == 1
    assert r["matched"][0]["diff_seconds"] == 60.0
    assert not r["solo_legacy"] and not r["solo_unified"]


# --- B: mismo ticker, eventos distintos, no deben mezclarse -----------------

def test_B_mismo_ticker_eventos_distintos_no_se_mezclan():
    legacy = [
        _legacy_row(detected_at="2026-08-26T14:00:00+00:00"),   # evento A (mañana)
        _legacy_row(detected_at="2026-08-26T19:45:00+00:00"),   # evento B (tarde), mismo ticker
    ]
    shadow = [_shadow_row(detected_at="2026-08-26T14:01:00+00:00")]  # solo cerca del evento A
    r = dc.match_detections(shadow, legacy)
    assert len(r["matched"]) == 1
    assert r["matched"][0]["legacy"]["detected_at"] == "2026-08-26T14:00:00+00:00"
    # El evento B (tarde) queda SOLO_LEGACY -- nunca se empareja con el
    # shadow de la mañana solo porque comparten ticker.
    assert len(r["solo_legacy"]) == 1
    assert r["solo_legacy"][0]["detected_at"] == "2026-08-26T19:45:00+00:00"


# --- C: detecciones simultaneas ---------------------------------------------

def test_C_detecciones_simultaneas():
    legacy = [_legacy_row(detected_at="2026-08-26T14:30:00+00:00")]
    shadow = [_shadow_row(detected_at="2026-08-26T14:30:00+00:00")]
    r = dc.match_detections(shadow, legacy)
    assert len(r["matched"]) == 1
    assert r["matched"][0]["diff_seconds"] == 0.0


# --- D: varios minutos de diferencia -- excede la ventana, NO matchea ------

def test_D_fuera_de_ventana_no_matchea():
    legacy = [_legacy_row(detected_at="2026-08-26T14:30:00+00:00")]
    shadow = [_shadow_row(detected_at="2026-08-26T14:40:00+00:00")]  # 600s > 180s
    r = dc.match_detections(shadow, legacy)
    assert not r["matched"]
    assert len(r["solo_legacy"]) == 1
    assert len(r["solo_unified"]) == 1


# --- E/F/G: solo legacy / solo unified / ambos, mezclados en un mismo día --

def test_E_F_G_solo_legacy_solo_unified_y_ambos_combinados():
    legacy = [
        _legacy_row(ticker="MATCH", detected_at="2026-08-26T14:30:00+00:00"),
        _legacy_row(ticker="SOLOLEG", detected_at="2026-08-26T15:00:00+00:00"),
    ]
    shadow = [
        _shadow_row(ticker="MATCH", detected_at="2026-08-26T14:31:00+00:00"),
        _shadow_row(ticker="SOLOUNI", detected_at="2026-08-26T15:30:00+00:00"),
    ]
    r = dc.match_detections(shadow, legacy)
    assert {m["ticker"] for m in r["matched"]} == {"MATCH"}
    assert {x["ticker"] for x in r["solo_legacy"]} == {"SOLOLEG"}
    assert {x["ticker"] for x in r["solo_unified"]} == {"SOLOUNI"}


# --- H: outcome asociado correctamente ---------------------------------------

def test_H_outcome_asociado_correctamente():
    _fresh()
    orig_get_outcome = reg.get_outcome
    try:
        outcomes = {"MATCH": {"reached_20": 1, "reached_50": 0, "max_return_after_detection_pct": 23.5},
                    "SOLOLEG": {"reached_20": 0, "reached_50": 0, "max_return_after_detection_pct": 4.2}}
        reg.get_outcome = lambda ticker, market_date: outcomes.get(ticker)

        legacy = [_legacy_row(ticker="MATCH", detected_at="2026-08-26T14:30:00+00:00"),
                  _legacy_row(ticker="SOLOLEG", detected_at="2026-08-26T15:00:00+00:00")]
        shadow = [_shadow_row(ticker="MATCH", detected_at="2026-08-26T14:31:00+00:00"),
                  _shadow_row(ticker="SOLOUNI", detected_at="2026-08-26T15:30:00+00:00")]
        matched_r = dc.match_detections(shadow, legacy)
        dc._attach_outcomes(matched_r["matched"], matched_r["solo_legacy"], matched_r["solo_unified"], "2026-08-26")

        assert matched_r["matched"][0]["outcome"]["reached_20"] == 1
        assert matched_r["solo_legacy"][0]["outcome"]["max_return_after_detection_pct"] == 4.2
        # SOLO_UNIFIED nunca recibe un outcome inventado -- queda marcado explícito.
        assert matched_r["solo_unified"][0]["outcome"] is None
        assert matched_r["solo_unified"][0]["outcome_status"] == "SIN_EVALUADOR_INDEPENDIENTE"
    finally:
        reg.get_outcome = orig_get_outcome
        _restore()


# --- I/J: no modifica candidate_detection ni candidate_outcome reales ------

def test_I_J_nunca_escribe_en_tablas_reales():
    _fresh()
    orig_list_shadow = sreg.list_shadow_detections
    orig_list_legacy = reg.list_candidates_for_date
    orig_get_outcome = reg.get_outcome
    try:
        sreg.list_shadow_detections = lambda market_date: [_shadow_row()]
        reg.list_candidates_for_date = lambda market_date: [_legacy_row()]
        reg.get_outcome = lambda ticker, market_date: None

        dc.compare_legacy_vs_unified("2026-08-26")

        # Nada se escribió en ninguna base real -- ambas siguen en cero.
        assert reg.count_candidates_for_date("2026-08-26") == 0
        assert sreg.count_shadow_detections("2026-08-26") == 0
    finally:
        sreg.list_shadow_detections = orig_list_shadow
        reg.list_candidates_for_date = orig_list_legacy
        reg.get_outcome = orig_get_outcome
        _restore()


# --- K: no importa gates/scoring/Atlas Decision Core ------------------------

def test_K_no_importa_gates_scoring_ni_decision_core():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(dc))
    modulos = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modulos.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modulos.add(node.module)
            modulos.update(a.name for a in node.names)

    prohibidos = ("candidate_gates", "candidate_tracker", "decision_engine", "priority_classifier",
                  "explosive_engine", "atlas_decision_core", "catalyst_score", "historical_scoring")
    for m in modulos:
        for p in prohibidos:
            assert p not in m, f"import prohibido: {m}"


# --- L: sin llamadas de red nuevas (solo lectura de DB/estructuras) --------

def test_L_sin_llamadas_de_red():
    import inspect

    src = inspect.getsource(dc)
    for prohibido in ("fetch_universe_quotes", "requests.", "urllib", "yfinance", "get_quote("):
        assert prohibido not in src


# --- M: aislamiento de la DB shadow ------------------------------------------

def test_M_aislamiento_db_shadow():
    _fresh()
    try:
        assert sreg.DB_PATH != _ORIG_SHADOW_DB
        assert reg.DB_PATH != _ORIG_REG_DB
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
