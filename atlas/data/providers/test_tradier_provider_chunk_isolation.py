"""Tests de aislamiento de errores por chunk en `TradierProvider.get_quotes()`/
`get_quotes_by_chunk()` (2026-09-14, auditoría read-only + fix autorizado
explícitamente).

Hallazgo real que motiva este fix: antes, un solo chunk de ~250 símbolos
que fallaba (timeout de red, HTTP != 200) propagaba la excepción de
inmediato, perdiendo TAMBIÉN los chunks anteriores ya resueltos con éxito
en la MISMA llamada -- con ~22-31 chunks por barrido (universo ampliado),
un solo fallo transitorio podía dejar al radar sin ninguna detección ese
ciclo. Ahora cada chunk se intenta de forma independiente.

`TRADIER_CHUNK_SIZE` se monkeypatchea a un valor chico (2) para poder
ejercitar varios chunks sin tener que generar cientos de símbolos --
mismo patrón ya usado en `atlas_live/test_market_view.py`
(`monkeypatch.setattr(mv, "TRADIER_CHUNK_SIZE", chunk_size)`)."""

import pytest

import atlas.data.providers.tradier_provider as tp
from atlas.data.providers.base import ProviderError


def _provider():
    return tp.TradierProvider(api_token="fake-token-solo-para-test")


def _quote_response(symbols):
    """Respuesta cruda de Tradier para un chunk exitoso -- un `last`
    presente por símbolo, suficiente para que `_to_quote()` no descarte
    nada."""
    items = [{"symbol": s, "last": 10.0, "prevclose": 10.0, "change_percentage": 0.0} for s in symbols]
    return {"quotes": {"quote": items if len(items) > 1 else items[0]}}


def _fake_get_factory(fail_chunks_containing):
    """Devuelve un `_get` falso que lanza `ProviderError` cuando el chunk
    solicitado contiene alguno de los símbolos marcados como "roto" --
    símbolos reales de esta suite, nunca network real."""
    def _fake_get(self, path, params):
        assert path == "/v1/markets/quotes"
        chunk_symbols = params["symbols"].split(",")
        if any(s in fail_chunks_containing for s in chunk_symbols):
            raise ProviderError(f"Tradier devolvió HTTP 503 para {path}: fallo simulado")
        return _quote_response(chunk_symbols)
    return _fake_get


def test_todos_los_chunks_ok_resultado_identico_al_actual(monkeypatch):
    """Camino feliz -- comportamiento idéntico al de antes del fix: todos
    los símbolos resueltos, sin ningún error."""
    monkeypatch.setattr(tp, "TRADIER_CHUNK_SIZE", 2)
    monkeypatch.setattr(tp.TradierProvider, "_get", _fake_get_factory(fail_chunks_containing=set()))

    symbols = ["AAA", "BBB", "CCC", "DDD", "EEE"]  # 3 chunks: [AAA,BBB], [CCC,DDD], [EEE]
    quotes, diag = _provider().get_quotes_by_chunk(symbols)

    assert {q.symbol for q in quotes} == set(symbols)
    assert diag.total_chunks == 3
    assert diag.chunks_ok == 3
    assert diag.chunks_error == 0
    assert diag.chunk_errors == []

    # get_quotes() (interfaz pública, sin cambios de firma) da el mismo resultado.
    quotes_public = _provider().get_quotes(symbols)
    assert {q.symbol for q in quotes_public} == set(symbols)


def test_un_chunk_falla_los_demas_se_conservan(monkeypatch):
    """Un chunk roto (contiene CCC/DDD) -- los otros 2 chunks exitosos
    (AAA/BBB y EEE) se conservan, nada se pierde por su culpa."""
    monkeypatch.setattr(tp, "TRADIER_CHUNK_SIZE", 2)
    monkeypatch.setattr(tp.TradierProvider, "_get", _fake_get_factory(fail_chunks_containing={"CCC", "DDD"}))

    symbols = ["AAA", "BBB", "CCC", "DDD", "EEE"]
    quotes, diag = _provider().get_quotes_by_chunk(symbols)

    resolved = {q.symbol for q in quotes}
    assert resolved == {"AAA", "BBB", "EEE"}
    assert "CCC" not in resolved and "DDD" not in resolved
    assert diag.total_chunks == 3
    assert diag.chunks_ok == 2
    assert diag.chunks_error == 1
    assert len(diag.chunk_errors) == 1
    assert "HTTP 503" in diag.chunk_errors[0]  # el error real, nunca oculto

    # get_quotes() no lanza -- fallo parcial nunca debe propagarse como excepción.
    quotes_public = _provider().get_quotes(symbols)
    assert {q.symbol for q in quotes_public} == {"AAA", "BBB", "EEE"}


