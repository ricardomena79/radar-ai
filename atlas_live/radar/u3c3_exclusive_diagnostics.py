"""Diagnóstico temporal de SOLO LECTURA (2026-09-02, autorizado
explícitamente) sobre las detecciones exclusivas de Unified encontradas por
la auditoría U3-C3. Investiga qué representan realmente los ~3,1M de
`solo_unified` -- volumen/distribución, episodios probables (repetición del
mismo candidato en sweeps sucesivos), distribución por puerta, por qué
Legacy detecta casos que Unified pierde, timing de quién detecta primero, y
cobertura estructural de `candidate_outcome` (nunca evaluación de resultado
de una detección puntual -- ver nota en `structural_outcome_coverage()`).

AISLADO A PROPÓSITO -- este módulo nunca se importa desde
`detector_comparison.py` ni desde ningún flujo real de Atlas, para poder
retirarlo después (borrar este archivo + la ruta que lo expone en
`server.py`) sin tocar nada de lo que ya está en producción.

GARANTÍA DE SOLO LECTURA: toda conexión propia de este módulo se abre en
modo read-only REAL de SQLite (`file:...?mode=ro`, verificado empíricamente
-- un intento de escritura falla con `OperationalError: attempt to write a
readonly database`, no depende solo de que el código nunca ejecute
INSERT/UPDATE/DELETE) + `PRAGMA query_only=ON` como defensa adicional
redundante. Las funciones que reutilizan `detector_comparison.compare_legacy_vs_unified()`
(B.4/B.5, por pedido explícito) usan la conexión normal de esa función --
su carácter de solo lectura está garantizado por revisión de código + los
tests estructurales ya existentes (`test_I_J_nunca_escribe_en_tablas_reales`,
`test_K_no_importa_gates_scoring_ni_decision_core`), no por `mode=ro` -- se
declara esta distinción explícitamente, no se oculta.

Ninguna función carga las filas crudas de `shadow_candidate_detection` --
todo lo pesado corre como agregación SQL (`GROUP BY`/`COUNT` en B.1/B.3, o
por grupo chico en B.7, ver más abajo) dentro de SQLite; a Python solo
llegan resultados ya reducidos (cientos de filas como mucho, nunca
millones).

CORRECCIÓN 2026-09-02 (tras un HTTP 500 real en producción, autorizada
explícitamente, sin haber vuelto a ejecutar el endpoint para diagnosticarlo):
diagnóstico por código encontró 2 problemas de eficiencia reales --
B.4/B.5 llamaban a `compare_legacy_vs_unified()` 8 veces (2 funciones × 4
días), cada una recargando y re-deserializando el día COMPLETO de
`shadow_candidate_detection` desde cero; y B.7 ordenaba las 3,1M+ filas
completas con `LAG()` CUATRO veces (una por ventana), sin índice que cubra
`detected_at`, con riesgo real de volcar a un archivo temporal en disco
(los ~5,3 MB libres en `/data` en ese momento). Ambos corregidos en esta
misma revisión -- ver `_compute_solo_legacy_and_timing()` y
`episode_grouping()`."""

import statistics
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence, Tuple

import sqlite3

from atlas_live.radar import candidate_registry as reg
from atlas_live.radar import detector_comparison as dc
from atlas_live.radar import shadow_detector_registry as sreg

# Único rango de fechas que este módulo puede consultar -- hardcodeado,
# nunca aceptado desde el cliente/request.
DIAGNOSTIC_MARKET_DATES: Tuple[str, ...] = ("2026-08-26", "2026-08-27", "2026-08-28", "2026-08-31")

_EPISODE_WINDOWS_SECONDS: Tuple[int, ...] = (30, 60, 180, 300)

_COMPARATIVE_GATES = {"aceleracion", "despertar", "recuperacion", "cambio_de_comportamiento"}
_SIMPLE_GATES = {"cambio_de_precio", "volumen_relativo", "sostenido_premarket"}


