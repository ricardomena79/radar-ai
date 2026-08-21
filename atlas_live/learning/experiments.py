"""Motor de experimentos walk-forward (2026-08-16) -- Hipótesis A/B/C de la
`PROPUESTA PRIORIZADA DE EXPERIMENTOS` (aprobada por el usuario). Responde
UNA pregunta por experimento: "¿segmentar por esta señal mejora, fuera de
muestra, la tasa de +20/+50/+100% frente a no usarla?" -- nunca decide qué
candidata detecta Atlas ni cambia su ranking; es exclusivamente diagnóstico.

Anti-leakage estricto, distinto del ya existente en `daily_reference.py`:
acá el riesgo no es "usar una vela futura de ESE símbolo" (eso ya lo cubre
`compute_features`), es un riesgo de POBLACIÓN -- calcular los terciles
(los cortes "alto/medio/bajo" de una señal) usando datos de fechas que
todavía no habían pasado. Por eso los cortes de la fecha D se calculan
SIEMPRE con filas de fecha < D solamente (walk-forward, ventana expansiva).

No importa nada de `candidate_gates.py`/`phase_classifier.py`/score/
DecisionEngine, no los modifica, no los usa.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from atlas_live.learning import thresholds as th

DIRECTIONS = ("ALCISTA", "BAJISTA", "NEUTRAL")
THRESHOLDS_PCT = (20, 50, 100)

# Hipótesis B -- agrupación de los 6 buckets de timing en 3 grupos (2026-08-15,
# hallazgo real: "antes_del_movimiento" es ~75% del dataset y en su mayoría
# NO significa "a punto de explotar" -- se mantiene como grupo aparte, nunca
# mezclado con EARLY genuino).
EARLY_GENUINE = {"al_comienzo", "expansion_temprana"}
LATE = {"recorrido_significativo_ya_hecho", "demasiado_tarde", "agotamiento"}
ANTES_DEL_MOVIMIENTO = {"antes_del_movimiento"}

MIN_CALIBRATION_DATES_DEFAULT = 10  # fechas reservadas SOLO para calibrar, nunca evaluadas
MIN_PRIOR_ROWS_FOR_CUTS = 30        # piso de filas previas para que un corte de tercil tenga sentido
DIAS_RECIENTE_DEFAULT = 5           # últimas N fechas evaluadas = "reciente"


@dataclass
class BucketStats:
    label: str
    n: int = 0
    aciertos_20: int = 0
    aciertos_50: int = 0
    aciertos_100: int = 0
    # Predicción de magnitud (2026-08-20, aprobado por el usuario): valores
    # reales de `max_advance_pct` del bucket, para poder responder "¿a qué %
    # estima Atlas que puede llegar?" con la MEDIANA real de casos parecidos,
    # no solo con la fracción que cruzó 20/50/100. Aditivo -- no cambia
    # ningún conteo/porcentaje ya existente.
    values: List[float] = field(default_factory=list)

    def add(self, max_advance_pct: Optional[float]) -> None:
        self.n += 1
        if max_advance_pct is None:
            return
        self.values.append(max_advance_pct)
        if max_advance_pct >= 20:
            self.aciertos_20 += 1
        if max_advance_pct >= 50:
            self.aciertos_50 += 1
        if max_advance_pct >= 100:
            self.aciertos_100 += 1

    def mediana(self) -> Optional[float]:
        if not self.values:
            return None
        s = sorted(self.values)
        mid = len(s) // 2
        m = s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2
        return round(m, 1)

    def to_dict(self) -> Dict[str, Any]:
        def pct(a: int) -> Optional[float]:
            return round(100 * a / self.n, 1) if self.n else None
        return {
            "label": self.label, "n": self.n,
            "aciertos_20": self.aciertos_20, "pct_20": pct(self.aciertos_20),
            "aciertos_50": self.aciertos_50, "pct_50": pct(self.aciertos_50),
            "aciertos_100": self.aciertos_100, "pct_100": pct(self.aciertos_100),
            "mediana_max_advance_pct": self.mediana(),
        }


def _tercile_cuts(values: List[float]) -> Optional[Sequence[float]]:
    """Cortes 33%/66% de una lista YA FILTRADA a fechas anteriores. `None`
    si no hay piso suficiente de muestra (nunca se inventa un corte con
    pocos datos)."""
    if len(values) < MIN_PRIOR_ROWS_FOR_CUTS:
        return None
    s = sorted(values)
    n = len(s)
    return (s[n // 3], s[(2 * n) // 3])


def _bucket_of_row(row: Dict[str, Any], feature_cols: Sequence[str], cuts_by_feature: Dict[str, Sequence[float]]) -> Optional[str]:
    """'alto' solo si TODAS las features pedidas están en su propio tercil
    alto ese día; 'bajo' solo si TODAS están en el tercil bajo; 'medio' en
    cualquier otro caso con dato disponible. `None` si falta algún dato o
    algún corte todavía no existe (walk-forward sin calibrar todavía)."""
    niveles = []
    for col in feature_cols:
        cuts = cuts_by_feature.get(col)
        v = row.get(col)
        if cuts is None or v is None:
            return None
        lo, hi = cuts
        niveles.append("bajo" if v <= lo else ("alto" if v > hi else "medio"))
    if all(n == "alto" for n in niveles):
        return "alto"
    if all(n == "bajo" for n in niveles):
        return "bajo"
    return "medio"


@dataclass
class ExperimentReport:
    nombre: str
    feature_cols: List[str]
    fechas_calibracion: List[str]
    fechas_evaluadas: List[str]
    por_direccion: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    reciente_vs_acumulada: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nombre": self.nombre, "feature_cols": self.feature_cols,
            "n_fechas_calibracion": len(self.fechas_calibracion),
            "rango_calibracion": (self.fechas_calibracion[0], self.fechas_calibracion[-1]) if self.fechas_calibracion else None,
            "n_fechas_evaluadas": len(self.fechas_evaluadas),
            "rango_evaluado": (self.fechas_evaluadas[0], self.fechas_evaluadas[-1]) if self.fechas_evaluadas else None,
            "por_direccion": self.por_direccion,
            "reciente_vs_acumulada": self.reciente_vs_acumulada,
        }


def cuts_for_date(rows: List[Dict[str, Any]], feature_cols: Sequence[str], fecha: str) -> Dict[str, Optional[Sequence[float]]]:
    """Cortes de tercil que se usarían para evaluar `fecha`, calculados
    EXCLUSIVAMENTE con filas ALCISTA de fecha estrictamente anterior --
    utilidad chica y pura, reutilizada por `run_walk_forward_experiment` y
    expuesta acá para poder verificar la propiedad anti-leakage de forma
    aislada en los tests (los cortes de una fecha no pueden depender de
    ninguna fila de esa fecha ni de fechas posteriores)."""
    historial = [r for r in rows if r["date"] < fecha and r.get("direction") == "ALCISTA"]
    return {col: _tercile_cuts([r[col] for r in historial if r.get(col) is not None]) for col in feature_cols}


def run_walk_forward_experiment(
    rows: List[Dict[str, Any]],
    feature_cols: Sequence[str],
    nombre: str,
    min_calibration_dates: int = MIN_CALIBRATION_DATES_DEFAULT,
    dias_reciente: int = DIAS_RECIENTE_DEFAULT,
) -> ExperimentReport:
    """`rows`: filas ya unidas de features+outcome (una por symbol+date), con
    al menos `date`, `direction`, `max_advance_pct`, y las columnas de
    `feature_cols`. Los cortes de tercil para la fecha D se calculan
    exclusivamente con filas ALCISTA de fecha < D -- nunca con la fecha D
    misma ni posteriores (walk-forward real, ventana expansiva)."""
    fechas = sorted({r["date"] for r in rows})
    if len(fechas) <= min_calibration_dates:
        raise ValueError(
            f"Solo hay {len(fechas)} fechas distintas -- hacen falta más de "
            f"{min_calibration_dates} para reservar calibración y todavía evaluar algo."
        )
    fechas_calibracion = fechas[:min_calibration_dates]
    fechas_evaluables = fechas[min_calibration_dates:]

    por_fecha: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        por_fecha.setdefault(r["date"], []).append(r)

    # acumuladores: direction -> bucket_label -> BucketStats, + serie por
    # fecha evaluada (para reciente vs acumulada)
    acumulado: Dict[str, Dict[str, BucketStats]] = {
        d: {"poblacion_total": BucketStats("poblacion_total"), "alto": BucketStats("alto"),
            "medio": BucketStats("medio"), "bajo": BucketStats("bajo")}
        for d in DIRECTIONS
    }
    serie_por_fecha: List[Dict[str, Any]] = []  # solo ALCISTA, para reciente/acumulada
    fechas_realmente_evaluadas: List[str] = []

    historial_alcista: List[Dict[str, Any]] = []  # crece SOLO con fechas ya pasadas
    for fecha in fechas:
        filas_fecha = por_fecha.get(fecha, [])
        if fecha in fechas_calibracion:
            historial_alcista.extend(r for r in filas_fecha if r.get("direction") == "ALCISTA")
            continue

        # cortes calculados SOLO con historial_alcista (fechas < fecha actual)
        cuts_by_feature = {}
        cuts_ok = True
        for col in feature_cols:
            valores = [r[col] for r in historial_alcista if r.get(col) is not None]
            cuts = _tercile_cuts(valores)
            if cuts is None:
                cuts_ok = False
            cuts_by_feature[col] = cuts

        dia_fila = {"date": fecha, "n": 0, "aciertos_20": 0}
        if cuts_ok:
            fechas_realmente_evaluadas.append(fecha)
            for r in filas_fecha:
                direction = r.get("direction")
                if direction not in DIRECTIONS:
                    continue
                bucket = _bucket_of_row(r, feature_cols, cuts_by_feature)
                acumulado[direction]["poblacion_total"].add(r.get("max_advance_pct"))
                if bucket is not None:
                    acumulado[direction][bucket].add(r.get("max_advance_pct"))
                if direction == "ALCISTA":
                    dia_fila["n"] += 1
                    if (r.get("max_advance_pct") or 0) >= 20:
                        dia_fila["aciertos_20"] += 1
            serie_por_fecha.append(dia_fila)

        # el historial SIEMPRE crece con la fecha que se acaba de evaluar,
        # antes de pasar a la siguiente -- así la próxima fecha ya la ve
        historial_alcista.extend(r for r in filas_fecha if r.get("direction") == "ALCISTA")

    por_direccion = {d: {k: v.to_dict() for k, v in buckets.items()} for d, buckets in acumulado.items()}

    recientes = serie_por_fecha[-dias_reciente:]
    n_rec = sum(f["n"] for f in recientes)
    a_rec = sum(f["aciertos_20"] for f in recientes)
    n_acum = sum(f["n"] for f in serie_por_fecha)
    a_acum = sum(f["aciertos_20"] for f in serie_por_fecha)
    reciente_vs_acumulada = {
        "reciente": {"fechas": [f["date"] for f in recientes], "n": n_rec, "aciertos_20": a_rec,
                     "pct_20": round(100 * a_rec / n_rec, 1) if n_rec else None},
        "acumulada": {"fechas": [f["date"] for f in serie_por_fecha], "n": n_acum, "aciertos_20": a_acum,
                      "pct_20": round(100 * a_acum / n_acum, 1) if n_acum else None},
    }

    return ExperimentReport(
        nombre=nombre, feature_cols=list(feature_cols),
        fechas_calibracion=fechas_calibracion, fechas_evaluadas=fechas_realmente_evaluadas,
        por_direccion=por_direccion, reciente_vs_acumulada=reciente_vs_acumulada,
    )


# ---------------------------------------------------------------------------
# Hipótesis B -- EARLY genuino vs LATE vs antes_del_movimiento (validación
# continua de phase_classifier, no una regla nueva). Misma agrupación se usa
# tanto sobre el histórico (reference_registry) como sobre CAPA 2 en vivo
# (candidate_registry, ver early_vs_late_summary allá).
# ---------------------------------------------------------------------------

def timing_group(timing_deteccion: Optional[str]) -> Optional[str]:
    if timing_deteccion in EARLY_GENUINE:
        return "early_genuino"
    if timing_deteccion in LATE:
        return "late"
    if timing_deteccion in ANTES_DEL_MOVIMIENTO:
        return "antes_del_movimiento"
    return None


def early_vs_late_historical(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """`rows`: filas de daily_features+daily_outcome unidas, con
    `timing_deteccion`, `direction`, `max_advance_pct`. Agrupa en los 3
    grupos de Hipótesis B, separado por dirección -- nunca mezcla BAJISTA
    con ALCISTA."""
    grupos: Dict[str, Dict[str, BucketStats]] = {
        d: {"early_genuino": BucketStats("early_genuino"), "late": BucketStats("late"),
            "antes_del_movimiento": BucketStats("antes_del_movimiento")}
        for d in DIRECTIONS
    }
    for r in rows:
        direction = r.get("direction")
        if direction not in DIRECTIONS:
            continue
        grupo = timing_group(r.get("timing_deteccion"))
        if grupo is None:
            continue
        grupos[direction][grupo].add(r.get("max_advance_pct"))
    return {d: {k: v.to_dict() for k, v in buckets.items()} for d, buckets in grupos.items()}
