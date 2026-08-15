"""Hilo A -- barrido continuo del universo completo (2026-08-14).

Corre DENTRO del mismo proceso de Atlas (no un segundo servidor/scanner),
como hilo daemon independiente del `_refresh_loop` de `scan_worker` --
mismo patrón ya usado y probado por `study_worker`. Activo SOLO durante
premarket y regular (nunca after-hours, nunca fin de semana -- pedido
explícito). Al detectar el cierre del mercado regular, corre la evaluación
de fin de día UNA sola vez (idempotente por `market_date`).

CADENCIA (pedido explícito: no un número arbitrario, documentar la
decisión): auto-ajustada con margen de seguridad 3x sobre el tiempo REAL
medido del barrido anterior -- el mismo criterio ya usado y reportado en la
validación de CAPA 1 (barrido completo medido en ~11-15s -> intervalo
recomendado ~33-45s). Con piso mínimo configurable (`ATLAS_RADAR_SWEEP_FLOOR_SECONDS`,
default 30s) para no depender de una sola medición optimista, y techo
(`ATLAS_RADAR_SWEEP_CEILING_SECONDS`, default 120s) para que un barrido
lento por algún motivo no deje al radar dormido media hora.

No usa el proveedor de respaldo (Yahoo/Finnhub) en el barrido por defecto
-- medido en la sesión de validación en 190-240s con 0 resultados extra
localmente; incluirlo en cada barrido volvería el radar demasiado lento
para su propósito (ver informe de sesión 2026-08-14). Los símbolos no
resueltos por Tradier quedan documentados vía `SymbolTrace` (Capa 1) y
simplemente no llegan a la evaluación de puertas de este ciclo -- decisión
explícita del usuario ("los revisaremos después"), no una pérdida oculta.
Configurable con `ATLAS_RADAR_USE_FALLBACK=true` si se decide más adelante.
"""

import os
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Optional

from atlas.data.providers.tradier_provider import TradierProvider
from atlas.data.universe import get_equities, get_etfs
from atlas_live.data_fusion.registry import get_default_provider
from atlas_live.data_fusion.universe_quotes import build_tradier_provider, fetch_universe_quotes
from atlas_live.memory import market_hours
from atlas_live.radar import candidate_registry as reg
from atlas_live.radar import candidate_tracker as tracker
from atlas_live.radar import eod_report as eod
from atlas_live.radar.sweep_history import SweepHistory


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "si", "sí")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


RADAR_ENABLED = _env_bool("ATLAS_RADAR_ENABLED", True)
RADAR_USE_FALLBACK = _env_bool("ATLAS_RADAR_USE_FALLBACK", False)
SWEEP_FLOOR_SECONDS = _env_float("ATLAS_RADAR_SWEEP_FLOOR_SECONDS", 30.0)
SWEEP_CEILING_SECONDS = _env_float("ATLAS_RADAR_SWEEP_CEILING_SECONDS", 120.0)
SWEEP_SAFETY_MARGIN = _env_float("ATLAS_RADAR_SWEEP_SAFETY_MARGIN", 3.0)
IDLE_RECHECK_SECONDS = _env_float("ATLAS_RADAR_IDLE_RECHECK_SECONDS", 60.0)

_lock = threading.Lock()
_stop = threading.Event()
_thread: Optional[threading.Thread] = None
_history = SweepHistory()
_last_quotes: Dict[str, object] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_interval(last_duration: Optional[float]) -> float:
    if not last_duration:
        return SWEEP_FLOOR_SECONDS
    target = SWEEP_SAFETY_MARGIN * last_duration
    return max(SWEEP_FLOOR_SECONDS, min(SWEEP_CEILING_SECONDS, target))


