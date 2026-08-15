"""Worker de fondo del estudio histórico (2026-08-10).

Corre DENTRO del mismo proceso de Atlas en Railway (no es un segundo servidor
ni un segundo scanner) como un hilo daemon gentil y persistente. Procesa el
universo amplio de a UN símbolo por vez, con pausas, para acumular evidencia
histórica de explosiones toda la noche SIN tumbar el scanner operativo.

Convivencia con el scanner operativo (comparten la IP de Yahoo en Railway):
  - Concurrencia 1 (un request por vez) + pausa configurable entre símbolos.
  - CEDE: si el scanner operativo está escaneando (STATE.scanning), el worker
    espera y no consulta Yahoo en simultáneo -> no le provoca throttling.
  - retry/backoff ante error; los errores no matan el hilo.

Persistencia/reanudación: el progreso vive en market_study.db (checkpoint +
study_meta). Si Railway reinicia, el worker relee el checkpoint y CONTINÚA
desde donde quedó -- nunca reempieza ni duplica.

Se habilita/deshabilita por entorno (ATLAS_STUDY_ENABLED, default OFF / opt-in).
Arranca APAGADO por seguridad: el estudio histórico no debe arrancar hasta
confirmar que el Atlas operativo está sano y hasta diseñar una separación segura
de recursos. Es aditivo y aislado: nunca toca el ranking, HOT, señales ni el
Marcador Histórico.
"""

import os
import threading
import time
from datetime import datetime, timezone

from atlas_live.market_study import explosion_scan
from atlas_live.market_study import study_registry as reg
from atlas_live.market_study import universe


def _env(name, default):
    return os.environ.get(name, default)


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name, default):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "si", "sí")


STUDY_ENABLED = _env_bool("ATLAS_STUDY_ENABLED", False)  # opt-in: apagado hasta confirmar Atlas operativo sano
STUDY_DELAY_SECONDS = _env_float("ATLAS_STUDY_DELAY_SECONDS", 5.0)   # pausa entre símbolos
STUDY_PERIOD = _env("ATLAS_STUDY_PERIOD", "6mo")
STUDY_MAX_RETRIES = _env_int("ATLAS_STUDY_MAX_RETRIES", 3)
STUDY_YIELD_MAX_WAIT = _env_int("ATLAS_STUDY_YIELD_MAX_WAIT", 120)   # espera máx al scanner
STUDY_IDLE_RECHECK_SECONDS = _env_int("ATLAS_STUDY_IDLE_RECHECK", 600)

_thread = None
_stop = threading.Event()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _operational_scanning() -> bool:
    """True si el scanner operativo está escaneando ahora -- para CEDERLE la
    IP de Yahoo y no consultar en simultáneo."""
    try:
        from atlas_live import scan_worker
        return bool(scan_worker.STATE.snapshot().get("scanning"))
    except Exception:
        return False


def _process_one(sym: str, racional: set) -> dict:
    """Procesa un símbolo con retry/backoff. scan_symbol ya aísla sus errores;
    acá se reintenta solo el status 'error' (fallo de red/proveedor)."""
    res = {"status": "error", "explosions": 0}
    for attempt in range(max(1, STUDY_MAX_RETRIES)):
        res = explosion_scan.scan_symbol(sym, racional, period=STUDY_PERIOD)
        if res.get("status") != "error":
            return res
        try:
            m = reg.get_meta()
            reg.set_meta(retries=int(m.get("retries") or 0) + 1)
        except Exception:
            pass
        if _stop.wait(min(30, (2 ** attempt) * 3)):  # backoff; sale si se pide stop
            break
    return res


def study_loop() -> None:
    try:
        # fetch_broad_universe_meta descarga y CACHEA la identidad (exchange +
        # nombre) de todo el universo, para que explosion_scan.lookup_identity
        # la resuelva sin red. La lista de símbolos deriva de esa misma meta.
        meta = universe.fetch_broad_universe_meta()
        universo = sorted(meta)
        racional = universe.racional_symbols()
        reg.set_meta(universe_total=len(universo), provider="yahoo_finance", state="RUNNING")
    except Exception as exc:
        try:
            reg.set_meta(state="ERROR", last_error=f"{type(exc).__name__}: {exc}")
        except Exception:
            pass
        return

    run_start = time.time()
    done_this_run = 0
    while not _stop.is_set():
        try:
            procesados = reg.processed_symbols()
            pendientes = [s for s in universo if s not in procesados]
            if not pendientes:
                reg.set_meta(state="COMPLETE")
                if _stop.wait(STUDY_IDLE_RECHECK_SECONDS):
                    break
                continue

            sym = pendientes[0]

            # CEDER al scanner operativo: no consultar Yahoo mientras escanea.
            waited = 0
            while _operational_scanning() and waited < STUDY_YIELD_MAX_WAIT:
                if _stop.wait(2):
                    break
                waited += 2
            if _stop.is_set():
                break

            res = _process_one(sym, racional)
            reg.mark_processed(sym, res["status"], res.get("explosions", 0), res.get("note"))
            done_this_run += 1
            if res["status"] == "error":
                m = reg.get_meta()
                reg.set_meta(errors=int(m.get("errors") or 0) + 1)

            elapsed_min = max(1e-6, (time.time() - run_start) / 60.0)
            reg.set_meta(state="RUNNING", last_symbol=sym, last_advance_at=_now(),
                         speed_symbols_min=round(done_this_run / elapsed_min, 2))
        except Exception:
            # Un fallo inesperado del cuerpo del loop NO puede matar el worker.
            pass
        if _stop.wait(STUDY_DELAY_SECONDS):
            break

    try:
        if reg.get_meta().get("state") != "COMPLETE":
            reg.set_meta(state="PAUSED")
    except Exception:
        pass


def start_study_worker() -> None:
    """Arranca el worker una sola vez por proceso. No hace nada si está
    deshabilitado por entorno."""
    global _thread
    if not STUDY_ENABLED:
        return
    if _thread is not None:
        return
    _stop.clear()
    _thread = threading.Thread(target=study_loop, daemon=True, name="study_worker")
    _thread.start()


def request_stop() -> None:
    _stop.set()
