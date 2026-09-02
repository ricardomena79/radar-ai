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
todo lo pesado corre como agregación SQL (`GROUP BY`/`COUNT`/`LAG` sobre
ventana) dentro de SQLite; a Python solo llegan resultados ya reducidos
(cientos de filas como mucho, nunca millones)."""

import sqlite3
import statistics
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

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

def episode_grouping(
    market_dates: Sequence[str] = DIAGNOSTIC_MARKET_DATES,
    windows_seconds: Sequence[int] = _EPISODE_WINDOWS_SECONDS,
) -> Dict[str, Any]:
    ph = _placeholders(market_dates)
    with _ro_connect(sreg.DB_PATH) as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM shadow_candidate_detection WHERE market_date IN ({ph})",
            tuple(market_dates),
        ).fetchone()["n"]

        por_ventana: Dict[str, int] = {}
        for ventana in windows_seconds:
            row = conn.execute(
                f"""
                WITH ordenado AS (
                    SELECT ticker, market_date, detected_at,
                           LAG(detected_at) OVER (
                               PARTITION BY ticker, market_date ORDER BY detected_at
                           ) AS prev_detected_at
                    FROM shadow_candidate_detection
                    WHERE market_date IN ({ph})
                )
                SELECT SUM(
                    CASE WHEN prev_detected_at IS NULL
                              OR (julianday(detected_at) - julianday(prev_detected_at)) * 86400.0 > ?
                         THEN 1 ELSE 0 END
                ) AS episodios
                FROM ordenado
                """,
                (*market_dates, ventana),
            ).fetchone()
            por_ventana[f"ventana_{ventana}s"] = row["episodios"] or 0

    return {
        "nota_metodologica": (
            "APROXIMACION declarada, autorizada explicitamente: este conteo corre "
            "sobre TODAS las filas shadow de la ventana (matched + solo_unified "
            "mezcladas) -- NUNCA se presenta como 'episodios exactos de "
            "solo_unified'. matched es aproximadamente 0.57% del total Unified en "
            "esta muestra (17.650 de 3.112.489, segun el reporte U3-C3 ya "
            "ejecutado) -- sesgo marginal y declarado, aceptado para evitar "
            "retener ~3M tuplas en memoria Python. Metodo: gaps-and-islands via "
            "LAG() por (ticker, market_date) ordenado por detected_at -- una fila "
            "arranca un episodio NUEVO si no tiene fila anterior del mismo ticker/"
            "dia, o si el gap respecto a la anterior excede el umbral."
        ),
        "filas_totales_en_la_ventana": total,
        "episodios_shadow_unified_aproximado_por_ventana": por_ventana,
    }


# --------------------------------------------------------------------------
# B.4 -- Características de los solo_legacy (7.329 casos)
# --------------------------------------------------------------------------

def solo_legacy_characteristics(market_dates: Sequence[str] = DIAGNOSTIC_MARKET_DATES) -> Dict[str, Any]:
    """Reutiliza `detector_comparison.compare_legacy_vs_unified()` día por
    día -- ya construida, ya probada, puramente SELECT (confirmado por
    tests estructurales existentes). Solo extrae `solo_legacy_detalle` de
    cada día y descarta el resto (`matched`/`solo_unified_detalle`, con los
    snapshots shadow adentro) antes de pasar al día siguiente -- mismo
    patrón de `quality_report_aggregated()`."""
    total = 0
    por_sesion: Dict[str, int] = {}
    por_hora_utc: Dict[int, int] = {}
    por_gate: Dict[str, int] = {}
    por_ticker: Dict[str, int] = {}
    con_gate_comparativa = 0
    con_gate_simple_solamente = 0
    sin_ninguna_puerta_registrada = 0

    for market_date in market_dates:
        dia = dc.compare_legacy_vs_unified(market_date)
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
        del dia

    top_tickers = sorted(por_ticker.items(), key=lambda kv: -kv[1])[:20]

    return {
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


# --------------------------------------------------------------------------
# B.5 -- Timing de matched (Legacy antes / Unified antes / simultáneas)
# --------------------------------------------------------------------------

def matched_timing_percentiles(market_dates: Sequence[str] = DIAGNOSTIC_MARKET_DATES) -> Dict[str, Any]:
    legacy_antes: List[float] = []
    unified_antes: List[float] = []
    simultaneas = 0
    todos_abs: List[float] = []

    for market_date in market_dates:
        dia = dc.compare_legacy_vs_unified(market_date)
        for m in dia["matched"]:
            diff = m["diff_seconds"]
            todos_abs.append(abs(diff))
            if diff > 0:
                legacy_antes.append(diff)
            elif diff < 0:
                unified_antes.append(abs(diff))
            else:
                simultaneas += 1
        del dia

    percentiles = (0.25, 0.5, 0.75, 0.9, 0.95, 0.99)
    return {
        "n_matched_total": len(todos_abs),
        "legacy_antes_que_unified": _stats(legacy_antes, percentiles=percentiles),
        "unified_antes_que_legacy": _stats(unified_antes, percentiles=percentiles),
        "simultaneas": simultaneas,
        "diferencia_absoluta_todos": _stats(todos_abs, percentiles=percentiles),
    }


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

def full_report(market_dates: Sequence[str] = DIAGNOSTIC_MARKET_DATES) -> Dict[str, Any]:
    return {
        "market_dates": list(market_dates),
        "b1_volumen_y_distribucion": volume_and_distribution(market_dates),
        "b3_distribucion_por_gate": gates_distribution(market_dates),
        "b4_solo_legacy_caracteristicas": solo_legacy_characteristics(market_dates),
        "b5_timing_matched": matched_timing_percentiles(market_dates),
        "b6_cobertura_estructural_outcome": structural_outcome_coverage(market_dates),
        "b7_episodios_shadow_unified_aproximado": episode_grouping(market_dates),
    }
