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
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Dict, Optional

from atlas.data.providers.tradier_provider import TradierProvider
from atlas_live.data_fusion.registry import get_default_provider
from atlas_live.data_fusion.universe_quotes import build_tradier_provider, fetch_universe_quotes
from atlas_live.market_study import universe as broad_universe
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

# Hito 5, Fase 5.2 (2026-09-04, autorizado explícitamente): watchdog de
# auto-recuperación -- ver `_watchdog_loop()` más abajo. `WATCHDOG_MAX_REINTENTOS`
# es un límite EXPLÍCITO y duro (nunca reinicio infinito silencioso, pedido
# explícito del usuario) -- una vez alcanzado, el watchdog sigue observando
# pero deja de reiniciar hasta que el hilo vuelva a estar vivo por otra vía.
WATCHDOG_ENABLED = _env_bool("ATLAS_RADAR_WATCHDOG_ENABLED", True)
WATCHDOG_CHECK_SECONDS = _env_float("ATLAS_RADAR_WATCHDOG_CHECK_SECONDS", 60.0)
WATCHDOG_MAX_REINTENTOS = int(_env_float("ATLAS_RADAR_WATCHDOG_MAX_REINTENTOS", 5))
WATCHDOG_BACKOFF_SECONDS = _env_float("ATLAS_RADAR_WATCHDOG_BACKOFF_SECONDS", 30.0)

_lock = threading.Lock()
_stop = threading.Event()
_thread: Optional[threading.Thread] = None
_history = SweepHistory()
_last_quotes: Dict[str, object] = {}

# Estado propio del watchdog -- deliberadamente separado del `_lock`/`_stop`
# del hilo del radar (responsabilidades distintas: uno corre barridos, el
# otro solo vigila si el primero sigue vivo).
_watchdog_lock = threading.Lock()
_watchdog_stop = threading.Event()
_watchdog_thread: Optional[threading.Thread] = None
_watchdog_reintentos_consecutivos = 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_interval(last_duration: Optional[float]) -> float:
    if not last_duration:
        return SWEEP_FLOOR_SECONDS
    target = SWEEP_SAFETY_MARGIN * last_duration
    return max(SWEEP_FLOOR_SECONDS, min(SWEEP_CEILING_SECONDS, target))


def _record_error(etapa: str, exc: Exception, extra_meta: Optional[Dict[str, object]] = None) -> None:
    """Registro de error de última instancia (2026-08-31, blindaje
    posterior al incidente real del EOD -- el traceback de producción
    confirmó que el hilo murió porque el propio manejador de error de
    `run_sweep_once()` lanzó una SEGUNDA excepción al intentar registrar
    la primera). Usado por `run_sweep_once()`, `maybe_run_eod_evaluation()`,
    `_maybe_generate_experience_knowledge()` y el blindaje exterior de
    `_loop()` -- punto único, para no repetir esta protección 4 veces.

    Nunca relanza: ni formatear el traceback, ni leer sesión/fecha, ni
    persistir en `radar_meta` pueden convertirse en una segunda excepción
    sin atrapar. Si la persistencia falla, el intento queda solo en
    stderr (que Railway sí captura) -- nunca se vuelve a intentar de un
    modo que pueda fallar de nuevo."""
    try:
        tb_text = traceback.format_exc()
    except Exception:
        tb_text = "traceback no disponible"
    try:
        print(
            f"[radar_worker] ERROR en {etapa}: {type(exc).__name__}: {exc}\n{tb_text}",
            file=sys.stderr, flush=True,
        )
    except Exception:
        pass
    try:
        session = market_hours.get_session()
    except Exception:
        session = None
    try:
        market_date = market_hours.market_date()
    except Exception:
        market_date = None
    payload: Dict[str, object] = {
        "ultimo_error_etapa": etapa,
        "ultimo_error_tipo": type(exc).__name__,
        "ultimo_error_traceback": tb_text[-4000:],
        "ultimo_error_market_date": market_date,
        "ultimo_error_session": session,
        "ultimo_error_at": _now_iso(),
    }
    if extra_meta:
        payload.update(extra_meta)
    try:
        reg.set_meta(**payload)
    except Exception as meta_exc:
        try:
            print(
                f"[radar_worker] no se pudo persistir el registro de error de '{etapa}': "
                f"{type(meta_exc).__name__}: {meta_exc}",
                file=sys.stderr, flush=True,
            )
        except Exception:
            pass


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

        # Universo de mercado completo (2026-08-17, Fase 5 -- Racional ya
        # NO limita qué escanea el radar, solo etiqueta operabilidad en
        # lectura). Misma fuente/clasificación ya aprobada y probada en
        # scripts/build_historical_reference.py: solo EQUITY, sin ETFs ni
        # derivados mezclados en la misma detección.
        #
        # Excepción quirúrgica (2026-08-20, pedido explícito del usuario,
        # caso real MSTU/ETHU/CONL/BITX): se suman los ETFs APALANCADOS
        # (1x/2x/3x sobre una sola acción/cripto, ver
        # `broad_universe.is_leveraged_etf_name`) -- amplifican
        # directamente el movimiento de su subyacente, la categoría exacta
        # que causó una brecha real (un rally de cripto se movió sobre
        # todo a través de estos ETFs, invisibles para el radar). El resto
        # de los ~4.780 ETFs (bonos, índices pasivos, sectoriales sin
        # apalancamiento) sigue excluido, sin cambios -- no se toca
        # `classify_instrument_type()` ni la Base Histórica.
        meta = broad_universe.fetch_broad_universe_meta()
        symbols = sorted(
            s for s, info in meta.items()
            if info.get("type") == "EQUITY"
            or (info.get("type") == "ETF" and broad_universe.is_leveraged_etf_name(info.get("name")))
        )
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
        # 2026-08-31: el traceback real de producción confirmó que ESTE
        # bloque (antes: un `reg.set_meta()` directo, sin protección
        # propia) fue el que mató al hilo -- leer los contadores y
        # persistir el error quedan blindados por separado, para que
        # ninguno de los dos pueda volver a convertirse en la causa real.
        extra_meta: Dict[str, object] = {"state": "ERROR", "ultimo_error": f"{type(exc).__name__}: {exc}"}
        try:
            meta = reg.get_meta()
            extra_meta["sweeps_total"] = int(meta.get("sweeps_total") or 0) + 1
            extra_meta["sweeps_error"] = int(meta.get("sweeps_error") or 0) + 1
        except Exception:
            pass  # sin los contadores previos no se pueden incrementar -- se omiten, nunca se inventan
        _record_error("run_sweep_once", exc, extra_meta)
        return None
    finally:
        _lock.release()


