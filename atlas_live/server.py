"""Servidor de Atlas Live.

Expone el estado cacheado de `scan_worker` como JSON, y sirve el
dashboard estático. No calcula nada por sí mismo: cada endpoint delega en
`scan_worker`, que a su vez delega en Atlas Core. Cero lógica de negocio
en esta capa.

Uso local: `python -m atlas_live.server` (arranca en http://localhost:5000).
En producción (Railway) el proceso lo levanta gunicorn apuntando a
`atlas_live.server:app`, por lo que el refresco en segundo plano se arranca
a nivel de módulo (más abajo), no solo dentro de `main()` -- gunicorn nunca
llama a `main()`, solo importa `app`.
"""

import os
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, redirect, request, send_from_directory
from flask import got_request_exception

from atlas.data.collectors.data_collector import DataCollector
from atlas_live import evolution_panel, explosive_config, hot_quote, performance_panel, scan_worker
from atlas_live.backtest import seed_import
from atlas_live.data_fusion.registry import get_default_provider
from atlas_live.memory import classifier, exit_journal, explosion_history, live_integration, market_hours, observation_recovery, prediction_journal
from atlas_live.mission_control import heartbeat, timeline
from atlas_live.predictive_engine import prediction_log
from atlas_live.signals import signal_registry, signal_tracker

STATIC_DIR = Path(__file__).parent / "static"

app = Flask(__name__, static_folder=None)

# Instrumentación temporal de diagnóstico (2026-09-01, autorizado
# explícitamente): Railway no está mostrando el traceback real de los 500
# de /api/memory-engine (ni por request-id ni por rango horario) en su
# visor de logs. Se usa la señal `got_request_exception` de Flask --
# puramente observacional, NUNCA cambia la respuesta que recibe el
# cliente (Flask sigue devolviendo exactamente el mismo 500 genérico de
# siempre; esta señal se dispara en paralelo, no reemplaza nada) -- solo
# imprime a stderr (mismo canal que Railway sí captura, patrón ya probado
# esta sesión en radar_worker.py::_record_error()) un marcador buscable +
# el traceback completo. Nunca registra el body/headers de la request
# (podrían traer tokens/datos sensibles) -- solo path, método, tipo de
# excepción, mensaje y el traceback del CÓDIGO (nunca credenciales).
def _log_unhandled_exception(sender, exception, **extra):
    marcador = "MEMORY_ENGINE_EXCEPTION" if request.path == "/api/memory-engine" else "ATLAS_UNHANDLED_EXCEPTION"
    try:
        tb_text = "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))
    except Exception:
        tb_text = "traceback no disponible"
    try:
        print(
            f"[{marcador}] {datetime.now(timezone.utc).isoformat()} "
            f"path={request.path} method={request.method} "
            f"tipo={type(exception).__name__} mensaje={exception}\n{tb_text}",
            file=sys.stderr, flush=True,
        )
    except Exception:
        pass  # el registro de un error NUNCA puede convertirse en una causa nueva de fallo


got_request_exception.connect(_log_unhandled_exception, app)

# Reinicio del aprendizaje anterior (2026-08-15, "Reinicio v2" -- pedido
# explícito del usuario): las 73.123 observaciones "v1" del Memory Store, el
# seed histórico del Exit Journal y las señales/predicciones ya resueltas
# quedaron atadas a una arquitectura anterior (sin Tradier, sin radar de
# universo completo) y pueden no ser representativas -- no deben seguir
# acumulándose. Corre UNA sola vez (marca persistida junto a los datos,
# nunca se repite), con backup automático de cada base antes de vaciarla.
# Antes de seed_import: así, si esto es la primera vez que corre, no hay
# una ventana donde el seed viejo se recupera y el reinicio lo borra recién
# después -- el orden importa para que el resultado final sea siempre "base
# limpia", nunca "recuperado y luego vaciado a medias".
from atlas_live import learning_reset
learning_reset.reset_learning_once()

# Investigación 4 (2026-08-06, ver DECISION_LOG.md): recuperación
# automática de la base oficial -- si el Volume se pierde por completo,
# arrancar el proceso de nuevo la reconstruye sola, de forma acumulativa
# e idempotente, a partir de todos los seeds JSONL ya comiteados. Antes
# de start_background_refresh() para que el Exit Journal ya tenga la
# base histórica cargada cuando arranque el primer ciclo de escaneo.
seed_import.import_all_seeds()

# Recuperación de observaciones live del Memory Store (F5, 2026-08-09): si el
# Volume se perdió, reimporta las observaciones de aprendizaje generadas en
# vivo desde sus JSONL comiteados. Idempotente y aditivo -- si no hay ninguno
# (aún no hubo export), no hace nada. Después de seed_import y antes del
# refresco, para que el primer ciclo ya vea el conocimiento completo.
observation_recovery.import_all()

scan_worker.start_background_refresh()

# Worker del estudio histórico (2026-08-10): hilo de fondo gentil, en ESTE
# mismo proceso (no un segundo servidor/scanner). Acumula evidencia de
# explosiones sobre el universo amplio toda la noche, cediendo al scanner
# operativo y reanudando desde el checkpoint tras un reinicio. Se habilita/
# deshabilita por ATLAS_STUDY_ENABLED.
from atlas_live.market_study import study_worker
study_worker.start_study_worker()

# CAPA 2 -- radar de universo completo (2026-08-14): hilo de fondo propio
# (Hilo A), igual patrón que study_worker -- barre los ~2.575 símbolos del
# universo Racional vía Tradier con cadencia propia (auto-ajustada, no los
# 300s del ciclo de scoring), detecta candidatas con puertas independientes,
# y alimenta _build_watchlist() como fuente PRINCIPAL (los mecanismos
# anteriores -- muestra estratificada, pre-filtro de movers -- se conservan
# como respaldo, no se eliminan). Se habilita/deshabilita por
# ATLAS_RADAR_ENABLED (default: encendido).
from atlas_live.radar import radar_worker
radar_worker.start_universe_radar()
# Hito 5, Fase 5.2 (2026-09-04, autorizado explícitamente): watchdog de
# auto-recuperación -- hilo separado que vigila si `universe_radar` sigue
# vivo y lo reinicia con límite de reintentos/backoff explícito (ver
# `radar_worker._watchdog_loop()`). No cambia nada del comportamiento del
# radar en sí, solo agrega supervisión.
radar_worker.start_radar_watchdog()

# Motor de Catalizadores/Noticias (2026-08-23, plan aprobado -- ver
# ethereal-mixing-anchor.md): hilo de fondo COMPLETAMENTE APARTE del radar
# técnico de arriba -- nunca llamado desde run_sweep_once(), nunca bloquea
# ni depende de él. Degradación segura: sin FINNHUB_API_KEY el hilo igual
# arranca pero cada ciclo se salta sin romper nada. Se habilita/deshabilita
# por ATLAS_CATALYST_WORKER_ENABLED (default: encendido).
from atlas_live.catalyst import catalyst_worker
catalyst_worker.start_catalyst_worker()

# Detector Unificado -- MODO SHADOW (2026-08-26, U3-C2, autorizado
# explícitamente). Completamente aislado: propia SweepHistory, propia
# persistencia (`shadow_unified_detector.db`), nunca escribe en
# `candidate_detection`, nunca alimenta ninguna decisión/score/ranking/UI.
# Cualquier excepción interna se descarta silenciosamente (ver
# `unified_detector._loop()`) -- no puede afectar el resto de Atlas aunque
# falle por completo.
from atlas_live.radar import unified_detector
unified_detector.start_shadow_detector()

# Mercado (2026-08-29, autorizado explícitamente): vista de ranking en
# vivo del universo EQUITY de Racional (1.646 símbolos), ordenado por
# variación del día. Hilo de fondo COMPLETAMENTE AISLADO -- solo lee
# precios vía Tradier y los presenta; nunca escribe en ninguna tabla de
# decisión, nunca es importado por current_top_opportunity.py,
# atlas_decision_core.py, scan_worker.py, radar_worker.py ni ningún
# módulo de aprendizaje. Se habilita/deshabilita por
# ATLAS_MARKET_VIEW_ENABLED (default: encendido).
from atlas_live import market_view
market_view.start_market_view()


@app.route("/")
def index():
    # La raiz servia atlas_live/static/index.html -- una app legacy
    # ("Atlas Live": Radar Explosivo/General/Watchlist/Diagnostico)
    # distinta de la Cabina del Piloto real (cabina/index.html, donde vive
    # Mercado). Redirige para que el dominio siempre abra la Cabina; la
    # app legacy sigue existiendo en disco, solo deja de ser la raiz.
    return redirect("/cabina/index.html")


@app.route("/<path:filename>")
def static_files(filename):
    # Cache-busting de la Cabina (2026-08-21, caso real: el usuario vio un
    # deploy ya confirmado en el servidor -- verificado con curl -- pero su
    # navegador seguía sirviendo el `cabina.js`/`cabina.css` viejo desde
    # caché, porque `index.html` los referencia con la MISMA URL siempre
    # (`src="cabina.js"`, sin query string) y los navegadores cachean ese
    # tipo de recurso agresivamente entre cargas normales de la página,
    # incluso con un F5 simple. Se agrega `?v=<mtime>` -- el timestamp real
    # de modificación de cada archivo -- así la URL cambia sola en cada
    # deploy que toque esos 2 archivos, forzando al navegador a pedir la
    # versión nueva, sin afectar el caching normal entre deploys (mismo
    # archivo = misma URL = sigue sirviendo desde caché sin red de más).
    if filename == "cabina/index.html":
        path = STATIC_DIR / "cabina" / "index.html"
        html = path.read_text(encoding="utf-8")
        js_v = int((STATIC_DIR / "cabina" / "cabina.js").stat().st_mtime)
        css_v = int((STATIC_DIR / "cabina" / "cabina.css").stat().st_mtime)
        html = html.replace('src="cabina.js"', f'src="cabina.js?v={js_v}"')
        html = html.replace('href="cabina.css"', f'href="cabina.css?v={css_v}"')
        return html
    return send_from_directory(STATIC_DIR, filename)


@app.route("/api/ranking")
def api_ranking():
    """Antigüedad de precio recalculada EN CADA REQUEST (2026-08-18,
    punto 4 -- caso real SBLK): `STATE.ranking` puede llevar minutos
    congelado si el ciclo siguiente falló (`_run_scan_once_locked()`
    conserva el último valor bueno a propósito, ver ese docstring) -- acá
    se corrige que nunca se presente como precio actual sin decirlo."""
    snapshot = scan_worker.STATE.snapshot()
    snapshot["ranking"] = [
        scan_worker.apply_serving_freshness_to_ranking_row(r) for r in snapshot.get("ranking", [])
    ]
    return jsonify(snapshot)


@app.route("/api/symbol/<symbol>")
def api_symbol(symbol):
    try:
        detail = scan_worker.get_symbol_detail(symbol.upper())
        return jsonify(detail)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/explosive-diagnostics")
def api_explosive_diagnostics():
    """Diagnóstico del Radar Explosivo: embudo de filtros + tabla de auditoría
    del último escaneo completo. No se pide en el polling normal de
    /api/ranking -- solo cuando el usuario abre la vista Diagnóstico."""
    diagnostics = scan_worker.get_explosive_diagnostics()
    if diagnostics is None:
        return jsonify({"available": False})
    return jsonify({"available": True, **diagnostics})


@app.route("/api/memory-ranking")
def api_memory_ranking():
    """Ranking del Memory Engine (Ranking Score de desempate sobre Radar
    Explosivo) del último ciclo -- Cabina del Piloto, Panel 2 en adelante
    (alimenta también "Radar Completo"). Mismo mecanismo ya validado en
    atlas_live/memory/live_integration.py. La antigüedad de cada precio
    se recalcula EN CADA REQUEST (2026-08-18, punto 4) -- ver
    `apply_serving_freshness_to_memory_candidate`."""
    data = scan_worker.get_memory_ranking()
    data["candidates"] = [
        scan_worker.apply_serving_freshness_to_memory_candidate(c) for c in data.get("candidates", [])
    ]
    return jsonify(data)


@app.route("/api/memory-engine")
def api_memory_engine():
    """Estado real del Memory Engine (Entregables 4-5) -- Cabina del
    Piloto, Panel 9. Reutiliza la evidencia ya cacheada por día en
    `live_integration`, sin recalcular nada nuevo.

    Instrumentación temporal de diagnóstico (2026-09-01, autorizado
    explícitamente): el marcador MEMORY_ENGINE_EXCEPTION vía
    `got_request_exception` (más arriba en este archivo) no apareció en
    los logs de Railway pese a un 500 real reproducido en producción --
    esto captura la excepción DIRECTO en el punto donde ocurre, sin
    depender del mecanismo de señales de Flask. Exclusivamente para
    diagnóstico -- no cambia el comportamiento HTTP (sigue siendo 500,
    el traceback nunca llega al cliente) y vuelve a lanzar la excepción
    de inmediato. No registra headers/body de la request ni ningún dato
    de la base -- solo tipo/mensaje/traceback de la excepción."""
    try:
        return jsonify(live_integration.get_memory_engine_summary())
    except BaseException as exc:
        try:
            tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        except Exception:
            tb_text = "traceback no disponible"
        # Frame exacto donde se originó la excepción (2026-09-01, ampliación
        # autorizada explícitamente): el traceback completo de arriba ya lo
        # incluye, pero se extrae también por separado -- archivo/línea/
        # función/línea de código -- para que quede legible sin tener que
        # parsear el texto completo del traceback a mano.
        origen = "no disponible"
        try:
            frames = traceback.extract_tb(exc.__traceback__)
            if frames:
                ultimo = frames[-1]
                origen = (
                    f"archivo={ultimo.filename} linea={ultimo.lineno} "
                    f"funcion={ultimo.name} operacion={ultimo.line!r}"
                )
        except Exception:
            pass
        try:
            print(
                f"[MEMORY_ENGINE_EXCEPTION] {datetime.now(timezone.utc).isoformat()} "
                f"path=/api/memory-engine tipo={type(exc).__name__} mensaje={exc} "
                f"origen=({origen})\n{tb_text}",
                file=sys.stderr, flush=True,
            )
        except Exception:
            pass  # el registro de un error NUNCA puede convertirse en una causa nueva de fallo
        raise


@app.route("/api/prediction-journal")
def api_prediction_journal():
    """Prediction Journal -- Cabina del Piloto, Panel 10. Ranking sellado
    de hoy (si ya se selló) + el candidato #1 de los últimos días
    sellados. Solo lectura sobre `prediction_journal.py`."""
    today = market_hours.market_date()
    sealed_today = None
    meta = prediction_journal.get_sealed_meta(today)
    if meta is not None:
        preds = prediction_journal.get_sealed_predictions(today)
        top = preds[0] if preds else None
        sealed_today = {
            "sealed_at": meta["sealed_at"],
            "candidate_count": len(preds),
            "top_symbol": top["symbol"] if top else None,
        }
    recent_days = prediction_journal.get_recent_sealed_days(limit=10)
    return jsonify({
        "date": today,
        "sealed_today": sealed_today,
        "recent_days": [
            {
                "date": r["date"],
                "top_symbol": r["symbol"],
                "predicted_probability_pct": r["probability_pct"],
                "result_category": r["result_category"],
                "result_pct": r["result_change_pct"],
                "anticipation_minutes": r["anticipation_minutes"],
            }
            for r in recent_days
        ],
    })


@app.route("/api/exit-journal")
def api_exit_journal():
    """Exit Journal -- Cabina del Piloto, Panel 11. Últimos resúmenes
    objetivos cerrados (sin ningún umbral de salida, ver exit_journal.py)."""
    return jsonify({"summaries": exit_journal.get_recent_summaries(limit=20)})


@app.route("/api/exit-journal/inventory")
def api_exit_journal_inventory():
    """Investigación 4 -- Persistencia y sincronización del conocimiento de
    Atlas (2026-08-06, ver DECISION_LOG.md). Solo lectura: pares
    (symbol, date) que ya existen en la base oficial (este servidor), para
    que `export_seed_delta.py` pueda calcular qué falta sincronizar sin
    tener que traer la base completa. No participa de ningún flujo de
    escritura."""
    pares = exit_journal.get_all_symbol_dates()
    return jsonify({"pairs": pares, "count": len(pares)})


