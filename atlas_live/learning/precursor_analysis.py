"""Análisis de precursores (2026-08-17, Fase 3b) -- ALERTA TEMPRANA.

Responde la pregunta real del objetivo operativo de Atlas: no "qué días ya
se movieron", sino "qué características tenía el MISMO símbolo en los días
`T-1..T-lookback_days` ANTES de que empezara un movimiento fuerte, y cómo
transiciona esa condición día a día hasta la confirmación".

Un "onset" (inicio de movimiento) es el PRIMER día de una racha contigua
donde `max_advance_pct` (ya calculado por `daily_reference.compute_outcome`,
ventana de `MIN_FORWARD_DAYS=10` días hacia adelante) cruza un umbral --
evita contar el mismo movimiento varias veces con ventanas superpuestas.
Para ese onset, se juntan las features YA GUARDADAS (nunca recalculadas)
del mismo símbolo en sus `lookback_days` días de trading previos, y se
comparan contra el promedio de todo el universo (baseline).

Features usadas -- TODAS ya existentes en `daily_features`, ninguna
inventada (ver `atlas_live/reference/daily_reference.py::DailyFeatures`):
`volatility_14d_pct`, `daily_range_pct`, `relative_volume`, `gap_pct`.
Se agregan dos derivadas de comparar cada fila contra la fila anterior EN
LA MISMA serie del símbolo (dato ya almacenado, solo se resta):
`volume_change_pct` (cambio de volumen día a día) y `change_pct_delta`
(aceleración simple: `change_pct` de hoy menos el de ayer). `price` se
reporta como contexto descriptivo, no como feature predictiva -- el precio
absoluto no discrimina magnitud de movimiento futuro por sí solo.

Límite declarado, no oculto: la Base Histórica es de velas DIARIAS
(Tradier `/v1/markets/history`) -- este módulo puede hablar en DÍAS antes
del inicio (`T-1..T-5`), nunca en minutos. Un análisis a nivel de minutos
necesitaría datos intradía, que Tradier solo cubre ~8-14 días hacia atrás
(limitación ya documentada en el proyecto) -- queda fuera de este módulo.

Standalone: no se importa desde `candidate_gates.py`, `candidate_tracker.py`
ni `decision_engine.py`.
"""

from typing import Any, Dict, List, Optional, Sequence

from atlas_live.learning.historical_scoring import _load_rows_from_db

DEFAULT_FEATURE_COLS = ("volatility_14d_pct", "daily_range_pct", "relative_volume", "gap_pct",
                         "volume_change_pct", "change_pct_delta")
DEFAULT_THRESHOLDS = (20, 50, 100)
DEFAULT_LOOKBACK_DAYS = 5

# Las 5 ventanas temporales pedidas -- se responden empíricamente (no se
# asignan a mano): en `por_offset`, la distribución real de
# `timing_deteccion` en cada T-k muestra en qué offset predomina cada una.
TIMING_TO_VENTANA = {
    "antes_del_movimiento": "PREPARACION",   # ver aclaración: "día tranquilo", no garantiza nada por sí solo
    "expansion_temprana": "ALERTA_TEMPRANA",
    "al_comienzo": "INICIO",
    "recorrido_significativo_ya_hecho": "CONFIRMACION",
    "demasiado_tarde": "TARDIO",
    "agotamiento": "TARDIO",
}


