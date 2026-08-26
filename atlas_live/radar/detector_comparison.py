"""Comparación LEGACY vs UNIFIED (2026-08-26, U3-C3, autorizado
explícitamente). Puramente de LECTURA -- nunca escribe en
`candidate_detection`, `candidate_outcome`, `shadow_candidate_detection`, ni
en ninguna tabla de conocimiento/aprendizaje. Nunca importa
`candidate_gates.py`/`candidate_tracker.py`/`decision_engine.py`/
`priority_classifier.py`/`explosive_engine.py` -- esta auditoría LEE lo que
esos módulos ya produjeron, nunca los llama de nuevo ni reimplementa su
lógica.

VENTANA DE MATCHING TEMPORAL -- criterio explícito, no inventado (pedido
explícito: "primero revisa los timestamps reales disponibles... y proponé
el criterio"): se deriva de las dos únicas cadencias reales que ya existen
en el sistema, sumadas para cubrir el peor caso de AMBOS lados
independientemente:
  - `radar_worker.SWEEP_CEILING_SECONDS` (120s) -- el barrido LEGACY puede
    estar hasta ese tiempo "atrasado" respecto al movimiento real de
    mercado, por su propia cadencia auto-ajustada.
  - `unified_detector.SHADOW_LOOP_INTERVAL_SECONDS` (60s) -- el chequeo
    UNIFIED puede estar hasta ese tiempo atrasado respecto al mismo
    movimiento, por su propio loop.
Dos detecciones del MISMO ticker cuyo `detected_at` difiere en menos de
esta suma (180s = 3 min) se consideran el mismo evento de mercado. Más
allá de esa ventana, se tratan como eventos DISTINTOS del mismo ticker
(pedido explícito: "mismo ticker pero eventos distintos no deben
mezclarse") -- el matching es por ticker + más cercano en el tiempo dentro
de la ventana, nunca por ticker solo.

LIMITACIÓN DECLARADA, NO RESUELTA ACÁ (pedido explícito: "si hace falta
infraestructura nueva, DETENTE"): para detecciones SOLO_UNIFIED que
ocurrieron en afterhours (la única cobertura genuinamente nueva de U3-C2),
`candidate_outcome` NUNCA tiene una fila real -- ese outcome solo lo
calcula `eod_report.py` para candidatas del pipeline LEGACY. Evaluar el
resultado de mercado de una detección SOLO_UNIFIED en afterhours
requeriría un evaluador de outcome INDEPENDIENTE (misma lógica de
`get_intraday_timesales()` que ya usa `eod_report.py`, pero implementado
aparte, sin tocarlo) -- esa pieza NO EXISTE todavía y NO se construye en
este módulo. `outcome_status="SIN_EVALUADOR_INDEPENDIENTE"` queda marcado
explícitamente en vez de inventar un resultado."""

from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from atlas_live.radar import candidate_registry as reg
from atlas_live.radar import radar_worker
from atlas_live.radar import shadow_detector_registry as sreg
from atlas_live.radar import unified_detector as ud

MATCH_WINDOW_SECONDS = radar_worker.SWEEP_CEILING_SECONDS + ud.SHADOW_LOOP_INTERVAL_SECONDS  # 120 + 60 = 180s


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _is_legacy_stale(legacy_row: Dict[str, Any]) -> Optional[bool]:
    basis = legacy_row.get("price_basis_at_detection")
    if basis is None:
        return None
    return basis == "tradier_regular_close_stale"