def _maybe_generate_experience_knowledge(market_date: str) -> None:
    """EXPERIENCIA → CONOCIMIENTO (2026-08-25, Fase 3/5 del circuito de
    aprendizaje, autorizado explícitamente). Se llama SOLO después de que
    `run_eod_evaluation()` ya terminó con éxito para `market_date` (así
    `candidate_outcome.is_final=1` de HOY queda lo más fresco posible
    antes de generar conocimiento) -- una sola vez por `market_date`,
    marcador propio (`conocimiento_generado_para`) independiente del de
    EOD, mismo patrón exacto ya usado para `eod_ejecutado_para`.

    Aislado con su propio try/except, additional a la protección interna
    de `run_experience_learning_cycle()` -- una falla acá (import roto,
    excepción inesperada) NUNCA debe impedir que `maybe_run_eod_evaluation()`
    devuelva `True` ni tumbar el hilo del radar. NO conecta nada a ninguna
    decisión -- solo genera y persiste conocimiento (Fase 1 + Fase 2, sin
    modificarlas)."""
    meta = reg.get_meta()
    if meta.get("conocimiento_generado_para") == market_date:
        return
    try:
        from atlas_live.learning import live_experience_pipeline as lep

        resumen = lep.run_experience_learning_cycle(market_date)
        reg.set_meta(
            conocimiento_generado_para=market_date,
            conocimiento_ultima_ejecucion_at=resumen["ejecutado_at"],
            conocimiento_resumen=resumen,
        )
    except Exception as exc:
        _record_error("experience_knowledge", exc, {"conocimiento_ultimo_error": f"{type(exc).__name__}: {exc}"})


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
        _maybe_generate_experience_knowledge(market_date)
        return True
    except Exception as exc:
        _record_error("maybe_run_eod_evaluation", exc, {"ultimo_error_eod": f"{type(exc).__name__}: {exc}"})
        return False


def _loop() -> None:
    while not _stop.is_set():
        # 2026-08-31: blindaje exterior post-incidente (EOD del 31/08 no
        # se ejecutó porque una excepción no capturada mató este hilo a
        # media mañana, y `maybe_run_eod_evaluation()` solo se llama desde
        # acá). `run_sweep_once()`/`maybe_run_eod_evaluation()` ya tienen
        # su propia protección interna, pero esta capa es la red de
        # última instancia: NINGUNA excepción, venga de donde venga
        # (incluida una que escape de esa protección interna), puede
        # volver a terminar el hilo -- como mucho, se pierde UN ciclo.
        interval = IDLE_RECHECK_SECONDS
        try:
            session = market_hours.get_session()
            if session in ("premarket", "regular"):
                duration = run_sweep_once()
                interval = _next_interval(duration)
            else:
                maybe_run_eod_evaluation()
                interval = IDLE_RECHECK_SECONDS
        except Exception as exc:
            _record_error("loop_outer", exc)
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


