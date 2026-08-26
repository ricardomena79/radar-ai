"""Tests de `decision_composition.py` y de la UNIFICACIÓN real entre las
superficies backend (2026-08-26, U3-B). Cubre lo efectivamente implementado
en esta fase: `/api/radar-oportunidades` (server.py), `_score_symbol()`/
`get_symbol_detail()` (scan_worker.py) y el pass-through hacia
`/api/memory-ranking` (demo_ranking.py/live_integration.py) -- todos deben
producir, para la MISMA candidata, la MISMA decisión del mismo
`atlas_decision_core.decide()`."""

import dataclasses

from atlas_live.core import atlas_decision_core as core
from atlas_live.core import decision_composition as dcomp


def _tradier_row(ticker="NSSC", stage="INICIO", direction="ALCISTA", change_pct_confiable=True,
                  dinero_entra_sector=False):
    return {
        "ticker": ticker, "stage": stage, "direction": direction,
        "change_pct_confiable": change_pct_confiable, "dinero_entra_sector": dinero_entra_sector,
        "price_actual": 38.09,
    }


def _scan_row(symbol="NSSC", explosive_eligible=True, explosive_score=71.0):
    return {
        "symbol": symbol, "price": 38.09, "atlas_score": 80.0, "momentum_score": 65.0,
        "money_flow_score": 40.0,
        "explosive": {"eligible": explosive_eligible, "score": explosive_score, "excluded_reason": None},
    }


# --- A: misma candidata, misma decision entre pipeline Tradier y pipeline
#        Yahoo (cuando el ticker es una candidata real de Tradier hoy) -----

def test_A_misma_candidata_misma_decision_entre_pipelines():
    ticker_row = _tradier_row()
    scan_row = _scan_row()

    # Pipeline Tradier -- exactamente lo que hace /api/radar-oportunidades.
    decision_tradier = core.decide(
        dcomp.candidate_from_radar_row(ticker_row, "2026-08-26", core.pc.VALIDACION_OK),
        dcomp.features_from_radar_row(ticker_row),
        dcomp.scores_from_radar_row(ticker_row),
        dcomp.evidence_from_radar_row(ticker_row, None),
    )

    # Pipeline Yahoo -- exactamente lo que hace scan_worker._score_symbol()
    # cuando el símbolo TAMBIÉN es una candidata real de Tradier hoy.
    decision_scan = core.decide(
        dcomp.candidate_from_scan_row(scan_row, "2026-08-26"),
        dcomp.features_from_scan_row(scan_row, ticker_row),
        dcomp.scores_from_scan_row(scan_row),
        dcomp.evidence_from_scan_row(scan_row),
    )

    assert decision_tradier.decision == decision_scan.decision == "OPORTUNIDAD_PRIORITARIA"


# --- N: una misma candidata conserva una unica decision entre superficies,
#        incluyendo cuando el pipeline Yahoo escanea un simbolo que Tradier
#        NUNCA detecto -- ahi la unica respuesta honesta es NO_TOCAR ------

def test_N_simbolo_sin_deteccion_tradier_resuelve_no_tocar():
    scan_row = _scan_row(symbol="ZZZZ")
    decision = core.decide(
        dcomp.candidate_from_scan_row(scan_row, "2026-08-26"),
        dcomp.features_from_scan_row(scan_row, tradier_row=None),
        dcomp.scores_from_scan_row(scan_row),
        dcomp.evidence_from_scan_row(scan_row),
    )
    # Sin stage real (Tradier nunca lo detecto), la decision canonica es
    # NO_TOCAR -- pese a que explosive_eligible=True y los scores sean
    # altos. eligible/score siguen viajando como FEATURE/SCORE, visibles
    # en el snapshot, pero no determinan la decision.
    assert decision.decision == "NO_TOCAR"
    assert decision.features_snapshot["explosive_eligible"] is True
    assert decision.scores_snapshot["atlas_score"] == 80.0


# --- O: no se inventa un valor cuando falta un dato -----------------------

def test_O_no_inventa_valores_faltantes():
    scan_row = {"symbol": "XYZ", "price": None, "explosive": {}}
    features = dcomp.features_from_scan_row(scan_row, tradier_row=None)
    scores = dcomp.scores_from_scan_row(scan_row)
    evidence = dcomp.evidence_from_scan_row(scan_row)

    assert features.stage is None and features.direction is None and features.change_pct_confiable is None
    assert features.explosive_eligible is None  # explosive vacio -- nunca False inventado
    assert scores.atlas_score is None and scores.momentum_score is None and scores.money_flow_score is None
    assert evidence.historical_evidence is None and evidence.memory_engine_semaforo is None

    radar_row = {"ticker": "XYZ"}  # sin price_actual
    candidate = dcomp.candidate_from_radar_row(radar_row, "2026-08-26", core.pc.VALIDACION_OK)
    assert candidate.tiene_precio_actual is False


# --- P: los motores de calculo existentes (RankedCandidate/serialize) -----
#        siguen funcionando con el campo nuevo, sin romper su forma -------

def test_P_ranked_candidate_acepta_atlas_decision_pass_through():
    from atlas_live.memory import demo_ranking as dr

    # Construccion SIN atlas_decision (compatibilidad hacia atras -- codigo
    # viejo/tests viejos que construyan RankedCandidate directo sin el
    # campo nuevo no deben romperse, ya que tiene default None).
    campos_obligatorios = {f.name: None for f in dataclasses.fields(dr.RankedCandidate) if f.default is dataclasses.MISSING}
    campos_obligatorios.update({
        "symbol": "NSSC", "eligible_radar": True, "market_cap_bucket": "micro",
        "price_type": "regular", "price_source": "tradier", "confidence": "Alta",
        "semaforo": "🟢", "explanation": "x", "evidence_sample_size": 10,
        "evidence_baseline_pct": 20.0, "sort_key": 0.5,
    })
    import atlas_live.memory.ranking_score as rs
    campos_obligatorios["ranking_score"] = rs.RankingScore(
        nivel1_wilson_lower_bound=0.1, nivel2_condiciones_adicionales=0, nivel3_percentil_dentro_de_banda=0.5,
        nivel4_score_radar=71.0,
    )
    rc = dr.RankedCandidate(**campos_obligatorios)
    assert rc.atlas_decision is None  # default seguro, sin romper nada


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