def _ro_connect(path: Path) -> sqlite3.Connection:
    """Conexión read-only REAL de SQLite -- ver docstring del módulo."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _placeholders(values: Sequence[str]) -> str:
    return ",".join("?" for _ in values)


def _percentile(ordenados: List[float], p: float) -> float:
    """Percentil por interpolación lineal simple sobre una lista YA
    ordenada -- método explícito y controlado, sin depender de
    `statistics.quantiles()` (cuyo método de interpolación por defecto no
    es el mismo)."""
    if len(ordenados) == 1:
        return ordenados[0]
    k = (len(ordenados) - 1) * p
    f = int(k)
    c = min(f + 1, len(ordenados) - 1)
    if f == c:
        return ordenados[f]
    return ordenados[f] + (ordenados[c] - ordenados[f]) * (k - f)


def _stats(valores: List[float], percentiles: Tuple[float, ...] = (0.5, 0.9, 0.95, 0.99)) -> Dict[str, Any]:
    if not valores:
        return {"n": 0, "media": None, **{f"p{int(p * 100)}": None for p in percentiles}}
    ordenados = sorted(valores)
    out: Dict[str, Any] = {"n": len(ordenados), "media": round(statistics.mean(ordenados), 4)}
    for p in percentiles:
        out[f"p{int(p * 100)}"] = round(_percentile(ordenados, p), 4)
    return out


# --------------------------------------------------------------------------
# B.1 -- Volumen y distribución
# --------------------------------------------------------------------------

def volume_and_distribution(market_dates: Sequence[str] = DIAGNOSTIC_MARKET_DATES) -> Dict[str, Any]:
    ph = _placeholders(market_dates)
    with _ro_connect(sreg.DB_PATH) as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM shadow_candidate_detection WHERE market_date IN ({ph})",
            tuple(market_dates),
        ).fetchone()["n"]

        por_fecha = [
            {"market_date": r["market_date"], "n": r["n"]}
            for r in conn.execute(
                f"SELECT market_date, COUNT(*) AS n FROM shadow_candidate_detection "
                f"WHERE market_date IN ({ph}) GROUP BY market_date ORDER BY market_date",
                tuple(market_dates),
            ).fetchall()
        ]

        top50 = [
            {"ticker": r["ticker"], "n": r["n"]}
            for r in conn.execute(
                f"SELECT ticker, COUNT(*) AS n FROM shadow_candidate_detection "
                f"WHERE market_date IN ({ph}) GROUP BY ticker ORDER BY n DESC LIMIT 50",
                tuple(market_dates),
            ).fetchall()
        ]

        # Agregado COMPLETO por ticker -- acotado por la cantidad de
        # tickers DISTINTOS (miles, no millones); se trae a Python solo
        # para calcular percentiles, nunca las filas crudas.
        conteos_por_ticker = [
            r["n"] for r in conn.execute(
                f"SELECT ticker, COUNT(*) AS n FROM shadow_candidate_detection "
                f"WHERE market_date IN ({ph}) GROUP BY ticker",
                tuple(market_dates),
            ).fetchall()
        ]

    n_tickers = len(conteos_por_ticker)
    stats_ticker = _stats([float(v) for v in conteos_por_ticker], percentiles=(0.5, 0.9, 0.95, 0.99))

    stats_por_dia = _stats([float(f["n"]) for f in por_fecha], percentiles=(0.5, 0.9, 0.95, 0.99))

    top10_sum = sum(t["n"] for t in top50[:10])
    top50_sum = sum(t["n"] for t in top50)

    return {
        "total_filas_unified": total,
        "n_tickers_distintos": n_tickers,
        "distribucion_por_market_date": por_fecha,
        "filas_por_dia_stats": stats_por_dia,
        "top_50_tickers": top50,
        "filas_por_ticker_stats": stats_ticker,
        "concentracion": {
            "pct_del_total_top10_tickers": round(100 * top10_sum / total, 2) if total else None,
            "pct_del_total_top50_tickers": round(100 * top50_sum / total, 2) if total else None,
        },
    }


# --------------------------------------------------------------------------
# B.3 -- Distribución por gate (estado)
# --------------------------------------------------------------------------

def gates_distribution(market_dates: Sequence[str] = DIAGNOSTIC_MARKET_DATES) -> Dict[str, Any]:
    ph = _placeholders(market_dates)
    with _ro_connect(sreg.DB_PATH) as conn:
        try:
            rows = conn.execute(
                f"SELECT json_extract(je.value,'$.gate') AS gate, COUNT(*) AS n "
                f"FROM shadow_candidate_detection, json_each(shadow_candidate_detection.gates_fired) AS je "
                f"WHERE market_date IN ({ph}) GROUP BY gate ORDER BY n DESC",
                tuple(market_dates),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            return {"json1_disponible": False, "error": str(exc), "distribucion_por_gate": []}
    return {
        "json1_disponible": True,
        "distribucion_por_gate": [{"gate": r["gate"], "n": r["n"]} for r in rows],
    }


# --------------------------------------------------------------------------
# B.7 -- Episodios (aproximación declarada: matched + solo_unified mezclados)
# --------------------------------------------------------------------------
#
# CORREGIDO 2026-09-02 (tras el 500 real): la versión anterior ordenaba las
# 3,1M+ filas completas con LAG() CUATRO veces (una por ventana) -- sin
# índice que cubra `detected_at`, riesgo real de volcar a un archivo
# temporal en disco. Rediseño por streaming/particionado, exactamente como
# se autorizó: (1) `SELECT DISTINCT ticker, market_date` -- una consulta
# chica (miles de pares, no millones); (2) por cada par, `SELECT detected_at
# ... WHERE ticker=? AND market_date=? ORDER BY detected_at` -- usa el
# índice `idx_shadow_ticker_date` para el filtro, y el ORDER BY es sobre un
# grupo YA chico (los detected_at de un ticker en un día -- decenas o
# cientos, nunca millones), trivial de ordenar sin archivo temporal; (3)
# gaps-and-islands en Python sobre esa lista chica, calculando las 4
# ventanas en un solo recorrido -- se libera antes del siguiente grupo,
# nunca se retienen los timestamps de todos los tickers simultáneamente.


def _count_episodes_for_group(
    detected_ats_ordenados: List[str], windows_seconds: Sequence[int],
) -> Dict[int, int]:
    """Gaps-and-islands puro sobre los `detected_at` YA ordenados de UN
    grupo (ticker, market_date) -- misma semántica exacta que el `LAG()`
    original: cada fila se compara contra la fila INMEDIATAMENTE anterior
    (no contra el inicio del episodio actual), para cada ventana a la vez
    en un solo recorrido de la lista."""
    conteos = {w: 0 for w in windows_seconds}
    anterior = None
    for ts in detected_ats_ordenados:
        actual = datetime.fromisoformat(ts)
        for w in windows_seconds:
            if anterior is None or (actual - anterior).total_seconds() > w:
                conteos[w] += 1
        anterior = actual
    return conteos


def episode_grouping(
    market_dates: Sequence[str] = DIAGNOSTIC_MARKET_DATES,
    windows_seconds: Sequence[int] = _EPISODE_WINDOWS_SECONDS,
) -> Dict[str, Any]:
    ph = _placeholders(market_dates)
    totales_por_ventana: Dict[int, int] = {w: 0 for w in windows_seconds}

    with _ro_connect(sreg.DB_PATH) as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM shadow_candidate_detection WHERE market_date IN ({ph})",
            tuple(market_dates),
        ).fetchone()["n"]

        pares = [
            (r["ticker"], r["market_date"])
            for r in conn.execute(
                f"SELECT DISTINCT ticker, market_date FROM shadow_candidate_detection "
                f"WHERE market_date IN ({ph})",
                tuple(market_dates),
            ).fetchall()
        ]

        for ticker, market_date in pares:
            filas_grupo = conn.execute(
                "SELECT detected_at FROM shadow_candidate_detection "
                "WHERE ticker=? AND market_date=? ORDER BY detected_at",
                (ticker, market_date),
            ).fetchall()
            detected_ats = [r["detected_at"] for r in filas_grupo]
            conteos_grupo = _count_episodes_for_group(detected_ats, windows_seconds)
            for w in windows_seconds:
                totales_por_ventana[w] += conteos_grupo[w]
            del filas_grupo, detected_ats  # libera antes del siguiente grupo

    return {
        "nota_metodologica": (
            "APROXIMACION declarada, autorizada explicitamente: este conteo corre "
            "sobre TODAS las filas shadow de la ventana (matched + solo_unified "
            "mezcladas) -- NUNCA se presenta como 'episodios exactos de "
            "solo_unified'. matched es aproximadamente 0.57% del total Unified en "
            "esta muestra (17.650 de 3.112.489, segun el reporte U3-C3 ya "
            "ejecutado) -- sesgo marginal y declarado, aceptado para evitar "
            "retener ~3M tuplas en memoria Python. Metodo: gaps-and-islands POR "
            "GRUPO (ticker, market_date), streaming -- nunca un LAG() global "
            "sobre la tabla completa (ver correccion 2026-09-02 en el docstring "
            "del modulo). Una fila arranca un episodio NUEVO si no tiene fila "
            "anterior del mismo ticker/dia, o si el gap respecto a la anterior "
            "excede el umbral."
        ),
        "filas_totales_en_la_ventana": total,
        "n_grupos_ticker_dia_procesados": len(pares),
        "episodios_shadow_unified_aproximados": {
            f"ventana_{w}s": totales_por_ventana[w] for w in windows_seconds
        },
    }


# --------------------------------------------------------------------------
# B.4 (solo_legacy) + B.5 (timing de matched) -- UNA sola pasada por día
# --------------------------------------------------------------------------
#
# CORREGIDO 2026-09-02 (tras el 500 real): el diseño anterior tenía
# `solo_legacy_characteristics()` y `matched_timing_percentiles()` como 2
# funciones independientes, cada una llamando a
# `compare_legacy_vs_unified()` una vez por día -- 8 llamadas totales, cada
# una recargando y re-deserializando el día COMPLETO de
# `shadow_candidate_detection` desde cero (2 `json.loads()` por fila).
# `_compute_solo_legacy_and_timing()` la llama UNA sola vez por día (4
# total) y alimenta ambos acumuladores desde el mismo `dia`, que se
# descarta antes de pasar al día siguiente -- mismo patrón de
# `quality_report_aggregated()`. `compare_legacy_vs_unified()` en sí NO se
# toca.

def _compute_solo_legacy_and_timing(
    market_dates: Sequence[str] = DIAGNOSTIC_MARKET_DATES,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    total = 0
    por_sesion: Dict[str, int] = {}
    por_hora_utc: Dict[int, int] = {}
    por_gate: Dict[str, int] = {}
    por_ticker: Dict[str, int] = {}
    con_gate_comparativa = 0
    con_gate_simple_solamente = 0
    sin_ninguna_puerta_registrada = 0

    legacy_antes: List[float] = []
    unified_antes: List[float] = []
    simultaneas = 0
    todos_abs: List[float] = []

    for market_date in market_dates:
        dia = dc.compare_legacy_vs_unified(market_date)  # UNA vez por día, no 2

        for l in dia["solo_legacy_detalle"]:
            total += 1
            sesion = l.get("session") or "desconocida"
            por_sesion[sesion] = por_sesion.get(sesion, 0) + 1

            detected_at = l.get("detected_at") or ""
            try:
                hora = int(detected_at[11:13])
            except (ValueError, IndexError):
                hora = None
            if hora is not None:
                por_hora_utc[hora] = por_hora_utc.get(hora, 0) + 1

            ticker = l.get("ticker")
            if ticker:
                por_ticker[ticker] = por_ticker.get(ticker, 0) + 1

            gates_de_esta_fila = set()
            for g in (l.get("gates_fired") or []):
                nombre = g.get("gate") if isinstance(g, dict) else None
                if nombre:
                    por_gate[nombre] = por_gate.get(nombre, 0) + 1
                    gates_de_esta_fila.add(nombre)

            if gates_de_esta_fila & _COMPARATIVE_GATES:
                con_gate_comparativa += 1
            elif gates_de_esta_fila & _SIMPLE_GATES:
                con_gate_simple_solamente += 1
            else:
                sin_ninguna_puerta_registrada += 1

        for m in dia["matched"]:
            diff = m["diff_seconds"]
            todos_abs.append(abs(diff))
            if diff > 0:
                legacy_antes.append(diff)
            elif diff < 0:
                unified_antes.append(abs(diff))
            else:
                simultaneas += 1

        del dia  # libera matched/solo_legacy_detalle/solo_unified_detalle de ESTE día

    top_tickers = sorted(por_ticker.items(), key=lambda kv: -kv[1])[:20]

    b4 = {
        "total_solo_legacy": total,
        "por_sesion": por_sesion,
        "por_hora_utc": dict(sorted(por_hora_utc.items())),
        "por_gate_disparada": dict(sorted(por_gate.items(), key=lambda kv: -kv[1])),
        "top_20_tickers": [{"ticker": t, "n": n} for t, n in top_tickers],
        "evidencia_circunstancial_mecanismos": {
            "con_al_menos_una_puerta_comparativa": con_gate_comparativa,
            "solo_puertas_simples": con_gate_simple_solamente,
            "sin_ninguna_puerta_registrada": sin_ninguna_puerta_registrada,
            "nota": (
                "Circunstancial, NO prueba directa de ningun mecanismo. Una "
                "concentracion alta en puertas comparativas (aceleracion/"
                "despertar/recuperacion/cambio_de_comportamiento) es consistente "
                "con la hipotesis 'historial separado y mas corto en Unified' -- "
                "pero no se instrumento el timing real de cada sweep de Unified "
                "para confirmarlo de forma directa."
            ),
        },
    }

    percentiles = (0.25, 0.5, 0.75, 0.9, 0.95, 0.99)
    b5 = {
        "n_matched_total": len(todos_abs),
        "legacy_antes_que_unified": _stats(legacy_antes, percentiles=percentiles),
        "unified_antes_que_legacy": _stats(unified_antes, percentiles=percentiles),
        "simultaneas": simultaneas,
        "diferencia_absoluta_todos": _stats(todos_abs, percentiles=percentiles),
    }

    return b4, b5


def solo_legacy_characteristics(market_dates: Sequence[str] = DIAGNOSTIC_MARKET_DATES) -> Dict[str, Any]:
    """B.4 en solitario (para tests/uso puntual) -- internamente corre la
    misma pasada combinada que B.5; `full_report()` NO llama a esta función
    -- llama a `_compute_solo_legacy_and_timing()` directamente una sola
    vez y usa ambas mitades, para no duplicar el trabajo."""
    b4, _ = _compute_solo_legacy_and_timing(market_dates)
    return b4


def matched_timing_percentiles(market_dates: Sequence[str] = DIAGNOSTIC_MARKET_DATES) -> Dict[str, Any]:
    """B.5 en solitario (para tests/uso puntual) -- ver nota de
    `solo_legacy_characteristics()`."""
    _, b5 = _compute_solo_legacy_and_timing(market_dates)
    return b5


# --------------------------------------------------------------------------
# B.6 -- Cobertura ESTRUCTURAL de candidate_outcome (nunca evaluación de resultado)
# --------------------------------------------------------------------------

def structural_outcome_coverage(market_dates: Sequence[str] = DIAGNOSTIC_MARKET_DATES) -> Dict[str, Any]:
    ph = _placeholders(market_dates)
    with _ro_connect(sreg.DB_PATH) as conn:
        shadow_pairs = {
            (r["ticker"], r["market_date"])
            for r in conn.execute(
                f"SELECT DISTINCT ticker, market_date FROM shadow_candidate_detection "
                f"WHERE market_date IN ({ph})",
                tuple(market_dates),
            ).fetchall()
        }
    with _ro_connect(reg.DB_PATH) as conn:
        outcome_pairs = {
            (r["ticker"], r["market_date"])
            for r in conn.execute(
                f"SELECT DISTINCT ticker, market_date FROM candidate_outcome "
                f"WHERE market_date IN ({ph})",
                tuple(market_dates),
            ).fetchall()
        }

    con_outcome = shadow_pairs & outcome_pairs

    return {
        "nota": (
            "Cobertura ESTRUCTURAL -- cuenta si el PAR (ticker, market_date) "
            "tiene alguna fila en candidate_outcome, sin importar a que evento "
            "puntual corresponde. NUNCA es una evaluacion de resultado de una "
            "deteccion Unified especifica -- candidate_outcome solo se genera "
            "desde candidate_detection (Legacy), nunca desde "
            "shadow_candidate_detection. No se atribuye ningun outcome a una "
            "fila shadow puntual."
        ),
        "total_pares_ticker_dia_con_deteccion_shadow": len(shadow_pairs),
        "con_candidate_outcome_disponible": len(con_outcome),
        "sin_candidate_outcome_disponible": len(shadow_pairs) - len(con_outcome),
        "pct_con_outcome_estructural": (
            round(100 * len(con_outcome) / len(shadow_pairs), 2) if shadow_pairs else None
        ),
    }


# --------------------------------------------------------------------------
# Orquestador -- informe compacto completo
# --------------------------------------------------------------------------
#
# Instrumentación mínima (2026-09-02, tras el 500 real): cada etapa corre
# envuelta en `_run_stage()` -- si lanza una excepción, se registra un
# marcador buscable `[U3C3_DIAGNOSTIC_EXCEPTION] etapa=...` + traceback
# completo a stderr, y la excepción se RELANZA de inmediato -- nunca
# cambia el comportamiento HTTP normal del endpoint (sigue siendo 500, sin
# traceback expuesto al cliente), mismo patrón ya usado en
# `/api/memory-engine`. B.4 y B.5 comparten una sola etapa ("B4_B5") desde
# que se combinaron en `_compute_solo_legacy_and_timing()`.


def _run_stage(etapa: str, fn: Callable[..., Any], *args: Any) -> Any:
    try:
        return fn(*args)
    except BaseException as exc:
        try:
            tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        except Exception:
            tb_text = "traceback no disponible"
        try:
            print(
                f"[U3C3_DIAGNOSTIC_EXCEPTION] etapa={etapa} {datetime.now(timezone.utc).isoformat()} "
                f"tipo={type(exc).__name__} mensaje={exc}\n{tb_text}",
                file=sys.stderr, flush=True,
            )
        except Exception:
            pass  # el registro de un error NUNCA puede convertirse en una causa nueva de fallo
        raise


def full_report(market_dates: Sequence[str] = DIAGNOSTIC_MARKET_DATES) -> Dict[str, Any]:
    resultado: Dict[str, Any] = {"market_dates": list(market_dates)}
    resultado["b1_volumen_y_distribucion"] = _run_stage("B1", volume_and_distribution, market_dates)
    resultado["b3_distribucion_por_gate"] = _run_stage("B3", gates_distribution, market_dates)

    b4, b5 = _run_stage("B4_B5", _compute_solo_legacy_and_timing, market_dates)
    resultado["b4_solo_legacy_caracteristicas"] = b4
    resultado["b5_timing_matched"] = b5

    resultado["b6_cobertura_estructural_outcome"] = _run_stage("B6", structural_outcome_coverage, market_dates)
    resultado["b7_episodios_shadow_unified_aproximado"] = _run_stage("B7", episode_grouping, market_dates)
    return resultado