@app.route("/api/performance")
def api_performance():
    """Panel de Desempeño de Atlas (2026-08-07, ver DECISION_LOG.md).
    Nivel 1 (Oportunidad Oficial del Día, Prediction Journal) + Nivel 2
    (Rendimiento histórico, Exit Journal) -- ver performance_panel.py
    para el detalle de cada cálculo. Solo lectura."""
    return jsonify({
        "oportunidad_del_dia": performance_panel.get_daily_opportunity(),
        "rendimiento_global": performance_panel.get_global_performance(),
    })


@app.route("/api/explosion-history")
def api_explosion_history():
    """Marcador Histórico de Explosiones (2026-08-09): estudio real de cómo
    se comportaron las acciones que explotaron (hitos +10..+200%, máximo,
    fin de impulso, anticipación medida), derivado de las trayectorias de 5
    min del Exit Journal. Solo lectura. Separa calidad de datos (limpias /
    pre-iniciadas / artefactos) con n explícito; nada se inventa -- lo que no
    tiene evidencia dice "No disponible"."""
    registry = explosion_history.build_registry()
    return jsonify({
        "por_banda": explosion_history.summarize_by_band(registry),
        "anticipacion": explosion_history.lead_time_stats(registry),
        "grupos": explosion_history.group_study(registry),
        "eventos": explosion_history._clean_for_json(registry)["eventos"],
    })


@app.route("/api/market-study")
def api_market_study():
    """Estudio amplio del mercado (2026-08-10): resumen del conocimiento
    histórico de explosiones acumulado sobre el universo AMPLIO (no solo
    Racional). Solo lectura -- NO dispara el job batch (ese corre aparte, ver
    atlas_live/market_study/run.py). Separa explosiones dentro/fuera de
    Racional con n explícito. Vacío hasta que se corra el estudio."""
    from atlas_live.market_study import study_registry
    return jsonify({
        "status": study_registry.study_status(),
        "summary": study_registry.summary(),
        "top_explosions": study_registry.list_explosions(limit=50),
    })


@app.route("/api/mercado")
def api_mercado():
    """Mercado (2026-08-29, autorizado explícitamente): snapshot ya
    cacheado del ranking en vivo del universo EQUITY de Racional (1.646
    símbolos), ordenado por variación % del día, descendente. Público,
    sin token -- mismo patrón que `/api/radar-universo`. Solo lectura del
    snapshot ya calculado por el hilo de fondo (`market_view.py`) -- este
    endpoint NUNCA dispara una consulta nueva a Tradier, así que el
    frontend puede pedirlo con la frecuencia que quiera (cada 3s) sin
    ningún costo adicional. Completamente aislado de cualquier decisión
    de Atlas -- ver docstring de `atlas_live/market_view.py`."""
    return jsonify(market_view.get_market_snapshot())


