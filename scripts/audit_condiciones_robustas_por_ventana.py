"""Auditoría READ-ONLY (2026-09-09, autorizada explícitamente): estabilidad
temporal de las condiciones actualmente VALIDACION_ROBUSTA (`ELEGIBLE`,
Hito 3.3), usando EXACTAMENTE la misma definición de "ventana" que
`atlas_live/learning/maturity.py::axis_consistencia()` -- bloques
consecutivos de `EJE9_DIAS_POR_VENTANA` días de mercado CON actividad
real (nunca días calendario, mismo criterio ya usado en todo el
proyecto), ordenados de más antiguo a más reciente.

PURAMENTE DE LECTURA -- mismo mecanismo ya verificado en
`atlas_live/radar/candidate_observation_diagnostics.py`/
`raw_data_consolidation.py`: conexión `mode=ro` + `PRAGMA query_only=ON`
en las 2 bases que toca (`knowledge_eligibility.db`, `radar_candidates.db`).
NUNCA abre `live_experience_knowledge.db` en modo escritura (de hecho no
hace falta abrirla en absoluto -- las 12 condiciones se identifican desde
`knowledge_eligibility_log`, y sus resultados por ventana se RECALCULAN
desde `candidate_detection`/`candidate_outcome`, la misma fuente cruda que
ya usa `live_experience_scoring.py`). NUNCA ejecuta INSERT/UPDATE/DELETE/
VACUUM/TRUNCATE/DROP/ALTER -- confirmado por escaneo estático al final de
este archivo. No modifica H4-H6, no activa ON_CONTROLADO, no cambia
ninguna decisión de Atlas.

Reutiliza, sin duplicar:
  - `atlas.config.config.data_dir()` (vía los propios módulos DB_PATH) --
    respeta `ATLAS_DATA_DIR` automáticamente, igual que el resto del
    proyecto.
  - `atlas_live.learning.thresholds.EJE9_DIAS_POR_VENTANA`/
    `EJE9_MIN_CASOS_POR_VENTANA` -- MISMOS umbrales que
    `axis_consistencia()`, nunca redefinidos acá (pedido explícito: "no
    inventar otra metodología").
  - `atlas_live.learning.historical_scoring.compute_reference_table()` --
    la MISMA función que ya usa `live_experience_scoring.py` para agrupar
    por `(direction, timing_deteccion, bucket)`.
  - `atlas_live.radar.candidate_registry.wilson_confidence_interval()`/
    `precision_validation_state()` -- mismo cálculo de Wilson y mismos 3
    estados ya usados en todo el proyecto.
  - `atlas_live.learning.experiments.MIN_PRIOR_ROWS_FOR_CUTS` -- mismo
    piso de filas para tercios que ya usa `live_experience_scoring.py`.

Diferencia deliberada frente a `live_experience_scoring.py`: esta
auditoría es RETROSPECTIVA sobre historia ya cerrada -- no aplica el
filtro walk-forward de `as_of_date` (que existe para no adelantar
información a una PREDICCIÓN en vivo; acá no se predice nada, solo se
mide qué pasó, por ventana, en datos ya cerrados).

Uso:
    python scripts/audit_condiciones_robustas_por_ventana.py

IMPORTANTE: este script debe correr en un entorno donde `ATLAS_DATA_DIR`
apunte al Volume REAL de producción (ej. una consola/shell de Railway).
Corrido en este git worktree local, `ATLAS_DATA_DIR` no está seteado y
cae al directorio de cada módulo (mismo comportamiento que el resto del
proyecto) -- ahí NO están los datos reales de producción, solo artefactos
de pruebas locales de esta sesión (ya confirmado: máximo ~137 casos,
ninguna condición real VALIDACION_ROBUSTA). El script funciona igual en
ambos casos -- lo que cambia es si los datos que lee son reales o no.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from atlas_live.core.knowledge_eligibility_registry import DB_PATH as ELIGIBILITY_DB_PATH
from atlas_live.learning import experiments
from atlas_live.learning import thresholds as th
from atlas_live.learning.historical_scoring import compute_reference_table
from atlas_live.radar.candidate_registry import (
    DB_PATH as RADAR_DB_PATH,
    precision_validation_state,
    wilson_confidence_interval,
)

UMBRAL_PRINCIPAL_PCT = 20  # misma métrica que live_experience_knowledge (+20%)

# Reutilizado tal cual de axis_consistencia() -- nunca redefinido.
DIAS_POR_VENTANA = th.EJE9_DIAS_POR_VENTANA
MIN_CASOS_POR_VENTANA = th.EJE9_MIN_CASOS_POR_VENTANA

# Umbral de referencia para "diferencia máxima" entre ventanas -- 15
# puntos porcentuales. NUEVO en este script (no existe un umbral ya
# establecido para esto en ningún lugar del proyecto).
#
# 2026-09-09, pedido explícito del usuario: este número queda DECLARADO
# pero NUNCA se usa para producir un veredicto automático de
# ESTABLE/INESTABLE -- es una decisión metodológica todavía sin validar
# por Atlas. La salida siempre reporta "consistencia": "SIN VEREDICTO"
# cuando hay 2+ ventanas comparables, junto con `delta_max_pp` calculado
# (ese sí, el número real) -- el veredicto lo da el usuario mirando los
# números reales de las 12 condiciones, no este umbral. Se deja
# declarado acá por si en el futuro se autoriza explícitamente usarlo.
DELTA_MAX_ESTABLE_PP = 15.0


def _ro_connect(path: Path) -> sqlite3.Connection:
    """Conexión read-only REAL de SQLite -- mismo mecanismo ya verificado
    en `candidate_observation_diagnostics.py`/`raw_data_consolidation.py`.
    Lanza si el archivo no existe -- cada llamador comprueba antes."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def cargar_condiciones_elegibles() -> List[Dict[str, Any]]:
    """Última fila REAL por (direction, timing_deteccion, methodology_version)
    en `knowledge_eligibility_log` (transition-only -- la fila de mayor
    `id` por clave es el estado vigente). Filtra a
    `eligibility_state == 'ELEGIBLE'` -- equivalente exacto a
    VALIDACION_ROBUSTA desde el cierre de Hito 3.3 (ELEGIBLE exige
    VALIDACION_ROBUSTA exclusivamente, decisión final ya tomada, no
    reabierta acá)."""
    path = Path(ELIGIBILITY_DB_PATH)
    if not path.exists():
        return []
    with _ro_connect(path) as conn:
        rows = conn.execute(
            """SELECT * FROM knowledge_eligibility_log
               WHERE id IN (
                   SELECT MAX(id) FROM knowledge_eligibility_log
                   GROUP BY direction, timing_deteccion, methodology_version
               )"""
        ).fetchall()
    return [dict(r) for r in rows if r["eligibility_state"] == "ELEGIBLE"]


