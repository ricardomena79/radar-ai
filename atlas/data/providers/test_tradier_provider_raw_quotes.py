"""Test aislado (sin red) de `TradierProvider.get_raw_quotes()` (2026-08-18,
auditoría del caso real "precio de Tradier congelado en el cierre de ayer
durante premarket" -- ver server.py::api_diagnostics_tradier_raw_quotes).

`get_raw_quotes()` debe devolver el JSON crudo de Tradier sin pasar por
`_to_quote()`, para poder inspeccionar campos que hoy se descartan
(`bid`, `ask`, `bid_date`, `ask_date`) además de los que ya se usan
(`last`, `trade_date`, `volume`, `change_percentage`)."""

from atlas.data.providers.tradier_provider import TradierProvider


def _provider():
    return TradierProvider(api_token="fake-token-solo-para-test")


def test_get_raw_quotes_devuelve_lista_de_dicts_sin_parsear(monkeypatch):
    raw_item = {
        "symbol": "NVDA", "last": 225.01, "trade_date": 1755460800000,
        "bid": 226.10, "ask": 226.15, "bid_date": 1755511200000, "ask_date": 1755511200000,
        "volume": 1234567, "change_percentage": 0.42,
    }

    def _fake_get(self, path, params):
        assert path == "/v1/markets/quotes"
        assert params["symbols"] == "NVDA"
        return {"quotes": {"quote": raw_item}}

    monkeypatch.setattr(TradierProvider, "_get", _fake_get)
    result = _provider().get_raw_quotes(["NVDA"])

    assert result == [raw_item]
    # campos que _to_quote() ignora hoy siguen presentes acá, sin filtrar
    assert result[0]["bid"] == 226.10
    assert result[0]["ask"] == 226.15
    assert result[0]["bid_date"] == 1755511200000


def test_get_raw_quotes_maneja_respuesta_de_lista_multisimbolo(monkeypatch):
    items = [
        {"symbol": "TSLA", "last": 339.3, "trade_date": 1755460800000},
        {"symbol": "AMD", "last": 506.0, "trade_date": 1755460800000},
    ]

    def _fake_get(self, path, params):
        return {"quotes": {"quote": items}}

    monkeypatch.setattr(TradierProvider, "_get", _fake_get)
    result = _provider().get_raw_quotes(["TSLA", "AMD"])

    assert len(result) == 2
    assert {r["symbol"] for r in result} == {"TSLA", "AMD"}


def test_get_raw_quotes_simbolo_no_encontrado_no_lanza():
    # Tradier responde sin `quote` (ausente) para símbolos no reconocidos --
    # get_raw_quotes debe devolver lista vacía, nunca lanzar (a diferencia
    # de get_quote(), que lanza QuoteNotFoundError -- acá no aplica porque
    # no hay UN símbolo específico que deba existir).
    import atlas.data.providers.tradier_provider as tp

    def _fake_get(self, path, params):
        return {"quotes": {"unmatched_symbols": {"symbol": "ZZZZNOTREAL"}}}

    original = tp.TradierProvider._get
    tp.TradierProvider._get = _fake_get
    try:
        result = _provider().get_raw_quotes(["ZZZZNOTREAL"])
        assert result == []
    finally:
        tp.TradierProvider._get = original


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn(None) if fn.__code__.co_argcount else fn()
            print("PASS", fn.__name__); p += 1
        except Exception as e:
            print("FAIL", fn.__name__, e); traceback.print_exc(); f += 1
    print(f"--- {p} passed, {f} failed ---")
