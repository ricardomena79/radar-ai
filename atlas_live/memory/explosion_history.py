"""Marcador Histórico de Explosiones (2026-08-09).

Estudia, con datos REALES, cómo se comportaron históricamente las acciones
que explotaron: para cada trayectoria de 5 minutos ya registrada en el Exit
Journal, reconstruye su recorrido por los hitos (+10/+20/+30/+50/+100/+150/
+200%), su máximo real, el fin del impulso y el retroceso posterior.

**Fuente única de verdad**: `exit_journal.trajectory_samples` (serie de
`return_pct` = % de cambio del día, a 5 min). Este módulo NO duplica esos
datos en otra tabla: es una vista derivada, siempre consistente con la
memoria, recalculable. Solo lectura -- no escribe ni toca ningún motor.

**Calidad de datos, explícita (regla absoluta: cero fabricación)**. La
reconstrucción histórica tiene dos patologías reales, medidas en los datos:
  1. **Artefactos**: algunas trayectorias muestran valores imposibles
     (p. ej. +11.886% ya presentes en la primera muestra) -- errores de
     `previous_close`/splits en la reconstrucción, no explosiones reales.
     Se excluyen del estudio y se cuentan aparte (`SANITY_CEILING_PCT`).
  2. **Pre-iniciadas**: otras ya arrancan altas (el movimiento ocurrió
     antes de la primera muestra). Para esas, los hitos y la anticipación
     anteriores a la ventana son "No disponible" -- nunca se inventan.
Solo las trayectorias LIMPIAS (arrancan por debajo de `START_CEILING_PCT` y
alcanzan el hito con el movimiento observado dentro de la ventana) sirven
para medir anticipación; el resto se reporta con su limitación.

**Fin del impulso**: no se asume un criterio. Se calcula un retroceso
RELATIVO respecto del pico (`MOMENTUM_END_DROP_FRAC`, por defecto 20% por
debajo del máximo), sostenido en la muestra siguiente. Se eligió relativo y
no absoluto porque un umbral en puntos porcentuales no escala entre una
explosión de +30% y una de +275%. El parámetro es configurable y queda
documentado en la salida (`rule`), para que el criterio sea reproducible.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from atlas_live.memory import exit_journal as ej
from atlas_live.memory import store

ET = ZoneInfo("America/New_York")

# Hitos de movimiento (en % de cambio del día).
MILESTONES = [10, 20, 30, 50, 100, 150, 200]
# Bandas para agrupar el marcador histórico.
BANDS = [30, 50, 100, 150, 200]  # ">200" se maneja aparte

# --- Umbrales de calidad y de reglas (configurables, documentados) ---
SANITY_CEILING_PCT = 1000.0   # por encima: casi seguro artefacto de reconstrucción
START_CEILING_PCT = 10.0      # first_return por encima -> "pre-iniciada" (start no observado)
MIN_START_FLOOR_PCT = -50.0   # first_return por debajo: dato dudoso (posible artefacto)
MOMENTUM_END_DROP_FRAC = 0.20  # retroceso >= 20% por debajo del pico = fin del impulso


def _et_iso(utc_iso: Optional[str]) -> Optional[str]:
    """Convierte un timestamp ISO (UTC) a hora ET en formato HH:MM:SS, o None."""
    if not utc_iso:
        return None
    try:
        return datetime.fromisoformat(utc_iso).astimezone(ET).strftime("%H:%M:%S")
    except Exception:
        return None


def _minutes_between(a: Optional[str], b: Optional[str]) -> Optional[float]:
    if not a or not b:
        return None
    try:
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 60.0
    except Exception:
        return None


def _analyze_trajectory(symbol: str, date: str) -> Optional[Dict[str, Any]]:
    """Reconstruye el recorrido de UNA trayectoria. Devuelve None si no hay
    ninguna muestra con `return_pct`."""
    samples = [s for s in ej.get_trajectory(symbol, date) if s.get("return_pct") is not None]
    if not samples:
        return None
    samples.sort(key=lambda s: s["sampled_at"])
    series = [(s["sampled_at"], s["return_pct"]) for s in samples]

    first_return = series[0][1]
    peak_idx = max(range(len(series)), key=lambda i: series[i][1])
    max_at, max_return = series[peak_idx]

    # Calidad de datos.
    if max_return > SANITY_CEILING_PCT or first_return < MIN_START_FLOOR_PCT:
        quality = "artefacto"
    elif first_return >= START_CEILING_PCT:
        quality = "pre_iniciada"
    else:
        quality = "limpia"

    # Hito: primer cruce de cada umbral. Si la trayectoria ya arrancó por
    # encima del hito (pre-iniciada), el cruce es anterior a la ventana:
    # "No disponible", no se inventa.
    milestones: Dict[str, Any] = {}
    for m in MILESTONES:
        cruce = next((ts for ts, r in series if r >= m), None)
        if cruce is not None and first_return >= m:
            milestones[str(m)] = {"alcanzado": True, "hora_et": None, "nota": "anterior a la ventana"}
        elif cruce is not None:
            milestones[str(m)] = {"alcanzado": True, "hora_et": _et_iso(cruce), "_utc": cruce}
        else:
            milestones[str(m)] = {"alcanzado": False, "hora_et": None}

    # Inicio del movimiento observado: primera muestra en que cruza +10%
    # DESDE abajo (solo si la trayectoria es limpia; si pre-inició, es None).
    movimiento_inicio_utc = None
    if quality == "limpia":
        movimiento_inicio_utc = next((ts for ts, r in series if r >= 10), None)

    # Fin del impulso: primer retroceso relativo sostenido tras el pico.
    fin_impulso_utc = None
    retroceso_pct = None
    umbral_caida = max_return * (1 - MOMENTUM_END_DROP_FRAC)
    for i in range(peak_idx + 1, len(series) - 1):
        if series[i][1] <= umbral_caida and series[i + 1][1] <= umbral_caida:
            fin_impulso_utc = series[i][0]
            retroceso_pct = max_return - series[i][1]
            break

    # Máximo retroceso observado después del pico (informativo).
    post = [r for _, r in series[peak_idx + 1:]]
    max_retroceso_desde_pico = (max_return - min(post)) if post else None

    # Features disponibles ANTES (snapshot +10 min del Memory Store, si existe).
    snap = store.get_observations(symbol=symbol, date=date)
    features_snapshot = None
    if snap:
        o = snap[0]
        features_snapshot = {k: o.get(k) for k in
                             ("gap_pct", "change_pct", "relative_volume", "dollar_volume",
                              "volatility_score", "market_cap", "price", "checkpoint_minutes")}

    duracion_min = _minutes_between(movimiento_inicio_utc, max_at)

    return {
        "symbol": symbol,
        "date": date,
        "quality": quality,
        "first_return_pct": round(first_return, 1),
        "max_return_pct": round(max_return, 1),
        "max_hora_et": _et_iso(max_at),
        "movimiento_inicio_hora_et": _et_iso(movimiento_inicio_utc),
        "fin_impulso_hora_et": _et_iso(fin_impulso_utc),
        "retroceso_en_fin_impulso_pct": round(retroceso_pct, 1) if retroceso_pct is not None else None,
        "max_retroceso_desde_pico_pct": round(max_retroceso_desde_pico, 1) if max_retroceso_desde_pico is not None else None,
        "duracion_movimiento_min": round(duracion_min, 0) if duracion_min is not None else None,
        "hitos": milestones,
        "features_disponibles_antes": features_snapshot,
        "muestras": len(series),
    }


def build_registry(min_band_pct: float = 30.0) -> Dict[str, Any]:
    """Marcador histórico completo: todas las trayectorias que alcanzaron
    `min_band_pct`, clasificadas por calidad. Devuelve la lista de eventos +
    un resumen de calidad de datos, con n explícito y sin inventar nada."""
    registros = []
    artefactos = 0
    for symbol, date in ej.get_all_symbol_dates():
        r = _analyze_trajectory(symbol, date)
        if r is None:
            continue
        if r["max_return_pct"] < min_band_pct:
            continue
        if r["quality"] == "artefacto":
            artefactos += 1
            continue  # excluido del estudio, contado aparte
        registros.append(r)

    registros.sort(key=lambda r: r["max_return_pct"], reverse=True)
    limpias = [r for r in registros if r["quality"] == "limpia"]
    return {
        "rule": {
            "min_band_pct": min_band_pct,
            "sanity_ceiling_pct": SANITY_CEILING_PCT,
            "start_ceiling_pct": START_CEILING_PCT,
            "fin_impulso": f"retroceso relativo >= {int(MOMENTUM_END_DROP_FRAC*100)}% bajo el pico, sostenido",
            "resolucion_minutos": 5,
        },
        "calidad": {
            "eventos_incluidos": len(registros),
            "limpias_start_observado": len(limpias),
            "pre_iniciadas": len(registros) - len(limpias),
            "artefactos_excluidos": artefactos,
        },
        "eventos": registros,
    }


def summarize_by_band(registry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Marcador ACUMULATIVO por banda: cuántas explosiones alcanzaron AL
    MENOS +30/+50/+100/+150/+200%, con n explícito y estadísticas reales
    (mediana del máximo, máximo absoluto, duración mediana) sobre cada
    grupo. Una banda sin casos -> "No disponible", nunca un 0 disfrazado."""
    import statistics
    reg = registry or build_registry()
    eventos = reg["eventos"]

    resumen = {}
    for band in BANDS:  # 30, 50, 100, 150, 200 (acumulativo: max >= band)
        casos = [e for e in eventos if e["max_return_pct"] >= band]
        if not casos:
            resumen[str(band)] = {"n": 0, "estado": "No disponible"}
            continue
        maxes = [c["max_return_pct"] for c in casos]
        dur = [c["duracion_movimiento_min"] for c in casos if c["duracion_movimiento_min"] is not None]
        resumen[str(band)] = {
            "n": len(casos),
            "mediana_max_pct": round(statistics.median(maxes), 1),
            "max_absoluto_pct": round(max(maxes), 1),
            "mediana_duracion_min": round(statistics.median(dur), 0) if dur else None,
            "duracion_muestra_n": len(dur),
        }
    return {"rule": reg["rule"], "calidad": reg["calidad"], "por_banda_acumulativa": resumen}