def _watchdog_check_and_restart_if_dead() -> Dict[str, object]:
    """Hito 5, Fase 5.2 (2026-09-04, autorizado explícitamente): UN chequeo
    del watchdog, sin dormir -- lógica pura y testeable en aislamiento,
    separada del bucle que la llama cada `WATCHDOG_CHECK_SECONDS`.

    Nunca reinicia si: (a) el radar nunca arrancó (`_thread is None` --
    respeta `ATLAS_RADAR_ENABLED=false`/arranque todavía no ocurrido), o
    (b) ya se alcanzó `WATCHDOG_MAX_REINTENTOS` consecutivos -- en ese caso
    sigue OBSERVANDO (para poder recuperarse solo si el hilo vuelve a estar
    vivo por otra vía) pero deja de reiniciar, dejando constancia explícita
    en `radar_meta` en vez de reintentar en silencio para siempre."""
    global _thread, _watchdog_reintentos_consecutivos
    with _watchdog_lock:
        if _thread is None:
            return {"accion": "sin_hilo_que_vigilar"}

        if _thread.is_alive():
            if _watchdog_reintentos_consecutivos:
                _watchdog_reintentos_consecutivos = 0
                try:
                    reg.set_meta(watchdog_reintentos_consecutivos=0, watchdog_state="OK")
                except Exception:
                    pass
            return {"accion": "hilo_vivo"}

        # El hilo está muerto -- confirmado por `is_alive()`, no supuesto.
        # Fase 5.2, corrección tras auditoría de 5.2 (2026-09-04): si ya se
        # pidió un apagado intencional (`_stop`/`_watchdog_stop`), el hilo
        # muerto es el resultado ESPERADO de `request_stop()`, no una
        # caída real -- reiniciarlo acá "ganaría" una carrera contra un
        # apagado deliberado. `_watchdog_loop()` va a salir solo en su
        # próxima vuelta (chequea `_watchdog_stop` al tope del `while` y en
        # el `.wait()` final), pero esta única llamada en curso podía
        # ejecutarse ANTES de esa vuelta -- se corta acá explícitamente,
        # sin tocar el contador de reintentos ni intentar ningún reinicio.
        if _stop.is_set() or _watchdog_stop.is_set():
            return {"accion": "apagado_intencional_no_reinicia"}

        if _watchdog_reintentos_consecutivos >= WATCHDOG_MAX_REINTENTOS:
            try:
                reg.set_meta(
                    watchdog_state="DETENIDO_LIMITE_REINTENTOS",
                    watchdog_detenido_at=_now_iso(),
                )
            except Exception:
                pass
            return {"accion": "limite_reintentos_alcanzado", "reintentos": _watchdog_reintentos_consecutivos}

        _watchdog_reintentos_consecutivos += 1
        intento_actual = _watchdog_reintentos_consecutivos
        try:
            meta = reg.get_meta()
            reg.set_meta(
                watchdog_reinicios_total=int(meta.get("watchdog_reinicios_total") or 0) + 1,
                watchdog_reintentos_consecutivos=intento_actual,
                watchdog_ultimo_reinicio_at=_now_iso(),
                watchdog_state="REINICIADO",
            )
        except Exception:
            pass

        # `start_universe_radar()` es un no-op mientras `_thread` siga
        # apuntando a la referencia vieja (aunque esté muerta) -- se limpia
        # acá, bajo el mismo lock, para que el reinicio sea real.
        _thread = None
        start_universe_radar()
        return {"accion": "reiniciado", "reintentos": intento_actual}


def _watchdog_loop() -> None:
    """Bucle del watchdog -- nunca puede morir por una excepción de su
    propio chequeo (mismo criterio de blindaje exterior que `_loop()`).
    El intervalo entre chequeos CRECE con reintentos consecutivos
    (backoff explícito: `WATCHDOG_CHECK_SECONDS + WATCHDOG_BACKOFF_SECONDS
    * reintentos`) -- nunca reinicia en ráfaga."""
    while not _watchdog_stop.is_set():
        try:
            resultado = _watchdog_check_and_restart_if_dead()
        except Exception as exc:
            _record_error("watchdog_loop", exc)
            resultado = {"accion": "error_interno_watchdog"}
        reintentos = resultado.get("reintentos") or 0
        intervalo = WATCHDOG_CHECK_SECONDS + (WATCHDOG_BACKOFF_SECONDS * reintentos)
        if _watchdog_stop.wait(intervalo):
            break


