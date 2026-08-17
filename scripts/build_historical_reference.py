"""Batch de construcción de la base histórica de referencia (2026-08-15).

DESACOPLADO del ciclo operativo -- corrida manual, offline, igual criterio
que `atlas_live/market_study/run.py`: checkpointeada (resumible si se corta
a mitad de camino), concurrencia acotada, cada símbolo aislado.

Pide ~3 meses de barras diarias por símbolo vía Tradier (normalizado con
`tradier_symbol_map`), calcula features/resultado con anti-leakage estricto
(`atlas_live.reference.daily_reference`) y persiste (`reference_registry`).

Universo (2026-08-17, ampliado a mercado completo -- pedido explícito del
usuario): ya NO se limita al universo Racional. Se usa
`atlas_live.market_study.universe.fetch_broad_universe_meta()` (listados
oficiales NASDAQ Trader, ~13.000 símbolos, ya clasificados por
`classify_instrument_type` en EQUITY/ETF/WARRANT/UNIT/RIGHT/PREFERRED/DEBT)
y se procesan SOLO los EQUITY -- acciones ordinarias, todas las
capitalizaciones, sin filtro de sector. ETFs y derivados quedan excluidos
de este batch (contados, nunca descartados en silencio). Racional
(`racional_symbols()`) es SOLO una etiqueta de operabilidad que viaja con
cada símbolo (`racional_available`) -- nunca decide qué se procesa.

Uso:
    python scripts/build_historical_reference.py --limit 300 --workers 8
"""

import argparse
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from atlas.data.providers.tradier_symbol_map import normalize
from atlas_live.data_fusion.universe_quotes import build_tradier_provider
from atlas_live.market_study import universe as broad_universe
from atlas_live.radar import phase_classifier as pc
from atlas_live.reference import daily_reference as dr
from atlas_live.reference import reference_registry as reg


def _process_one(symbol: str, provider, period: str, identity: Optional[dict] = None,
                  racional_available: Optional[bool] = None) -> dict:
    exchange = (identity or {}).get("exchange")
    name = (identity or {}).get("name")
    query_symbol = normalize(symbol).query_symbol
    try:
        df = provider.get_history(query_symbol, period=period, interval="1d")
    except Exception as exc:
        reg.mark_processed(symbol, "error", 0, 0, note=f"{type(exc).__name__}: {exc}",
                            exchange=exchange, name=name, racional_available=racional_available)
        return {"status": "error"}

    if df is None or df.empty or len(df) < dr.MIN_BASELINE_DAYS + 1:
        reg.mark_processed(symbol, "sin_datos", 0, 0, note=f"solo {len(df) if df is not None else 0} velas",
                            exchange=exchange, name=name, racional_available=racional_available)
        return {"status": "sin_datos"}

    n_feat = n_out = 0
    for idx in range(len(df)):
        feats = dr.compute_features(df, idx)
        timing = None
        if feats is not None:
            percentile_90 = dr.rolling_percentile_abs_change(df, idx)  # solo df[:idx], sin fuga
            tag = pc.from_historical_day(feats.change_pct, percentile_90,
                                          feats.drop_from_peak_10d_pct, feats.rebound_from_trough_pct,
                                          feats.peak_gain_10d_pct)
            timing = tag.timing_deteccion
            if reg.record_features(symbol, feats, timing_deteccion=timing):
                n_feat += 1
        outcome = dr.compute_outcome(df, idx)
        if outcome is not None and reg.record_outcome(symbol, outcome):
            n_out += 1

    reg.mark_processed(symbol, "ok", n_feat, n_out, exchange=exchange, name=name,
                       racional_available=racional_available)
    return {"status": "ok", "n_features": n_feat, "n_outcomes": n_out}


def _recompute_one(symbol: str, provider, period: str) -> dict:
    """Recalcula SOLO `timing_deteccion`/`peak_gain_10d_pct` (UPDATE, nunca
    borra ni reinserta) de un símbolo YA procesado con una versión anterior
    de la regla de clasificación -- 2026-08-15, revisión de 'agotamiento'."""
    query_symbol = normalize(symbol).query_symbol
    try:
        df = provider.get_history(query_symbol, period=period, interval="1d")
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    if df is None or df.empty or len(df) < dr.MIN_BASELINE_DAYS + 1:
        return {"status": "sin_datos"}

    n_updated = 0
    for idx in range(len(df)):
        feats = dr.compute_features(df, idx)
        if feats is None:
            continue
        percentile_90 = dr.rolling_percentile_abs_change(df, idx)
        tag = pc.from_historical_day(feats.change_pct, percentile_90,
                                      feats.drop_from_peak_10d_pct, feats.rebound_from_trough_pct,
                                      feats.peak_gain_10d_pct)
        if reg.update_timing(symbol, feats.date, tag.timing_deteccion, feats.peak_gain_10d_pct):
            n_updated += 1
    return {"status": "ok", "n_updated": n_updated}


