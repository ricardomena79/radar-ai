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


def _merge_conteo_dict(total: Dict[str, int], nuevo: Dict[str, int]) -> None:
    """Suma in-place un dict `{clave: conteo}` de UN día dentro del
    acumulador total -- mismo patrón de merge en cada llamada, para no
    repetirlo 2 veces (estado PM-percentile / estado PM-acceleration)."""
    for k, v in nuevo.items():
        total[k] = total.get(k, 0) + v


def quality_report_aggregated(market_dates: List[str]) -> Dict[str, Any]:
    """Versión de `quality_report()` que procesa UN `market_date` a la vez
    y NUNCA retiene en memoria el detalle completo (`matched`/
    `solo_legacy_detalle`/`solo_unified_detalle`, con los snapshots JSON de
    `shadow_candidate_detection` adentro) de más de un día -- 2026-09-02,
    autorizado explícitamente, tras un intento real contra producción que
    devolvió "upstream error" al intentar transportar el detalle completo
    de todo el período por HTTP. `compare_legacy_vs_unified()` NO se toca
    -- el matching de 180s, la clasificación Legacy/Unified y el cálculo de
    cada métrica base son EXACTAMENTE los mismos; lo único que cambia es
    CUÁNDO se extraen los números chicos que hacen falta y CUÁNDO se
    descarta el resto.

    Cubre las 10 métricas originales de U3-C3:
      1-4 (conteos Legacy/Unified/comunes/solo-Unified), 5 (quién detectó
      primero), 6-8 (hit +20/+50/+100%), 9 (magnitud máxima) -- todas con
      datos YA disponibles en `candidate_detection`/`candidate_outcome`/
      `shadow_candidate_detection`, agregadas incrementalmente, MISMA
      definición matemática que `quality_report()` (6/7/9 literalmente
      copiadas; 5/8 son extensiones del mismo patrón, ver el mensaje que
      autorizó este cambio).
      10 (tiempo al objetivo) -- `candidate_outcome.minutes_to_max`, solo
      poblado por `eod_report.py` (el cálculo real con velas de 1 min de
      Tradier); `None` en outcomes "en curso" -- mismo tratamiento que
      `magnitudes`, nunca se inventa un valor.
      PM-RVOL -- ver investigación en el docstring del bloque de abajo:
      solo existe para el lado LEGACY, en sesión premarket. Nunca se
      calcula para Unified (no existe esa lógica en `unified_detector.py`
      hoy, y esta función no la crea).
      Conocimiento vs. baseline -- reutiliza `candidate_registry.shadow_validation_report()`
      TAL CUAL (ya existente, ya de solo lectura, ya usado por
      `/api/admin/shadow-validation-report`) -- nunca recalcula
      `decision_shadow`, nunca toca `atlas_decision_core.py`,
      `apply_recalibration` sigue en `False`."""
    total_matched = total_solo_legacy = total_solo_unified = 0
    total_unified_antes = total_legacy_antes = total_simultaneas = 0
    diffs_abs: List[float] = []
    outcomes_evaluables: List[Dict[str, Any]] = []
    dias_procesados = 0
    dias_con_error: List[Dict[str, str]] = []

    # PM-RVOL -- investigado antes de implementar (2026-09-02): calculado
    # HOY solo para el lado LEGACY (`premarket_volume_percentile_at_detection`/
    # `premarket_volume_acceleration_at_detection`, columnas reales de
    # `candidate_detection`, pobladas por `candidate_tracker.py`), y SOLO
    # para detecciones en sesión "premarket" -- `NOT_PREMARKET` en
    # cualquier otra sesión, por diseño de `candidate_gates.premarket_volume_percentile/
    # _acceleration()`, no un dato faltante. `unified_detector.py` NUNCA
    # llama a esas 2 funciones ni las persiste en `shadow_candidate_detection`
    # -- confirmado leyendo el archivo completo, cero referencias. Por eso
    # el rol del PM-RVOL se reporta EXCLUSIVAMENTE sobre la población
    # LEGACY (matched + solo_legacy) -- nunca se inventa un valor para
    # Unified, se declara `unified_coverage` explícito en el resultado.
    pm_percentile_por_estado: Dict[str, int] = {}
    pm_acceleration_por_estado: Dict[str, int] = {}
    pm_percentiles_validos: List[float] = []
    pm_acceleraciones_validas: List[float] = []
    pm_percentile_por_reached20: Dict[str, List[float]] = {"alcanzo_20": [], "no_alcanzo_20": []}

    # Conocimiento vs. baseline -- investigado antes de implementar
    # (2026-09-02): "baseline" = la decisión REAL (`decision`, de
    # `priority_classifier.classify_final_priority()`); "conocimiento
    # aprendido" = `decision_shadow` (LEK, `atlas_decision_core.decide()`
    # con `learned_evidence`); ambas ya se calculan en cada request real de
    # `/api/radar-oportunidades`, y CUANDO DIVERGEN
    # (`shadow_differs=True`) quedan persistidas en `shadow_decision_log`
    # (Fase 2 de la transición SHADOW->VALIDACIÓN, 2026-08-27) -- write-once
    # por (ticker, market_date), tabla chica por diseño (solo eventos de
    # divergencia, no una fila por candidata). `shadow_validation_report()`
    # (`candidate_registry.py`, ya existente, ya de solo lectura, ya usado
    # por `/api/admin/shadow-validation-report`) cruza esos eventos contra
    # `candidate_outcome` YA CERRADO para clasificar cada downgrade de LEK
    # como correcto/incorrecto/ambiguo -- se reutiliza TAL CUAL, nunca se
    # recalcula `decision_shadow` ni se activa `apply_recalibration`.
    sv_total_eventos = sv_con_outcome = sv_pendientes = 0
    sv_downgrade_correcto = sv_downgrade_incorrecto = sv_ambiguos = 0

    for market_date in market_dates:
        try:
            dia = compare_legacy_vs_unified(market_date)
        except Exception as exc:
            dias_con_error.append({"market_date": market_date, "error": f"{type(exc).__name__}: {exc}"})
            continue

        total_matched += dia["detectadas_por_ambos"]
        total_solo_legacy += dia["solo_legacy"]
        total_solo_unified += dia["solo_unified"]
        total_unified_antes += dia["unified_antes_que_legacy"]
        total_legacy_antes += dia["legacy_antes_que_unified"]
        total_simultaneas += dia["detecciones_simultaneas"]

        legacy_rows_del_dia: List[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]] = []
        for m in dia["matched"]:
            diffs_abs.append(abs(m["diff_seconds"]))
            if m["outcome"]:
                outcomes_evaluables.append(m["outcome"])
            legacy_rows_del_dia.append((m["legacy"], m["outcome"]))
        for l in dia["solo_legacy_detalle"]:
            if l["outcome"]:
                outcomes_evaluables.append(l["outcome"])
            legacy_rows_del_dia.append((l, l["outcome"]))

        for legacy_row, outcome in legacy_rows_del_dia:
            estado_pct = legacy_row.get("premarket_volume_percentile_state_at_detection")
            if estado_pct:
                pm_percentile_por_estado[estado_pct] = pm_percentile_por_estado.get(estado_pct, 0) + 1
                if estado_pct == "VALID":
                    val = legacy_row.get("premarket_volume_percentile_at_detection")
                    if val is not None:
                        pm_percentiles_validos.append(val)
                        if outcome and outcome.get("reached_20") is not None:
                            bucket = "alcanzo_20" if outcome.get("reached_20") else "no_alcanzo_20"
                            pm_percentile_por_reached20[bucket].append(val)

            estado_acc = legacy_row.get("premarket_volume_acceleration_state_at_detection")
            if estado_acc:
                pm_acceleration_por_estado[estado_acc] = pm_acceleration_por_estado.get(estado_acc, 0) + 1
                if estado_acc == "VALID":
                    val = legacy_row.get("premarket_volume_acceleration_at_detection")
                    if val is not None:
                        pm_acceleraciones_validas.append(val)

        sv = reg.shadow_validation_report(market_date)
        sv_total_eventos += sv["total_eventos_shadow_differs"]
        sv_con_outcome += sv["con_outcome_final"]
        sv_pendientes += sv["pendientes"]
        sv_downgrade_correcto += sv["downgrade_correcto"]
        sv_downgrade_incorrecto += sv["downgrade_incorrecto"]
        sv_ambiguos += sv["ambiguos"]

        dias_procesados += 1
        del dia, legacy_rows_del_dia  # libera el detalle completo de ESTE día antes del próximo

    total_legacy = total_matched + total_solo_legacy
    total_unified = total_matched + total_solo_unified
    muestra_total = total_matched + total_solo_legacy + total_solo_unified
    recall_relativo_unified = (total_matched / total_legacy) if total_legacy else None
    tasa_deteccion_compartida = (total_matched / muestra_total) if muestra_total else None

    n_con_outcome = len(outcomes_evaluables)
    pct_reached_20 = (
        sum(1 for o in outcomes_evaluables if o.get("reached_20")) / n_con_outcome * 100
        if n_con_outcome else None
    )
    pct_reached_50 = (
        sum(1 for o in outcomes_evaluables if o.get("reached_50")) / n_con_outcome * 100
        if n_con_outcome else None
    )
    pct_reached_100 = (
        sum(1 for o in outcomes_evaluables if o.get("reached_100")) / n_con_outcome * 100
        if n_con_outcome else None
    )
    magnitudes = [o.get("max_return_after_detection_pct") for o in outcomes_evaluables
                  if o.get("max_return_after_detection_pct") is not None]
    tiempos_a_objetivo = [o.get("minutes_to_max") for o in outcomes_evaluables
                           if o.get("minutes_to_max") is not None]

    pm_rvol_disponible = bool(pm_percentiles_validos) or bool(pm_acceleraciones_validas)
    pm_rvol = {
        "unavailable": not pm_rvol_disponible,
        "reason": (
            None if pm_rvol_disponible else
            "sin detecciones LEGACY con PM-RVOL VALID en el rango -- puede ser que "
            "ninguna detección haya ocurrido en sesión premarket, o que no haya "
            "alcanzado el piso de universo/historial que exige candidate_gates.py"
        ),
        "unified_coverage": (
            "no disponible -- unified_detector.py no calcula ni persiste PM-RVOL hoy"
        ),
        "percentile_conteo_por_estado": pm_percentile_por_estado,
        "percentile_promedio": statistics.mean(pm_percentiles_validos) if pm_percentiles_validos else None,
        "percentile_mediana": statistics.median(pm_percentiles_validos) if pm_percentiles_validos else None,
        "percentile_promedio_alcanzo_20": (
            statistics.mean(pm_percentile_por_reached20["alcanzo_20"])
            if pm_percentile_por_reached20["alcanzo_20"] else None
        ),
        "percentile_promedio_no_alcanzo_20": (
            statistics.mean(pm_percentile_por_reached20["no_alcanzo_20"])
            if pm_percentile_por_reached20["no_alcanzo_20"] else None
        ),
        "acceleration_conteo_por_estado": pm_acceleration_por_estado,
        "acceleration_promedio": statistics.mean(pm_acceleraciones_validas) if pm_acceleraciones_validas else None,
        "acceleration_mediana": statistics.median(pm_acceleraciones_validas) if pm_acceleraciones_validas else None,
    }

    sv_n_evaluables_tasa = sv_downgrade_correcto + sv_downgrade_incorrecto
    sv_tasa_acierto_pct = (
        round(100 * sv_downgrade_correcto / sv_n_evaluables_tasa, 1) if sv_n_evaluables_tasa else None
    )
    sv_wilson_ci = (
        reg.wilson_confidence_interval(sv_downgrade_correcto, sv_n_evaluables_tasa)
        if sv_n_evaluables_tasa else None
    )
    conocimiento_vs_baseline = {
        "unavailable": sv_total_eventos == 0,
        "reason": (
            None if sv_total_eventos > 0 else
            "sin eventos de shadow_differs=True registrados en el rango -- LEK "
            "(decision_shadow) coincidió siempre con la decisión real, o no hubo "
            "candidatas evaluadas por atlas_decision_core.decide() en este período"
        ),
        "total_eventos_shadow_differs": sv_total_eventos,
        "con_outcome_final": sv_con_outcome,
        "pendientes": sv_pendientes,
        "downgrade_correcto": sv_downgrade_correcto,
        "downgrade_incorrecto": sv_downgrade_incorrecto,
        "ambiguos": sv_ambiguos,
        "n_evaluables_tasa": sv_n_evaluables_tasa,
        "tasa_acierto_pct": sv_tasa_acierto_pct,
        "wilson_ci": list(sv_wilson_ci) if sv_wilson_ci else None,
        "nota_metodologica": (
            "downgrade_correcto = LEK habria sido mas cauteloso y el resultado real "
            "le dio la razon (candidate_outcome.category == 'falsa_senal'); "
            "downgrade_incorrecto = la candidata SI era buena "
            "(mejor_oportunidad/buena_oportunidad) y LEK la habria rebajado sin "
            "motivo real. apply_recalibration sigue en False -- este numero es "
            "puramente observacional, nunca cambio ninguna decision real."
        ),
    }

    return {
        "market_dates": market_dates,
        "dias_procesados": dias_procesados,
        "dias_con_error": dias_con_error,
        "match_window_seconds": MATCH_WINDOW_SECONDS,
        "total_legacy": total_legacy,
        "total_unified": total_unified,
        "detectadas_por_ambos": total_matched,
        "solo_legacy": total_solo_legacy,
        "solo_unified": total_solo_unified,
        "quien_detecto_primero": {
            "unified_antes_que_legacy": total_unified_antes,
            "legacy_antes_que_unified": total_legacy_antes,
            "simultaneas": total_simultaneas,
            "diferencia_tiempo_promedio_segundos": statistics.mean(diffs_abs) if diffs_abs else None,
            "diferencia_tiempo_mediana_segundos": statistics.median(diffs_abs) if diffs_abs else None,
        },
        "recall_relativo_unified": recall_relativo_unified,
        "tasa_deteccion_compartida": tasa_deteccion_compartida,
        "muestra_total": muestra_total,
        "estado_validacion_muestra": reg.precision_validation_state(muestra_total),
        "outcome_n_evaluable": n_con_outcome,
        "outcome_pct_reached_20": pct_reached_20,
        "outcome_pct_reached_50": pct_reached_50,
        "outcome_pct_reached_100": pct_reached_100,
        "outcome_magnitud_maxima_promedio": statistics.mean(magnitudes) if magnitudes else None,
        "outcome_magnitud_maxima_mediana": statistics.median(magnitudes) if magnitudes else None,
        "outcome_tiempo_a_objetivo_promedio_minutos": (
            statistics.mean(tiempos_a_objetivo) if tiempos_a_objetivo else None
        ),
        "outcome_tiempo_a_objetivo_mediano_minutos": (
            statistics.median(tiempos_a_objetivo) if tiempos_a_objetivo else None
        ),
        "outcome_tiempo_a_objetivo_n": len(tiempos_a_objetivo),
        "solo_unified_outcome_status": "SIN_EVALUADOR_INDEPENDIENTE" if total_solo_unified else None,
        "pm_rvol": pm_rvol,
        "conocimiento_vs_baseline": conocimiento_vs_baseline,
    }