def start_radar_watchdog() -> None:
    """Arranca el hilo supervisor una sola vez por proceso. No hace nada
    si el radar (`ATLAS_RADAR_ENABLED=false`) o el watchdog
    (`ATLAS_RADAR_WATCHDOG_ENABLED=false`) están deshabilitados por
    entorno. Llamado desde `server.py` junto a `start_universe_radar()`."""
    global _watchdog_thread
    if not RADAR_ENABLED or not WATCHDOG_ENABLED:
        return
    if _watchdog_thread is not None:
        return
    _watchdog_stop.clear()
    _watchdog_thread = threading.Thread(target=_watchdog_loop, daemon=True, name="universe_radar_watchdog")
    _watchdog_thread.start()


def request_stop() -> None:
    _stop.set()
    _watchdog_stop.set()


def get_last_quotes() -> Dict[str, object]:
    return dict(_last_quotes)


def get_symbol_sweep_history(symbol: str):
    """Historial de barridos de HOY para un símbolo (`SweepHistory`, ya
    poblado por `candidate_tracker.process_sweep()` para TODO el universo,
    no solo candidatas -- confirmado por auditoría 2026-08-25). Solo
    lectura -- para señales de observabilidad (PM-RVOL) que necesitan el
    volumen acumulado de barridos anteriores sin llamar a ningún
    proveedor. El más reciente queda último en la lista devuelta."""
    return _history.get(symbol)


RACIONAL_MOVERS_THRESHOLDS = (20, 30, 40, 50, 100)


def racional_movers_report(thresholds=RACIONAL_MOVERS_THRESHOLDS) -> Dict[str, object]:
    """Barrido de solo lectura sobre el universo Racional completo (2026-08-19,
    pedido explícito del usuario: "cuando cierre el mercado, que Atlas
    haga un barrido a todas las acciones de Racional y me diga cuántas
    subieron sobre 30/40/50%"). Pensado para correr DESPUÉS del cierre del
    mercado regular, para ver el resultado real del día completo.

    Hace un barrido FRESCO vía Tradier (`fetch_universe_quotes()`, la
    MISMA función ya usada y probada por `run_sweep_once()` para el
    universo completo) -- deliberadamente NO reutiliza `get_last_quotes()`
    (memoria del proceso): esa memoria se pierde en cualquier reinicio del
    contenedor (deploy), y el radar deja de barrer fuera de premarket/
    regular, así que después del cierre podría quedar vacía o desactualizada
    para siempre hasta el otro día -- un barrido fresco a pedido es la
    única forma confiable de que este reporte funcione siempre que se
    llame, sin depender de cuándo fue el último deploy.

    `change_percent` de cada Quote ya es el cambio vs. cierre anterior
    (mismo campo usado en todo el proyecto). Bandas ACUMULATIVAS (>=
    umbral), mismo criterio que `candidate_registry.explosion_bands_tradier()`.
    No depende de que Atlas haya detectado el símbolo -- barre TODO el
    universo Racional, detectado o no, a diferencia de `/api/radar-movers`."""
    from atlas.data.universe import get_equities, get_etfs
    from atlas_live.data_fusion.universe_quotes import fetch_universe_quotes

    racional = sorted({a.symbol for a in get_equities() + get_etfs()})
    # fallback_provider=None (2026-08-19, mismo criterio ya documentado y
    # validado para el radar principal, ver docstring del módulo): el
    # respaldo Yahoo/Finnhub agrega ~150s extra por símbolo no resuelto
    # sin sumar resultados nuevos -- se desactiva acá también.
    result = fetch_universe_quotes(racional, fallback_provider=None)

    movers = []
    for ticker, q in result.quotes.items():
        change_pct = getattr(q, "change_percent", None)
        if change_pct is None:
            continue
        movers.append({"ticker": ticker, "change_pct": change_pct, "price": getattr(q, "last_price", None)})

    bandas: Dict[str, object] = {}
    for t in thresholds:
        casos = sorted((m for m in movers if m["change_pct"] >= t), key=lambda m: -m["change_pct"])
        bandas[str(t)] = {"n": len(casos), "movers": casos}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_racional_total": len(racional),
        "n_racional_con_dato": len(movers),
        "bandas_acumulativas": bandas,
    }


def status() -> Dict[str, object]:
    return reg.radar_status()