def group_by_symbol_sorted(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Agrupa filas por símbolo, ordenadas por fecha -- base para poder
    'mirar hacia atrás' en la propia serie de cada símbolo."""
    by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(r)
    for sym in by_symbol:
        by_symbol[sym].sort(key=lambda r: r["date"])
    return by_symbol


def find_episode_onsets(rows_by_symbol: Dict[str, List[Dict[str, Any]]], threshold_pct: float) -> Dict[str, List[str]]:
    """Por símbolo, el PRIMER día de cada racha contigua donde
    `max_advance_pct >= threshold_pct` -- el inicio real de cada
    movimiento, no cada día dentro de la racha."""
    onsets: Dict[str, List[str]] = {}
    for sym, srows in rows_by_symbol.items():
        dates: List[str] = []
        in_episode = False
        for r in srows:
            hit = (r.get("max_advance_pct") or 0) >= threshold_pct
            if hit and not in_episode:
                dates.append(r["date"])
            in_episode = hit
        if dates:
            onsets[sym] = dates
    return onsets


def _pct_change(new: Optional[float], old: Optional[float]) -> Optional[float]:
    if new is None or old is None or not old:
        return None
    return round(100 * (new - old) / old, 3)


def precursor_rows_for_onsets(rows_by_symbol: Dict[str, List[Dict[str, Any]]], onsets: Dict[str, List[str]],
                               lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> List[Dict[str, Any]]:
    """Para cada onset, junta las filas de ese mismo símbolo en
    `T-1..T-lookback_days` (`offset=1` = el día de trading inmediatamente
    anterior). Agrega `volume_change_pct`/`change_pct_delta` comparando
    cada fila contra la fila anterior EN LA SERIE DEL SÍMBOLO (no contra el
    onset) -- son derivadas de datos ya guardados, no recalculan nada del
    proveedor."""
    out: List[Dict[str, Any]] = []
    for sym, onset_dates in onsets.items():
        srows = rows_by_symbol.get(sym, [])
        date_index = {r["date"]: i for i, r in enumerate(srows)}
        for onset_date in onset_dates:
            idx = date_index.get(onset_date)
            if idx is None:
                continue
            onset_row = srows[idx]
            for offset in range(1, lookback_days + 1):
                j = idx - offset
                if j < 0:
                    break  # sin más historial previo para este símbolo
                row = dict(srows[j])
                prev = srows[j - 1] if j - 1 >= 0 else None
                row["volume_change_pct"] = _pct_change(row.get("volume"), prev.get("volume")) if prev else None
                row["change_pct_delta"] = (
                    round(row["change_pct"] - prev["change_pct"], 3)
                    if prev and row.get("change_pct") is not None and prev.get("change_pct") is not None else None
                )
                row["symbol"] = sym
                row["onset_date"] = onset_date
                row["offset"] = offset
                row["onset_max_advance_pct"] = onset_row.get("max_advance_pct")
                row["onset_max_drawdown_pct"] = onset_row.get("max_drawdown_pct")
                out.append(row)
    return out


def _avg(values: List[Optional[float]]) -> Dict[str, Any]:
    vals = [v for v in values if v is not None]
    return {"n": len(vals), "promedio": round(sum(vals) / len(vals), 3) if vals else None}


def precursor_summary(baseline_rows: List[Dict[str, Any]], precursor_rows: List[Dict[str, Any]],
                       feature_cols: Sequence[str], lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> Dict[str, Any]:
    """Promedio real (+ `n`) de cada feature en cada offset `T-k`, más la
    distribución de `direction` y de `timing_deteccion` (esto último
    responde empíricamente "qué ventana aparece cuántos días antes"),
    comparado contra el baseline del universo completo."""
    baseline = {col: _avg([r.get(col) for r in baseline_rows]) for col in feature_cols}

    por_offset: Dict[str, Any] = {}
    for offset in range(1, lookback_days + 1):
        offset_rows = [r for r in precursor_rows if r["offset"] == offset]
        features = {col: _avg([r.get(col) for r in offset_rows]) for col in feature_cols}
        direcciones: Dict[str, int] = {}
        timings: Dict[str, int] = {}
        for r in offset_rows:
            d = r.get("direction") or "INDEFINIDA"
            direcciones[d] = direcciones.get(d, 0) + 1
            t = r.get("timing_deteccion") or "sin_clasificar"
            timings[t] = timings.get(t, 0) + 1
        n_episodios = len({(r["symbol"], r["onset_date"]) for r in offset_rows})
        por_offset[f"T-{offset}"] = {
            "n_episodios": n_episodios, "features": features,
            "direccion": direcciones, "timing_deteccion": timings,
        }

    return {"baseline_universo_completo": baseline, "por_offset": por_offset}


def onset_outcome_breakdown(rows_by_symbol: Dict[str, List[Dict[str, Any]]],
                             onsets_20: Dict[str, List[str]]) -> Dict[str, Any]:
    """De los onsets de +20% (todos, por construcción, llegan a +20%),
    cuántos TAMBIÉN llegan a +50%/+100% (anidado, mismo evento) y cuál es
    el drawdown promedio real de los que se quedan solo en +20-49% -- la
    respuesta concreta a "qué distingue un falso positivo parcial de un
    movimiento que sigue"."""
    onset_rows = []
    for sym, dates in onsets_20.items():
        srows = rows_by_symbol.get(sym, [])
        by_date = {r["date"]: r for r in srows}
        for d in dates:
            r = by_date.get(d)
            if r:
                onset_rows.append(r)
    n = len(onset_rows)
    llega_50 = [r for r in onset_rows if (r.get("max_advance_pct") or 0) >= 50]
    llega_100 = [r for r in onset_rows if (r.get("max_advance_pct") or 0) >= 100]
    se_queda_en_20 = [r for r in onset_rows if (r.get("max_advance_pct") or 0) < 50]
    drawdowns = [r["max_drawdown_pct"] for r in se_queda_en_20 if r.get("max_drawdown_pct") is not None]

    def _rate(sub: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {"n": len(sub), "pct": round(100 * len(sub) / n, 1) if n else None}

    return {
        "n_onsets_20": n,
        "tambien_llega_50": _rate(llega_50),
        "tambien_llega_100": _rate(llega_100),
        "se_queda_solo_en_20_49": {
            **_rate(se_queda_en_20),
            "drawdown_promedio": round(sum(drawdowns) / len(drawdowns), 1) if drawdowns else None,
        },
    }


def racional_comparison(precursor_rows_t1: List[Dict[str, Any]], onset_rows: List[Dict[str, Any]],
                         feature_cols: Sequence[str]) -> Dict[str, Any]:
    """Compara racional_available=true vs false vs desconocido, en el
    offset T-1 (el más cercano y accionable al onset) y en el resultado de
    los onsets mismos -- para saber si el patrón encontrado en todo el
    mercado también se sostiene dentro de lo operable en Racional."""
    def _split(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = {"true": [], "false": [], "desconocido": []}
        for r in rows:
            v = r.get("racional_available")
            key = "true" if v == 1 else ("false" if v == 0 else "desconocido")
            out[key].append(r)
        return out

    t1_split = _split(precursor_rows_t1)
    onset_split = _split(onset_rows)

    result: Dict[str, Any] = {}
    for key in ("true", "false", "desconocido"):
        t1_rows = t1_split[key]
        onsets_rows = onset_split[key]
        n_onsets = len(onsets_rows)
        llega_100 = sum(1 for r in onsets_rows if (r.get("max_advance_pct") or 0) >= 100)
        result[key] = {
            "n_episodios_t1": len(t1_rows),
            "features_t1": {col: _avg([r.get(col) for r in t1_rows]) for col in feature_cols},
            "n_onsets": n_onsets,
            "pct_llega_100": round(100 * llega_100 / n_onsets, 1) if n_onsets else None,
        }
    return result


def generate_precursor_report(feature_cols: Sequence[str] = DEFAULT_FEATURE_COLS,
                               thresholds: Sequence[int] = DEFAULT_THRESHOLDS,
                               lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> Dict[str, Any]:
    """Reporte completo sobre la Base Histórica real: para cada umbral
    (+20/+50/+100%), cuántos episodios de inicio real se detectaron, qué
    tenían en común las condiciones de `T-1..T-lookback_days` antes de cada
    uno (comparado contra el baseline del mercado completo), qué distingue
    a los que siguen escalando de los que se quedan cortos, y cómo se
    compara todo esto dentro vs fuera del universo operable en Racional."""
    rows = _load_rows_from_db()
    rows_by_symbol = group_by_symbol_sorted(rows)

    report: Dict[str, Any] = {
        "n_filas_totales": len(rows), "n_simbolos": len(rows_by_symbol),
        "feature_cols": list(feature_cols), "lookback_days": lookback_days,
        "limite_temporal": "Datos diarios (Tradier) -- habla en DIAS de trading antes del inicio (T-1..T-N), nunca en minutos.",
        "ventanas_propuestas": TIMING_TO_VENTANA,
        "por_umbral": {},
    }
    for th in thresholds:
        onsets = find_episode_onsets(rows_by_symbol, th)
        n_episodios = sum(len(v) for v in onsets.values())
        precursor_rows = precursor_rows_for_onsets(rows_by_symbol, onsets, lookback_days)
        summary = precursor_summary(rows, precursor_rows, feature_cols, lookback_days)

        entry: Dict[str, Any] = {"n_episodios_detectados": n_episodios, **summary}

        if th == min(thresholds):
            entry["desglose_continuacion"] = onset_outcome_breakdown(rows_by_symbol, onsets)
            onset_rows = []
            for sym, dates in onsets.items():
                by_date = {r["date"]: r for r in rows_by_symbol.get(sym, [])}
                onset_rows.extend(by_date[d] for d in dates if d in by_date)
            t1_rows = [r for r in precursor_rows if r["offset"] == 1]
            entry["comparacion_racional"] = racional_comparison(t1_rows, onset_rows, feature_cols)

        report["por_umbral"][f"+{th}%"] = entry
    return report


# ---------------------------------------------------------------------------
# Estudio de separación A/B/C (2026-08-17) -- "relative_volume alto" solo
# dice que un movimiento fuerte es más probable, no distingue cuánto va a
# durar. Este bloque compara, con evidencia real (mediana + percentiles,
# no solo promedio), los onsets de +20% que se quedan cortos (A) contra los
# que continúan a +50-99% (B) o +100%+ (C) -- qué es distinto ANTES de que
# se sepa en cuál de las 3 categorías va a terminar cada uno.
# ---------------------------------------------------------------------------

CATEGORY_LABELS = ("A_20_49", "B_50_99", "C_100_mas")
VOLUME_ELEVATED_THRESHOLD = 2.0  # relative_volume >= esto = "día con volumen anormal"


def categorize_onsets(rows_by_symbol: Dict[str, List[Dict[str, Any]]],
                       onsets_20: Dict[str, List[str]]) -> Dict[str, List[Dict[str, Any]]]:
    """Parte los onsets de +20% en 3 grupos MUTUAMENTE EXCLUYENTES según
    hasta dónde llegó cada movimiento realmente: A (se quedó en 20-49%),
    B (llegó a 50-99%), C (llegó a 100% o más)."""
    out: Dict[str, List[Dict[str, Any]]] = {k: [] for k in CATEGORY_LABELS}
    for sym, dates in onsets_20.items():
        by_date = {r["date"]: r for r in rows_by_symbol.get(sym, [])}
        for d in dates:
            r = by_date.get(d)
            if r is None:
                continue
            adv = r.get("max_advance_pct") or 0
            if adv >= 100:
                out["C_100_mas"].append(r)
            elif adv >= 50:
                out["B_50_99"].append(r)
            else:
                out["A_20_49"].append(r)
    return out


def _percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round(p / 100 * (len(s) - 1)))))
    return round(s[idx], 3)


def distribution_stats(values: List[Optional[float]]) -> Dict[str, Any]:
    """`n`, promedio, mediana y percentiles p10/p25/p75/p90 -- nunca solo
    un promedio (pedido explícito: el promedio esconde outliers en
    features de cola larga como relative_volume)."""
    vals = [v for v in values if v is not None]
    if not vals:
        return {"n": 0, "promedio": None, "mediana": None, "p10": None, "p25": None,
                "p75": None, "p90": None, "min": None, "max": None}
    return {
        "n": len(vals), "promedio": round(sum(vals) / len(vals), 3), "mediana": _percentile(vals, 50),
        "p10": _percentile(vals, 10), "p25": _percentile(vals, 25),
        "p75": _percentile(vals, 75), "p90": _percentile(vals, 90),
        "min": round(min(vals), 3), "max": round(max(vals), 3),
    }


def _onset_keys(rows: List[Dict[str, Any]]) -> set:
    return {(r["symbol"], r["date"]) for r in rows}


def category_precursor_stats(precursor_rows: List[Dict[str, Any]], categories: Dict[str, List[Dict[str, Any]]],
                              feature_cols: Sequence[str], lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> Dict[str, Any]:
    """Por categoría (A/B/C) y por offset T-1..T-lookback_days, distribución
    completa (no solo promedio) de cada feature -- responde "qué es
    distinto, ANTES del hecho, entre lo que se queda corto y lo que sigue
    escalando"."""
    keys_by_cat = {cat: _onset_keys(rows) for cat, rows in categories.items()}
    result: Dict[str, Any] = {}
    for cat, keys in keys_by_cat.items():
        cat_rows = [r for r in precursor_rows if (r["symbol"], r["onset_date"]) in keys]
        por_offset: Dict[str, Any] = {}
        for offset in range(1, lookback_days + 1):
            offset_rows = [r for r in cat_rows if r["offset"] == offset]
            n_episodios = len({(r["symbol"], r["onset_date"]) for r in offset_rows})
            features = {col: distribution_stats([r.get(col) for r in offset_rows]) for col in feature_cols}
            por_offset[f"T-{offset}"] = {"n_episodios": n_episodios, "features": features}
        result[cat] = {"n_onsets": len(keys), "por_offset": por_offset}
    return result


def volume_persistence(precursor_rows: List[Dict[str, Any]], categories: Dict[str, List[Dict[str, Any]]],
                        threshold: float = VOLUME_ELEVATED_THRESHOLD) -> Dict[str, Any]:
    """Por categoría, cuántos de los días `T-1..T-5` disponibles (0 a 5)
    tenían `relative_volume >= threshold` -- "persistencia" real del
    volumen anormal, no solo un pico de un día."""
    keys_by_cat = {cat: _onset_keys(rows) for cat, rows in categories.items()}
    result: Dict[str, Any] = {}
    for cat, keys in keys_by_cat.items():
        by_onset: Dict[Any, List[Dict[str, Any]]] = {}
        for r in precursor_rows:
            key = (r["symbol"], r["onset_date"])
            if key in keys:
                by_onset.setdefault(key, []).append(r)
        counts = [sum(1 for r in rows if (r.get("relative_volume") or 0) >= threshold) for rows in by_onset.values()]
        dist: Dict[str, int] = {}
        for c in counts:
            dist[str(c)] = dist.get(str(c), 0) + 1
        result[cat] = {
            "n_onsets_con_ventana_previa": len(counts),
            "distribucion_dias_con_volumen_elevado": dist,
            "promedio_dias_elevados": round(sum(counts) / len(counts), 2) if counts else None,
        }
    return result


def volume_acceleration(precursor_rows: List[Dict[str, Any]], categories: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Por categoría, `relative_volume` en T-1 menos `relative_volume` en
    T-5 (mismo onset) -- aceleración real del volumen entre el inicio de
    la ventana y el día previo al onset."""
    keys_by_cat = {cat: _onset_keys(rows) for cat, rows in categories.items()}
    result: Dict[str, Any] = {}
    for cat, keys in keys_by_cat.items():
        by_onset: Dict[Any, Dict[int, Optional[float]]] = {}
        for r in precursor_rows:
            key = (r["symbol"], r["onset_date"])
            if key in keys:
                by_onset.setdefault(key, {})[r["offset"]] = r.get("relative_volume")
        deltas = [
            offsets[1] - offsets[5] for offsets in by_onset.values()
            if offsets.get(1) is not None and offsets.get(5) is not None
        ]
        result[cat] = distribution_stats(deltas)
    return result


def category_racional_split(precursor_rows: List[Dict[str, Any]], categories: Dict[str, List[Dict[str, Any]]],
                             feature_cols: Sequence[str]) -> Dict[str, Any]:
    """Dentro de cada categoría (A/B/C), separa racional_available
    true/false/desconocido en T-1 -- para saber si la separación entre
    categorías también existe DENTRO de lo operable en Racional, o si es
    un efecto que solo aparece fuera de ese universo."""
    keys_by_cat = {cat: _onset_keys(rows) for cat, rows in categories.items()}
    result: Dict[str, Any] = {}
    for cat, keys in keys_by_cat.items():
        t1_rows = [r for r in precursor_rows if r["offset"] == 1 and (r["symbol"], r["onset_date"]) in keys]
        split: Dict[str, List[Dict[str, Any]]] = {"true": [], "false": [], "desconocido": []}
        for r in t1_rows:
            v = r.get("racional_available")
            k = "true" if v == 1 else ("false" if v == 0 else "desconocido")
            split[k].append(r)
        result[cat] = {
            k: {"n": len(rows), **{col: distribution_stats([r.get(col) for r in rows]) for col in feature_cols}}
            for k, rows in split.items()
        }
    return result


def generate_separation_report(feature_cols: Sequence[str] = DEFAULT_FEATURE_COLS,
                                lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> Dict[str, Any]:
    """Reporte completo del estudio de separación A(20-49%)/B(50-99%)/C(100%+)
    sobre la Base Histórica real -- distribución completa por feature y
    offset, persistencia y aceleración del volumen, y el cruce con
    racional_available dentro de cada categoría."""
    rows = _load_rows_from_db()
    rows_by_symbol = group_by_symbol_sorted(rows)
    onsets_20 = find_episode_onsets(rows_by_symbol, 20)
    categories = categorize_onsets(rows_by_symbol, onsets_20)
    precursor_rows = precursor_rows_for_onsets(rows_by_symbol, onsets_20, lookback_days)

    return {
        "n_filas_totales": len(rows), "n_simbolos": len(rows_by_symbol),
        "feature_cols": list(feature_cols), "lookback_days": lookback_days,
        "n_onsets_por_categoria": {k: len(v) for k, v in categories.items()},
        "por_categoria": category_precursor_stats(precursor_rows, categories, feature_cols, lookback_days),
        "persistencia_volumen": volume_persistence(precursor_rows, categories),
        "aceleracion_volumen_t1_menos_t5": volume_acceleration(precursor_rows, categories),
        "comparacion_racional_por_categoria": category_racional_split(precursor_rows, categories, feature_cols),
    }