def recompute_timing_for_processed(workers: int, delay_ms: int, period: str, batch_timeout_s: int) -> dict:
    """Recorre los símbolos YA en el checkpoint con status 'ok' y corrige
    `timing_deteccion`/`peak_gain_10d_pct` con la regla vigente -- ningún
    símbolo se reinicia ni se borra, solo se corrige esa columna."""
    symbols = reg.processed_ok_symbols()

    provider = build_tradier_provider()
    if provider is None:
        return {"error": "TRADIER_API_TOKEN no configurado"}

    t0 = time.time()
    ok = sin_datos = errores = 0
    total_updated = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {}
        for sym in symbols:
            futures[ex.submit(_recompute_one, sym, provider, period)] = sym
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)
        for fut in as_completed(futures, timeout=batch_timeout_s if batch_timeout_s > 0 else None):
            sym = futures[fut]
            try:
                res = fut.result()
                if res["status"] == "ok":
                    ok += 1
                    total_updated += res.get("n_updated", 0)
                elif res["status"] == "sin_datos":
                    sin_datos += 1
                else:
                    errores += 1
            except Exception:
                errores += 1

    return {
        "simbolos_recomputados": len(symbols), "ok": ok, "sin_datos": sin_datos, "errores": errores,
        "filas_actualizadas": total_updated, "tiempo_s": round(time.time() - t0, 2),
    }


def run_batch(limit: int, workers: int, delay_ms: int, period: str, batch_timeout_s: int) -> dict:
    """Universo = mercado completo (NASDAQ Trader), filtrado a EQUITY
    solamente (2026-08-17) -- ver docstring del módulo. `clasificacion`
    reporta cuántos símbolos de cada tipo trajo la fuente, para que la
    exclusión de ETFs/derivados quede visible y auditable, nunca en
    silencio."""
    meta = broad_universe.fetch_broad_universe_meta()
    clasificacion: Dict[str, int] = {}
    for info in meta.values():
        t = info.get("type", "EQUITY")
        clasificacion[t] = clasificacion.get(t, 0) + 1
    universo = sorted(s for s, info in meta.items() if info.get("type") == "EQUITY")
    racional = broad_universe.racional_symbols()

    ya = reg.processed_symbols()
    pendientes = [s for s in universo if s not in ya][:limit]

    reg.set_meta(universe_total=len(universo), universe_total_bruto=len(meta), clasificacion=clasificacion)

    if not pendientes:
        return {"universo_total": len(universo), "ya_procesados": len(ya), "procesados_esta_corrida": 0,
                "clasificacion": clasificacion, "nota": "universo completo -- nada pendiente"}

    provider = build_tradier_provider()
    if provider is None:
        return {"error": "TRADIER_API_TOKEN no configurado"}

    t0 = time.time()
    ok = sin_datos = errores = 0

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {}
        for sym in pendientes:
            identity = meta.get(sym) or {}
            racional_available = sym.upper() in racional
            futures[ex.submit(_process_one, sym, provider, period, identity, racional_available)] = sym
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)
        for fut in as_completed(futures, timeout=batch_timeout_s if batch_timeout_s > 0 else None):
            sym = futures[fut]
            try:
                res = fut.result()
                if res["status"] == "ok":
                    ok += 1
                elif res["status"] == "sin_datos":
                    sin_datos += 1
                else:
                    errores += 1
            except Exception:
                errores += 1
                reg.mark_processed(sym, "error", 0, 0, note="excepción no capturada en el future")

    return {
        "universo_total": len(universo),
        "universo_total_bruto": len(meta),
        "clasificacion": clasificacion,
        "ya_procesados_antes": len(ya),
        "procesados_esta_corrida": len(pendientes),
        "ok": ok, "sin_datos": sin_datos, "errores": errores,
        "tiempo_s": round(time.time() - t0, 2),
        "conteos_totales": reg.counts(),
        "universo_breakdown_racional": reg.universe_breakdown(),
    }