def run_sweep_once() -> Optional[float]:
    """Corre UN barrido si no hay otro en curso (no-reentrante, mismo
    criterio que `scan_worker.run_scan_once`). Devuelve la duración en
    segundos, o None si no corrió (lock ocupado, fuera de ventana, o sin
    token de Tradier)."""
    if not _lock.acquire(blocking=False):
        return None
    try:
        session = market_hours.get_session()
        if session not in ("premarket", "regular"):
            return None

        tradier_provider = build_tradier_provider()
        if tradier_provider is None:
            reg.set_meta(state="ERROR", ultimo_error="TRADIER_API_TOKEN no configurado -- radar no puede operar sin Tradier")
            return None

        symbols = sorted({a.symbol for a in get_equities() + get_etfs()})
        market_date = market_hours.market_date()

        t0 = time.time()
        fallback = get_default_provider() if RADAR_USE_FALLBACK else None
        result = fetch_universe_quotes(symbols, tradier_provider=tradier_provider, fallback_provider=fallback)
        observed_at = _now_iso()

        proc = tracker.process_sweep(result.quotes, _history, market_date, session, observed_at)

        global _last_quotes
        _last_quotes = dict(result.quotes)

        duration = round(time.time() - t0, 2)
        meta = reg.get_meta()
        reg.set_meta(
            state="RUNNING",
            session_actual=session,
            current_market_date=market_date,
            ultimo_sweep_at=observed_at,
            ultimo_sweep_duracion_s=duration,
            sweeps_total=int(meta.get("sweeps_total") or 0) + 1,
            sweeps_ok=int(meta.get("sweeps_ok") or 0) + 1,
            ultimo_n_candidatas_nuevas=len(proc.n_nuevas_detecciones),
            ultimo_n_evaluados=proc.n_evaluados,
            ultimo_tradier_error=result.diagnostics.tradier_error,
        )
        return duration
    except Exception as exc:  # un barrido roto nunca puede tumbar el hilo
        meta = reg.get_meta()
        reg.set_meta(
            state="ERROR",
            ultimo_error=f"{type(exc).__name__}: {exc}",
            sweeps_total=int(meta.get("sweeps_total") or 0) + 1,
            sweeps_error=int(meta.get("sweeps_error") or 0) + 1,
        )
        return None
    finally:
        _lock.release()


def maybe_run_eod_evaluation() -> bool:
    """Si el mercado regular ya cerró y todavía no se corrió la evaluación
    de HOY, la corre. Idempotente vía `candidate_registry` (una sola vez
    por `market_date`). Devuelve True si corrió."""
    session = market_hours.get_session()
    if session not in ("afterhours", "closed"):
        return False
    market_date = market_hours.market_date()
    meta = reg.get_meta()
    if meta.get("eod_ejecutado_para") == market_date:
        return False
    if not meta.get("current_market_date") == market_date:
        # nunca hubo un barrido hoy (ej. arrancó el proceso después del cierre) -- nada que evaluar
        return False

    tradier_provider = build_tradier_provider()
    if tradier_provider is None:
        return False

    try:
        n_estudiadas = int(meta.get("ultimo_n_evaluados") or 0) or len(_last_quotes) or None
        report = eod.run_eod_evaluation(
            market_date, tradier_provider, last_sweep_quotes=_last_quotes or None, n_estudiadas=n_estudiadas
        )
        reg.set_meta(
            state="EOD_COMPLETO",
            eod_resumen={
                "market_date": report.market_date,
                "n_estudiadas": report.n_estudiadas,
                "n_candidatas": report.n_candidatas,
                "n_senales": report.n_senales,
                "n_evaluadas": report.n_evaluadas,
                "n_aciertos": report.n_aciertos,
                "n_reached_20": report.n_reached_20,
                "n_reached_50": report.n_reached_50,
                "n_reached_100": report.n_reached_100,
                "n_falsas_senales": report.n_falsas_senales,
                "n_deteccion_tardia": report.n_deteccion_tardia,
                "n_direccion_correcta": report.n_direccion_correcta,
                "n_direccion_incorrecta": report.n_direccion_incorrecta,
                "n_mejores_oportunidades": len(report.mejores_oportunidades),
                "n_posibles_no_detectadas": len(report.posibles_no_detectadas),
            },
        )
        return True
    except Exception as exc:
        reg.set_meta(ultimo_error_eod=f"{type(exc).__name__}: {exc}")
        return False


def _loop() -> None:
    while not _stop.is_set():
        session = market_hours.get_session()
        if session in ("premarket", "regular"):
            duration = run_sweep_once()
            interval = _next_interval(duration)
        else:
            maybe_run_eod_evaluation()
            interval = IDLE_RECHECK_SECONDS
        if _stop.wait(interval):
            break


def start_universe_radar() -> None:
    """Arranca el hilo una sola vez por proceso. No hace nada si está
    deshabilitado por entorno (`ATLAS_RADAR_ENABLED=false`)."""
    global _thread
    if not RADAR_ENABLED:
        return
    if _thread is not None:
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="universe_radar")
    _thread.start()


def request_stop() -> None:
    _stop.set()


def get_last_quotes() -> Dict[str, object]:
    return dict(_last_quotes)


def status() -> Dict[str, object]:
    return reg.radar_status()
