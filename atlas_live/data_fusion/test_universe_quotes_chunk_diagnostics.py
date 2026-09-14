"""Test de integración: `fetch_universe_quotes()` con un `TradierProvider`
que tiene chunks parcialmente rotos (2026-09-14, auditoría read-only + fix
autorizado explícitamente).

Confirma que el diagnóstico por chunk de `TradierProvider.get_quotes_by_chunk()`
llega intacto hasta `UniverseQuotesDiagnostics` -- nunca oculto -- y que un
chunk roto no vacía el barrido completo (el bug real que motivó el fix)."""

from atlas.data.models.quote import Quote
from atlas_live.data_fusion.universe_quotes import fetch_universe_quotes


class _ChunkDiag:
    def __init__(self, total_chunks, chunks_ok, chunks_error, chunk_errors):
        self.total_chunks = total_chunks
        self.chunks_ok = chunks_ok
        self.chunks_error = chunks_error
        self.chunk_errors = chunk_errors


class _FakeTradierProviderPartialFailure:
    """Simula exactamente lo que `TradierProvider.get_quotes_by_chunk()`
    devuelve cuando 1 de 2 chunks falla -- sin tocar red real."""

    def get_quotes_by_chunk(self, symbols):
        # Símbolos "malos" (segunda mitad) simulan el chunk roto.
        mitad = len(symbols) // 2
        buenos = symbols[:mitad] or symbols  # al menos 1 símbolo bueno si la lista es chica
        quotes = [
            Quote(
                symbol=s, name=s, last_price=10.0, change_percent=0.0, volume=100,
                open=10.0, high=10.0, low=10.0, previous_close=10.0,
            )
            for s in buenos
        ]
        diag = _ChunkDiag(
            total_chunks=2, chunks_ok=1, chunks_error=1,
            chunk_errors=["Tradier devolvió HTTP 503 para /v1/markets/quotes: fallo simulado"],
        )
        return quotes, diag


class _FakeTradierProviderAllOk:
    def get_quotes_by_chunk(self, symbols):
        quotes = [
            Quote(
                symbol=s, name=s, last_price=10.0, change_percent=0.0, volume=100,
                open=10.0, high=10.0, low=10.0, previous_close=10.0,
            )
            for s in symbols
        ]
        diag = _ChunkDiag(total_chunks=1, chunks_ok=1, chunks_error=0, chunk_errors=[])
        return quotes, diag


def test_chunk_roto_no_vacia_el_barrido_completo():
    """El hallazgo real que motiva el fix: con el chunk roto, el universo
    completo NO debe quedar sin datos -- los símbolos del chunk que sí
    funcionó siguen resueltos."""
    symbols = ["AAA", "BBB", "CCC", "DDD"]
    result = fetch_universe_quotes(symbols, tradier_provider=_FakeTradierProviderPartialFailure(), fallback_provider=None)

    assert len(result.quotes) > 0  # NUNCA cero -- ese era el bug real
    assert set(result.quotes.keys()) == {"AAA", "BBB"}  # la mitad "buena"


def test_diagnostico_por_chunk_llega_intacto_nunca_oculto():
    symbols = ["AAA", "BBB", "CCC", "DDD"]
    result = fetch_universe_quotes(symbols, tradier_provider=_FakeTradierProviderPartialFailure(), fallback_provider=None)

    diag = result.diagnostics
    assert diag.tradier_chunks_ok == 1
    assert diag.tradier_chunks_error == 1
    assert len(diag.tradier_chunk_errors) == 1
    assert "HTTP 503" in diag.tradier_chunk_errors[0]
    # `tradier_error` (fallo TOTAL) sigue en None -- esto fue un fallo PARCIAL,
    # nunca debe confundirse con el caso "Tradier completamente caído".
    assert diag.tradier_error is None


def test_simbolos_del_chunk_roto_quedan_marcados_sin_ocultar_el_motivo():
    symbols = ["AAA", "BBB", "CCC", "DDD"]
    result = fetch_universe_quotes(symbols, tradier_provider=_FakeTradierProviderPartialFailure(), fallback_provider=None)

    trace_ccc = result.traces["CCC"]
    assert trace_ccc.resultado_final == "sin_datos"
    assert "chunk(s)" in trace_ccc.motivo_fallo  # menciona explícitamente que hubo chunks rotos


def test_todos_los_chunks_ok_comportamiento_identico_al_actual():
    symbols = ["AAA", "BBB", "CCC"]
    result = fetch_universe_quotes(symbols, tradier_provider=_FakeTradierProviderAllOk(), fallback_provider=None)

    assert set(result.quotes.keys()) == {"AAA", "BBB", "CCC"}
    assert result.diagnostics.tradier_chunks_ok == 1
    assert result.diagnostics.tradier_chunks_error == 0
    assert result.diagnostics.tradier_chunk_errors == []
    assert result.diagnostics.tradier_error is None


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__); p += 1
        except Exception as e:
            print("FAIL", fn.__name__, e); traceback.print_exc(); f += 1
    print(f"--- {p} passed, {f} failed ---")