# ---------------------------------------------------------------------------
# Disparo manual en segundo plano (2026-08-16) -- para que el endpoint admin
# de `server.py` pueda iniciar esto DENTRO del proceso real de Railway (el
# único que tiene el Volume persistente montado), sin bloquear la petición
# HTTP y sin duplicar nada de `run_batch`/`_process_one` de arriba -- los
# reutiliza tal cual. El estado queda persistido vía
# `reference_registry.set_meta()` (mismo mecanismo ya usado por el radar),
# así que sobrevive a un reinicio del proceso -- ver docstring de
# `start_background_build` para qué pasa exactamente en ese caso.
# ---------------------------------------------------------------------------

_build_lock = threading.Lock()
_build_thread: Optional[threading.Thread] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_batch_background(limit: int, workers: int, delay_ms: int, period: str, batch_timeout_s: int) -> None:
    global _build_thread
    reg.set_meta(build_state="RUNNING", build_started_at=_now_iso(), build_finished_at=None, build_error=None)
    try:
        result = run_batch(limit, workers, delay_ms, period, batch_timeout_s)
        if isinstance(result, dict) and result.get("error"):
            reg.set_meta(build_state="ERROR", build_finished_at=_now_iso(), build_error=result["error"])
        else:
            reg.set_meta(build_state="COMPLETED", build_finished_at=_now_iso(), build_last_result=result)
    except Exception as exc:
        reg.set_meta(build_state="ERROR", build_finished_at=_now_iso(), build_error=f"{type(exc).__name__}: {exc}")
    finally:
        with _build_lock:
            _build_thread = None


def start_background_build(
    limit: int = 2600, workers: int = 8, delay_ms: int = 80, period: str = "3mo", batch_timeout_s: int = 3600,
) -> Dict[str, Any]:
    """Punto de entrada para el endpoint admin -- NUNCA se llama solo al
    arrancar el proceso (eso queda a cargo exclusivo de quien dispara el
    endpoint). No-reentrante: si ya hay un hilo vivo, no inicia uno segundo,
    devuelve `started=False`.

    Reinicio de Railway a mitad de camino: el hilo muere con el proceso: el
    Volume ya tiene, checkpointeados, todos los símbolos procesados HASTA
    ese momento (mark_processed corre símbolo por símbolo dentro de
    run_batch, no al final). `build_state` queda "RUNNING" en el meta viejo
    -- informativo, no bloqueante: como el lock es un objeto en memoria del
    PROCESO (se resetea solo con el reinicio), una nueva llamada a este
    endpoint arranca sin problema y `run_batch` retoma exactamente donde
    quedó (salta los símbolos ya en `reference_checkpoint`)."""
    global _build_thread
    with _build_lock:
        if _build_thread is not None and _build_thread.is_alive():
            return {"started": False, "reason": "ya hay una construcción en curso"}
        _build_thread = threading.Thread(
            target=_run_batch_background, args=(limit, workers, delay_ms, period, batch_timeout_s), daemon=True,
        )
        _build_thread.start()
    return {"started": True, "limit": limit, "workers": workers, "delay_ms": delay_ms, "period": period}


def build_status() -> Dict[str, Any]:
    """Estado consultable -- corriendo o no, avance real, errores, cuándo
    terminó. Todo sale de datos reales (`reference_registry`), nada
    inventado si el proceso nunca se disparó."""
    meta = reg.get_meta()
    with _build_lock:
        vivo_en_este_proceso = _build_thread is not None and _build_thread.is_alive()
    return {
        "corriendo_en_este_proceso": vivo_en_este_proceso,
        "build_state": meta.get("build_state", "NUNCA_INICIADO"),
        "build_started_at": meta.get("build_started_at"),
        "build_finished_at": meta.get("build_finished_at"),
        "build_error": meta.get("build_error"),
        "build_last_result": meta.get("build_last_result"),
        "conteos_actuales": reg.counts(),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=300)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--delay-ms", type=int, default=100)
    p.add_argument("--period", default="3mo")
    p.add_argument("--timeout-s", type=int, default=1800)
    p.add_argument("--recompute-timing", action="store_true",
                    help="Solo corrige timing_deteccion/peak_gain_10d_pct de símbolos YA procesados (UPDATE, no reinicia ni borra).")
    args = p.parse_args()

    import json
    if args.recompute_timing:
        result = recompute_timing_for_processed(args.workers, args.delay_ms, args.period, args.timeout_s)
    else:
        result = run_batch(args.limit, args.workers, args.delay_ms, args.period, args.timeout_s)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
