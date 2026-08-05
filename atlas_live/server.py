"""Servidor de Atlas Live.

Expone el estado cacheado de `scan_worker` como JSON, y sirve el
dashboard estático. No calcula nada por sí mismo: cada endpoint delega en
`scan_worker`, que a su vez delega en Atlas Core. Cero lógica de negocio
en esta capa.

Uso local: `python -m atlas_live.server` (arranca en http://localhost:5000).
En producción (Railway) el proceso lo levanta gunicorn apuntando a
`atlas_live.server:app`, por lo que el refresco en segundo plano se arranca
a nivel de módulo (más abajo), no dentro de `main()`.
"""

import os
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

from atlas_live import scan_worker
from atlas_live.memory.seed import ensure_seeded

STATIC_DIR = Path(__file__).parent / "static"

app = Flask(__name__, static_folder=None)

# Bootstrap del Memory Engine: si el SQLite local no tiene ninguna
# observación todavía (clon nuevo, deploy nuevo, máquina nueva), la carga
# desde el seed versionado en Git. No hace nada si ya hay datos -- ver
# atlas_live/memory/seed.py. Antes de start_background_refresh() a
# propósito: el primer ciclo de escaneo ya puede encontrar el Memory
# Engine poblado.
ensure_seeded()
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


@app.route("/api/learning")
def api_learning():
    try:
        return jsonify(scan_worker.get_learning_summary())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/explosive-diagnostics")
def api_explosive_diagnostics():
    """Diagnóstico del Radar Explosivo: embudo de filtros + tabla de auditoría
    del último escaneo completo. No se pide en el polling normal de
    /api/ranking -- solo cuando el usuario abre la vista Diagnóstico."""
    try:
        diagnostics = scan_worker.get_explosive_diagnostics()
        if diagnostics is None:
            return jsonify({"available": False})
        return jsonify({"available": True, **diagnostics})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/memory-engine")
def api_memory_engine():
    """Tasas base reales del Memory Engine (73.123 observaciones de
    backtest al portar este módulo) -- solo lectura, no se pide en el
    polling normal de /api/ranking."""
    try:
        return jsonify(scan_worker.get_memory_engine_summary())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/rescan", methods=["POST"])
def api_rescan():
    """Fuerza un escaneo inmediato (además del refresco automático periódico)."""
    import threading
    threading.Thread(target=scan_worker.run_scan_once, daemon=True).start()
    return jsonify({"status": "escaneo iniciado"})


def main() -> None:
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