def lead_time_stats(registry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Anticipación REAL medida (resolución 5 min): tiempo entre el inicio
    observado del movimiento (+10%) y el hito +30%, solo sobre trayectorias
    LIMPIAS (start observado). Reporta la distribución con n explícito -- NO
    afirma un número de minutos, lo mide. Si n es chico, se dice."""
    import statistics
    reg = registry or build_registry()
    leads = []
    for e in reg["eventos"]:
        if e["quality"] != "limpia":
            continue
        h10 = e["hitos"].get("10", {})
        h30 = e["hitos"].get("30", {})
        t10 = h10.get("_utc")
        t30 = h30.get("_utc")
        lt = _minutes_between(t10, t30)
        if lt is not None and lt >= 0:
            leads.append(lt)
    leads.sort()
    n = len(leads)
    if n == 0:
        return {"n": 0, "estado": "No disponible", "definicion": "tiempo de +10% a +30% (trayectorias limpias)"}
    def pct(p):
        return round(leads[min(n - 1, int(n * p))], 0)
    return {
        "definicion": "minutos entre el primer +10% observado y el +30% (solo trayectorias limpias)",
        "n": n,
        "muestra_suficiente": n >= 30,
        "media_min": round(statistics.mean(leads), 0),
        "mediana_min": round(statistics.median(leads), 0),
        "p25_min": pct(0.25),
        "p75_min": pct(0.75),
        "pct_ge_5min": round(sum(1 for x in leads if x >= 5) / n * 100, 0),
        "pct_ge_10min": round(sum(1 for x in leads if x >= 10) / n * 100, 0),
        "pct_ge_15min": round(sum(1 for x in leads if x >= 15) / n * 100, 0),
    }


def _clean_for_json(registry: Dict[str, Any]) -> Dict[str, Any]:
    """Quita los campos internos `_utc` de los hitos antes de serializar."""
    import copy
    reg = copy.deepcopy(registry)
    for e in reg["eventos"]:
        for m in e["hitos"].values():
            m.pop("_utc", None)
    return reg
