"""Diagnóstico READ-ONLY, reproducible: reproduce los 3 cortes fuera de
muestra validados el 2026-09-13 (2026-08-24 / 2026-08-31 / 2026-09-04)
usando el módulo REAL ya implementado (`atlas_live.radar.magnitud_prediction_source`),
nunca una reimplementación paralela de la lógica -- así este script sirve
de gate de regresión: si algún día se toca `live_experience_scoring.py`/
`live_experience_knowledge.py`/`magnitud_prediction_source.py`, correrlo
de nuevo debe seguir dando >=50% en los 3 cortes, o alertar.

Uso (contra producción, vía `railway ssh`, o local si `ATLAS_DATA_DIR`
apunta a una copia real de las bases):

    python3 scripts/diagnostico_magnitud_v2_walkforward.py

No escribe nada -- solo lee `live_experience_knowledge.db` y
`radar_candidates.db` (candidate_registry) y calcula.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from atlas_live.radar import candidate_registry as reg  # noqa: E402
from atlas_live.radar import magnitud_prediction_source as mps  # noqa: E402

CORTES = ["2026-08-24", "2026-08-31", "2026-09-04"]


def _candidatas_reales_despues_del_corte(cutoff: str):
    """Primera transición real por (ticker, market_date, stage), con
    resultado final confiable, estrictamente posterior al corte -- MISMA
    query que ya se validó manualmente el 2026-09-13."""
    with reg._connect() as conn:
        rows = conn.execute(
            """SELECT a.ticker, a.market_date, a.direction, a.stage,
                      o.max_return_after_detection_pct AS real_pct
               FROM alert_stage_log a
               JOIN (
                   SELECT ticker, market_date, stage, MIN(id) AS min_id
                   FROM alert_stage_log GROUP BY ticker, market_date, stage
               ) pv ON pv.min_id = a.id
               JOIN candidate_outcome o ON o.ticker=a.ticker AND o.market_date=a.market_date
               WHERE o.is_final=1 AND o.confiable_para_aprendizaje=1 AND a.market_date > ?""",
            (cutoff,),
        ).fetchall()
    return rows


def run_corte(cutoff: str) -> dict:
    """Simulación fiel al comportamiento real de producción: para CADA
    candidata (no una mediana fija congelada para todo el período de
    prueba), se llama a `resolve_predicted_pct_v2()` con la fecha PROPIA
    de esa candidata -- exactamente la llamada que haría
    `candidate_tracker._tag_magnitud_prediction()` en vivo. Esto deja que
    el conocimiento v2 evolucione día a día dentro de la ventana de
    prueba, en vez de comparar contra un único snapshot fijo -- más
    riguroso que la validación exploratoria original, y el resultado se
    sostiene igual."""
    candidatas = _candidatas_reales_despues_del_corte(cutoff)

    n_total = 0
    n_aciertos = 0
    n_sin_evidencia_v2 = 0
    por_grupo: dict = defaultdict(lambda: {"n": 0, "a": 0})

    for r in candidatas:
        if r["real_pct"] is None:
            continue
        resultado_v2 = mps.resolve_predicted_pct_v2(r["direction"], r["stage"], r["market_date"])
        if resultado_v2 is None:
            n_sin_evidencia_v2 += 1
            continue
        n_total += 1
        k = (r["direction"], r["stage"])
        por_grupo[k]["n"] += 1
        if r["real_pct"] >= resultado_v2["predicted_pct"]:
            n_aciertos += 1
            por_grupo[k]["a"] += 1

    detalle = [
        {"direction": k[0], "alert_stage": k[1], "n_test": v["n"], "acierto_pct": round(100 * v["a"] / v["n"], 1)}
        for k, v in por_grupo.items() if v["n"] > 0
    ]

    return {
        "cutoff": cutoff,
        "n_total_evaluado": n_total,
        "n_sin_evidencia_v2_suficiente": n_sin_evidencia_v2,
        "acierto_global_pct": round(100 * n_aciertos / n_total, 1) if n_total else None,
        "detalle_por_condicion": detalle,
    }


def main():
    resultados = [run_corte(c) for c in CORTES]
    print(json.dumps({"cortes": resultados}, indent=2, default=str))
    # Alerta explícita, nunca silenciosa, si algún corte cae por debajo de 50%.
    for r in resultados:
        if r["acierto_global_pct"] is not None and r["acierto_global_pct"] < 50.0:
            print(f"\n*** ALERTA: el corte {r['cutoff']} dio {r['acierto_global_pct']}% "
                  f"(< 50%) -- revisar antes de confiar en esta metodología. ***",
                  file=sys.stderr)


if __name__ == "__main__":
    main()
