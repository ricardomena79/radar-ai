"""Primera prueba funcional del Memory Engine -- demo retroactiva (Entregable Nº6, parte 1).

Genera el ranking completo pedido para la primera prueba de Atlas, sobre
UN día histórico real ya cargado en el Memory Store (por defecto
2026-07-30, que tiene dos detecciones reales confirmadas: NUWE +135.4% y
XRX +32.2%). El resultado de ese día YA se conoce -- esto es una prueba
del MECANISMO de punta a punta con evidencia real, no un pronóstico
(para eso hace falta conectar al escaneo en vivo, etapa siguiente y
separada, sin mezclar -- ver MEMORY_ENGINE.md).

Combina dos fuentes, cada una de solo lectura, sin modificar nada:
  - El archivo histórico original del día (`results_v1/{fecha}.json`) --
    de ahí sale el Score real de Radar Explosivo (Etapa B), que el Memory
    Store NO guarda (guarda categoría y métricas crudas, no el score).
  - Las propuestas confiables del Entregable 5 (`calibration_advisor`) --
    de ahí sale la Probabilidad, la Confianza, el Semáforo y la Evidencia
    histórica de cada símbolo.

Reglas de presentación, documentadas explícitamente (no hay "supuestos"
ocultos):
  - market_cap_bucket se deriva en el momento, sin tocar el Memory Store,
    con los umbrales que Radar Explosivo YA tiene aprobados en
    `explosive_config.json` (small_cap_reference=$300M, large_cap_ceiling=
    $10B) -- no son números nuevos inventados para esta demo.
  - Probabilidad = win_rate de la propuesta confiable con mayor límite
    inferior de Wilson que matchea al símbolo (la evidencia más sólida
    entre las que aplican). Si ninguna propuesta confiable matchea, el
    símbolo se marca explícitamente "sin evidencia histórica suficiente"
    -- nunca se le asigna una probabilidad de 0% ni se inventa un número.
  - Nivel de confianza: Alta (muestra >= 500), Media (100-499), Baja
    (10-99) -- todas las propuestas de la grilla del Entregable 5 ya
    tienen muestra >= 10 por construcción (esa es la condición 1 de
    confiabilidad); "Sin evidencia" si no matchea ninguna.
  - Semáforo: 🟢 si la mejor evidencia que matchea tiene una tasa >= 10x
    el baseline poblacional; 🟡 si matchea pero con lift menor; 🔴 si no
    matchea ninguna propuesta confiable (no hay evidencia histórica que
    respalde una probabilidad elevada para ese símbolo hoy).

Ranking Score de desempate (aprobado el 2026-08-02, ver
`atlas_live/memory/ranking_score.py` y MEMORY_ENGINE.md): cuando varios
candidatos matchean la misma condición ganadora (Nivel 1, sin cambios),
el orden entre ellos ya NO es alfabético -- usa 3 niveles adicionales de
evidencia ya existente (condiciones adicionales matcheadas, percentil
dentro de la banda, score real de Radar Explosivo). Este mecanismo no
reemplaza a Radar Explosivo ni modifica la detección -- solo decide el
orden entre candidatos que ya tenían la misma Probabilidad reportada.

Uso: `python -m atlas_live.memory.demo_ranking --date 2026-07-30`
"""

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from atlas_live.memory import base_rates as br
from atlas_live.memory import calibration_advisor as ca
from atlas_live.memory import ranking_score as rs

MICRO_CAP_CEILING = 300_000_000       # explosive_config.json -> size_factor.small_cap_reference
LARGE_CAP_CEILING = 10_000_000_000    # explosive_config.json -> gates.large_cap_ceiling

CONFIDENCE_HIGH_MIN_SAMPLE = 500
CONFIDENCE_MEDIUM_MIN_SAMPLE = 100

SEMAFORO_VERDE_LIFT_MIN = 10.0


def _market_cap_bucket(market_cap: Optional[float]) -> str:
    if market_cap is None:
        return "desconocido"
    if market_cap < MICRO_CAP_CEILING:
        return "micro"
    if market_cap < LARGE_CAP_CEILING:
        return "mid"
    return "large"


def _confidence_level(sample_size: int) -> str:
    if sample_size >= CONFIDENCE_HIGH_MIN_SAMPLE:
        return "Alta"
    if sample_size >= CONFIDENCE_MEDIUM_MIN_SAMPLE:
        return "Media"
    return "Baja"


