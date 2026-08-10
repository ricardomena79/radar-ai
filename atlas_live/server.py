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
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from atlas.data.collectors.data_collector import DataCollector
from atlas_live import evolution_panel, explosive_config, hot_quote, performance_panel, scan_worker
from atlas_live.backtest import seed_import
from atlas_live.data_fusion.registry import get_default_provider
from atlas_live.memory import classifier, exit_journal, explosion_history, learning_status, live_integration, market_hours, observation_recovery, prediction_journal
from atlas_live.mission_control import heartbeat, timeline
from atlas_live.predictive_engine import prediction_log
from atlas_live.signals import signal_registry, signal_tracker

STATIC_DIR = Path(__file__).parent / "static"

app = Flask(__name__, static_folder=None)

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


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.route("/api/ranking")
def api_ranking():
    return jsonify(scan_worker.STATE.snapshot())


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
    Explosivo) del último ciclo -- Cabina del Piloto, Panel 2 en adelante.
    Mismo mecanismo ya validado en atlas_live/memory/live_integration.py,
    servido tal cual, sin recalcular nada acá."""
    return jsonify(scan_worker.get_memory_ranking())


@app.route("/api/memory-engine")
def api_memory_engine():
    """Estado real del Memory Engine (Entregables 4-5) -- Cabina del
    Piloto, Panel 9. Reutiliza la evidencia ya cacheada por día en
    `live_integration`, sin recalcular nada nuevo."""
    return jsonify(live_integration.get_memory_engine_summary())


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


@app.route("/api/learning-status")
def api_learning_status():
    """Estructura preparada para los dos indicadores permanentes de la
    barra superior (🧠 Aprendizaje, 🎯 Confianza de Atlas), aprobados el
    2026-08-02. No calcula nada todavía -- ver docstring de
    `learning_status.py` para el porqué de cada valor."""
    return jsonify({
        "learning": learning_status.get_learning_status(),
        "confidence": learning_status.get_atlas_confidence(),
    })


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
