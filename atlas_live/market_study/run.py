"""Job BATCH del estudio amplio (2026-08-10) -- CLI, con checkpointing.

DESACOPLADO del scanner operativo 24/7: es una corrida separada, offline. NO
se llama desde run_scan_once ni desde el servidor, para no competir por
recursos ni tumbar Atlas. Se ejecuta a demanda (o programado), procesa un
LOTE de símbolos aún no vistos y persiste incrementalmente. Si se interrumpe,
la próxima corrida CONTINÚA desde el checkpoint -- nunca reempieza de cero ni
duplica.

Estabilidad: concurrencia acotada, pacing entre requests, timeout global del
lote, y cada símbolo aislado (scan_symbol captura sus propios errores).

Uso:
    python -m atlas_live.market_study.run --limit 200 --workers 6 --delay-ms 200
"""

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from atlas_live.market_study import explosion_scan, study_registry, universe


def run_batch(limit: int = 200, workers: int = 6, delay_ms: int = 150,
              period: str = "6mo", batch_timeout_s: int = 1800) -> dict:
    """Procesa hasta `limit` símbolos NO vistos todavía. Devuelve un reporte."""
    universo = universe.fetch_broad_universe()
    ya = study_registry.processed_symbols()
    pendientes = [s for s in universo if s not in ya][:limit]
    if not pendientes:
        return {"universo_total": len(universo), "ya_procesados": len(ya),
                "procesados_esta_corrida": 0, "nota": "universo completo -- nada pendiente"}

    racional = universe.racional_symbols()
    t0 = time.time()
    procesados = explosiones_nuevas = errores = 0

    def _one(sym):
        res = explosion_scan.scan_symbol(sym, racional, period=period)
        study_registry.mark_processed(sym, res["status"], res.get("explosions", 0), res.get("note"))
        return res

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = []
        for sym in pendientes:
            futures.append(ex.submit(_one, sym))
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)
        for fut in as_completed(futures, timeout=batch_timeout_s if batch_timeout_s > 0 else None):
            try:
                res = fut.result()
                procesados += 1
                explosiones_nuevas += res.get("nuevas", 0)
                if res["status"] == "error":
                    errores += 1
            except Exception:
                errores += 1

    return {
        "universo_total": len(universo),
        "ya_procesados_antes": len(ya),
        "procesados_esta_corrida": procesados,
        "explosiones_nuevas": explosiones_nuevas,
        "errores": errores,
        "duracion_s": round(time.time() - t0, 1),
        "checkpoint": study_registry.summary(),
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--delay-ms", type=int, default=150)
    p.add_argument("--period", default="6mo")
    args = p.parse_args()
    rep = run_batch(limit=args.limit, workers=args.workers, delay_ms=args.delay_ms, period=args.period)
    import json
    print(json.dumps(rep, ensure_ascii=False, indent=1))