@dataclass
class RankedCandidate:
    symbol: str
    score: Optional[float]
    eligible_radar: bool
    market_cap_bucket: str
    price: Optional[float]
    change_pct: Optional[float]
    # Trazabilidad de precio (2026-08-02) -- pasante desde `metrics`, igual
    # que `price`/`change_pct` de arriba: no participa en el cálculo del
    # Ranking Score, solo se muestra en la Cabina del Piloto.
    price_type: str
    price_source: str
    market_state: Optional[str]
    price_regular: Optional[float]
    price_premarket: Optional[float]
    price_afterhours: Optional[float]
    price_as_of: Optional[str]
    probability_pct: Optional[float]
    confidence: str
    semaforo: str
    explanation: str
    evidence_condition: Optional[str]
    evidence_sample_size: int
    evidence_wilson_lower_bound_pct: Optional[float]
    evidence_baseline_pct: float
    ranking_score: rs.RankingScore
    tie_break_note: Optional[str]
    sort_key: float  # se mantiene por compatibilidad de lectura del Nivel 1; el orden real usa ranking_score


def build_ranked_candidate(
    row: Dict[str, Any],
    proposals: List[ca.CalibrationProposal],
    condition_value_cache: Dict[str, Dict[str, List[float]]],
    baseline: float,
    category: str = "EXPLOSION",
) -> RankedCandidate:
    """Arma UN candidato rankeado a partir de una fila con forma
    `row['explosive']['metrics'/'score'/'eligible']` -- el mismo formato
    que usan tanto los archivos históricos (`results_v1/*.json`) como el
    ranking en vivo que ya calcula `scan_worker.py` en cada ciclo. Extraído
    de `build_ranking()` para que la integración en tiempo real
    (`live_integration.py`) reutilice exactamente esta misma lógica, sin
    duplicarla ni tocar el Radar Explosivo."""
    metrics = row["explosive"]["metrics"]
    obs = dict(metrics)  # mismas claves que Condition.matches espera (relative_volume, gap_pct, etc.)

    score, best = rs.compute_ranking_score(
        obs, proposals, condition_value_cache,
        radar_score=row["explosive"]["score"],
        radar_eligible=bool(row["explosive"]["eligible"]),
    )

    if best is None:
        probability_pct = None
        confidence = "Sin evidencia"
        semaforo = "🔴"
        explanation = (
            "Ninguna condición con evidencia histórica confiable (de las 10 generadas en el "
            "Entregable 5) coincide con las métricas de hoy de este símbolo -- no hay base para "
            "asignarle una probabilidad elevada."
        )
        evidence_condition = None
        evidence_sample_size = 0
        evidence_wlb_pct = None
        tie_break_note = None
    else:
        e = best.evidence
        probability_pct = e.win_rate * 100 if e.win_rate is not None else None
        confidence = _confidence_level(e.sample_size)
        lift = (e.win_rate / e.baseline_win_rate) if e.baseline_win_rate > 0 else float("inf")
        semaforo = "🟢" if lift >= SEMAFORO_VERDE_LIFT_MIN else "🟡"
        explanation = (
            f"Coincide con la condición '{best.condition_label}', que en los 30 días validados "
            f"(2026-06-18 a 2026-07-31) tuvo una tasa de {category} de {probability_pct:.2f}% "
            f"({lift:.1f}x el promedio general de {baseline*100:.2f}%), con {e.sample_size} "
            f"observaciones de respaldo y un límite inferior de Wilson de {e.wilson_lower_bound*100:.2f}% "
            f"(el peor caso estadísticamente razonable, no solo el promedio)."
        )
        evidence_condition = best.condition_label
        evidence_sample_size = e.sample_size
        evidence_wlb_pct = e.wilson_lower_bound * 100 if e.wilson_lower_bound is not None else None
        tie_break_note = (
            f"Desempate (Ranking Score): {score.nivel2_condiciones_adicionales} condición(es) "
            f"confiable(s) adicional(es) matcheada(s); percentil {score.nivel3_percentil_dentro_de_banda*100:.0f} "
            f"dentro de la banda de '{best.condition_label}'; "
            + (f"score real de Radar Explosivo={score.nivel4_score_radar:.1f}."
               if score.nivel4_score_radar else "sin score real de Radar Explosivo (no elegible).")
        )

    return RankedCandidate(
        symbol=row["symbol"],
        score=row["explosive"]["score"],
        eligible_radar=bool(row["explosive"]["eligible"]),
        market_cap_bucket=_market_cap_bucket(metrics.get("market_cap")),
        price=metrics.get("price"),
        change_pct=metrics.get("change_pct"),
        price_type=metrics.get("price_type", "unknown"),
        price_source=metrics.get("source", "yahoo_finance"),
        market_state=metrics.get("market_state"),
        price_regular=metrics.get("price_regular"),
        price_premarket=metrics.get("price_premarket"),
        price_afterhours=metrics.get("price_afterhours"),
        price_as_of=metrics.get("price_as_of"),
        probability_pct=probability_pct,
        confidence=confidence,
        semaforo=semaforo,
        explanation=explanation,
        evidence_condition=evidence_condition,
        evidence_sample_size=evidence_sample_size,
        evidence_wilson_lower_bound_pct=evidence_wlb_pct,
        evidence_baseline_pct=baseline * 100,
        ranking_score=score,
        tie_break_note=tie_break_note,
        sort_key=score.nivel1_wilson_lower_bound,
    )