def match_detections(
    shadow_rows: List[Dict[str, Any]],
    legacy_rows: List[Dict[str, Any]],
    window_seconds: float = MATCH_WINDOW_SECONDS,
) -> Dict[str, List[Dict[str, Any]]]:
    """Empareja detecciones LEGACY (`candidate_detection`, campo
    `detected_at`) contra UNIFIED (`shadow_candidate_detection`, mismo
    campo), por ticker, más cercano en el tiempo dentro de `window_seconds`
    -- one-to-one (cada detección se usa como máximo una vez), para que
    eventos distintos del mismo ticker nunca se mezclen entre sí. Devuelve
    `{"matched": [...], "solo_legacy": [...], "solo_unified": [...]}`."""
    shadow_by_ticker: Dict[str, List[Dict[str, Any]]] = {}
    for r in shadow_rows:
        shadow_by_ticker.setdefault(r["ticker"], []).append(r)
    legacy_by_ticker: Dict[str, List[Dict[str, Any]]] = {}
    for r in legacy_rows:
        legacy_by_ticker.setdefault(r["ticker"], []).append(r)

    matched: List[Dict[str, Any]] = []
    solo_legacy: List[Dict[str, Any]] = []
    solo_unified: List[Dict[str, Any]] = []

    all_tickers = sorted(set(shadow_by_ticker) | set(legacy_by_ticker))
    for ticker in all_tickers:
        s_list = sorted(shadow_by_ticker.get(ticker, []), key=lambda r: r["detected_at"])
        l_list = sorted(legacy_by_ticker.get(ticker, []), key=lambda r: r["detected_at"])
        used_s: set = set()
        used_l: set = set()

        for li, l in enumerate(l_list):
            l_ts = _parse_iso(l["detected_at"])
            best_si: Optional[int] = None
            best_diff: Optional[float] = None
            for si, s in enumerate(s_list):
                if si in used_s:
                    continue
                s_ts = _parse_iso(s["detected_at"])
                diff = abs((s_ts - l_ts).total_seconds())
                if diff <= window_seconds and (best_diff is None or diff < best_diff):
                    best_diff = diff
                    best_si = si
            if best_si is not None:
                used_s.add(best_si)
                used_l.add(li)
                s = s_list[best_si]
                s_ts = _parse_iso(s["detected_at"])
                matched.append({
                    "ticker": ticker,
                    "legacy": l,
                    "unified": s,
                    # positivo = unified detectó DESPUÉS que legacy (legacy antes).
                    # negativo = unified detectó ANTES que legacy (unified antes).
                    "diff_seconds": (s_ts - l_ts).total_seconds(),
                })

        for li, l in enumerate(l_list):
            if li not in used_l:
                solo_legacy.append(l)
        for si, s in enumerate(s_list):
            if si not in used_s:
                solo_unified.append(s)

    return {"matched": matched, "solo_legacy": solo_legacy, "solo_unified": solo_unified}


def _attach_outcomes(matched: List[Dict[str, Any]], solo_legacy: List[Dict[str, Any]],
                      solo_unified: List[Dict[str, Any]], market_date: str) -> None:
    """Reutiliza `candidate_registry.get_outcome()` TAL CUAL -- nunca
    calcula un outcome nuevo, nunca toca `eod_report.py`. Para
    SOLO_UNIFIED, marca la limitación explícita en vez de inventar un
    resultado (ver docstring del módulo)."""
    for m in matched:
        m["outcome"] = reg.get_outcome(m["ticker"], market_date)
    for l in solo_legacy:
        l["outcome"] = reg.get_outcome(l["ticker"], market_date)
    for s in solo_unified:
        s["outcome"] = None
        s["outcome_status"] = "SIN_EVALUADOR_INDEPENDIENTE"


def compare_legacy_vs_unified(market_date: str) -> Dict[str, Any]:
    """Comparación de UN día -- solo lectura. Nunca escribe en ninguna
    tabla real ni shadow."""
    shadow_rows = sreg.list_shadow_detections(market_date)
    legacy_rows = reg.list_candidates_for_date(market_date)

    resultado = match_detections(shadow_rows, legacy_rows)
    matched, solo_legacy, solo_unified = resultado["matched"], resultado["solo_legacy"], resultado["solo_unified"]
    _attach_outcomes(matched, solo_legacy, solo_unified, market_date)

    unified_antes = [m for m in matched if m["diff_seconds"] < 0]
    legacy_antes = [m for m in matched if m["diff_seconds"] > 0]
    simultaneas = [m for m in matched if m["diff_seconds"] == 0]
    diffs_abs = [abs(m["diff_seconds"]) for m in matched]

    total_legacy = len(matched) + len(solo_legacy)
    total_unified = len(matched) + len(solo_unified)
    muestra_total = len(matched) + len(solo_legacy) + len(solo_unified)

    return {
        "market_date": market_date,
        "match_window_seconds": MATCH_WINDOW_SECONDS,
        "total_legacy": total_legacy,
        "total_unified": total_unified,
        "detectadas_por_ambos": len(matched),
        "solo_legacy": len(solo_legacy),
        "solo_unified": len(solo_unified),
        "unified_antes_que_legacy": len(unified_antes),
        "legacy_antes_que_unified": len(legacy_antes),
        "detecciones_simultaneas": len(simultaneas),
        "diferencia_tiempo_promedio_segundos": statistics.mean(diffs_abs) if diffs_abs else None,
        "diferencia_tiempo_mediana_segundos": statistics.median(diffs_abs) if diffs_abs else None,
        "muestra_total": muestra_total,
        "estado_validacion_muestra": reg.precision_validation_state(muestra_total),
        "matched": matched,
        "solo_legacy_detalle": solo_legacy,
        "solo_unified_detalle": solo_unified,
    }


