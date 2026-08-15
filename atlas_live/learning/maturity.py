"""Motor de Madurez del Aprendizaje -- los 11 ejes + cuello de botella
(2026-08-15).

Implementa la arquitectura aprobada en `PROPUESTA_MADUREZ_APRENDIZAJE.md`
(secciones 3-5 y el anexo de umbrales, sección 8). Funciones PURAS: reciben
listas de dicts ya leídas (por defecto de `atlas_live.radar.candidate_registry`,
inyectables para tests/demo) y no hacen ninguna llamada de red ni tocan
`candidate_gates.py`/`phase_classifier.py`.

Regla central (aprobada, sección 5): la madurez GLOBAL es el MÍNIMO de los
11 estados de eje, nunca un promedio. Cada eje, a su vez, se mide por su
SUB-BUCKET peor cubierto, nunca por su total agregado (sección 3).

Fuente de datos: exclusivamente `candidate_registry` (radar CAPA 2, en vivo,
arranca en cero tras el reset). Nunca lee `atlas_live/reference/`
(Base Histórica) -- esa separación es la regla principal de esta ronda.
"""

import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from atlas_live.learning import thresholds as th


@dataclass
class AxisResult:
    key: str
    label: str
    level: int
    evidence: Dict[str, Any] = field(default_factory=dict)
    explanation: str = ""

    @property
    def level_label(self) -> str:
        return th.state_label(self.level)


@dataclass
class MaturityReport:
    global_level: int
    axes: List[AxisResult]
    limiting_axis: AxisResult
    limiting_explanation: str

    @property
    def global_level_label(self) -> str:
        return th.state_label(self.global_level)


# ---------------------------------------------------------------------------
# Eje 1 -- Volumen
# ---------------------------------------------------------------------------

def axis_volumen(evaluated: List[Dict[str, Any]]) -> AxisResult:
    n = len(evaluated)
    level = th.level_from_breakpoints(n, th.EJE1_VOLUMEN_BREAKPOINTS)
    return AxisResult(
        key="volumen", label="Casos evaluados (cerrados)", level=level,
        evidence={"casos_cerrados": n},
        explanation=f"{n} casos cerrados y evaluados en vivo.",
    )


# ---------------------------------------------------------------------------
# Eje 2 -- Días distintos de mercado
# ---------------------------------------------------------------------------

def axis_dias(evaluated: List[Dict[str, Any]]) -> AxisResult:
    dias = {e["market_date"] for e in evaluated if e.get("market_date")}
    n = len(dias)
    level = th.level_from_breakpoints(n, th.EJE2_DIAS_BREAKPOINTS)
    return AxisResult(
        key="dias", label="Días distintos de mercado", level=level,
        evidence={"dias_distintos": n},
        explanation=f"{n} días de mercado distintos con al menos 1 caso cerrado.",
    )


# ---------------------------------------------------------------------------
# Eje 3 -- Símbolos distintos + concentración
# ---------------------------------------------------------------------------

def axis_simbolos(evaluated: List[Dict[str, Any]]) -> AxisResult:
    por_simbolo: Dict[str, int] = {}
    for e in evaluated:
        t = e.get("ticker")
        if t:
            por_simbolo[t] = por_simbolo.get(t, 0) + 1
    n_simbolos = len(por_simbolo)
    total = sum(por_simbolo.values())
    top3 = sum(sorted(por_simbolo.values(), reverse=True)[:3])
    concentracion_top3 = round(top3 / total, 3) if total else None

    level = th.level_from_breakpoints(n_simbolos, th.EJE3_SIMBOLOS_BREAKPOINTS)
    # cap por concentración: si el nivel alcanzado exige un tope de
    # concentración y no se cumple, se baja al nivel más alto que sí cumple.
    if concentracion_top3 is not None:
        while level in th.EJE3_CONCENTRACION_MAX_TOP3 and concentracion_top3 > th.EJE3_CONCENTRACION_MAX_TOP3[level]:
            level -= 1

    return AxisResult(
        key="simbolos", label="Símbolos distintos", level=level,
        evidence={"simbolos_distintos": n_simbolos, "concentracion_top3_pct": (
            round(concentracion_top3 * 100, 1) if concentracion_top3 is not None else None
        )},
        explanation=(
            f"{n_simbolos} símbolos distintos" +
            (f", top-3 concentra {round(concentracion_top3*100,1)}% de los casos" if concentracion_top3 is not None else "")
            + "."
        ),
    )