def test_multiples_chunks_fallan_los_exitosos_siguen_disponibles(monkeypatch):
    """2 de 4 chunks fallan -- los 2 exitosos siguen disponibles, cada
    fallo queda registrado por separado."""
    monkeypatch.setattr(tp, "TRADIER_CHUNK_SIZE", 2)
    # chunks: [AAA,BBB] ok, [CCC,DDD] roto, [EEE,FFF] ok, [GGG,HHH] roto
    symbols = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"]
    monkeypatch.setattr(tp.TradierProvider, "_get", _fake_get_factory(fail_chunks_containing={"CCC", "GGG"}))

    quotes, diag = _provider().get_quotes_by_chunk(symbols)
    resolved = {q.symbol for q in quotes}
    assert resolved == {"AAA", "BBB", "EEE", "FFF"}
    assert diag.total_chunks == 4
    assert diag.chunks_ok == 2
    assert diag.chunks_error == 2
    assert len(diag.chunk_errors) == 2


def test_todos_los_chunks_fallan_comportamiento_seguro_y_reportado(monkeypatch):
    """Todos los chunks fallan -- debe lanzar `ProviderError` (mismo
    "fallo total" de siempre), agregando los mensajes de cada chunk, sin
    devolver ninguna cotización parcial ni inventada."""
    monkeypatch.setattr(tp, "TRADIER_CHUNK_SIZE", 2)
    monkeypatch.setattr(tp.TradierProvider, "_get", _fake_get_factory(fail_chunks_containing={"AAA", "BBB", "CCC", "DDD"}))

    symbols = ["AAA", "BBB", "CCC", "DDD"]  # 2 chunks, ambos rotos
    with pytest.raises(ProviderError) as excinfo:
        _provider().get_quotes_by_chunk(symbols)
    assert "2 chunk" in str(excinfo.value)  # cuenta clara de cuántos fallaron

    # get_quotes() (interfaz pública) también debe seguir lanzando -- mismo
    # comportamiento de "fallo total" que antes del fix.
    with pytest.raises(ProviderError):
        _provider().get_quotes(symbols)


def test_ningun_chunk_fallido_provoca_datos_inventados(monkeypatch):
    """Los símbolos de un chunk roto NUNCA aparecen en el resultado con
    ningún valor (ni `None`, ni un precio inventado) -- simplemente están
    ausentes, igual que un símbolo no reconocido por Tradier."""
    monkeypatch.setattr(tp, "TRADIER_CHUNK_SIZE", 2)
    monkeypatch.setattr(tp.TradierProvider, "_get", _fake_get_factory(fail_chunks_containing={"CCC", "DDD"}))

    symbols = ["AAA", "BBB", "CCC", "DDD"]
    quotes, _diag = _provider().get_quotes_by_chunk(symbols)
    resolved_symbols = {q.symbol for q in quotes}
    assert "CCC" not in resolved_symbols
    assert "DDD" not in resolved_symbols
    assert len(quotes) == 2  # exactamente AAA/BBB, nada más, nada inventado


def test_single_chunk_caso_market_view_comportamiento_sin_cambios(monkeypatch):
    """`market_view.py::_fetch_chunk()` siempre pasa <=250 símbolos --
    internamente es UN solo chunk. Con un único chunk, "algunos fallan,
    otros no" es imposible por definición -- el resultado debe ser
    IDÉNTICO al de antes del fix: todo OK, o `ProviderError` si ese único
    chunk falla. Confirma que el fix no cambia nada para este caller real."""
    monkeypatch.setattr(tp, "TRADIER_CHUNK_SIZE", 250)  # tamaño real de producción
    symbols = ["AAA", "BBB", "CCC"]  # <=250, un solo chunk interno

    # Caso OK -- sin cambios.
    monkeypatch.setattr(tp.TradierProvider, "_get", _fake_get_factory(fail_chunks_containing=set()))
    quotes = _provider().get_quotes(symbols)
    assert {q.symbol for q in quotes} == set(symbols)

    # Caso roto -- sigue lanzando ProviderError, igual que siempre.
    monkeypatch.setattr(tp.TradierProvider, "_get", _fake_get_factory(fail_chunks_containing={"AAA"}))
    with pytest.raises(ProviderError):
        _provider().get_quotes(symbols)


def test_symbols_vacio_sin_chunks_no_lanza(monkeypatch):
    """Lista vacía -- 0 chunks intentados -- debe devolver vacío sin
    lanzar (mismo comportamiento que antes del fix, no es un "fallo
    total" porque no hubo ningún chunk que intentar)."""
    monkeypatch.setattr(tp.TradierProvider, "_get", lambda self, path, params: (_ for _ in ()).throw(
        AssertionError("no debería llamarse con símbolos vacíos")
    ))
    quotes, diag = _provider().get_quotes_by_chunk([])
    assert quotes == []
    assert diag.total_chunks == 0
    assert diag.chunks_ok == 0
    assert diag.chunks_error == 0


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