def build_ranking(date: str, source_path: str, category: str = "EXPLOSION") -> List[RankedCandidate]:
    with open(source_path, "r", encoding="utf-8") as f:
        scan = json.load(f)
    if scan["target_date"] != date:
        raise ValueError(f"El archivo {source_path} es de {scan['target_date']!r}, no de {date!r}")

    observations = br.load_all_observations()
    baseline = br.compute_population_base_rate(observations, category)
    proposals = ca.generate_proposals(observations, category=category)
    condition_value_cache = rs.build_condition_value_cache(observations, proposals)

    ranked = [
        build_ranked_candidate(row, proposals, condition_value_cache, baseline, category)
        for row in scan["rows"]
    ]

    # Nivel 1 sin cambios (probabilidad/orden general); Niveles 2-4 solo
    # desempatan dentro de candidatos con el mismo Nivel 1 -- comparación
    # lexicográfica de la tupla RankingScore, no una suma con pesos.
    ranked.sort(key=lambda c: c.ranking_score, reverse=True)
    return ranked


def _print_candidate(rank: int, c: RankedCandidate) -> None:
    score_str = f"{c.score:.1f}" if c.score is not None else "N/A (fuera del gate de Radar Explosivo)"
    prob_str = f"{c.probability_pct:.2f}%" if c.probability_pct is not None else "sin evidencia suficiente"
    print(f"#{rank:<3} {c.symbol:<8} bucket={c.market_cap_bucket:<10} score={score_str:<30} "
          f"prob={prob_str:<12} confianza={c.confidence:<10} {c.semaforo}")
    print(f"      Explicación: {c.explanation}")
    if c.evidence_condition:
        print(f"      Evidencia histórica: condición='{c.evidence_condition}', muestra={c.evidence_sample_size}, "
              f"límite inferior de Wilson={c.evidence_wilson_lower_bound_pct:.2f}%, "
              f"baseline poblacional={c.evidence_baseline_pct:.2f}%")
    if c.tie_break_note:
        print(f"      {c.tie_break_note}")
    print()


if __name__ == "__main__":
    # La consola de Windows (cp1252) no puede imprimir el semáforo con
    # emoji sin forzar UTF-8 -- no afecta el cálculo, solo la impresión.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="2026-07-30")
    parser.add_argument("--source", default="atlas_live/backtest/results_v1/{date}.json")
    args = parser.parse_args()
    source_path = args.source.format(date=args.date)

    ranking = build_ranking(args.date, source_path)

    print("=" * 90)
    print(f"MEMORY ENGINE -- PRIMERA PRUEBA FUNCIONAL (demo retroactiva, {args.date})")
    print("=" * 90)
    print()
    print("--- TOP 10 ACCIONES ---")
    print()
    for i, c in enumerate(ranking[:10], start=1):
        _print_candidate(i, c)

    microcaps = [c for c in ranking if c.market_cap_bucket == "micro"]
    print("--- TOP 10 MICROCAPS (market_cap < $300M) ---")
    print()
    for i, c in enumerate(microcaps[:10], start=1):
        _print_candidate(i, c)