# ---------------------------------------------------------------------------
# Eje 4 -- Regímenes de mercado distintos (proxy: dispersión + sesgo
# direccional de las propias candidatas detectadas cada día -- no depende de
# una fuente externa. Terciles calculados dinámicamente sobre los días ya
# observados, no valores fijos.)
# ---------------------------------------------------------------------------

def _terciles(valores: List[float]) -> List[float]:
    """Devuelve los 2 cortes (33%, 66%) de una lista de valores -- partición
    dinámica, no un valor fijo inventado."""
    s = sorted(valores)
    n = len(s)
    return [s[max(0, n // 3 - 1)], s[max(0, (2 * n) // 3 - 1)]]


def _tercil_index(valor: float, cortes: List[float]) -> int:
    if valor <= cortes[0]:
        return 0
    if valor <= cortes[1]:
        return 1
    return 2


def axis_regimenes(evaluated: List[Dict[str, Any]]) -> AxisResult:
    por_dia: Dict[str, List[Dict[str, Any]]] = {}
    for e in evaluated:
        d = e.get("market_date")
        if d:
            por_dia.setdefault(d, []).append(e)

    if len(por_dia) < th.EJE4_MIN_DIAS_PARA_TERCILES:
        return AxisResult(
            key="regimenes", label="Regímenes de mercado distintos", level=0,
            evidence={"dias_con_dato": len(por_dia), "regimenes_distintos": 0},
            explanation=(
                f"Solo {len(por_dia)} día(s) con datos -- hacen falta al menos "
                f"{th.EJE4_MIN_DIAS_PARA_TERCILES} para poder hablar de regímenes distintos."
            ),
        )

    dispersiones: Dict[str, float] = {}
    sesgos: Dict[str, float] = {}
    for d, filas in por_dia.items():
        cambios = [f["change_pct_at_detection"] for f in filas if f.get("change_pct_at_detection") is not None]
        dispersiones[d] = statistics.pstdev(cambios) if len(cambios) >= 2 else 0.0
        direcciones = [f.get("direction_at_detection") for f in filas if f.get("direction_at_detection") in ("ALCISTA", "BAJISTA", "NEUTRAL")]
        sesgos[d] = (sum(1 for x in direcciones if x == "ALCISTA") / len(direcciones)) if direcciones else 0.5

    cortes_vol = _terciles(list(dispersiones.values()))
    cortes_dir = _terciles(list(sesgos.values()))

    regimenes = set()
    for d in por_dia:
        regimenes.add((_tercil_index(dispersiones[d], cortes_vol), _tercil_index(sesgos[d], cortes_dir)))

    n_regimenes = len(regimenes)
    level = th.level_from_breakpoints(n_regimenes, th.EJE4_REGIMENES_BREAKPOINTS)
    return AxisResult(
        key="regimenes", label="Regímenes de mercado distintos", level=level,
        evidence={"regimenes_distintos": n_regimenes, "regimenes_posibles": th.EJE4_MAX_REGIMENES, "dias_con_dato": len(por_dia)},
        explanation=(
            f"{n_regimenes} de {th.EJE4_MAX_REGIMENES} combinaciones de volatilidad x sesgo direccional "
            f"observadas (proxy derivado de las propias candidatas detectadas cada día)."
        ),
    )


# ---------------------------------------------------------------------------
# Eje 5 -- Cobertura por timing de detección (peor de los 6 buckets)
# ---------------------------------------------------------------------------

def _peor_bucket(evaluated: List[Dict[str, Any]], campo: str, buckets: List[str]) -> Dict[str, int]:
    conteo = {b: 0 for b in buckets}
    for e in evaluated:
        v = e.get(campo)
        if v in conteo:
            conteo[v] += 1
    return conteo


def axis_timing(evaluated: List[Dict[str, Any]]) -> AxisResult:
    conteo = _peor_bucket(evaluated, "phase_tag", th.TIMING_BUCKETS)
    peor_bucket, peor_n = min(conteo.items(), key=lambda kv: kv[1])
    level = th.level_from_breakpoints(peor_n, th.EJE5_TIMING_BREAKPOINTS)
    return AxisResult(
        key="timing", label="Cobertura por timing de detección", level=level,
        evidence={"por_bucket": conteo, "peor_bucket": peor_bucket, "peor_n": peor_n},
        explanation=f"El bucket con menos evidencia es '{peor_bucket}' con {peor_n} casos.",
    )


# ---------------------------------------------------------------------------
# Eje 6 -- Cobertura por dirección (peor de las 3)
# ---------------------------------------------------------------------------

def axis_direccion(evaluated: List[Dict[str, Any]]) -> AxisResult:
    conteo = _peor_bucket(evaluated, "direction_at_detection", th.DIRECTION_BUCKETS)
    peor_bucket, peor_n = min(conteo.items(), key=lambda kv: kv[1])
    level = th.level_from_breakpoints(peor_n, th.EJE6_DIRECCION_BREAKPOINTS)
    return AxisResult(
        key="direccion", label="Cobertura por dirección", level=level,
        evidence={"por_bucket": conteo, "peor_bucket": peor_bucket, "peor_n": peor_n},
        explanation=f"La dirección con menos evidencia es {peor_bucket} con {peor_n} casos.",
    )


# ---------------------------------------------------------------------------
# Eje 7 -- Comportamiento post-apertura (solo detecciones premarket)
# ---------------------------------------------------------------------------

def axis_post_apertura(evaluated: List[Dict[str, Any]]) -> AxisResult:
    premarket = [e for e in evaluated if e.get("session") == "premarket" and e.get("comportamiento_post_apertura") in ("continua", "colapsa")]
    total = len(premarket)
    if total == 0:
        return AxisResult(
            key="post_apertura", label="Comportamiento post-apertura (premarket)", level=0,
            evidence={"total_premarket_evaluado": 0, "continua": 0, "colapsa": 0},
            explanation="Sin detecciones premarket evaluadas todavía -- no aplicable hasta que existan.",
        )
    continua = sum(1 for e in premarket if e["comportamiento_post_apertura"] == "continua")
    colapsa = total - continua
    peor = min(continua, colapsa)
    level = 1 + th.level_from_breakpoints(peor, th.EJE7_POST_APERTURA_BREAKPOINTS)
    return AxisResult(
        key="post_apertura", label="Comportamiento post-apertura (premarket)", level=level,
        evidence={"total_premarket_evaluado": total, "continua": continua, "colapsa": colapsa},
        explanation=(
            f"{total} detecciones premarket evaluadas ({continua} continúa / {colapsa} colapsa) -- "
            f"el peor de los dos tiene {peor} casos."
        ),
    )


# ---------------------------------------------------------------------------
# Eje 8 -- Evidencia por objetivo (+20% / +50% / +100%)
# ---------------------------------------------------------------------------

def axis_objetivos(evaluated: List[Dict[str, Any]]) -> AxisResult:
    r20 = sum(1 for e in evaluated if e.get("reached_20"))
    r50 = sum(1 for e in evaluated if e.get("reached_50"))
    r100 = sum(1 for e in evaluated if e.get("reached_100"))
    piso = th.EJE8_PISO_POSITIVOS

    if r20 == 0:
        level = 0
    elif r20 < piso:
        level = 1
    elif r50 < th.EJE8_PISO_INTERMEDIO_50:
        level = 2
    elif r50 < piso:
        level = 3
    elif r100 < th.EJE8_PISO_INTERMEDIO_100:
        level = 4
    elif r100 < piso:
        level = 5
    else:
        level = 6

    return AxisResult(
        key="objetivos", label="Evidencia por objetivo (+20/+50/+100%)", level=level,
        evidence={"positivos_20": r20, "positivos_50": r50, "positivos_100": r100, "piso_requerido": piso},
        explanation=(
            f"Positivos reales observados: {r20} de +20%, {r50} de +50%, {r100} de +100% "
            f"(piso de {piso} para considerar cada umbral con evidencia real)."
        ),
    )


# ---------------------------------------------------------------------------
# Eje 9 -- Consistencia (ventanas no solapadas, Wilson-overlap)
# ---------------------------------------------------------------------------

def _ventanas(daily_summaries: List[Dict[str, Any]], dias_por_ventana: int) -> List[Dict[str, Any]]:
    """Agrupa `daily_summaries` (ya ordenados por fecha ascendente) en
    ventanas consecutivas de `dias_por_ventana` filas -- no de días
    calendario, de FILAS reales con actividad (mismo criterio que el resto
    del proyecto: nunca inventar datos para días sin actividad)."""
    ventanas = []
    for i in range(0, len(daily_summaries), dias_por_ventana):
        chunk = daily_summaries[i:i + dias_por_ventana]
        evaluables = sum(c.get("n_evaluables") or 0 for c in chunk)
        aciertos = sum(c.get("n_aciertos") or 0 for c in chunk)
        ventanas.append({
            "desde": chunk[0]["market_date"], "hasta": chunk[-1]["market_date"],
            "n_evaluables": evaluables, "n_aciertos": aciertos,
        })
    return ventanas


def axis_consistencia(daily_summaries: List[Dict[str, Any]]) -> AxisResult:
    ventanas = _ventanas(daily_summaries, th.EJE9_DIAS_POR_VENTANA)
    comparables = [v for v in ventanas if v["n_evaluables"] >= th.EJE9_MIN_CASOS_POR_VENTANA]
    n_comp = len(comparables)
    total_evaluables = sum(v["n_evaluables"] for v in ventanas)

    if n_comp < 2:
        level = 2 if total_evaluables >= th.EJE9_MIN_CASOS_POR_VENTANA else (1 if total_evaluables > 0 else 0)
        return AxisResult(
            key="consistencia", label="Consistencia entre ventanas de tiempo", level=level,
            evidence={"ventanas_comparables": n_comp, "ventanas_totales": len(ventanas)},
            explanation=(
                f"Todavía no hay 2 ventanas de ~{th.EJE9_DIAS_POR_VENTANA} días de mercado con muestra "
                f"propia suficiente (piso {th.EJE9_MIN_CASOS_POR_VENTANA} casos/ventana) para comparar."
            ),
        )

    intervalos = [th.wilson_interval(v["n_aciertos"], v["n_evaluables"]) for v in comparables]
    pares = list(zip(intervalos, intervalos[1:]))
    n_solapan = sum(1 for a, b in pares if th.intervals_overlap(a, b))

    if n_comp < 3:
        level = 3
    elif n_comp < 4:
        level = 4 if (pares and th.intervals_overlap(*pares[-1])) else 3
    elif n_comp < 6:
        level = 5 if n_solapan >= max(1, len(pares) - 1) else 3
    else:
        level = 6 if n_solapan == len(pares) else 5

    return AxisResult(
        key="consistencia", label="Consistencia entre ventanas de tiempo", level=level,
        evidence={
            "ventanas_comparables": n_comp,
            "precision_por_ventana_pct": [
                round(100 * v["n_aciertos"] / v["n_evaluables"], 1) if v["n_evaluables"] else None for v in comparables
            ],
            "pares_que_solapan": n_solapan, "pares_totales": len(pares),
        },
        explanation=f"{n_comp} ventanas comparables, {n_solapan} de {len(pares)} pares consecutivos con intervalos que se solapan.",
    )


# ---------------------------------------------------------------------------
# Eje 10 -- Recencia y estabilidad
# ---------------------------------------------------------------------------

def axis_recencia(daily_summaries: List[Dict[str, Any]]) -> AxisResult:
    recientes = daily_summaries[-th.EJE10_DIAS_VENTANA_RECIENTE:]
    n_recent = sum(c.get("n_evaluables") or 0 for c in recientes)
    aciertos_recent = sum(c.get("n_aciertos") or 0 for c in recientes)
    n_hist = sum(c.get("n_evaluables") or 0 for c in daily_summaries)
    aciertos_hist = sum(c.get("n_aciertos") or 0 for c in daily_summaries)

    if n_recent == 0:
        return AxisResult(
            key="recencia", label="Recencia y estabilidad", level=0,
            evidence={"n_reciente": 0},
            explanation="Sin casos cerrados en la ventana reciente todavía.",
        )

    level = th.level_from_breakpoints(n_recent, th.EJE10_BREAKPOINTS)  # 0..4 (hasta "consistente")

    alerta = False
    if n_recent >= th.EJE9_MIN_CASOS_POR_VENTANA and (n_hist - n_recent) >= th.EJE9_MIN_CASOS_POR_VENTANA:
        recent_iv = th.wilson_interval(aciertos_recent, n_recent)
        hist_iv = th.wilson_interval(aciertos_hist - aciertos_recent, n_hist - n_recent)
        alerta = not th.intervals_overlap(recent_iv, hist_iv)

    if level >= 4 and not alerta:
        # "sostenido" (L7): se exige además que la muestra histórica total ya
        # sea grande (mismo piso que Eje 1 nivel "madurez_alta") -- proxy de
        # que esto no es la primera vez que se alcanza este volumen.
        piso_sostenido = th.EJE1_VOLUMEN_BREAKPOINTS[-1]  # 300
        level = 6 if n_hist >= piso_sostenido else 5
    elif level >= 4 and alerta:
        level = 4  # se muestra la alerta, no se oculta, pero cap por debajo de "validado" hasta explicarla

    precision_reciente_pct = round(100 * aciertos_recent / n_recent, 1) if n_recent else None
    precision_historica_pct = round(100 * aciertos_hist / n_hist, 1) if n_hist else None

    return AxisResult(
        key="recencia", label="Recencia y estabilidad", level=level,
        evidence={
            "n_reciente": n_recent, "aciertos_reciente": aciertos_recent, "precision_reciente_pct": precision_reciente_pct,
            "n_historico": n_hist, "aciertos_historico": aciertos_hist, "precision_historica_pct": precision_historica_pct,
            "alerta_caida_fuera_de_intervalo": alerta,
        },
        explanation=(
            f"Reciente: {aciertos_recent}/{n_recent} = {precision_reciente_pct}% · "
            f"Histórica: {aciertos_hist}/{n_hist} = {precision_historica_pct}%"
            + (" -- ALERTA: la reciente cae fuera del intervalo histórico." if alerta else "")
        ),
    )


# ---------------------------------------------------------------------------
# Eje 11 -- Validación fuera de muestra (walk-forward)
# ---------------------------------------------------------------------------

def _bucket_coverage_en_rango(evaluated: List[Dict[str, Any]], desde: str, hasta: str) -> Dict[str, Any]:
    en_rango = [e for e in evaluated if desde <= e.get("market_date", "") <= hasta]
    timing_cubiertos = {e["phase_tag"] for e in en_rango if e.get("phase_tag") in th.TIMING_BUCKETS}
    direcciones_cubiertas = {e["direction_at_detection"] for e in en_rango if e.get("direction_at_detection") in ("ALCISTA", "BAJISTA")}
    return {"n": len(en_rango), "timing_buckets_cubiertos": len(timing_cubiertos), "direcciones_cubiertas": len(direcciones_cubiertas)}


def axis_validacion(daily_summaries: List[Dict[str, Any]], evaluated: List[Dict[str, Any]]) -> AxisResult:
    k = th.EJE11_DIAS_HOLDOUT
    if len(daily_summaries) <= k:
        return AxisResult(
            key="validacion", label="Validación fuera de muestra", level=0,
            evidence={"dias_totales": len(daily_summaries), "dias_holdout_requeridos": k},
            explanation=f"Hacen falta más de {k} días de mercado para separar calibración de un holdout real.",
        )

    holdout_rows = daily_summaries[-k:]
    holdout_range = (holdout_rows[0]["market_date"], holdout_rows[-1]["market_date"])
    holdout = _bucket_coverage_en_rango(evaluated, *holdout_range, )

    if holdout["n"] < th.EJE11_MIN_CASOS_L5:
        level = 3
    elif holdout["n"] < th.EJE11_MIN_CASOS_L6 or holdout["timing_buckets_cubiertos"] < th.EJE11_MIN_BUCKETS_TIMING_L6:
        level = 4
    else:
        level = 5
        if len(daily_summaries) >= 2 * k:
            prev_rows = daily_summaries[-2 * k:-k]
            prev_range = (prev_rows[0]["market_date"], prev_rows[-1]["market_date"])
            prev = _bucket_coverage_en_rango(evaluated, *prev_range)
            if (prev["n"] >= th.EJE11_MIN_CASOS_L6 and prev["timing_buckets_cubiertos"] >= th.EJE11_MIN_BUCKETS_TIMING_L6
                    and holdout["direcciones_cubiertas"] >= 2 and prev["direcciones_cubiertas"] >= 2):
                level = 6

    return AxisResult(
        key="validacion", label="Validación fuera de muestra", level=level,
        evidence={"holdout_rango": holdout_range, "holdout_n": holdout["n"],
                  "holdout_timing_buckets_cubiertos": holdout["timing_buckets_cubiertos"],
                  "holdout_direcciones_cubiertas": holdout["direcciones_cubiertas"]},
        explanation=(
            f"Holdout (últimos {k} días de mercado, {holdout_range[0]} a {holdout_range[1]}, nunca usado para "
            f"calibrar ningún umbral): {holdout['n']} casos, {holdout['timing_buckets_cubiertos']}/6 buckets de timing."
        ),
    )


# ---------------------------------------------------------------------------
# Orquestador
# ---------------------------------------------------------------------------

def compute_maturity(
    evaluated: Optional[List[Dict[str, Any]]] = None,
    daily_summaries: Optional[List[Dict[str, Any]]] = None,
) -> MaturityReport:
    """Punto de entrada único. Si `evaluated`/`daily_summaries` no se pasan,
    se leen de `candidate_registry` (radar CAPA 2, en vivo). Inyectables
    para tests y para el script de demostración con datos sintéticos."""
    if evaluated is None or daily_summaries is None:
        from atlas_live.radar import candidate_registry as reg
        if evaluated is None:
            evaluated = reg.list_all_evaluated_candidates()
        if daily_summaries is None:
            daily_summaries = reg.list_daily_summaries()

    axes = [
        axis_volumen(evaluated),
        axis_dias(evaluated),
        axis_simbolos(evaluated),
        axis_regimenes(evaluated),
        axis_timing(evaluated),
        axis_direccion(evaluated),
        axis_post_apertura(evaluated),
        axis_objetivos(evaluated),
        axis_consistencia(daily_summaries),
        axis_recencia(daily_summaries),
        axis_validacion(daily_summaries, evaluated),
    ]

    limiting = min(axes, key=lambda a: a.level)
    global_level = limiting.level

    return MaturityReport(
        global_level=global_level,
        axes=axes,
        limiting_axis=limiting,
        limiting_explanation=(
            f"Eje que limita la madurez global: {limiting.label} "
            f"({limiting.level_label}). {limiting.explanation}"
        ),
    )
