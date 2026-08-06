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

from flask import Flask, jsonify, request, send_from_directory

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


@app.route("/api/diagnostics/providers")
def api_diagnostics_providers():
    """TEMPORAL -- verificación puntual de que Railway realmente arma un
    MultiProvider Yahoo+Finnhub, pedida el 2026-08-06 para confirmar la
    configuración real del deploy sin adivinar (ver DECISIONES.md).
    Candidato a quedar protegido para diagnóstico interno en vez de
    borrarse -- decisión pendiente hasta ver esta evidencia.

    Nunca devuelve el valor de ningún secreto -- solo su presencia (bool),
    el nombre de las clases de proveedor realmente activas, y el resultado
    de una consulta real de prueba a Finnhub (ok/inválida/ausente),
    reutilizando exactamente `verify_failover.check_finnhub_isolated()`
    (no se inventa un chequeo nuevo)."""
    from atlas.data.providers import _configured_provider_names, get_default_provider
    from atlas.data.providers.multi_provider import MultiProvider
    from atlas.data.providers.verify_failover import check_finnhub_isolated

    configured = _configured_provider_names()
    finnhub_key_present = bool(os.environ.get("FINNHUB_API_KEY"))

    try:
        provider = get_default_provider()
        active_provider_class = type(provider).__name__
        is_multi_provider = isinstance(provider, MultiProvider)
        wrapped_provider_classes = provider.provider_names if is_multi_provider else None
    except Exception:
        active_provider_class = None
        is_multi_provider = False
        wrapped_provider_classes = None

    if not finnhub_key_present:
        finnhub_authentication = "missing"
    else:
        finnhub_authentication = "ok" if check_finnhub_isolated()["ok"] else "invalid"

    return jsonify({
        "active_provider_class": active_provider_class,
        "is_multi_provider": is_multi_provider,
        "wrapped_provider_classes": wrapped_provider_classes,
        "configured_providers": configured,
        "finnhub_key_present": finnhub_key_present,
        "finnhub_authentication": finnhub_authentication,
        "yahoo_provider_present": "yahoo_finance" in configured,
    })


@app.route("/api/diagnostics/forced-failover-test")
def api_diagnostics_forced_failover_test():
    """TEMPORAL -- prueba controlada de failover real, pedida el 2026-08-06
    (ver DECISIONES.md). Fuerza un RateLimitError como el que Yahoo devolvió
    de verdad en producción (mismo texto, mismo tipo de excepción) desde un
    proveedor de prueba que nunca toca la red -- no desactiva el Yahoo real
    ni afecta el escaneo en curso -- y arma un MultiProvider con ese
    proveedor forzado a fallar + FinnhubProvider real (la key configurada
    en Railway). Si `get_quote`/`get_quotes` devuelven una cotización real,
    solo puede haber venido de Finnhub -- el primer proveedor no puede
    responder nunca.

    Alcance deliberado: prueba el mecanismo de MultiProvider con datos
    reales de Finnhub, no reemplaza el Yahoo real del escaneo en vivo --
    hacer eso sería degradar a propósito el ranking en producción, fuera
    del alcance de esta verificación."""
    from atlas.data.providers.base import DataProvider, ProviderError, RateLimitError
    from atlas.data.providers.finnhub import FinnhubProvider
    from atlas.data.providers.multi_provider import MultiProvider

    class _SimulatedYahooRateLimit(DataProvider):
        def get_quote(self, symbol):
            raise RateLimitError(
                "Yahoo Finance limitó la tasa de consultas: Too Many Requests "
                "(SIMULADO para esta prueba -- no es una falla real de Yahoo)"
            )

        def get_quotes(self, symbols):
            raise RateLimitError(
                "Yahoo Finance limitó la tasa de consultas: Too Many Requests "
                "(SIMULADO para esta prueba -- no es una falla real de Yahoo)"
            )

        def get_history(self, symbol, period="6mo", interval="1d"):
            raise ProviderError("simulado, no usado en esta prueba")

    symbols = [s.strip().upper() for s in request.args.get("symbols", "AAPL,MSFT,TSLA").split(",") if s.strip()]
    result = {
        "symbols_requested": symbols,
        "simulated_failure": "RateLimitError (Yahoo Finance) -- generado localmente, sin red",
    }

    if not os.environ.get("FINNHUB_API_KEY"):
        result["error"] = "FINNHUB_API_KEY no está configurada -- no se puede probar failover real a Finnhub"
        return jsonify(result), 400

    provider = MultiProvider([_SimulatedYahooRateLimit(), FinnhubProvider()])

    try:
        quote = provider.get_quote(symbols[0])
        result["get_quote"] = {
            "ok": True,
            "symbol": quote.symbol,
            "served_by": "FinnhubProvider (el primer proveedor fue forzado a fallar -- no puede haber respondido)",
            "last_price": quote.last_price,
            "previous_close": quote.previous_close,
            "change_percent": quote.change_percent,
        }
    except Exception as exc:
        result["get_quote"] = {"ok": False, "error": str(exc)}

    try:
        quotes = provider.get_quotes(symbols)
        result["get_quotes"] = {
            "ok": len(quotes) > 0,
            "requested_count": len(symbols),
            "received_count": len(quotes),
            "served_by": "FinnhubProvider (el primer proveedor fue forzado a fallar -- no puede haber respondido)",
            "quotes": [
                {"symbol": q.symbol, "last_price": q.last_price, "previous_close": q.previous_close}
                for q in quotes
            ],
        }
    except Exception as exc:
        result["get_quotes"] = {"ok": False, "error": str(exc)}

    return jsonify(result)


def main() -> None:
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