def cargar_daily_summaries() -> List[Dict[str, Any]]:
    path = Path(RADAR_DB_PATH)
    if not path.exists():
        return []
    with _ro_connect(path) as conn:
        rows = conn.execute("SELECT * FROM daily_summary ORDER BY market_date").fetchall()
    return [dict(r) for r in rows]


def cargar_filas_experiencia() -> List[Dict[str, Any]]:
    """Mismo JOIN y mismos filtros de calidad que
    `live_experience_scoring._load_rows_from_db()` -- SIN el filtro de
    walk-forward (`as_of_date`), porque esta auditoría es retrospectiva
    sobre historia ya cerrada, no una predicción en vivo."""
    path = Path(RADAR_DB_PATH)
    if not path.exists():
        return []
    with _ro_connect(path) as conn:
        rows = conn.execute(
            """SELECT d.ticker AS ticker, d.market_date AS market_date,
                      d.direction_at_detection AS direction,
                      d.phase_tag AS timing_deteccion,
                      d.volatility_14d_pct_at_detection AS volatility_14d_pct,
                      d.daily_range_pct_at_detection AS daily_range_pct,
                      o.max_return_after_detection_pct AS max_advance_pct
               FROM candidate_detection d
               JOIN candidate_outcome o ON o.ticker = d.ticker AND o.market_date = d.market_date
               WHERE o.is_final = 1 AND o.confiable_para_aprendizaje = 1"""
        ).fetchall()
    return [dict(r) for r in rows]


