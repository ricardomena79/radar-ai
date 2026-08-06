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

from flask import Flask, jsonify, send_from_directory

from atlas_live import scan_worker
from atlas_live.memory import exit_journal, learning_status, live_integration, market_hours, prediction_journal
from atlas_live.mission_control import heartbeat, timeline

STATIC_DIR = Path(__file__).parent / "static"

app = Flask(__name__, static_folder=None)

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
    })


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
    """Fuerza un escaneo inmediato (además del refresco automático periódico)."""
    import threading
    threading.Thread(target=scan_worker.run_scan_once, daemon=True).start()
    return jsonify({"status": "escaneo iniciado"})


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