@app.route("/api/admin/mercado-cycle-now", methods=["POST"])
def api_admin_mercado_cycle_now():
    """Disparo manual, bajo demanda, de UN ciclo real de Mercado
    (2026-08-31, autorizado explícitamente) -- reutiliza
    `market_view.run_market_cycle_once()` tal cual, sin duplicar su
    lógica. NO reemplaza ni modifica el hilo de fondo automático
    (`market_view.start_market_view()`), que sigue respetando el guard de
    sesión cerrada sin cambios -- Mercado NO se convierte en un worker
    24/7, este endpoint es solo la forma de forzar un ciclo puntual antes
    de que el reloj llegue a sesión activa. No-reentrante: reutiliza el
    mismo lock que ya protege `run_market_cycle_once()` (`_lock.acquire(blocking=False)`)
    -- si ya hay un ciclo en curso (del hilo automático o de otra llamada
    a este mismo endpoint), esta llamada devuelve de inmediato sin
    esperar ni disparar un segundo ciclo simultáneo. Protegido con
    `ATLAS_ADMIN_TOKEN` (mismo patrón fail-closed que el resto de
    `/api/admin/*`, header `X-Admin-Token` o `?token=`)."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    duration = market_view.run_market_cycle_once()
    snap = market_view.get_market_snapshot()
    if duration is None:
        # `None` cubre dos casos reales, nunca se inventa cuál fue: el
        # lock ya estaba tomado (otro ciclo real en curso, ni se intenta
        # uno nuevo) o el ciclo corrió y terminó en excepción real
        # (queda en `ultimo_error`, ya seteado por run_market_cycle_once()).
        motivo = "ciclo_fallido" if snap.get("ultimo_error") else "ciclo_ya_en_curso_no_se_disparo_otro"
        return jsonify({"disparado": False, "motivo": motivo, "snapshot": snap}), 202

    return jsonify({
        "disparado": True,
        "duration_s": duration,
        "total_universe": snap.get("total_universe"),
        "filas_mostradas": len(snap.get("rows", [])),
        "cycles_total": snap.get("cycles_total"),
        "cycles_ok": snap.get("cycles_ok"),
        "cycles_error": snap.get("cycles_error"),
        "session_at_generation": snap.get("session_at_generation"),
    }), 200


@app.route("/api/radar-universo")
def api_radar_universo():
    """Radar de universo completo (CAPA 2, 2026-08-14): estado del Hilo A
    (barridos, última corrida, candidatas de hoy) + la lista de candidatas
    detectadas hoy con sus condiciones EN el momento de detección. Solo
    lectura -- no dispara ningún barrido (eso corre aparte, ver
    atlas_live/radar/radar_worker.py).

    Universo de aprendizaje vs. universo operable (2026-08-18, pedido
    explícito del usuario): `status.candidatas_hoy` (el contador) sigue
    reflejando TODO lo que Atlas detectó y guardó para aprendizaje -- ese
    número no se toca, Atlas sigue escaneando y aprendiendo del mercado
    completo sin límite de Racional. Pero la LISTA `candidatas_hoy` que
    devuelve este endpoint (la que llena la tabla "Candidatas detectadas
    hoy" de la Cabina) SÍ se filtra a `racional_available == True`,
    server-side -- mismo criterio ya usado en `/api/radar-oportunidades`
    (caso real BATL, 2026-08-18): la Cabina es para decisiones de trading,
    nunca debe mostrar algo que no se puede comprar en Racional, aunque
    Atlas lo siga estudiando internamente. `total_detectadas_hoy`/
    `total_disponibles_racional` exponen ambos números para que el filtro
    sea auditable, no silencioso."""
    from atlas_live.memory import market_hours as _mh
    from atlas_live.radar import candidate_registry as radar_registry

    try:
        from atlas.data.universe import is_available
    except Exception:
        is_available = None

    def _es_racional(ticker: str) -> bool:
        if is_available is None:
            return False
        try:
            return bool(is_available(ticker))
        except Exception:
            return False

    market_date = _mh.market_date()
    candidatas = radar_registry.list_candidates_for_date(market_date)
    total_detectadas_hoy = len(candidatas)
    candidatas = [c for c in candidatas if _es_racional(c["ticker"])]
    total_disponibles_racional = len(candidatas)

    return jsonify({
        "status": radar_registry.radar_status(),
        "candidatas_hoy": candidatas,
        "total_detectadas_hoy": total_detectadas_hoy,
        "total_disponibles_racional": total_disponibles_racional,
    })


@app.route("/api/radar-movers")
def api_radar_movers():
    """Investigación de solo lectura (2026-08-18, caso real XOS validado
    externamente con TradingView, +110%): para CADA candidata detectada
    hoy en TODO el universo Tradier (nunca solo Racional -- Atlas debe
    poder mostrar de qué aprendió aunque no se pueda operar en Racional),
    calcula el % real desde el precio de detección hasta el máximo
    efectivamente observado (`candidate_registry.movers_since_detection`,
    ambos ya persistidos, sin inventar ni recalcular nada). `?min_pct=X`
    (default 10.0) filtra el piso; `?date=YYYY-MM-DD` para otro día.
    Público, sin token -- mismo patrón que `/api/radar-universo`."""
    from atlas_live.memory import market_hours as _mh
    from atlas_live.radar import candidate_registry as radar_registry

    try:
        from atlas.data.universe import is_available
    except Exception:
        is_available = None

    def _es_racional(ticker: str):
        if is_available is None:
            return None
        try:
            return bool(is_available(ticker))
        except Exception:
            return None

    market_date = request.args.get("date") or _mh.market_date()
    try:
        min_pct = float(request.args.get("min_pct", 10.0))
    except (TypeError, ValueError):
        min_pct = 10.0

    movers = radar_registry.movers_since_detection(market_date, min_pct=min_pct)
    stages_by_ticker = {a["ticker"]: a for a in radar_registry.current_alert_stages_for_date(market_date)}
    for m in movers:
        stage_row = stages_by_ticker.get(m["ticker"])
        m["stage"] = stage_row["stage"] if stage_row else None
        m["direction"] = stage_row.get("direction") if stage_row else None
        m["racional_available"] = _es_racional(m["ticker"])

    return jsonify({"market_date": market_date, "min_pct": min_pct, "n": len(movers), "movers": movers})


@app.route("/api/racional-movers")
def api_racional_movers():
    """Barrido de solo lectura sobre TODO el universo Racional (2026-08-19,
    pedido explícito del usuario: "cuando cierre el mercado, que Atlas
    haga un barrido a todas las acciones de Racional y me diga cuántas
    subieron sobre 30/40/50%"). A diferencia de `/api/radar-movers` (solo
    candidatas ya DETECTADAS por Atlas), este barre el universo Racional
    COMPLETO, detectado o no -- responde "¿hubo algo que Atlas no vio?".

    Hace un barrido FRESCO vía Tradier en el momento de la request (ver
    `radar_worker.racional_movers_report()`) -- pensado para llamarse
    DESPUÉS del cierre del mercado regular, para ver el resultado real del
    día completo. Puede tardar (~2.575 símbolos vía Tradier, mismo orden
    de magnitud que un barrido normal del radar). `?thresholds=20,30,40,50,100`
    opcional para cambiar las bandas (default las mismas 5). Público, sin
    token -- mismo patrón que `/api/radar-movers` (solo lectura, sin
    escribir ni mutar ningún estado)."""
    thresholds_param = request.args.get("thresholds")
    if thresholds_param:
        try:
            thresholds = tuple(sorted(float(t) for t in thresholds_param.split(",")))
        except ValueError:
            thresholds = radar_worker.RACIONAL_MOVERS_THRESHOLDS
    else:
        thresholds = radar_worker.RACIONAL_MOVERS_THRESHOLDS

    return jsonify(radar_worker.racional_movers_report(thresholds=thresholds))


@app.route("/api/radar-informe-dia")
def api_radar_informe_dia():
    """Informe de cierre del radar (2026-08-14): condiciones en detección vs.
    resultado real posterior, por candidata -- la base del aprendizaje.
    Vacío/parcial hasta que el mercado regular cierre y corra la evaluación
    (`radar_worker.maybe_run_eod_evaluation`, automática, una vez por día)."""
    from atlas_live.memory import market_hours as _mh
    from atlas_live.radar import candidate_registry as radar_registry

    date_param = request.args.get("date") or _mh.market_date()
    resumen_dia = radar_registry.get_daily_summary(date_param)
    return jsonify({
        "market_date": date_param,
        "resumen": radar_registry.get_meta().get("eod_resumen"),
        "resumen_dia": resumen_dia,
        "precision_acumulada": radar_registry.cumulative_precision(),
        "outcomes": radar_registry.list_outcomes_for_date(date_param),
        "candidatas": radar_registry.list_candidates_for_date(date_param),
        # "Que Atlas aprenda" (2026-08-19): movimientos grandes del día que
        # NINGÚN gate detectó -- se calcula y persiste en cada corrida del
        # EOD (ver eod_report.py), vacío hasta que el mercado cierre y
        # corra la evaluación de hoy.
        "missed_movers": radar_registry.list_missed_movers(date_param),
        # Predicción de magnitud (2026-08-20, aprobado por el usuario): cruza
        # cada predicción congelada (`candidate_tracker._tag_magnitud_prediction`)
        # con el resultado real ya cerrado -- "acierto" = el resultado real
        # igualó o superó la predicción. Vacío hasta que haya predicciones
        # congeladas Y resultados finales para ese día.
        "precision_de_magnitud": radar_registry.magnitud_precision_report(date_param),
        "precision_de_magnitud_acumulada": radar_registry.magnitud_precision_report(),
        # Versión Racional (2026-08-23, pedido explícito del usuario: "esa
        # info la quiero en atlas") -- mismo criterio, filtrado al universo
        # operable real, para que el % de acierto se juzgue contra lo que
        # realmente se puede comprar, no contra todo el mercado estudiado.
        "precision_de_magnitud_racional": radar_registry.magnitud_precision_report_racional(date_param),
        "precision_de_magnitud_racional_acumulada": radar_registry.magnitud_precision_report_racional(),
        # Evolución día por día (2026-08-23, pedido explícito del usuario:
        # "tiene q hacerlo todo los dias. para que ese % baje o suba") --
        # responde si la precisión mejora o empeora con el tiempo, no solo
        # un acumulado total.
        "precision_de_magnitud_por_dia": radar_registry.magnitud_precision_by_day(),
        "precision_de_magnitud_por_dia_racional": radar_registry.magnitud_precision_by_day_racional(),
        # Rigor estadístico (2026-08-24, pedido explícito del usuario: "evitar
        # que una muestra pequeña produzca una falsa impresión de precisión")
        # -- ventanas móviles sobre evaluables REALES (no predicciones
        # totales), reutilizando `magnitud_precision_rolling()`. Los campos
        # `validation_state`/`wilson_ci`/`meta_confirmada` ya vienen incluidos
        # dentro de `precision_de_magnitud_acumulada`/`_racional_acumulada`
        # de arriba (extendidos en la misma función), sin endpoint aparte.
        "precision_de_magnitud_ventanas": {
            "ultimas_50": radar_registry.magnitud_precision_rolling(50),
            "ultimas_100": radar_registry.magnitud_precision_rolling(100),
            "ultimas_250": radar_registry.magnitud_precision_rolling(250),
            "ultimas_500": radar_registry.magnitud_precision_rolling(500),
        },
        "precision_de_magnitud_ventanas_racional": {
            "ultimas_50": radar_registry.magnitud_precision_rolling(50, solo_racional=True),
            "ultimas_100": radar_registry.magnitud_precision_rolling(100, solo_racional=True),
            "ultimas_250": radar_registry.magnitud_precision_rolling(250, solo_racional=True),
            "ultimas_500": radar_registry.magnitud_precision_rolling(500, solo_racional=True),
        },
    })


@app.route("/api/radar-alert-stages")
def api_radar_alert_stages():
    """Capa OBSERVACIONAL de ALERTA TEMPRANA (Fase 4, 2026-08-17) -- ventana
    actual (PREPARACION/ALERTA_TEMPRANA/ALERTA_FUERTE/INICIO/CONFIRMACION/
    NO_PERSEGUIR) de cada candidata con alerta hoy, más un conteo por
    ventana. Solo lectura, mismo patrón que `/api/radar-universo`. Nunca
    bloquea ni prioriza candidatas -- ver `atlas_live/radar/alert_stage.py`."""
    from atlas_live.memory import market_hours as _mh
    from atlas_live.radar import candidate_registry as radar_registry

    market_date = _mh.market_date()
    actuales = radar_registry.current_alert_stages_for_date(market_date)
    conteos = {}
    for c in actuales:
        conteos[c["stage"]] = conteos.get(c["stage"], 0) + 1
    return jsonify({
        "market_date": market_date,
        "candidatas_con_alerta": actuales,
        "conteos_por_ventana": conteos,
    })


# Guard de reentrancia (2026-09-03, fix operativo post-deploy de Hito 3.5,
# autorizado explícitamente): el incidente real de este mismo día mostró
# que dos ejecuciones concurrentes de este endpoint -- cada una recorriendo
# ~1.500 candidatas, cada una abriendo varias conexiones SQLite por
# candidata (Fases 3.0/3.3/3.4) -- pueden contender por el mismo archivo en
# modo WAL (`busy_timeout=15000`) y, en conjunto, agotar los 8 threads de
# gunicorn, dejando sin capacidad incluso a endpoints simples ajenos
# (`/api/mercado`). Mismo patrón YA usado en `market_view.py`/
# `radar_worker.py`/`scan_worker.py`/`unified_detector.py`:
# `_lock.acquire(blocking=False)` -- si ya hay una ejecución en curso, el
# segundo request se rechaza de inmediato (429), sin esperar ni un
# milisegundo y sin consumir un thread por minutos. No modifica baseline,
# shadow, elegibilidad (3.3) ni activación (3.5) -- es exclusivamente una
# exclusión mutua alrededor del handler completo, implementada como un
# wrapper delgado para no tener que reindentar el cuerpo real de la
# función (que sigue exactamente igual, ahora en
# `_api_radar_oportunidades_impl()`).
_oportunidades_lock = threading.Lock()


@app.route("/api/radar-oportunidades")
def api_radar_oportunidades():
    """Wrapper no-reentrante de `_api_radar_oportunidades_impl()` -- ver el
    comentario de `_oportunidades_lock` arriba. Si el lock ya está tomado
    (otra ejecución pesada en curso), devuelve `429` de inmediato con un
    cuerpo JSON explícito (`"error": "ciclo_ya_en_curso"`) -- nunca espera,
    nunca reintenta, nunca deja un thread bloqueado. El primer request
    corre exactamente igual que antes de este cambio."""
    if not _oportunidades_lock.acquire(blocking=False):
        return jsonify({
            "error": "ciclo_ya_en_curso",
            "motivo": "otro_request_pesado_a_radar_oportunidades_en_ejecucion",
        }), 429
    try:
        return _api_radar_oportunidades_impl()
    finally:
        _oportunidades_lock.release()


def _api_radar_oportunidades_impl():
    """Oportunidades Detectadas (Fase 6, 2026-08-18; capa de prioridad
    final agregada 2026-08-18, cierre de arquitectura) -- CADA candidata
    que Tradier detectó hoy (`candidate_detection`, nunca se borra), con
    su etapa real (PREPARACION..NO_PERSEGUIR, o `DETECCION_TEMPRANA` si
    todavía no cruzó ningún umbral de Alerta Temprana), el precio EN VIVO
    del último barrido de Tradier (`radar_worker.get_last_quotes()`, ya en
    memoria -- cero llamadas nuevas a Yahoo/Finnhub; si el ticker no está
    en el último barrido, `price_actual` queda `null`), su sector y si ese
    sector tiene flujo de dinero activo (cruce de solo lectura con
    `scan_worker.STATE.sector_flow_snapshot`, cobertura declarada en
    `/api/flujo-sectorial`), evidencia histórica NO vinculante (de
    `historical_scoring.score_candidate`, cacheada), y `estado_final` --
    una de las 4 categorías operativas de
    `atlas_live/radar/priority_classifier.py` (🟢 OPORTUNIDAD_PRIORITARIA
    / 🟡 VIGILAR / 🔵 PREPARACION / 🔴 NO_TOCAR), con `motivo_estado_final`
    explicando por qué. Solo lectura, mismo patrón sin token que
    `/api/radar-universo` -- Memory Engine, Radar Explosivo y
    Yahoo/Finnhub no participan en la DETECCIÓN en absoluto: una detección
    real de Tradier nunca deja de GUARDARSE por esas capas (ver
    `atlas_live/radar/candidate_registry.py::live_opportunities`, sigue
    devolviendo TODO sin filtrar, para aprendizaje/análisis interno).

    Filtro de disponibilidad Racional (2026-08-18, caso real BATL): a
    partir de acá, la RESPUESTA de este endpoint -- la lista operable que
    se muestra en la Cabina para decidir una operación -- SÍ se filtra a
    `racional_available == True`. Universo Tradier -> detección completa
    -> filtro Racional -> candidatas mostradas. Server-side, no un filtro
    visual: un ticker sin disponibilidad en Racional nunca llega al JSON,
    aunque Tradier lo haya detectado con una señal fuerte. `total_detectadas_hoy`/
    `total_disponibles_racional` exponen ambos números para que el filtro
    sea auditable, no silencioso.

    Cadena de confiabilidad del precio (2026-08-18, caso real SBLK/BATL):
    cada oportunidad trae `price_actual_as_of` (timestamp real de Tradier)
    y `price_age_seconds` (antigüedad recalculada EN CADA REQUEST, nunca en
    el momento del barrido) + `estado_validacion` (OK/SIN_PRECIO_ACTUAL/
    SIN_TIMESTAMP/VENCIDO/CAMBIO_PCT_INCOHERENTE). Un `estado_validacion`
    distinto de OK fuerza `estado_final=NO_TOCAR` con prioridad sobre
    cualquier etapa -- una OPORTUNIDAD_PRIORITARIA nunca puede tener un
    precio vencido, sin timestamp o con % de cambio incoherente.

    Precio de premarket vía bid/ask (2026-08-18, autorizado tras auditoría
    con evidencia real -- ver `TradierProvider._resolve_current_price()`):
    `price_actual`/`price_actual_as_of` ya vienen resueltos desde el Quote
    (last fresco, o punto medio bid/ask cuando last está vencido pero
    bid/ask son frescos y confiables) -- este endpoint solo agrega
    `price_basis` ("tradier_last"/"tradier_bid_ask_mid"), `bid`, `ask`,
    `bid_timestamp`, `ask_timestamp`, `spread_abs`, `spread_pct` como
    trazabilidad visible de CÓMO se resolvió, sin cambiar la cadena de
    confiabilidad de arriba (que sigue leyendo `q.timestamp`/`q.last_price`
    tal cual, ahora ya corregidos en el origen).

    PM-RVOL Fase 1 (2026-08-25, capa de OBSERVABILIDAD, autorizada
    explícitamente): `premarket_volume_percentile`/`premarket_volume_acceleration`
    (+ sus `_state`) -- dos señales de volumen premarket que NUNCA se
    llaman "RVOL" (`relative_volume` de arriba, basado en `average_volume`
    de sesión regular completa, queda intacto y sin relación con esto).
    Puramente informativas: no participan en ninguna puerta, en
    `estado_final`, en el ranking ni en el aprendizaje -- ver
    `candidate_gates.premarket_volume_percentile()`/`premarket_volume_acceleration()`
    para la fórmula y los estados de validación explícitos.

    `learned_evidence` (2026-08-25, Fase 4/5 -- CONOCIMIENTO → EVIDENCIA,
    autorizado explícitamente, NUNCA EVIDENCIA → DECISIÓN): evidencia
    histórica de la PROPIA experiencia de Atlas (`live_experience_knowledge.db`,
    Fases 1-3) para la condición `(direction, timing_deteccion)` de cada
    candidata -- `available`/`validation_state`/`sample_size`/
    `historical_success_pct_20`/`baseline_pct_20`/`lift_20`/Wilson CI/
    `computed_as_of`/`methodology_version`. Se calcula y agrega DESPUÉS de
    que `estado_final` ya quedó fijado -- ver `atlas_live/learning/learned_evidence.py`.

    `atlas_decision`/`decision_shadow`/`shadow_differs` (2026-08-26, U3-B --
    Atlas Decision Core, autorizado explícitamente): `estado_final` sale
    ahora de `atlas_decision_core.decide()` -- que internamente llama a
    `priority_classifier.classify_final_priority()` SIN modificarla, mismo
    resultado exacto que antes. Se llama DOS VECES, igual que ya se hacía
    con `learned_evidence` arriba: la primera, SIN `learned_evidence` (fija
    `estado_final`/`motivo_estado_final`, arquitectónicamente imposible que
    el aprendizaje la haya visto); la segunda, CON `learned_evidence` ya
    calculado, solo para exponer `decision_shadow`/`shadow_differs` -- lo
    que la política de recalibración habría propuesto. `apply_recalibration`
    permanece `False` (Fase 5/5, Shadow Mode) -- `decision_shadow` nunca
    cambia `estado_final`, ver `atlas_live/core/atlas_decision_core.py`."""
    from datetime import datetime, timezone

    from atlas_live.core import activation_gate as ag
    from atlas_live.core import activation_registry as areg
    from atlas_live.core import atlas_decision_core as adc
    from atlas_live.core import decision_composition as dcomp
    from atlas_live.core import decision_knowledge_registry as dk_registry
    from atlas_live.core import knowledge_eligibility as ke
    from atlas_live.core import knowledge_eligibility_registry as ker
    from atlas_live.core import shadow_observation as so
    from atlas_live.core import shadow_observation_registry as sor
    from atlas_live.learning import historical_scoring as hsc
    from atlas_live.learning import learned_evidence as le
    from atlas_live.memory import market_hours as _mh
    from atlas_live.radar import candidate_gates as gates
    from atlas_live.radar import candidate_registry as radar_registry
    from atlas_live.radar import phase_classifier as pcls
    from atlas_live.radar import priority_classifier as pc
    from atlas_live.radar import sweep_history
    from atlas_live.reference.daily_reference import classify_direction

    market_date = _mh.market_date()
    oportunidades = radar_registry.live_opportunities(market_date)
    total_detectadas_hoy = len(oportunidades)
    oportunidades = [o for o in oportunidades if o.get("racional_available") is True]
    total_disponibles_racional = len(oportunidades)
    last_quotes = radar_worker.get_last_quotes()

    # PM-RVOL Fase 1 (2026-08-25) -- capa de OBSERVABILIDAD, nunca gates:
    # dos señales de volumen premarket que NO son "RVOL" (nunca usan
    # `average_volume`), calculadas acá una sola vez por request sobre
    # datos que ya están en memoria (cero llamadas nuevas a proveedores).
    # `relative_volume`/`MIN_RVOL`/`gate_relative_volume`/scoring/ranking
    # quedan intactos -- estos campos son puramente informativos.
    pm_session = _mh.get_session()
    pm_universe_dollar_volumes = [
        q2.last_price * q2.volume
        for q2 in last_quotes.values()
        if getattr(q2, "last_price", None) is not None and getattr(q2, "volume", None) is not None
        and q2.last_price >= 0 and q2.volume >= 0
    ]

    sector_snapshot = scan_worker.STATE.sector_flow_snapshot or {}
    symbol_sector_map = sector_snapshot.get("symbol_sector_map", {})
    top_sectores = {s["sector"] for s in sector_snapshot.get("sectores", [])[:5]}

    try:
        reference_table = hsc.get_cached_reference_table()
    except Exception:
        # Evidencia histórica es puramente informativa (nunca bloquea) --
        # si la Base Histórica no está disponible todavía, la lista
        # operativa sigue funcionando sin esa anotación.
        reference_table = None

    # Predicción de magnitud (2026-08-20): un solo fetch para todo el día,
    # nunca una consulta por candidata (serían miles) -- indexado por
    # ticker para lookup O(1) dentro del loop de abajo.
    magnitud_preds_by_ticker = {
        p["ticker"]: p for p in radar_registry.magnitud_predictions_for_date(market_date)
    }

    now = datetime.now(timezone.utc)

    for o in oportunidades:
        q = last_quotes.get(o["ticker"])
        o["price_actual"] = q.last_price if q else None

        # PM-RVOL Fase 1 -- señales de volumen premarket, observacionales.
        q_dollar_volume = (
            q.last_price * q.volume
            if q is not None and getattr(q, "last_price", None) is not None and getattr(q, "volume", None) is not None
            else None
        )
        pm_percentile = gates.premarket_volume_percentile(q_dollar_volume, pm_universe_dollar_volumes, pm_session)
        pm_full_history = radar_worker.get_symbol_sweep_history(o["ticker"])
        if pm_full_history:
            pm_current, pm_history = pm_full_history[-1], pm_full_history[:-1]
        else:
            # Sin historial todavía (símbolo nuevo/primer barrido) -- se
            # construye un snapshot mínimo desde el quote en vivo para que
            # la función SIEMPRE decida el estado (nunca se hardcodea
            # INSUFFICIENT_HISTORY acá: si la sesión ya no es premarket,
            # debe ganar NOT_PREMARKET, no un estado de historial).
            pm_current = sweep_history.SweepSnapshot(
                sweep_id="", observed_at="", price=None, change_pct=None,
                volume=getattr(q, "volume", None), average_volume=None,
                relative_volume=None, dollar_volume=None, session=pm_session,
            )
            pm_history = []
        pm_accel = gates.premarket_volume_acceleration(pm_current, pm_history, pm_session)
        o["premarket_volume_percentile"] = pm_percentile.value
        o["premarket_volume_percentile_state"] = pm_percentile.validation_state
        o["premarket_volume_acceleration"] = pm_accel.value
        o["premarket_volume_acceleration_state"] = pm_accel.validation_state
        o["change_pct_actual"] = q.change_percent if q else None
        o["price_actual_source"] = "tradier" if q else None

        # Trazabilidad del precio de premarket (2026-08-18, autorizado por
        # el usuario tras auditoría con evidencia real): `q.last_price`/
        # `q.change_percent`/`q.timestamp` YA vienen resueltos desde
        # `TradierProvider._to_quote()` (last fresco, o punto medio bid/ask
        # cuando last está vencido pero bid/ask son frescos y confiables) --
        # acá solo se expone CÓMO se resolvió, para que nunca quede oculto.
        q_bid = getattr(q, "bid", None) if q else None
        q_ask = getattr(q, "ask", None) if q else None
        q_bid_ts = getattr(q, "bid_timestamp", None) if q else None
        q_ask_ts = getattr(q, "ask_timestamp", None) if q else None
        o["price_basis"] = getattr(q, "price_basis", None) if q else None
        # `executable_price` (2026-08-24, Fase 1D -- separación señal/
        # ejecutable): `price_actual` de arriba es precio de SEÑAL --
        # `executable_price` es `None` cuando ese precio NO tiene una
        # contraparte de compra verificable (BID_ONLY/STALE_REGULAR_CLOSE).
        # La Cabina debe usar este campo, nunca `price_actual`, para decidir
        # si mostrar el precio como "comprable".
        o["executable_price"] = getattr(q, "executable_price", None) if q else None
        o["bid_only_reason"] = getattr(q, "bid_only_reason", None) if q else None
        o["bid"] = q_bid
        o["ask"] = q_ask
        o["bid_timestamp"] = q_bid_ts.isoformat() if q_bid_ts else None
        o["ask_timestamp"] = q_ask_ts.isoformat() if q_ask_ts else None
        if q_bid is not None and q_ask is not None:
            mid = (q_bid + q_ask) / 2
            o["spread_abs"] = q_ask - q_bid
            o["spread_pct"] = round((q_ask - q_bid) / mid * 100, 4) if mid else None
        else:
            o["spread_abs"] = None
            o["spread_pct"] = None

        # Cierre de la cadena de confiabilidad (2026-08-18, caso real
        # SBLK/BATL, ahora también para este pipeline Tradier): antigüedad
        # recalculada AHORA -- nunca en el momento del barrido -- para que
        # un precio congelado en `radar_worker._last_quotes` (si Tradier
        # empieza a fallar y el barrido no logra refrescarlo, ver
        # `radar_worker.py::run_sweep_once()`, el `except` no limpia
        # `_last_quotes`) nunca se presente como dato actual. Reutiliza
        # `scan_worker.compute_price_age_seconds()`/`is_price_stale()`/
        # `is_change_pct_coherent()` tal cual -- mismas funciones que ya
        # cierran esto en el pipeline Yahoo, sin duplicar lógica.
        price_actual_as_of = q.timestamp.isoformat() if (q and q.timestamp) else None
        o["price_actual_as_of"] = price_actual_as_of
        o["price_age_seconds"] = scan_worker.compute_price_age_seconds(price_actual_as_of, now=now)

        if q is None:
            estado_validacion = pc.VALIDACION_SIN_PRECIO_ACTUAL
        elif price_actual_as_of is None:
            estado_validacion = pc.VALIDACION_SIN_TIMESTAMP
        elif scan_worker.is_price_stale(price_actual_as_of, now=now):
            estado_validacion = pc.VALIDACION_VENCIDO
        elif not scan_worker.is_change_pct_coherent(q.last_price, q.previous_close, q.change_percent):
            estado_validacion = pc.VALIDACION_CAMBIO_PCT_INCOHERENTE
        else:
            estado_validacion = pc.VALIDACION_OK
        o["estado_validacion"] = estado_validacion

        # Dirección EN VIVO (2026-08-20, pedido explícito del usuario, caso
        # real COHR/SMCI): `o["direction"]` venía de `alert_stage_log`, que
        # solo se re-escribe cuando la ETAPA cambia (para no duplicar miles
        # de filas por sweep, ver `candidate_registry.record_alert_stage`)
        # -- si una candidata lleva mucho tiempo en la misma etapa, esa
        # dirección quedaba vieja mientras el precio seguía moviéndose
        # (ej. "Comprado" congelado de hace 30 min junto a un % actual ya
        # negativo). Se recalcula acá, en el MISMO lugar donde ya se
        # recalculan precio/antigüedad, con el mismo criterio de
        # confiabilidad que `phase_classifier.py` (RVOL casi nulo + precio
        # sintético = no confiar, caso KEN) -- para que "Dirección" y "%
        # cambio" sean siempre del mismo instante. No toca
        # `alert_stage_log` ni cómo/cuándo se escribe -- solo lo que se
        # MUESTRA en este endpoint de solo lectura.
        if q is not None:
            q_rvol = getattr(q, "relative_volume", None)
            direction_confiable = True
            if q.change_percent is None:
                direction_confiable = False
            elif o["price_basis"] == "tradier_bid_ask_mid" and (q_rvol is None or q_rvol < pcls.CHANGE_PCT_MIN_RVOL_TO_TRUST_ZERO):
                direction_confiable = False
            elif q.change_percent == 0.0 and q_rvol is not None and q_rvol < pcls.CHANGE_PCT_MIN_RVOL_TO_TRUST_ZERO:
                direction_confiable = False
            o["direction"] = classify_direction(q.change_percent) if direction_confiable else "INDEFINIDA"
            o["change_pct_confiable"] = direction_confiable
        # si `q` es None (ticker fuera del último barrido), no hay nada más
        # fresco -- se deja `direction`/`change_pct_confiable` tal como
        # vinieron de `live_opportunities()` (última lectura conocida).

        sector = symbol_sector_map.get(o["ticker"])
        o["sector"] = sector
        o["dinero_entra_sector"] = bool(sector) and sector in top_sectores

        try:
            detected_at = datetime.fromisoformat(o.get("detected_at"))
            o["minutos_desde_deteccion"] = round((now - detected_at).total_seconds() / 60, 1)
        except (TypeError, ValueError):
            o["minutos_desde_deteccion"] = None

        # Bug real encontrado en vivo (2026-08-20/21, caso COHR/CLSK/MRNA):
        # esta condición exigía `daily_range_pct_at_detection` no-None ADEMÁS
        # de `volatility_14d_pct_at_detection` -- pero ese campo solo se
        # calcula si `quote.high`/`quote.low` ya estaban poblados en el
        # instante EXACTO de la primera detección (write-once, ver
        # `_tag_experimental_signals_at_detection`), algo que Tradier
        # sistemáticamente todavía no tiene en los primeros ticks de una
        # sesión -- por eso las candidatas más frescas y accionables
        # (INICIO/CONFIRMACION recién detectadas) eran justo las que se
        # quedaban SIEMPRE sin evidencia histórica, sin ningún caso real
        # donde ambos campos coincidieran a tiempo. `historical_scoring.
        # score_candidate()`/`_bucket_of_row()` YA está diseñado para
        # degradar con gracia a `"poblacion_total"` cuando falta una sola
        # feature (ver sus tests) -- alcanza con `direction`/`timing_deteccion_hoy`
        # para intentarlo; `vol`/`rng` viajan tal cual (incluso `None`) a
        # `feature_values`, nunca se inventan.
        historical = None
        vol = o.get("volatility_14d_pct_at_detection")
        rng = o.get("daily_range_pct_at_detection")
        if reference_table is not None and o.get("direction") and o.get("timing_deteccion_hoy"):
            historical = hsc.score_candidate(
                reference_table, o["direction"], o["timing_deteccion_hoy"],
                {"volatility_14d_pct": vol, "daily_range_pct": rng},
            )
        o["evidencia_historica"] = historical

        # Predicción de magnitud congelada (2026-08-20, aprobado por el
        # usuario): la mediana histórica en el momento EXACTO en que esta
        # candidata se volvió accionable por primera vez -- ver
        # `candidate_tracker._tag_magnitud_prediction`. `None` hasta que
        # exista una (nunca se recalcula acá, para que se pueda calificar
        # después contra el resultado real sin que "se mueva").
        o["prediccion_magnitud_congelada"] = magnitud_preds_by_ticker.get(o["ticker"])

        candidate_snapshot = dcomp.candidate_from_radar_row(o, market_date, estado_validacion)
        features = dcomp.features_from_radar_row(o)
        scores = dcomp.scores_from_radar_row(o)
        evidence = dcomp.evidence_from_radar_row(o, historical)

        # Primera llamada -- SIN learned_evidence -- fija la decisión real
        # (arquitectónicamente imposible que el aprendizaje la haya visto,
        # se calcula recién más abajo).
        atlas_decision = adc.decide(candidate_snapshot, features, scores, evidence)
        o["estado_final"] = atlas_decision.decision
        o["motivo_estado_final"] = atlas_decision.reason

        # Fase 4/5 (2026-08-25, capa CONTROLADA, autorizada explícitamente):
        # CONOCIMIENTO → EVIDENCIA, nunca EVIDENCIA → DECISIÓN -- agregado
        # DESPUÉS de que `estado_final`/`motivo_estado_final` ya quedaron
        # fijados arriba, por diseño: es arquitectónicamente imposible que
        # `learned_evidence` haya influido esa decisión, ya se calculó antes
        # de que este campo exista. Puramente observacional -- ver
        # `atlas_live/learning/learned_evidence.py` para el filtro anti-
        # look-ahead (más estricto que Fase 2, `computed_as_of < market_date`).
        o["learned_evidence"] = le.get_learned_evidence(
            o.get("direction"), o.get("timing_deteccion_hoy"), market_date, volatility_14d_pct=vol,
        )

        # Segunda llamada -- CON learned_evidence -- SOLO para exponer el
        # shadow. `apply_recalibration` permanece False (default): esta
        # llamada NUNCA puede cambiar `estado_final`, ya fijado arriba.
        shadow_decision = adc.decide(candidate_snapshot, features, scores, evidence, learned_evidence=o["learned_evidence"])
        o["decision_shadow"] = shadow_decision.decision_shadow
        o["shadow_differs"] = shadow_decision.shadow_differs
        o["atlas_decision_methodology_version"] = atlas_decision.methodology_version

        # SHADOW/VALIDACIÓN de LEK, Fase 2 (2026-08-27, autorizado
        # explícitamente): persiste el resultado que YA se calculó arriba
        # -- no recalcula nada, no es un segundo algoritmo de decisión.
        # Solo escribe cuando shadow_differs=True (record_shadow_decision
        # también lo verifica). apply_recalibration sigue False -- este
        # registro es puramente de auditoría, nunca participa en
        # `estado_final`, ya fijado en la primera llamada de arriba.
        # Protegido con su propio try/except: un fallo al escribir el log
        # nunca puede romper la respuesta del endpoint.
        if shadow_decision.shadow_differs:
            try:
                le_evidence = o["learned_evidence"] or {}
                radar_registry.record_shadow_decision(
                    ticker=o["ticker"], market_date=market_date,
                    decision=atlas_decision.decision, decision_shadow=shadow_decision.decision_shadow,
                    shadow_differs=True,
                    validation_state=le_evidence.get("validation_state"),
                    sample_size=le_evidence.get("sample_size"),
                    wilson_upper_bound_20_pct=le_evidence.get("wilson_upper_bound_20_pct"),
                    baseline_pct_20=le_evidence.get("baseline_pct_20"),
                )
            except Exception:
                pass

        # Hito 3, Fase 3.0/3.1 (2026-09-03, autorizado explícitamente):
        # snapshot inmutable de decisión+conocimiento, SIEMPRE (no solo
        # cuando difiere -- a diferencia de shadow_decision_log de arriba,
        # que es una alerta ligera SOLO de divergencias). Transition-only
        # (ver decision_knowledge_registry.py), nunca cambia estado_final ni
        # ninguna decisión real -- apply_recalibration_active=False siempre
        # en esta fase, no hay ninguna vía de configuración que lo cambie.
        # Protegido con su propio try/except: un fallo acá nunca puede
        # romper la respuesta del endpoint.
        try:
            dk_registry.record_decision_knowledge_snapshot(
                ticker=o["ticker"], market_date=market_date,
                decision_timestamp=atlas_decision.decision_timestamp.isoformat(),
                decision=atlas_decision.decision,
                decision_shadow=shadow_decision.decision_shadow,
                shadow_differs=shadow_decision.shadow_differs,
                learned_evidence=o["learned_evidence"],
                direction=o.get("direction"), timing_deteccion=o.get("timing_deteccion_hoy"),
                core_methodology_version=atlas_decision.methodology_version,
                apply_recalibration_active=False,
            )
        except Exception:
            pass

        # Hito 3, Fase 3.3 (2026-09-03, autorizado explícitamente en Plan
        # Mode): elegibilidad de la condición (direction, timing_deteccion)
        # que ya representa `o["learned_evidence"]` -- puramente auditoría,
        # nunca influye `estado_final` (ya fijado arriba) ni activa
        # `apply_recalibration` (no aparece en ningún punto de este
        # bloque). Se registra por CONDICIÓN, no por ticker -- múltiples
        # candidatas que comparten (direction, timing_deteccion) producen
        # el mismo resultado y el registro transition-only ya lo deduplica.
        # Protegido con su propio try/except: un fallo acá nunca puede
        # romper la respuesta del endpoint.
        try:
            eligibilidad = ke.classify_eligibility(o["learned_evidence"], market_date)
            ker.record_eligibility_snapshot(
                direction=o.get("direction"), timing_deteccion=o.get("timing_deteccion_hoy"),
                evaluated_as_of=market_date, eligibility_result=eligibilidad,
            )
        except Exception:
            pass

        # Hito 3, Fase 3.4 (2026-09-03, autorizado explícitamente en Plan
        # Mode): observación shadow -- si el conocimiento hubiese sido
        # utilizado, ¿qué habría hecho Atlas, y ese conocimiento era
        # elegible según el veredicto que 3.3 ACABA de escribir (o
        # confirmar) arriba, en esta misma iteración? Nunca recalcula la
        # elegibilidad -- consulta el veredicto real vía
        # `ker.latest_eligibility_for()` (función de 3.3, sin modificar).
        # Solo se registra cuando `shadow_differs=True` (mismo gate que ya
        # usa `shadow_decision_log`, preexistente) -- puramente auditoría,
        # nunca influye `estado_final` (ya fijado arriba) ni activa
        # `apply_recalibration`. Protegido con su propio try/except: un
        # fallo acá nunca puede romper la respuesta del endpoint.
        try:
            veredicto_3_3 = ker.latest_eligibility_for(
                o.get("direction"), o.get("timing_deteccion_hoy"), atlas_decision.methodology_version,
            )
            observacion = so.classify_shadow_observation(
                decision=atlas_decision.decision, decision_shadow=shadow_decision.decision_shadow,
                shadow_differs=shadow_decision.shadow_differs,
                eligibility_state=(veredicto_3_3 or {}).get("eligibility_state"),
                computed_as_of=(o["learned_evidence"] or {}).get("computed_as_of"),
                market_date=market_date,
            )
            sor.record_shadow_observation(
                ticker=o["ticker"], market_date=market_date,
                decision_timestamp=atlas_decision.decision_timestamp.isoformat(),
                direction=o.get("direction"), timing_deteccion=o.get("timing_deteccion_hoy"),
                core_methodology_version=atlas_decision.methodology_version,
                observation=observacion, learned_evidence=o["learned_evidence"],
            )
        except Exception:
            pass

        # Hito 3, Fase 3.5 (2026-09-03, autorizado explícitamente en Plan
        # Mode, decisión funcional confirmada por el usuario): activación
        # controlada -- ÚNICO punto de todo el repo donde
        # `apply_recalibration=True` se pasa de verdad a `adc.decide()`,
        # y SOLO dentro del `if gate["activation_state"] == "ACTIVADO":`
        # de abajo. Corte inmediato si el mecanismo no está
        # `ON_CONTROLADO` (el default es `"OFF"`, fail-safe absoluto --
        # ver `activation_registry.get_mechanism_state()`): cero cómputo,
        # cero escritura mientras esté apagado. `decision_controlada`
        # (el resultado de esa tercera llamada) NUNCA se asigna a
        # `o[...]` -- no participa de la respuesta HTTP real, no influye
        # `o["estado_final"]` (ya fijado arriba) ni `o["decision_shadow"]`
        # (Fase 3.4, tampoco tocado acá). Reutiliza `veredicto_3_3` ya
        # consultado arriba para el bloque de 3.4 -- mismo veredicto real
        # de Fase 3.3, nunca recalculado. Protegido con su propio
        # try/except: cualquier error -- incluida la propia llamada con
        # `apply_recalibration=True` -- termina en nada activado, nada
        # persistido (fail-safe explícito, pedido por el usuario).
        try:
            mechanism_state = areg.get_mechanism_state()
            if mechanism_state == "ON_CONTROLADO":
                is_revoked = areg.is_revoked(
                    o.get("direction"), o.get("timing_deteccion_hoy"), atlas_decision.methodology_version,
                )
                eligibility_state_35 = (veredicto_3_3 or {}).get("eligibility_state")
                gate = ag.classify_activation(
                    mechanism_state=mechanism_state, eligibility_state=eligibility_state_35,
                    is_revoked=is_revoked,
                    computed_as_of=(o["learned_evidence"] or {}).get("computed_as_of"),
                    market_date=market_date,
                )
                decision_controlada = None
                if gate["activation_state"] == "ACTIVADO":
                    controlada = adc.decide(
                        candidate_snapshot, features, scores, evidence,
                        learned_evidence=o["learned_evidence"], apply_recalibration=True,
                    )
                    decision_controlada = controlada.decision
                areg.record_activation_state(
                    ticker=o["ticker"], market_date=market_date,
                    decision_timestamp=atlas_decision.decision_timestamp.isoformat(),
                    direction=o.get("direction"), timing_deteccion=o.get("timing_deteccion_hoy"),
                    core_methodology_version=atlas_decision.methodology_version,
                    mechanism_state=mechanism_state, eligibility_state=eligibility_state_35,
                    gate=gate, decision_controlada=decision_controlada,
                    learned_evidence=o["learned_evidence"],
                )
        except Exception:
            pass

    conteos: dict = {}
    conteos_estado_final: dict = {}
    for o in oportunidades:
        conteos[o["stage"]] = conteos.get(o["stage"], 0) + 1
        conteos_estado_final[o["estado_final"]] = conteos_estado_final.get(o["estado_final"], 0) + 1

    return jsonify({
        "market_date": market_date,
        "oportunidades": oportunidades,
        "conteos_por_etapa": conteos,
        "conteos_por_estado_final": conteos_estado_final,
        "total_detectadas_hoy": total_detectadas_hoy,
        "total_disponibles_racional": total_disponibles_racional,
    })


@app.route("/api/radar-explosion-bands")
def api_radar_explosion_bands():
    """Marcador Histórico Tradier (2026-08-18, aprendizaje unificado,
    pedido explícito del usuario) -- bandas ACUMULATIVAS (>=10/20/30/50/
    100/150/200%) de resultados FINALES y CONFIABLES del radar Tradier
    (CAPA1/2, `candidate_registry.explosion_bands_tradier`). Sistema
    NUEVO, en paralelo al Marcador Histórico legacy (Yahoo/exit_journal,
    `atlas_live/memory/explosion_history.py`) -- nunca lo reemplaza ni lo
    toca, se muestran lado a lado en la Cabina para comparación directa.
    `?date=YYYY-MM-DD` limita a un solo día de mercado; sin parámetro,
    agrega TODA la historia disponible. Público, sin token -- mismo
    patrón que `/api/radar-oportunidades`."""
    from atlas_live.radar import candidate_registry as radar_registry

    date_param = request.args.get("date")
    return jsonify(radar_registry.explosion_bands_tradier(date_param))


@app.route("/api/catalyst-events")
def api_catalyst_events():
    """Motor de Catalizadores/Noticias (2026-08-23, Fase 1; Segunda Fase
    2026-08-24 -- de "calendario de 175 earnings" a radar accionable) --
    solo lectura, público, mismo patrón sin token que `/api/radar-oportunidades`.
    Capa completamente aparte del radar técnico: nunca escribe en
    `candidate_gates.py`, el score en vivo ni `decision_engine.py`, y una
    falla del proveedor de noticias (Finnhub) nunca puede tumbar este
    endpoint ni el resto de Atlas -- `provider_health` degrada a
    SIN_CONFIGURAR/OFFLINE en vez de lanzar una excepción.

    Cada catalizador con `event_date` (ventana ±14 días) se enriquece vía
    `catalyst_market_join.enrich_catalyst_row()`: precio/cambio/volumen/RVOL/gap
    EN VIVO desde `radar_worker.get_last_quotes()` (ya en memoria, cero
    llamadas nuevas), datos técnicos si el ticker es TAMBIÉN candidata del
    radar hoy (`candidate_registry.live_opportunities()`), `catalyst_score`/
    `catalyst_opportunity_score`/`mrna_similarity_score` calculados EN VIVO
    (nunca el snapshot congelado, que sigue siendo solo para noticias), y
    Event Status/Trading Status separados.

    `top_catalyst_opportunities` (máx. 10): solo los que cruzan el piso de
    relevancia (`is_relevant_for_ranking` -- Racional + evidencia real de
    movimiento o detección técnica hoy), ordenados por
    `catalyst_opportunity_score` descendente -- el panel principal.
    `calendario_completo`: TODOS los catalizadores con fecha, sin filtrar,
    igual de enriquecidos -- vista secundaria (punto 1 del pedido:
    "el calendario completo puede mantenerse debajo").
    `noticias_recientes`: feed de las últimas noticias reales procesadas
    (Tier 1/3), más reciente primero -- sin cambios de esta fase."""
    from datetime import datetime, timezone

    from atlas_live.catalyst import catalyst_market_join as mj
    from atlas_live.catalyst import catalyst_registry as creg
    from atlas_live.memory import market_hours as _mh
    from atlas_live.radar import candidate_registry as radar_registry

    market_date = _mh.market_date()
    now = datetime.now(timezone.utc)
    last_quotes = radar_worker.get_last_quotes()
    radar_rows_by_ticker = {r["ticker"]: r for r in radar_registry.live_opportunities(market_date)}

    catalizadores_crudos = creg.list_upcoming_events(days_ahead=14)
    calendario_completo = []
    for c in catalizadores_crudos:
        # Sin fallback forzado a "OCURRIDO" (bug real encontrado en
        # verificación de UI, 2026-08-24): un catalizador sin transición
        # de ciclo de vida registrada todavía (`None`) NO debe mostrarse
        # como "ya ocurrido" -- event_status()/catalyst_score() ya
        # degradan correctamente con `lifecycle_state=None`, cayendo a
        # la clasificación por `dias_al_evento`/al default neutral.
        lifecycle_state = creg.latest_lifecycle_state(c["id"])
        enriquecido = mj.enrich_catalyst_row(
            c, lifecycle_state, last_quotes, radar_rows_by_ticker.get(c["ticker"]), now,
        )
        calendario_completo.append(enriquecido)

    top_catalyst_opportunities = sorted(
        (c for c in calendario_completo if mj.is_relevant_for_ranking(c)),
        key=lambda c: c["catalyst_opportunity_score"], reverse=True,
    )[:10]

    noticias_recientes = creg.list_recent_events(limit=100)
    for n in noticias_recientes:
        q = last_quotes.get(n["ticker"])
        n["price_actual"] = q.last_price if q else None
        n["change_pct_actual"] = q.change_percent if q else None
        n["lifecycle_state"] = creg.latest_lifecycle_state(n["id"])

    return jsonify({
        "generated_at": now.isoformat(),
        "market_date": market_date,
        "provider_health": creg.provider_health_summary(),
        "top_catalyst_opportunities": top_catalyst_opportunities,
        "calendario_completo": calendario_completo,
        "noticias_recientes": noticias_recientes,
    })


@app.route("/api/candidate-full-history")
def api_candidate_full_history():
    """Historia completa de UNA candidata (2026-08-18, aprendizaje
    unificado, pedido explícito del usuario, caso real XOS) -- separa en
    3 bloques que NUNCA se pisan entre sí: estado inicial (detección,
    write-once), evolución (etapas en vivo + máximo visto) y resultado
    final (EOD). `?ticker=XOS` obligatorio; `?date=YYYY-MM-DD` opcional
    (default: fecha de mercado actual). Público, sin token, solo lectura
    -- mismo patrón que `/api/radar-oportunidades`."""
    from atlas_live.memory import market_hours as _mh
    from atlas_live.radar import candidate_registry as radar_registry

    ticker = (request.args.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"error": "falta el parámetro 'ticker'"}), 400
    market_date = request.args.get("date") or _mh.market_date()

    historia = radar_registry.candidate_full_history(ticker, market_date)
    if historia is None:
        return jsonify({
            "error": f"sin detección para {ticker} en {market_date}",
            "ticker": ticker, "market_date": market_date,
        }), 404
    return jsonify(historia)


@app.route("/api/flujo-sectorial")
def api_flujo_sectorial():
    """Radar de Flujo de Dinero por Sector (2026-08-18, cierre de
    arquitectura) -- ranking de sectores por Money Flow Score, snapshot
    del ÚLTIMO ciclo de `scan_worker.py` (mismo `MoneyFlowEngine` que ya
    corre cada ciclo, cero cómputo nuevo acá). Radar PARALELO e
    independiente del radar de universo completo de Tradier -- nunca lo
    reemplaza ni lo limita. Cobertura real declarada en el propio
    payload (`cobertura`): watchlist Racional/Yahoo, no el universo
    completo -- Tradier no entrega sector por símbolo hoy. `null` /
    listas vacías antes de que corra el primer ciclo."""
    snapshot = scan_worker.STATE.sector_flow_snapshot
    if snapshot is None:
        return jsonify({
            "generated_at": None,
            "cobertura": scan_worker.SECTOR_FLOW_COBERTURA,
            "sectores": [],
            "symbol_sector_map": {},
        })
    return jsonify(snapshot)


@app.route("/api/aprendizaje-seguridad-resumen")
def api_aprendizaje_seguridad_resumen():
    """Hito 4, Fase 4.3 (2026-09-04, autorizado explícitamente en Plan
    Mode) -- resumen agregado SEGURO del estado de las 4 capas de Hito 3
    (elegibilidad/shadow observation/activación/evaluación continua), SIN
    token -- resuelve la limitación repetida en la auditoría de cierre de
    Hito 3 ("no verificable sin ATLAS_ADMIN_TOKEN"). Expone únicamente
    conteos agregados (ver `learning_safety_summary.build_safety_summary()`)
    -- NUNCA el detalle por ticker/condición (Wilson/baseline reales), que
    sigue exclusivamente detrás de los 4 endpoints admin ya existentes.
    Nunca lanza."""
    from atlas_live.core import learning_safety_summary as lss

    return jsonify(lss.build_safety_summary())


@app.route("/api/learning-maturity")
def api_learning_maturity():
    """Aprendizaje en Vivo + Madurez (2026-08-15, ver
    PROPUESTA_MADUREZ_APRENDIZAJE.md). SOLO datos de `candidate_registry`
    (radar CAPA 2, en vivo, arranca en cero tras el reset) -- nunca mezcla
    la Base Histórica de Referencia. La madurez es el mínimo de los 11 ejes
    (cuello de botella), nunca condiciones_confiables/14 (ver
    evolution_panel.py para ese cálculo, ahora solo diagnóstico interno)."""
    from atlas_live.learning import live_summary

    date_param = request.args.get("date")
    return jsonify(live_summary.get_live_learning_summary(market_date=date_param))


@app.route("/api/historical-reference-summary")
def api_historical_reference_summary():
    """Base Histórica de Referencia (2026-08-15) -- estudio de ~3 meses del
    universo vía Tradier. Explícitamente NUNCA es aprendizaje de Atlas, solo
    contexto para comparar patrones (ver live_summary.get_historical_reference_summary)."""
    from atlas_live.learning import live_summary

    return jsonify(live_summary.get_historical_reference_summary())


def _admin_token_ok() -> bool:
    """Fail-closed a propósito (2026-08-16): sin ATLAS_ADMIN_TOKEN
    configurado en el entorno, el endpoint admin SIEMPRE rechaza -- nunca
    queda abierto por accidente si alguien olvida configurar la variable."""
    expected = os.environ.get("ATLAS_ADMIN_TOKEN")
    if not expected:
        return False
    provided = request.headers.get("X-Admin-Token") or request.args.get("token")
    return provided == expected


@app.route("/api/admin/build-historical-reference", methods=["POST"])
def api_admin_build_historical_reference():
    """Dispara, de forma manual y una vez por llamada, la construcción/
    continuación de la Base Histórica de Referencia (2026-08-16) --
    reutiliza scripts/build_historical_reference.py sin duplicar ni cambiar
    su lógica. Corre en un hilo de fondo DENTRO de este mismo proceso (el
    único con el Volume real de Railway montado, via ATLAS_DATA_DIR) --
    nunca se llama automáticamente al arrancar el servidor. No-reentrante:
    si ya hay una construcción corriendo, devuelve 409 sin iniciar otra.
    Protegido con ATLAS_ADMIN_TOKEN (X-Admin-Token header o ?token=)."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from scripts import build_historical_reference as bhr

    limit = request.args.get("limit", default=2600, type=int)
    workers = request.args.get("workers", default=8, type=int)
    delay_ms = request.args.get("delay_ms", default=80, type=int)
    period = request.args.get("period", default="3mo")
    # batch_timeout_s=0 desactiva el timeout de as_completed() -- necesario
    # para corridas de varias horas (universo de mercado completo,
    # 2026-08-17): con el default de 3600s, ThreadPoolExecutor.__exit__
    # sigue esperando a que terminen todos los hilos igual (shutdown(wait=True)),
    # así que un timeout bajo solo produce un build_state=ERROR confuso
    # sin cortar el trabajo real.
    batch_timeout_s = request.args.get("batch_timeout_s", default=3600, type=int)

    result = bhr.start_background_build(limit=limit, workers=workers, delay_ms=delay_ms, period=period,
                                         batch_timeout_s=batch_timeout_s)
    return jsonify(result), (202 if result.get("started") else 409)


@app.route("/api/admin/build-historical-reference/status")
def api_admin_build_historical_reference_status():
    """Solo lectura: si está corriendo, cuántos símbolos/casos lleva,
    errores, y cuándo terminó -- todo real, de reference_registry."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from scripts import build_historical_reference as bhr

    return jsonify(bhr.build_status())


@app.route("/api/admin/catalyst-worker-status")
def api_admin_catalyst_worker_status():
    """Diagnóstico del Motor de Catalizadores (2026-08-23) -- salud del
    proveedor (`provider_health_summary()`) + cobertura real por tier
    (cursor actual de Tier 3, últimos horarios de corrida). Protegido con
    ATLAS_ADMIN_TOKEN, mismo patrón que el resto de /api/admin/*."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.catalyst import catalyst_registry as creg
    from atlas_live.catalyst import catalyst_worker as cw

    return jsonify({
        "provider_health": creg.provider_health_summary(),
        "worker_enabled": cw.CATALYST_WORKER_ENABLED,
        "thread_alive": cw._thread.is_alive() if cw._thread else False,
        "tier1_last_run_epoch": cw._tier1_last_run or None,
        "tier2_last_run_epoch": cw._tier2_last_run or None,
        "tier3_last_run_epoch": cw._tier3_last_run or None,
        "tier3_cursor": cw._tier3_cursor,
        "tier1_interval_seconds": cw.TIER1_INTERVAL_SECONDS,
        "tier2_interval_seconds": cw.TIER2_INTERVAL_SECONDS,
        "tier3_interval_seconds": cw.TIER3_INTERVAL_SECONDS,
        "inter_call_delay_seconds": cw.INTER_CALL_DELAY_SECONDS,
        "inter_tier_delay_seconds": cw.INTER_TIER_DELAY_SECONDS,
        "cooldown": cw.cooldown_status(),
    })


@app.route("/api/admin/unified-detector-shadow")
def api_admin_unified_detector_shadow():
    """Diagnóstico de solo lectura del Detector Unificado en modo Shadow
    (2026-08-26, U3-C2) -- NUNCA conectado a ninguna decisión/UI real,
    protegido con ATLAS_ADMIN_TOKEN igual que el resto de /api/admin/*.
    `market_date` opcional (query param), default HOY."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.memory import market_hours as _mh_shadow
    from atlas_live.radar import shadow_detector_registry as sreg
    from atlas_live.radar import unified_detector as ud

    market_date = request.args.get("market_date") or _mh_shadow.market_date()
    detecciones = sreg.list_shadow_detections(market_date)
    return jsonify({
        "market_date": market_date,
        "thread_alive": ud._thread.is_alive() if ud._thread else False,
        "symbols_tracked": ud._history.symbols_tracked(),
        "total_detecciones": len(detecciones),
        "detecciones": detecciones,
        "last_successful_sweep_at": ud._last_successful_sweep_at,
        "last_error": ud._last_error,
        "last_error_at": ud._last_error_at,
        "last_error_session": ud._last_error_session,
        "error_count": ud._error_count,
    })


# Fecha de deploy de U3 (commit c091d0c, 2026-08-26) -- constante
# hardcodeada, NUNCA aceptada desde el cliente. Antes de esta fecha
# `shadow_candidate_detection` no puede tener ninguna fila (el detector no
# existía), así que es el único límite inferior válido para U3-C3.
U3_DEPLOY_MARKET_DATE = "2026-08-26"


@app.route("/api/admin/u3c3-quality-report")
def api_admin_u3c3_quality_report():
    """Auditoría U3-C3 -- Legacy vs Unified (2026-09-02, autorizado
    explícitamente). Solo lectura, protegido por `_admin_token_ok()` igual
    que el resto de `/api/admin/*`. NO acepta ningún query param ni body --
    el rango de fechas se calcula enteramente server-side, a partir de
    `shadow_detector_registry.list_shadow_market_dates()` (única fuente
    real), acotado a `[U3_DEPLOY_MARKET_DATE, hoy]` -- nunca una fecha
    recibida del cliente. Delega en
    `detector_comparison.quality_report_aggregated()` (2026-09-02 --
    reemplaza a `quality_report()`, que un intento real contra producción
    demostró que no transporta bien un período de varios días por HTTP:
    devuelve el detalle completo de `matched`/`solo_legacy_detalle`/
    `solo_unified_detalle` con los snapshots JSON de
    `shadow_candidate_detection` adentro, potencialmente del orden de la
    base completa -- ~2 GB). La versión agregada procesa un `market_date`
    a la vez y solo retiene contadores/listas de números pequeños,
    devolviendo una respuesta de unos pocos KB. Solo ejecuta `SELECT` --
    nunca escribe en ninguna SQLite, nunca toca
    `shadow_candidate_detection`/`candidate_observation`, nunca activa
    `apply_recalibration` ni influye en ninguna decisión real de Atlas."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.memory import market_hours as _mh_u3c3
    from atlas_live.radar import detector_comparison as dc
    from atlas_live.radar import shadow_detector_registry as sreg

    hoy = _mh_u3c3.market_date()
    fechas_shadow = sreg.list_shadow_market_dates()
    market_dates = sorted(d for d in fechas_shadow if U3_DEPLOY_MARKET_DATE <= d <= hoy)

    if not market_dates:
        return jsonify({
            "error": "sin datos shadow en el rango U3-C3",
            "u3_deploy_date": U3_DEPLOY_MARKET_DATE,
            "hoy": hoy,
        }), 200

    reporte = dc.quality_report_aggregated(market_dates)
    reporte["u3c3_window"] = {
        "u3_deploy_date": U3_DEPLOY_MARKET_DATE,
        "hoy": hoy,
        "market_dates_usados": market_dates,
    }
    return jsonify(reporte)


@app.route("/api/admin/u3c3-exclusive-diagnostics")
def api_admin_u3c3_exclusive_diagnostics():
    """Diagnóstico temporal de SOLO LECTURA (2026-09-02, autorizado
    explícitamente) sobre las ~3,1M detecciones exclusivas de Unified que
    encontró la auditoría U3-C3 -- volumen/distribución por ticker/día,
    distribución por puerta, características de los 7.329 solo_legacy,
    timing de matched, cobertura estructural de `candidate_outcome`
    (nunca evaluación de resultado), y una aproximación de "episodios"
    (agrupamiento por gaps de 30/60/180/300s, declarada explícitamente
    como mezcla matched+solo_unified -- ver
    `u3c3_exclusive_diagnostics.episode_grouping()`).

    Módulo aislado (`atlas_live/radar/u3c3_exclusive_diagnostics.py`),
    nunca importado por `detector_comparison.py` ni por ningún flujo real
    -- retirable con un solo commit. Sin parámetros (ni query ni body) --
    las 4 fechas están hardcodeadas en el módulo
    (`DIAGNOSTIC_MARKET_DATES`), nunca aceptadas del cliente. Cada consulta
    propia de este módulo abre su conexión en modo read-only REAL de
    SQLite (`file:...?mode=ro` + `PRAGMA query_only=ON`) -- un intento de
    escritura falla a nivel del motor. Nunca carga las filas crudas de
    `shadow_candidate_detection` -- todo corre como agregación SQL
    (GROUP BY/COUNT) o por grupo chico (B.7) dentro de SQLite.

    Diagnóstico estructurado (2026-09-02, autorizado explícitamente, tras
    2 HTTP 500 reales sin poder ver el traceback en Railway):
    `full_report()` ya no deja que una excepción se propague hasta Flask
    -- la atrapa, arma un body chico (`ok`, `etapa_fallida`,
    `tipo_excepcion`, `mensaje` saneado -- nunca traceback/rutas/
    credenciales/datos de tablas, ver `_sanitize_message()`) y
    `etapas_completadas`. El traceback completo sigue escribiéndose a
    stderr vía `_run_stage()` (marcador `[U3C3_DIAGNOSTIC_EXCEPTION]`),
    solo que ya no es la única forma de saber qué pasó."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.radar import u3c3_exclusive_diagnostics as u3d

    resultado = u3d.full_report()
    return jsonify(resultado), (200 if resultado.get("ok") else 500)


@app.route("/api/admin/raw-data-consolidation/consolidate", methods=["POST"])
def api_admin_raw_data_consolidate_block():
    """Hito 2 -- consolidación de datos crudos (2026-09-02, autorizado
    explícitamente): corre el ciclo RAW → ANALYSIS → MANIFEST →
    PERSISTENCE VERIFICATION para UN bloque `(ticker, market_date)`
    explícitamente indicado por query param -- nunca un lote, nunca
    "todos los bloques", nunca fechas implícitas. `source_table` limitado
    a `candidate_observation`/`shadow_candidate_detection` (validado
    contra `raw_data_consolidation_registry.VALID_SOURCE_TABLES`, un 400
    si no coincide). `ticker`/`market_date` viajan siempre como parámetros
    ligados en el SQL interno -- nunca interpolados.

    NUNCA borra ni modifica `candidate_observation`/`candidate_detection`/
    `candidate_outcome`/`shadow_candidate_detection` -- solo LEE (conexión
    `mode=ro` + `PRAGMA query_only=ON`, ver `raw_data_consolidation.py`).
    Escribe ÚNICAMENTE en `raw_data_consolidation.db`, una base nueva y
    chica, separada. El resultado nunca avanza más allá de `status=
    "verified"` -- `compaction_authorized`/`compacted` no existen en el
    vocabulario de este endpoint, quedan para una fase futura separada."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.radar import raw_data_consolidation_pipeline as rdc_pipeline
    from atlas_live.radar import raw_data_consolidation_registry as rdc_registry

    source_table = request.args.get("source_table", "")
    ticker = request.args.get("ticker", "")
    market_date = request.args.get("market_date", "")

    if source_table not in rdc_registry.VALID_SOURCE_TABLES:
        return jsonify({
            "error": f"source_table inválida: {source_table!r}",
            "valores_permitidos": list(rdc_registry.VALID_SOURCE_TABLES),
        }), 400
    if not ticker or not market_date:
        return jsonify({"error": "faltan parámetros obligatorios: ticker y market_date"}), 400

    resultado = rdc_pipeline.consolidate_block(source_table, ticker, market_date)
    return jsonify(resultado), (200 if resultado.get("ok") else 500)


@app.route("/api/admin/raw-data-consolidation/status")
def api_admin_raw_data_consolidation_status():
    """Hito 2 -- solo lectura del estado de los manifiestos ya
    consolidados (2026-09-02, autorizado explícitamente).
    `?source_table=` opcional para filtrar; sin ese parámetro, devuelve
    todos los bloques de ambas tablas. Lee únicamente
    `raw_data_consolidation.db` -- nunca toca `candidate_observation`/
    `shadow_candidate_detection`."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.radar import raw_data_consolidation_registry as rdc_registry

    source_table = request.args.get("source_table") or None
    if source_table is not None and source_table not in rdc_registry.VALID_SOURCE_TABLES:
        return jsonify({
            "error": f"source_table inválida: {source_table!r}",
            "valores_permitidos": list(rdc_registry.VALID_SOURCE_TABLES),
        }), 400

    bloques = rdc_registry.list_blocks(source_table)
    return jsonify({"source_table_filter": source_table, "n_bloques": len(bloques), "bloques": bloques})


@app.route("/api/admin/decision-knowledge-tribunal")
def api_admin_decision_knowledge_tribunal():
    """Hito 3, Fase 3.2 -- Tribunal de comparación offline (2026-09-03,
    autorizado explícitamente): decisión baseline vs decision_shadow vs
    outcome real, agregado por condición, sobre el snapshot inmutable de
    Fase 3.0/3.1. Puramente de LECTURA -- nunca modifica ninguna decisión
    real, nunca activa `apply_recalibration`, nunca declara que Atlas está
    aprendiendo (ver `decision_outcome_tribunal.NOTA_ALCANCE` en la propia
    respuesta). `?market_date=`/`?direction=`/`?timing_deteccion=`
    opcionales para acotar el reporte. Protegido con ATLAS_ADMIN_TOKEN,
    mismo patrón que el resto de `/api/admin/*`."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.core import decision_outcome_tribunal as tribunal

    market_date = request.args.get("market_date") or None
    direction = request.args.get("direction") or None
    timing_deteccion = request.args.get("timing_deteccion") or None
    limit = request.args.get("limit", default=5000, type=int)

    resultado = tribunal.full_tribunal_report(
        market_date=market_date, direction=direction, timing_deteccion=timing_deteccion, limit=limit,
    )
    return jsonify(resultado), (200 if resultado.get("ok") else 500)


@app.route("/api/admin/knowledge-eligibility-report")
def api_admin_knowledge_eligibility_report():
    """Hito 3, Fase 3.3 -- elegibilidad de conocimiento (2026-09-03,
    autorizado explícitamente en Plan Mode): responde, para cada condición
    (direction, timing_deteccion, methodology_version) evaluada, si su
    `learned_evidence` es NO_ELEGIBLE/INSUFICIENTE/ELEGIBLE y por qué --
    ver `atlas_live/core/knowledge_eligibility.py`. Puramente de LECTURA
    sobre el registro de auditoría (`knowledge_eligibility_registry.py`),
    nunca modifica ninguna decisión real, nunca activa
    `apply_recalibration` (ese flag no aparece en ningún punto de este
    módulo ni del registro). `?evaluated_as_of=`/`?eligibility_state=`
    opcionales para acotar el reporte. Protegido con ATLAS_ADMIN_TOKEN,
    mismo patrón que el resto de `/api/admin/*`."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.core import knowledge_eligibility_registry as ker

    evaluated_as_of = request.args.get("evaluated_as_of") or None
    eligibility_state = request.args.get("eligibility_state") or None
    limit = request.args.get("limit", default=5000, type=int)

    resultado = ker.full_eligibility_report(
        evaluated_as_of=evaluated_as_of, eligibility_state=eligibility_state, limit=limit,
    )
    return jsonify(resultado), (200 if resultado.get("ok") else 500)


@app.route("/api/admin/shadow-observation-report")
def api_admin_shadow_observation_report():
    """Hito 3, Fase 3.4 -- observación shadow (2026-09-03, autorizado
    explícitamente en Plan Mode): para cada caso donde `decision_shadow`
    difirió de la decisión real (`shadow_differs=True`), registra si el
    conocimiento que respaldó esa divergencia era ELEGIBLE/INSUFICIENTE/
    NO_ELEGIBLE (veredicto real de Fase 3.3, nunca recalculado) y permite
    evaluar después, contra el mismo `candidate_outcome`, cómo le habría
    ido a Atlas bajo shadow comparado con lo que hizo realmente bajo
    baseline -- ver `atlas_live/core/shadow_observation_registry.py`.
    Puramente de LECTURA, nunca modifica ninguna decisión real, nunca
    activa `apply_recalibration`, nunca promueve una observación a
    decisión ejecutada. `?market_date=`/`?eligibility_state=` opcionales
    para acotar el reporte. Protegido con ATLAS_ADMIN_TOKEN, mismo patrón
    que el resto de `/api/admin/*`."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.core import shadow_observation_registry as sor

    market_date = request.args.get("market_date") or None
    eligibility_state = request.args.get("eligibility_state") or None
    limit = request.args.get("limit", default=5000, type=int)

    resultado = sor.full_shadow_observation_report(
        market_date=market_date, eligibility_state=eligibility_state, limit=limit,
    )
    return jsonify(resultado), (200 if resultado.get("ok") else 500)


@app.route("/api/admin/activation-mechanism-state", methods=["GET", "POST"])
def api_admin_activation_mechanism_state():
    """Hito 3, Fase 3.5 -- interruptor maestro de activación controlada
    (2026-09-03, autorizado explícitamente en Plan Mode, decisión
    funcional confirmada por el usuario: ejercer `apply_recalibration=True`
    de forma real y aislada). `GET` lee el estado actual (`"OFF"` por
    defecto, fail-safe absoluto -- ver
    `activation_registry.get_mechanism_state()`) + historial completo.
    `POST ?state=ON_CONTROLADO&reason=...` es el ÚNICO punto que puede
    encenderlo -- rechaza (400) cualquier `state` que no sea exactamente
    `"OFF"`/`"ON_CONTROLADO"`, o `reason` vacío. Protegido con
    ATLAS_ADMIN_TOKEN."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.core import activation_registry as areg

    if request.method == "GET":
        return jsonify({
            "mechanism_state": areg.get_mechanism_state(),
            "historial": areg.get_mechanism_history(),
            "revocaciones": areg.list_revocations(),
        })

    state = request.args.get("state")
    reason = request.args.get("reason")
    try:
        areg.set_mechanism_state(state, reason)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "mechanism_state": areg.get_mechanism_state()})


@app.route("/api/admin/activation-revoke", methods=["POST"])
def api_admin_activation_revoke():
    """Hito 3, Fase 3.5 -- revocación inmediata y permanente (2026-09-03,
    autorizado explícitamente en Plan Mode). `?scope=GLOBAL&reason=...`
    bloquea cualquier activación futura sin importar la condición;
    `?scope=CONDICION&direction=...&timing_deteccion=...&methodology_version=...&reason=...`
    bloquea solo esa condición puntual. Sin mecanismo de "des-revocar" --
    la revocación gana siempre. Protegido con ATLAS_ADMIN_TOKEN."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.core import activation_registry as areg

    scope = request.args.get("scope")
    reason = request.args.get("reason")
    direction = request.args.get("direction") or None
    timing_deteccion = request.args.get("timing_deteccion") or None
    methodology_version = request.args.get("methodology_version") or None
    try:
        areg.revoke(
            scope=scope, reason=reason, direction=direction,
            timing_deteccion=timing_deteccion, methodology_version=methodology_version,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@app.route("/api/admin/activation-report")
def api_admin_activation_report():
    """Hito 3, Fase 3.5 -- reporte offline de activación controlada
    (2026-09-03, autorizado explícitamente en Plan Mode). Puramente de
    LECTURA sobre `activation_state_log` -- ver
    `activation_registry.full_activation_report()`. `?market_date=`/
    `?activation_state=`/`?limit=` opcionales. Protegido con
    ATLAS_ADMIN_TOKEN."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.core import activation_registry as areg

    market_date = request.args.get("market_date") or None
    activation_state = request.args.get("activation_state") or None
    limit = request.args.get("limit", default=5000, type=int)

    resultado = areg.full_activation_report(
        market_date=market_date, activation_state=activation_state, limit=limit,
    )
    return jsonify(resultado), (200 if resultado.get("ok") else 500)


@app.route("/api/admin/continuous-evaluation-run", methods=["POST"])
def api_admin_continuous_evaluation_run():
    """Hito 3, Fase 3.6 -- evaluación continua / degradación, camino
    MANUAL/on-demand (2026-09-03, autorizado explícitamente en Plan Mode,
    revisión corregida). Con `?direction=&timing_deteccion=&methodology_version=`
    evalúa esa condición puntual; sin esos 3 parámetros, evalúa TODAS las
    condiciones actualmente `ELEGIBLE` según Fase 3.3
    (`continuous_evaluation_registry.list_eligible_conditions()`, lectura
    pública, sin modificar esa fase). `?auto_revoke=true` es requerido
    explícitamente para que una evaluación `DEGRADADO` dispare
    `activation_registry.revoke()` -- por defecto (`auto_revoke=false`)
    este camino SOLO informa, nunca revoca (a diferencia del camino
    event-driven, ver `live_experience_pipeline.run_experience_learning_cycle()`,
    que sí puede revocar automáticamente bajo los mismos guards).
    Protegido con ATLAS_ADMIN_TOKEN."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.core import continuous_evaluation_registry as cer
    from atlas_live.memory import market_hours as _mh

    direction = request.args.get("direction") or None
    timing_deteccion = request.args.get("timing_deteccion") or None
    methodology_version = request.args.get("methodology_version") or None
    as_of_date = request.args.get("as_of_date") or _mh.market_date()
    n_ventana = request.args.get("n_ventana", default=cer.DEFAULT_N_VENTANA, type=int)
    auto_revoke = (request.args.get("auto_revoke") or "false").strip().lower() == "true"

    if direction and timing_deteccion and methodology_version:
        condiciones = [(direction, timing_deteccion, methodology_version)]
    elif direction or timing_deteccion or methodology_version:
        return jsonify({
            "error": "parametros_incompletos",
            "motivo": "direction/timing_deteccion/methodology_version deben pasarse los 3 juntos, o ninguno",
        }), 400
    else:
        condiciones = cer.list_eligible_conditions()

    evaluaciones = [
        cer.evaluate_condition(
            direction=d, timing_deteccion=t, methodology_version=m,
            as_of_date=as_of_date, n_ventana=n_ventana, auto_revoke=auto_revoke,
        )
        for d, t, m in condiciones
    ]
    return jsonify({
        "ok": True, "as_of_date": as_of_date, "auto_revoke": auto_revoke,
        "n_condiciones": len(condiciones), "evaluaciones": evaluaciones,
    })


@app.route("/api/admin/continuous-evaluation-report")
def api_admin_continuous_evaluation_report():
    """Hito 3, Fase 3.6 -- reporte offline de evaluación continua
    (2026-09-03, autorizado explícitamente en Plan Mode). Puramente de
    LECTURA sobre `continuous_evaluation_log` -- ver
    `continuous_evaluation_registry.full_continuous_evaluation_report()`.
    `?market_date=`/`?evaluation_state=`/`?limit=` opcionales. Protegido
    con ATLAS_ADMIN_TOKEN."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.core import continuous_evaluation_registry as cer

    market_date = request.args.get("market_date") or None
    evaluation_state = request.args.get("evaluation_state") or None
    limit = request.args.get("limit", default=5000, type=int)

    resultado = cer.full_continuous_evaluation_report(
        market_date=market_date, evaluation_state=evaluation_state, limit=limit,
    )
    return jsonify(resultado), (200 if resultado.get("ok") else 500)


@app.route("/api/admin/hito3-integrity-check")
def api_admin_hito3_integrity_check():
    """Hito 4, Fase 4.2 -- autodiagnóstico de integridad de Hito 3
    (2026-09-04, autorizado explícitamente en Plan Mode). Automatiza los
    checks estáticos ya hechos a mano en cada auditoría de Hito 3 (AST de
    `apply_recalibration=True`, ausencia de auto-unrevoke/DELETE de
    evidencia, ausencia de vocabulario financiero, presencia de
    walk-forward en los 6 módulos) -- ver
    `atlas_live.core.hito3_integrity_check.run_all_checks()`. Puramente
    de LECTURA sobre el código fuente en disco (`ast`/`tokenize`), nunca
    una DB, nunca la red. Protegido con ATLAS_ADMIN_TOKEN."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.core import hito3_integrity_check as hic

    resultado = hic.run_all_checks()
    return jsonify(resultado), (200 if resultado.get("ok") else 500)


@app.route("/api/admin/candidate-observation-diagnostics")
def api_admin_candidate_observation_diagnostics():
    """Diagnóstico read-only de `candidate_observation`/`radar_candidates.db`
    (2026-09-03, auditoría de espacio previa a una eventual compactación,
    autorizado explícitamente): `page_size`/`page_count`/`freelist_count`/
    `auto_vacuum`/`journal_mode`, tamaño físico exacto del archivo (+
    `-wal`/`-shm` si existen), `COUNT(*)` de `candidate_observation`, y la
    distribución de filas por bloque `(ticker, market_date)` (min/max/
    mediana/percentiles/top-20/bottom-20), con el `EXPLAIN QUERY PLAN`
    real incluido como evidencia de si se usa el índice existente.

    PURAMENTE DE LECTURA -- conexión `mode=ro` + `PRAGMA query_only=ON`
    (`candidate_observation_diagnostics.py`), NUNCA `CREATE`/`INSERT`/
    `UPDATE`/`DELETE`/`VACUUM`/checkpoint. No compacta ni autoriza
    compactación de nada -- eso sigue sin implementarse. Protegido con
    ATLAS_ADMIN_TOKEN, mismo patrón que el resto de `/api/admin/*`."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.radar import candidate_observation_diagnostics as cod

    resultado = cod.full_report()
    return jsonify(resultado), (200 if resultado.get("ok") else 500)


@app.route("/api/admin/generate-experience-knowledge", methods=["POST"])
def api_admin_generate_experience_knowledge():
    """Recálculo MANUAL del conocimiento propio de Atlas (2026-08-25, Fase
    3/5 -- EXPERIENCIA → CONOCIMIENTO, autorizado explícitamente). El
    disparo AUTOMÁTICO ya corre solo, una vez por día, dentro de
    `radar_worker._maybe_generate_experience_knowledge()` (justo después
    del EOD) -- este endpoint existe exclusivamente para poder REPARAR o
    RECALCULAR una fecha específica a pedido, sin esperar al próximo
    cierre de mercado. Nunca toca el marcador `conocimiento_generado_para`
    del disparo automático -- son caminos independientes, uno no bloquea
    ni interfiere con el otro.

    Síncrono (a diferencia de `build-historical-reference`): el cálculo es
    una consulta SQL + estadística en memoria sobre datos ya locales,
    nunca red -- no necesita hilo de fondo.

    `?as_of_date=YYYY-MM-DD` opcional (default: fecha de mercado actual).
    Protegido con ATLAS_ADMIN_TOKEN, mismo patrón que el resto de
    /api/admin/*. NO conecta el conocimiento generado a ninguna decisión
    -- solo lo calcula y lo persiste, igual que el disparo automático."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.learning import live_experience_pipeline as lep
    from atlas_live.memory import market_hours as _mh2

    as_of_date = request.args.get("as_of_date") or _mh2.market_date()
    resumen = lep.run_experience_learning_cycle(as_of_date)
    return jsonify(resumen)


@app.route("/api/admin/backfill-close-return", methods=["POST"])
def api_admin_backfill_close_return():
    """Dispara, en un hilo de fondo, el recálculo de `close_price_after_detection`/
    `close_return_after_detection_pct` para las candidatas de `?date=` que
    ya tienen resultado final pero fueron calculadas ANTES de que existiera
    ese campo (2026-08-23, Precisión de Magnitud pasó de "máximo intradía"
    a "cierre real" -- ver `eod_report.backfill_close_return`, caso real
    MRNX). Solo pide velas de Tradier para los tickers que realmente
    tienen una predicción de magnitud congelada ese día -- no las miles de
    candidatas detectadas en total. No-reentrante: si ya hay un backfill
    corriendo, devuelve 409. Protegido con ATLAS_ADMIN_TOKEN."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    market_date = request.args.get("date")
    if not market_date:
        return jsonify({"error": "falta ?date=YYYY-MM-DD"}), 400

    from atlas_live.data_fusion.universe_quotes import build_tradier_provider
    from atlas_live.radar import eod_report as eod

    provider = build_tradier_provider()
    if provider is None:
        return jsonify({"error": "TRADIER_API_TOKEN no configurado"}), 503

    result = eod.start_background_backfill_close_return(market_date, provider)
    return jsonify(result), (202 if result.get("started") else 409)


@app.route("/api/admin/backfill-close-return/status")
def api_admin_backfill_close_return_status():
    """Solo lectura: estado del backfill en curso o el resultado del
    último que corrió (actualizados/saltados/errores, todo real)."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.radar import eod_report as eod

    return jsonify(eod.get_backfill_close_return_status())


@app.route("/api/admin/historical-scoring-report")
def api_admin_historical_scoring_report():
    """Solo lectura (2026-08-17, Fase 3): reporte estadístico real sobre la
    Base Histórica ya construida -- ver `atlas_live/learning/historical_scoring.py`.
    Standalone: no toca candidate_gates.py, el score en vivo ni
    DecisionEngine. Admin porque puede ser una consulta pesada sobre toda
    la base, no pensada para el refresco frecuente de la Cabina."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.learning import historical_scoring as hsc

    return jsonify(hsc.generate_report())


@app.route("/api/admin/precursor-report")
def api_admin_precursor_report():
    """Solo lectura (2026-08-17, Fase 3b): análisis de ALERTA TEMPRANA --
    qué características tenía cada símbolo en los días previos (T-1..T-5)
    al inicio real de un movimiento fuerte, comparado contra el baseline
    del mercado y contra racional_available. Ver
    `atlas_live/learning/precursor_analysis.py`. Standalone: no toca
    candidate_gates.py, el score en vivo ni DecisionEngine."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.learning import precursor_analysis as pa

    return jsonify(pa.generate_precursor_report())


@app.route("/api/admin/alert-effectiveness-report")
def api_admin_alert_effectiveness_report():
    """Solo lectura (Fase 4, 2026-08-17): mide con evidencia real qué tan
    efectiva fue cada ventana de ALERTA TEMPRANA en vivo -- cuántas
    avanzan a INICIO/CONFIRMACION, cuántas llegan a +20/+50/+100%, tiempo
    real hasta el inicio, falsos positivos, y el mismo desglose separado
    por racional_available (capturado en vivo). `?date=YYYY-MM-DD` limita a
    un día; sin parámetro, toda la historia registrada. Ver
    `atlas_live/radar/candidate_registry.py::alert_stage_effectiveness_report`.
    Admin porque puede ser una consulta pesada, no pensada para el refresco
    frecuente de la Cabina."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.radar import candidate_registry as radar_registry

    market_date = request.args.get("date")
    return jsonify(radar_registry.alert_stage_effectiveness_report(market_date))


@app.route("/api/admin/shadow-validation-report")
def api_admin_shadow_validation_report():
    """Solo lectura (Fase 2 de la transición SHADOW->VALIDACIÓN de LEK,
    2026-08-27, autorizado explícitamente): cruza `shadow_decision_log`
    (cada evento real donde `atlas_decision_core.decide()` calculó
    `shadow_differs=True`) contra `candidate_outcome` ya cerrado, para
    medir si el downgrade que LEK propuso en la sombra habría acertado
    más que la decisión real. Puramente observacional -- NO participa en
    ninguna decisión, NO cambia `apply_recalibration` (sigue en `False`,
    hardcodeado, sin ninguna vía de configuración -- ver
    `atlas_live/core/atlas_decision_core.py`). `?date=YYYY-MM-DD` limita a
    un día; sin parámetro, toda la historia registrada. Ver
    `atlas_live/radar/candidate_registry.py::shadow_validation_report` para
    la definición exacta de "downgrade correcto/incorrecto" (reutiliza la
    agrupación de categorías ya usada por `eod_report.py`, no inventa un
    criterio nuevo). Admin porque puede ser una consulta pesada, no
    pensada para el refresco frecuente de la Cabina."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.radar import candidate_registry as radar_registry

    market_date = request.args.get("date")
    return jsonify(radar_registry.shadow_validation_report(market_date))


@app.route("/api/admin/candidate-timeline")
def api_admin_candidate_timeline():
    """Solo lectura (Fase 5, 2026-08-17, pedido explícito del usuario tras
    el caso real de ZIM): evolución completa de UNA candidata en UN día --
    la detección inicial, cada observación de seguimiento (un punto por
    barrido, ya guardado por `candidate_tracker.process_sweep`), y cada
    transición real de ventana de alerta (PREPARACION/ALERTA_TEMPRANA/
    ALERTA_FUERTE/INICIO/CONFIRMACION/NO_PERSEGUIR) -- para poder confirmar
    con evidencia minuto a minuto si Atlas detectó una candidata ANTES o
    DESPUÉS de que ya se hubiera movido. `?ticker=` es obligatorio;
    `?date=YYYY-MM-DD` opcional (default: la fecha de mercado actual). Ver
    `atlas_live/radar/candidate_registry.py::candidate_timeline`. Admin
    porque es una consulta de diagnóstico puntual, no pensada para el
    refresco frecuente de la Cabina. Solo lectura: no dispara ningún
    barrido, no escribe nada, no toca `candidate_gates.py`, el score en
    vivo ni `decision_engine.py`."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    ticker = (request.args.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"error": "falta el parámetro 'ticker'"}), 400

    from atlas_live.memory import market_hours as _mh
    from atlas_live.radar import candidate_registry as radar_registry

    market_date = request.args.get("date") or _mh.market_date()
    return jsonify(radar_registry.candidate_timeline(ticker, market_date))


@app.route("/api/admin/separation-report")
def api_admin_separation_report():
    """Solo lectura (2026-08-17, Fase 3b): compara con evidencia real
    (mediana + percentiles, no solo promedio) los onsets de +20% que se
    quedan cortos (A: 20-49%) contra los que continúan a +50-99% (B) o
    +100%+ (C) -- persistencia y aceleración del volumen, y el cruce con
    racional_available dentro de cada categoría. Ver
    `atlas_live/learning/precursor_analysis.py::generate_separation_report`.
    Standalone: no toca candidate_gates.py, el score en vivo ni
    DecisionEngine."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.learning import precursor_analysis as pa

    return jsonify(pa.generate_separation_report())


@app.route("/api/admin/data-dir-diagnostics")
def api_admin_data_dir_diagnostics():
    """Solo lectura (2026-08-17): ruta real de ATLAS_DATA_DIR, si existe y
    es escribible, tamaño/fecha de `historical_reference.db`, y si el
    marcador de persistencia está presente -- ver
    `atlas_live/data_dir_diagnostics.py` para la prueba completa
    (marcador + redeploy) que confirma si el Volume realmente persiste."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live import data_dir_diagnostics as ddd

    return jsonify(ddd.diagnostics())


@app.route("/api/admin/data-dir-full-inventory")
def api_admin_data_dir_full_inventory():
    """Inventario COMPLETO, read-only, de todos los archivos bajo
    `ATLAS_DATA_DIR` (2026-09-03, auditoría de espacio de Hito 3.2,
    autorizado explícitamente) -- endpoint nuevo y separado del existente
    `/api/admin/data-dir-diagnostics` (que solo devuelve los 50 archivos
    más grandes). Lista TODOS los archivos (`path`+`size_bytes`),
    agrupados por extensión, sin recorte. `os.walk`+`Path.stat()`
    únicamente -- nunca abre el contenido de ningún archivo, nunca ejecuta
    SQL sobre ningún `.db` encontrado, nunca escribe nada bajo
    `ATLAS_DATA_DIR`. Protegido con ATLAS_ADMIN_TOKEN, mismo patrón que el
    resto de `/api/admin/*`."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live import data_dir_full_inventory as ddfi

    resultado = ddfi.full_report()
    return jsonify(resultado), (200 if resultado.get("ok") else 500)


@app.route("/api/admin/radar-worker-status")
def api_admin_radar_worker_status():
    """Diagnóstico read-only del hilo de radar_worker (2026-09-03,
    autorizado explícitamente) -- determina si el hilo actual sigue vivo,
    mismo patrón ya usado en /api/admin/catalyst-worker-status y
    /api/admin/shadow-detector-status (thread_alive vía _thread.is_alive()).
    NO modifica el comportamiento de radar_worker, NO agrega recuperación
    ni watchdog -- exclusivamente lectura de su estado interno + el mismo
    radar_status() ya público via /api/radar-universo, reunidos en un solo
    lugar.

    `lock_locked`/`thread_ident`/`stack_summary` (2026-09-03, autorizado
    explícitamente, Fase 2 -- localizar el bloqueo exacto tras confirmar
    thread_alive=True sin progreso): `_lock.locked()` es un método
    read-only estándar de `threading.Lock`, sin efectos secundarios.
    `sys._current_frames()` + `traceback.format_stack()` son mecanismos
    ESTÁNDAR de la librería estándar de Python, explícitamente pensados
    para depurar hilos colgados -- nunca pausan, interrumpen ni modifican
    el hilo inspeccionado, solo leen una instantánea de los punteros de
    stack que el intérprete ya mantiene internamente. No agrega ningún
    mecanismo de recuperación -- puramente diagnóstico.

    `ultimo_error_at`/`ultimo_error_etapa`/`ultimo_error_tipo` (2026-09-03,
    autorizado explícitamente, Fase 3): ya se persisten en `radar_meta`
    vía `_record_error()`, pero `radar_status()` los descarta (solo
    expone `ultimo_error`, sin su timestamp ni su etapa de origen). Se
    leen acá directamente de `get_meta()` (misma fuente de verdad ya
    existente, sin duplicarla) -- puramente de lectura, no agrega ningún
    campo nuevo a `radar_meta`, no escribe nada.

    Protegido con ATLAS_ADMIN_TOKEN, mismo patrón que el resto de
    /api/admin/*."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live.radar import candidate_registry as radar_registry
    from atlas_live.radar import radar_worker as rw

    thread_ident = rw._thread.ident if rw._thread is not None else None
    stack_summary = None
    if thread_ident is not None:
        frame = sys._current_frames().get(thread_ident)
        if frame is not None:
            stack_summary = "".join(traceback.format_stack(frame))

    meta = radar_registry.get_meta()

    return jsonify({
        "thread_exists": rw._thread is not None,
        "thread_alive": rw._thread.is_alive() if rw._thread is not None else False,
        "stop_requested": rw._stop.is_set(),
        "lock_locked": rw._lock.locked(),
        "thread_ident": thread_ident,
        "stack_summary": stack_summary,
        "ultimo_error_at": meta.get("ultimo_error_at"),
        "ultimo_error_etapa": meta.get("ultimo_error_etapa"),
        "ultimo_error_tipo": meta.get("ultimo_error_tipo"),
        "radar_enabled": rw.RADAR_ENABLED,
        "radar_status": radar_registry.radar_status(),
    })


@app.route("/api/admin/data-dir-diagnostics/marker", methods=["POST"])
def api_admin_write_persistence_marker():
    """Escribe el marcador de persistencia UNA SOLA VEZ (nunca lo
    sobrescribe si ya existe) -- comparar `marker_id` antes y después de un
    redeploy real es la única prueba aceptada de que el Volume persiste."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live import data_dir_diagnostics as ddd

    return jsonify(ddd.write_marker_once())


@app.route("/api/admin/delete-reconstructible-universe-cache", methods=["POST"])
def api_admin_delete_reconstructible_universe_cache():
    """Endpoint administrativo MÍNIMO Y DE UN SOLO PROPÓSITO (2026-09-02,
    autorizado explícitamente, alivio del incidente de espacio en `/data`):
    borra ÚNICAMENTE los 3 archivos de caché reconstruible de
    `broad_universe` (`atlas_live/market_study/universe.py`) bajo
    `ATLAS_DATA_DIR` -- nombres hardcodeados en
    `data_dir_diagnostics._RECONSTRUCTIBLE_CACHE_FILENAMES`, este endpoint
    nunca lee ningún path/nombre del request (ni query params ni body).
    Nunca toca ninguna `.db`/`.db-wal`/`.db-shm` ni `persistence_marker.json`
    -- ver `delete_reconstructible_universe_cache()` para el contrato
    completo. Nunca escribe SQL, nunca cambia schema/PRAGMA."""
    if not _admin_token_ok():
        return jsonify({"error": "no autorizado"}), 403

    from atlas_live import data_dir_diagnostics as ddd

    return jsonify(ddd.delete_reconstructible_universe_cache())


@app.route("/api/signals")
def api_signals():
    """Historial de señales registradas (validación en vivo). Solo lectura,
    datos reales. Vacío si aún no hay señales. Filtro opcional por fecha."""
    date = request.args.get("date")
    return jsonify({"signals": signal_registry.list_signals(market_date=date)})


@app.route("/api/signals/active")
def api_signals_active():
    """Señales todavía sin resolver (DETECTADA/OBSERVANDO)."""
    return jsonify({"active": signal_registry.list_active()})


@app.route("/api/signals/results")
def api_signals_results():
    """Señales resueltas con su resultado (tabla separada)."""
    return jsonify({"results": signal_registry.list_results()})


@app.route("/api/signals/stats")
def api_signals_stats():
    """Estadísticas reales de las señales: aciertos/fallos, % por banda,
    anticipación -- siempre con n; 'Evidencia insuficiente' si la muestra no
    alcanza. Nunca una tasa presentada como confiable sin respaldo."""
    return jsonify(signal_tracker.stats())


@app.route("/api/signals/<signal_uuid>")
def api_signal_detail(signal_uuid):
    """Detalle de una señal: detección (congelada) + seguimiento + resultado
    (si existe), en secciones separadas para que se vea que no hay leakage."""
    signal = signal_registry.get_signal(signal_uuid)
    if signal is None:
        return jsonify({"error": "señal no encontrada"}), 404
    return jsonify({
        "detection": signal,
        "observations": signal_registry.get_observations(signal_uuid),
        "result": signal_registry.get_result(signal_uuid),
    })


@app.route("/api/evolution")
def api_evolution():
    """Panel de Evolución de Atlas (2026-08-07, ver DECISION_LOG.md).
    Precisión del modelo + rendimiento financiero + evolución del
    aprendizaje, todo desde datos reales ya existentes -- ver
    evolution_panel.py. Solo lectura, aditivo, no toca /api/performance."""
    return jsonify(evolution_panel.get_evolution())


@app.route("/api/hot-quote")
def api_hot_quote():
    """Canal de actualización rápida -- EXCLUSIVO para la Oportunidad del
    Día (Plan A) y el Plan B (2026-08-07, ver DECISION_LOG.md "Optimización
    de latencia"). Devuelve SOLO la cotización cruda con su timestamp de
    los símbolos pedidos (máximo 2 -- cualquier exceso se ignora), para que
    esos 2 precios visibles puedan mantenerse con antigüedad <=3s cuando el
    proveedor lo permite. NO corre el scanner, ni Radar, ni Memory, ni el
    Motor Predictivo -- solo `DataCollector.get_quote` sobre el MultiProvider
    (failover Yahoo->Finnhub ya existente). Se construye un DataCollector
    fresco por request (caché vacío) a propósito: cada llamada trae dato
    nuevo, sin servir un valor viejo desde caché. Solo lectura.

    Presupuesto de API: 2 símbolos cada 3s ~= 40 req/min, dentro del límite
    de Finnhub (60/min) e independiente del escaneo del universo (~244) --
    no aumenta el consumo sobre ese escaneo. Toda la lógica vive en
    `hot_quote.py` (testeable sin arrancar el servidor); aquí solo se
    construye un DataCollector fresco por request (caché vacío -> dato nuevo)
    y se serializa -- cero lógica de negocio en esta capa."""
    symbols = hot_quote.parse_symbols(request.args.get("symbols", ""))
    # Sin símbolos válidos: no se construye proveedor ni se consulta nada.
    collector = DataCollector(get_default_provider()) if symbols else None
    # Reintento acotado SOLO en este canal (<=2 símbolos): Yahoo desde el
    # datacenter de Railway falla de forma transitoria (timeouts/SSL) en una
    # fracción alta de requests; un segundo/tercer intento recupera esos 2
    # precios visibles sin tocar el escaneo del universo. Ver hot_quote._fetch_one.
    return jsonify(hot_quote.collect_hot_quotes(
        symbols, collector, max_attempts=3, retry_backoff_seconds=0.3,
    ))


@app.route("/api/config")
def api_config():
    """Parámetros vigentes de Atlas -- valores REALES leídos de sus módulos,
    no hardcodeados en la interfaz (limpieza MOCK 2026-08-07, ver
    DECISION_LOG.md). Solo lectura: el intervalo de escaneo de `scan_worker`,
    los umbrales del clasificador (`classifier`), el techo de microcap y los
    gates de elegibilidad del Radar (`explosive_config`), y el horario de
    mercado + la ventana de sellado (`market_hours`)."""
    cls = classifier.load_config()
    exp = explosive_config.load_config()

    def hhmm(t):
        return t.strftime("%H:%M")

    return jsonify({
        "refresh_interval_seconds": scan_worker.REFRESH_INTERVAL_SECONDS,
        "seal_window": f"{hhmm(market_hours.SEAL_WINDOW_START)} - {hhmm(market_hours.REGULAR_START)} ET",
        "market_hours": {
            "premarket": f"{hhmm(market_hours.PREMARKET_START)}-{hhmm(market_hours.REGULAR_START)}",
            "regular": f"{hhmm(market_hours.REGULAR_START)}-{hhmm(market_hours.REGULAR_END)}",
            "afterhours": f"{hhmm(market_hours.REGULAR_END)}-{hhmm(market_hours.AFTERHOURS_END)}",
            "timezone": "America/New_York",
        },
        "explosion_threshold_pct": cls["explosion_threshold_pct"],
        "false_breakout_ceiling_pct": cls["false_breakout_ceiling_pct"],
        "loser_threshold_pct": cls["loser_threshold_pct"],
        "microcap_ceiling_usd": exp["size_factor"]["small_cap_reference"],
        "min_price_usd": exp["gates"]["min_price"],
        "min_dollar_volume_usd": exp["gates"]["min_dollar_volume"],
        "top_n": exp["top_n"],
    })


@app.route("/api/mission-control")
def api_mission_control():
    """Mission Control -- Cabina del Piloto, Panel 12. Todos los procesos
    con archivo de estado (heartbeat) activo o reciente en esta máquina,
    más el historial de cambios de `marketState` detectados por el
    escaneo en vivo (2026-08-02, trazabilidad de precio) -- reutiliza
    `timeline.get_recent_events()` ya existente, sin agregar ninguna
    tabla ni mecanismo de lectura nuevo."""
    market_state_events = [
        e for e in timeline.get_recent_events(limit=100)
        if e["event_type"] == "state_changed" and e["run_id"] == "SCAN_WORKER"
    ]
    # Failover del Data Fusion Engine (2026-08-07) -- run_id separado
    # ("DATA_FUSION"), a propósito, para no mezclarse con el historial de
    # marketState de arriba (dos conceptos distintos).
    provider_events = [
        e for e in timeline.get_recent_events(limit=100)
        if e["event_type"] == "state_changed" and e["run_id"] == "DATA_FUSION"
    ]
    return jsonify({
        "processes": heartbeat.list_active_processes(),
        "market_state_history": [
            {
                "timestamp": e["timestamp"],
                "market_state": e["metadata"].get("market_state"),
                "previous_market_state": e["metadata"].get("previous_market_state"),
            }
            for e in market_state_events
        ],
        "provider_failover_history": [
            {
                "timestamp": e["timestamp"],
                "severity": e["severity"],
                "provider_source": e["metadata"].get("provider_source"),
                "previous_provider_source": e["metadata"].get("previous_provider_source"),
                "message": e["message"],
            }
            for e in provider_events
        ],
    })


@app.route("/api/predictive-engine")
def api_predictive_engine():
    """Motor Predictivo -- verificación pública (Fase 1.1, 2026-08-06, ver
    DECISIONES.md). Últimas predicciones registradas por cualquier
    capacidad (hoy solo `entry_window`) y el resumen de precisión ya
    calificado (Sprint 5) -- "aprender tanto de los aciertos como de los
    errores" hecho visible, sin ningún modelo nuevo. Solo lectura sobre
    `prediction_log.py`."""
    return jsonify({
        "total_predictions": prediction_log.count_predictions(),
        "recent": prediction_log.get_predictions(limit=20),
        "accuracy": prediction_log.get_accuracy_summary(),
    })


@app.route("/api/predictive-engine/<symbol>")
def api_predictive_engine_symbol(symbol):
    """Motor Predictivo -- Cabina del Piloto, Sprint 4 (2026-08-06):
    última predicción de `entry_window` para un símbolo puntual (el Hero
    del Dashboard y el detalle de Oportunidad del día). Solo lectura,
    misma tabla que `/api/predictive-engine`. `available=False` es el
    estado honesto cuando todavía no se registró ninguna predicción para
    este símbolo hoy -- no se inventa un valor."""
    recientes = prediction_log.get_predictions(symbol=symbol.upper(), capability="entry_window", limit=1)
    if not recientes:
        return jsonify({"available": False})
    return jsonify({"available": True, **recientes[0]})


@app.route("/api/rescan", methods=["POST"])
def api_rescan():
    """Fuerza un escaneo inmediato (además del refresco automático periódico).

    Investigación 7 (2026-08-06): sin este guard, un clic en "Actualizar
    ahora" mientras el ciclo de fondo ya está corriendo lanzaba un SEGUNDO
    run_scan_once() en paralelo -- confirmado en vivo: dos ciclos compitiendo
    por el mismo _NETWORK_EXECUTOR y el mismo _prefilter_cursor global
    producían lotes de tamaño inconsistente entrelazados en el log y
    disparaban el ciclo a varios minutos de duración. Un escaneo en curso
    ahora se respeta -- no se decide nada nuevo del lado del failover
    (Investigación 6), solo se evita solaparlo.
    """
    if scan_worker.STATE.snapshot()["scanning"]:
        return jsonify({"status": "ya hay un escaneo en curso, se ignora esta solicitud"}), 409
    import threading
    threading.Thread(target=scan_worker.run_scan_once, daemon=True).start()
    return jsonify({"status": "escaneo iniciado"})


@app.route("/api/diagnostics/providers")
def api_diagnostics_providers():
    """Diagnóstico de solo lectura del Data Fusion Engine real (Ricardo,
    2026-08-07): qué proveedor sirvió el último ciclo de escaneo en vivo
    (evidencia real, no supuesta) y si Finnhub autentica de verdad -- una
    llamada real y aislada a `FinnhubProvider`, nunca expone el valor de
    la API key.

    `finnhub_authentication` distingue explícitamente "unauthorized" (401,
    key inválida) de "rate_limited" (429, key válida pero el techo de 60
    llamadas/minuto del tier gratuito ya se alcanzó -- límite real,
    documentado en DECISIONES.md, Ticket 1) -- confundir ambos casos bajo
    un genérico "invalid" llevaría a una conclusión equivocada sobre el
    estado real de la credencial."""
    from atlas.data.providers.base import ProviderError, QuoteNotFoundError
    from atlas_live.data_fusion.finnhub_provider import FinnhubProvider

    api_key = os.environ.get("FINNHUB_API_KEY")
    finnhub_authentication = "missing"
    if api_key:
        try:
            FinnhubProvider(api_key).get_quote("AAPL")
            finnhub_authentication = "ok"
        except QuoteNotFoundError:
            finnhub_authentication = "ok"
        except ProviderError as exc:
            mensaje = str(exc)
            if "HTTP 429" in mensaje:
                finnhub_authentication = "rate_limited"
            elif "HTTP 401" in mensaje or "HTTP 403" in mensaje:
                finnhub_authentication = "unauthorized"
            else:
                finnhub_authentication = f"error: {mensaje}"

    return jsonify({
        "active_provider_last_cycle": scan_worker.get_active_provider_source(),
        "yahoo_provider_present": True,
        "finnhub_key_present": bool(api_key),
        "finnhub_authentication": finnhub_authentication,
        "is_multi_provider": True,
    })


@app.route("/api/diagnostics/tradier-raw-quotes")
def api_diagnostics_tradier_raw_quotes():
    """Diagnóstico de solo lectura (2026-08-18, caso real: precios de
    Tradier congelados en el cierre de ayer para símbolos líquidos durante
    premarket -- ver `atlas/data/providers/tradier_provider.py::get_raw_quotes`).

    Devuelve el JSON crudo que Tradier realmente entrega para los símbolos
    pedidos -- `last`, `bid`, `ask`, `trade_date`, `bid_date`/`ask_date` si
    existen, `volume`, `change_percentage`, todo sin filtrar ni interpretar
    -- para poder auditar con evidencia real cuál campo refleja el
    premarket antes de cambiar cualquier lógica de precio. Nunca expone
    `TRADIER_API_TOKEN` (se construye vía `build_tradier_provider()`, mismo
    mecanismo ya usado por el radar). Símbolos vía `?symbols=NVDA,TSLA,AMD`
    (máx 20 por pedido, para no convertir esto en un scraper del universo)."""
    from datetime import datetime, timezone

    from atlas_live.data_fusion.universe_quotes import build_tradier_provider

    symbols_param = request.args.get("symbols", "")
    symbols = [s.strip().upper() for s in symbols_param.split(",") if s.strip()][:20]
    if not symbols:
        return jsonify({"error": "Falta ?symbols=NVDA,TSLA,AMD (máx 20)."}), 400

    provider = build_tradier_provider()
    if provider is None:
        return jsonify({"error": "TRADIER_API_TOKEN no configurado."}), 400

    try:
        raw = provider.get_raw_quotes(symbols)
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502

    return jsonify({
        "queried_symbols": symbols,
        "returned_count": len(raw),
        "quotes": raw,
        "server_now_utc": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/diagnostics/forced-failover-test", methods=["POST"])
def api_diagnostics_forced_failover_test():
    """Prueba controlada de failover (Ricardo, 2026-08-07): fuerza un
    `ProviderError` idéntico al que devolvería Yahoo caído, desde un
    proveedor de prueba que nunca toca la red -- no afecta el Yahoo real
    ni el escaneo en curso -- y confirma con el `MultiProvider` REAL
    (`atlas_live.data_fusion.multi_provider`) más el `FinnhubProvider`
    REAL que `get_quote()` obtiene datos reales del segundo proveedor.
    Falla honesta con 400 si `FINNHUB_API_KEY` no está configurada."""
    from atlas.data.providers.base import DataProvider, ProviderError
    from atlas_live.data_fusion.finnhub_provider import FinnhubProvider
    from atlas_live.data_fusion.multi_provider import MultiProvider

    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        return jsonify({"error": "FINNHUB_API_KEY no configurada -- no se puede probar el respaldo real."}), 400

    class _SiempreCaido(DataProvider):
        """Doble de prueba -- simula a Yahoo caído sin tocar la red real."""

        def get_quote(self, symbol: str):
            raise ProviderError("Simulado: proveedor primario caído (prueba controlada).")

        def get_quotes(self, symbols):
            raise ProviderError("Simulado: proveedor primario caído (prueba controlada).")

        def get_history(self, symbol: str, period: str = "6mo", interval: str = "1d"):
            raise ProviderError("Simulado: proveedor primario caído (prueba controlada).")

    multi = MultiProvider([_SiempreCaido(), FinnhubProvider(api_key)])
    try:
        quote = multi.get_quote("AAPL")
    except ProviderError as exc:
        return jsonify({"error": f"Failover falló incluso con el respaldo real: {exc}"}), 502

    return jsonify({
        "failover_confirmado": quote.source == "finnhub",
        "provider_que_respondio": quote.source,
        "symbol": quote.symbol,
        "last_price": quote.last_price,
    })


SHUTDOWN_TIMEOUT_SECONDS = 60


def main() -> None:
    # Modo Interactivo Continuo (decisión de arquitectura, 2026-08-02):
    # Atlas escanea, actualiza el Ranking, el Memory Engine y el Prediction
    # Journal mientras esta app esté abierta. Al cerrarla (Ctrl+C u otra
    # señal que Werkzeug traduzca en que app.run() retorne), el `finally`
    # pide al hilo de fondo que termine su ciclo actual antes de salir --
    # el estado en sí ya vive en SQLite con commit inmediato por escritura
    # (Memory Store, Prediction Journal); lo único que agrega este cierre
    # prolijo es no cortar un ciclo a mitad de una escritura en curso.
    # `start_background_refresh()` ya se llamó a nivel de módulo (ver
    # arriba); acá no hace falta repetirlo (es idempotente igual).
    #
    # `host`/`port`: en Railway, gunicorn controla el bind directamente
    # (`--bind 0.0.0.0:$PORT`) y nunca ejecuta `main()` -- esto solo
    # importa para `python -m atlas_live.server` en local o en cualquier
    # otro entorno que sí llame a `app.run()`.
    port = int(os.environ.get("PORT", 5000))
    try:
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
    finally:
        scan_worker.request_stop()
        terminado = scan_worker.wait_until_stopped(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        if not terminado:
            print(f"Aviso: el ciclo de fondo no terminó dentro de {SHUTDOWN_TIMEOUT_SECONDS}s -- "
                  f"el estado ya escrito está a salvo (SQLite), pero el ciclo en curso pudo quedar incompleto.")


if __name__ == "__main__":
    main()