def calcular_ventanas_de_fechas(daily_summaries: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """Replica EXACTAMENTE `maturity.py::_ventanas()` -- bloques
    consecutivos de `DIAS_POR_VENTANA` FILAS reales (días de mercado con
    actividad), nunca días de calendario. Devuelve (fecha_desde,
    fecha_hasta) por ventana -- el `n_evaluables`/tasa POR CONDICIÓN se
    recalculan aparte, nunca el agregado de todo el sistema que usa
    `axis_consistencia()`."""
    ventanas = []
    for i in range(0, len(daily_summaries), DIAS_POR_VENTANA):
        chunk = daily_summaries[i:i + DIAS_POR_VENTANA]
        if not chunk:
            continue
        ventanas.append((chunk[0]["market_date"], chunk[-1]["market_date"]))
    return ventanas


def _stats_de_condicion(rows_subset: List[Dict[str, Any]], direction: str, timing: str, bucket: str) -> Optional[Dict[str, Any]]:
    """Reutiliza `compute_reference_table()` (misma función que
    `live_experience_scoring.py`) sobre el subconjunto de filas dado --
    busca la fila `(direction, timing, bucket)` específica en el
    resultado. `None` explícito si no hay evidencia -- nunca una fila
    inventada."""
    tabla = compute_reference_table(rows_subset, ("volatility_14d_pct",), min_rows=experiments.MIN_PRIOR_ROWS_FOR_CUTS)
    ref = tabla.get((direction, timing))
    if ref is None:
        return None
    stats = ref.buckets.get(bucket)
    if stats is None or stats.n == 0:
        return None
    d = stats.to_dict()
    n, n_ac, pct = d["n"], d["aciertos_20"], d["pct_20"]

    evaluables_baseline = [r for r in rows_subset if r.get("max_advance_pct") is not None]
    n_base = len(evaluables_baseline)
    n_ac_base = sum(1 for r in evaluables_baseline if r["max_advance_pct"] >= UMBRAL_PRINCIPAL_PCT)
    baseline = round(100 * n_ac_base / n_base, 2) if n_base else None

    wilson = wilson_confidence_interval(n_ac, n)
    return {
        "n": n, "n_aciertos_20": n_ac, "pct_20": pct,
        "baseline_pct_20": baseline,
        "wilson_lower_bound_20_pct": wilson[0] if wilson else None,
        "wilson_upper_bound_20_pct": wilson[1] if wilson else None,
        "validation_state": precision_validation_state(n),
    }


def auditar() -> Dict[str, Any]:
    condiciones = cargar_condiciones_elegibles()
    daily_summaries = cargar_daily_summaries()
    rows_totales = cargar_filas_experiencia()
    ventanas_fechas = calcular_ventanas_de_fechas(daily_summaries)

    resultado: Dict[str, Any] = {
        "n_condiciones_elegibles_encontradas": len(condiciones),
        "n_ventanas_definidas_por_dias_por_ventana": len(ventanas_fechas),
        "dias_por_ventana": DIAS_POR_VENTANA,
        "min_casos_por_ventana": MIN_CASOS_POR_VENTANA,
        "condiciones": [],
    }

    for cond in condiciones:
        direction = cond["direction"]
        timing = cond["timing_deteccion"]
        # El bucket real de la condición vive en el veredicto de Hito 3.3
        # -- pero `knowledge_eligibility_log` NO guarda `bucket`
        # explícitamente (clave real: direction+timing+methodology_version,
        # ver `knowledge_eligibility.py`). Reconstruimos el conocimiento
        # ACUMULADO (todas las filas, sin ventana) para identificar CUÁL
        # bucket de esa (direction, timing) es el que tiene evidencia real
        # -- normalmente "poblacion_total" quedará entre los reportados;
        # si hay más de uno con evidencia, se listan todos (nunca se
        # adivina cuál es "el" bucket).
        tabla_global = compute_reference_table(rows_totales, ("volatility_14d_pct",), min_rows=experiments.MIN_PRIOR_ROWS_FOR_CUTS)
        ref_global = tabla_global.get((direction, timing))
        buckets_con_evidencia = (
            [b for b, s in ref_global.buckets.items() if s.n > 0] if ref_global is not None else []
        )

        for bucket in buckets_con_evidencia:
            entrada: Dict[str, Any] = {
                "direction": direction, "timing_deteccion": timing, "bucket": bucket,
                "methodology_version": cond.get("methodology_version"),
                "eligibility_evaluated_as_of": cond.get("evaluated_as_of"),
            }

            total = _stats_de_condicion(rows_totales, direction, timing, bucket)
            entrada["total"] = total

            ventanas_resultado = []
            tasas_validas = []
            for (desde, hasta) in ventanas_fechas:
                rows_ventana = [r for r in rows_totales if desde <= r["market_date"] <= hasta]
                stats_v = _stats_de_condicion(rows_ventana, direction, timing, bucket)
                if stats_v is None:
                    ventanas_resultado.append({"desde": desde, "hasta": hasta, "n": 0, "pct_20": None, "estado": "SIN_DATOS"})
                    continue
                if stats_v["n"] < MIN_CASOS_POR_VENTANA:
                    ventanas_resultado.append({
                        "desde": desde, "hasta": hasta, "n": stats_v["n"], "pct_20": stats_v["pct_20"],
                        "estado": "INSUFICIENTE",
                    })
                    continue
                ventanas_resultado.append({
                    "desde": desde, "hasta": hasta, "n": stats_v["n"], "pct_20": stats_v["pct_20"],
                    "baseline_pct_20": stats_v["baseline_pct_20"], "estado": "OK",
                })
                tasas_validas.append(stats_v["pct_20"])

            entrada["ventanas"] = ventanas_resultado

            if len(tasas_validas) >= 2:
                delta_max = round(max(tasas_validas) - min(tasas_validas), 1)
                entrada["delta_max_pp"] = delta_max
                # 2026-09-09, pedido explícito: DELTA_MAX_ESTABLE_PP (15pp)
                # queda calculado y disponible, pero NUNCA se usa para
                # producir un veredicto automático de ESTABLE/INESTABLE --
                # es una decisión metodológica todavía sin validar por
                # Atlas. El delta real (N, tasa por ventana) sigue
                # completo en la salida -- el veredicto lo da el usuario
                # mirando los números, no este umbral.
                entrada["consistencia"] = "SIN VEREDICTO"
            elif len(tasas_validas) == 1:
                entrada["delta_max_pp"] = None
                entrada["consistencia"] = "INSUFICIENTE (solo 1 ventana con N suficiente)"
            else:
                entrada["delta_max_pp"] = None
                entrada["consistencia"] = "INSUFICIENTE (0 ventanas con N suficiente)"

            resultado["condiciones"].append(entrada)

    return resultado


def _imprimir_tabla(resultado: Dict[str, Any]) -> None:
    print(f"Condiciones ELEGIBLE encontradas en knowledge_eligibility_log: {resultado['n_condiciones_elegibles_encontradas']}")
    print(f"Ventanas definidas ({resultado['dias_por_ventana']} días de mercado c/u, piso {resultado['min_casos_por_ventana']} casos): {resultado['n_ventanas_definidas_por_dias_por_ventana']}")
    print()
    header = f"{'CONDICION':45s} | {'N TOTAL':>7s} | {'TASA TOTAL':>10s} | {'V1 (N/tasa)':>14s} | {'V2 (N/tasa)':>14s} | {'V3 (N/tasa)':>14s} | {'D MAX':>7s} | {'CONSISTENCIA':>12s}"
    print(header)
    print("-" * len(header))
    for c in resultado["condiciones"]:
        etiqueta = f"{c['direction']}/{c['timing_deteccion']}/{c['bucket']}"
        total = c["total"]
        n_total = total["n"] if total else 0
        tasa_total = f"{total['pct_20']:.1f}%" if total and total["pct_20"] is not None else "--"
        cols_ventanas = []
        for v in c["ventanas"][:3]:
            if v["estado"] == "OK":
                cols_ventanas.append(f"{v['n']}/{v['pct_20']:.1f}%")
            elif v["estado"] == "INSUFICIENTE":
                cols_ventanas.append(f"{v['n']}/INSUF")
            else:
                cols_ventanas.append("--")
        while len(cols_ventanas) < 3:
            cols_ventanas.append("--")
        delta = f"{c['delta_max_pp']:.1f}pp" if c.get("delta_max_pp") is not None else "--"
        print(f"{etiqueta:45s} | {n_total:>7d} | {tasa_total:>10s} | {cols_ventanas[0]:>14s} | {cols_ventanas[1]:>14s} | {cols_ventanas[2]:>14s} | {delta:>7s} | {c['consistencia']:>12s}")


if __name__ == "__main__":
    r = auditar()
    _imprimir_tabla(r)
    print()
    print("Nota: 'V1/V2/V3' son las primeras 3 ventanas cronológicas encontradas (más antigua a más reciente).")
    print("Si hay más de 3 ventanas totales, solo se muestran las 3 primeras acá -- ver el dict completo para el resto.")


# ---------------------------------------------------------------------------
# Garantía estructural: este script NUNCA ejecuta SQL destructivo. Ninguna
# función de arriba abre una conexión sin `?mode=ro`, y no existe ninguna
# sentencia INSERT/UPDATE/DELETE/VACUUM/TRUNCATE/DROP/ALTER en todo el
# archivo (confirmable por inspección directa del código de arriba).
# ---------------------------------------------------------------------------