def _reached_pct(outcome: Optional[Dict[str, Any]], key: str) -> Optional[bool]:
    if not outcome:
        return None
    v = outcome.get(key)
    return bool(v) if v is not None else None


def quality_report(market_dates: List[str]) -> Dict[str, Any]:
    """Agrega `compare_legacy_vs_unified()` sobre varios días -- nunca
    declara "ganador" solo por cantidad de detecciones (pedido explícito):
    reporta cobertura Y resultado de mercado por separado, dejando la
    interpretación final al informe, no a este módulo."""
    dias = [compare_legacy_vs_unified(d) for d in market_dates]

    total_matched = sum(d["detectadas_por_ambos"] for d in dias)
    total_solo_legacy = sum(d["solo_legacy"] for d in dias)
    total_solo_unified = sum(d["solo_unified"] for d in dias)
    total_legacy = total_matched + total_solo_legacy
    total_unified = total_matched + total_solo_unified
    muestra_total = total_matched + total_solo_legacy + total_solo_unified

    # Recall relativo: de todo lo que LEGACY detectó, qué fracción UNIFIED
    # también encontró (nunca al revés -- LEGACY sigue siendo el terreno
    # conocido/de referencia en esta fase).
    recall_relativo_unified = (total_matched / total_legacy) if total_legacy else None
    tasa_deteccion_compartida = (total_matched / muestra_total) if muestra_total else None

    # Resultado de mercado -- SOLO sobre filas con outcome real (matched +
    # solo_legacy; solo_unified queda excluido, ver limitación declarada).
    outcomes_evaluables = []
    for d in dias:
        for m in d["matched"]:
            if m["outcome"]:
                outcomes_evaluables.append(m["outcome"])
        for l in d["solo_legacy_detalle"]:
            if l["outcome"]:
                outcomes_evaluables.append(l["outcome"])

    n_con_outcome = len(outcomes_evaluables)
    pct_reached_20 = (
        sum(1 for o in outcomes_evaluables if o.get("reached_20")) / n_con_outcome * 100
        if n_con_outcome else None
    )
    pct_reached_50 = (
        sum(1 for o in outcomes_evaluables if o.get("reached_50")) / n_con_outcome * 100
        if n_con_outcome else None
    )
    magnitudes = [o.get("max_return_after_detection_pct") for o in outcomes_evaluables
                  if o.get("max_return_after_detection_pct") is not None]

    return {
        "market_dates": market_dates,
        "match_window_seconds": MATCH_WINDOW_SECONDS,
        "total_legacy": total_legacy,
        "total_unified": total_unified,
        "detectadas_por_ambos": total_matched,
        "solo_legacy": total_solo_legacy,
        "solo_unified": total_solo_unified,
        "recall_relativo_unified": recall_relativo_unified,
        "tasa_deteccion_compartida": tasa_deteccion_compartida,
        "muestra_total": muestra_total,
        "estado_validacion_muestra": reg.precision_validation_state(muestra_total),
        "outcome_n_evaluable": n_con_outcome,
        "outcome_pct_reached_20": pct_reached_20,
        "outcome_pct_reached_50": pct_reached_50,
        "outcome_magnitud_maxima_promedio": statistics.mean(magnitudes) if magnitudes else None,
        "outcome_magnitud_maxima_mediana": statistics.median(magnitudes) if magnitudes else None,
        "solo_unified_outcome_status": "SIN_EVALUADOR_INDEPENDIENTE" if total_solo_unified else None,
        "por_dia": dias,
    }
