"""Caché negativo de DataCollector.get_history() (2026-09-29, autorizado
explícitamente -- ver DEFAULT_HISTORY_ERROR_TTL en data_collector.py para
la evidencia real que motivó esto: hasta 5 llamadas redundantes por
símbolo por ciclo, cada una repitiendo un fallo ya conocido de Yahoo).
Sin red -- proveedor falso, control total del tiempo."""

import pandas as pd
import pytest

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.providers.base import ProviderError, QuoteNotFoundError


class _FakeProvider:
    def __init__(self):
        self.history_calls = []
        self.quote_calls = []
        self.next_error = None
        self.next_result = None
        self.next_quote = None

    def get_history(self, symbol, period="6mo", interval="1d"):
        self.history_calls.append((symbol, period, interval))
        if self.next_error is not None:
            raise self.next_error
        return self.next_result if self.next_result is not None else pd.DataFrame({"Close": [1.0]})

    def get_quote(self, symbol):
        self.quote_calls.append(symbol)
        if self.next_error is not None:
            raise self.next_error
        if self.next_quote is not None:
            return self.next_quote
        from atlas.data.models.quote import Quote
        return Quote(symbol=symbol, name=None, last_price=1.0, change_percent=0.0, volume=1,
                     open=1.0, high=1.0, low=1.0, previous_close=1.0)

    def get_quotes(self, symbols):
        raise NotImplementedError


def test_fallo_se_cachea_y_evita_segunda_llamada_de_red():
    provider = _FakeProvider()
    provider.next_error = ProviderError("Too Many Requests")
    collector = DataCollector(provider, history_error_ttl=90.0)

    with pytest.raises(ProviderError):
        collector.get_history("AAPL", period="1d", interval="5m")
    with pytest.raises(ProviderError):
        collector.get_history("AAPL", period="1d", interval="5m")
    with pytest.raises(ProviderError):
        collector.get_history("AAPL", period="1d", interval="5m")

    assert len(provider.history_calls) == 1  # solo el primer intento tocó la red


def test_fallo_cacheado_no_afecta_otro_symbol_ni_otro_period_interval():
    provider = _FakeProvider()
    provider.next_error = ProviderError("Too Many Requests")
    collector = DataCollector(provider, history_error_ttl=90.0)

    with pytest.raises(ProviderError):
        collector.get_history("AAPL", period="1d", interval="5m")
    with pytest.raises(ProviderError):
        collector.get_history("NVDA", period="1d", interval="5m")  # otro símbolo
    with pytest.raises(ProviderError):
        collector.get_history("AAPL", period="6mo", interval="1d")  # otro period/interval

    assert len(provider.history_calls) == 3  # las 3 combinaciones son independientes


def test_exito_real_no_queda_bloqueado_por_un_fallo_previo_ya_expirado():
    provider = _FakeProvider()
    provider.next_error = ProviderError("Too Many Requests")
    collector = DataCollector(provider, history_error_ttl=0.01)  # TTL casi nulo para el test

    with pytest.raises(ProviderError):
        collector.get_history("AAPL", period="1d", interval="5m")

    import time
    time.sleep(0.02)  # deja expirar el caché negativo

    provider.next_error = None
    provider.next_result = pd.DataFrame({"Close": [123.0]})
    result = collector.get_history("AAPL", period="1d", interval="5m")
    assert result["Close"].iloc[0] == 123.0
    assert len(provider.history_calls) == 2  # el segundo SÍ volvió a intentar, TTL ya vencido


def test_exito_se_sigue_cacheando_normalmente_sin_cambios():
    provider = _FakeProvider()
    provider.next_result = pd.DataFrame({"Close": [55.0]})
    collector = DataCollector(provider)

    r1 = collector.get_history("AAPL", period="1d", interval="5m")
    r2 = collector.get_history("AAPL", period="1d", interval="5m")
    assert r1["Close"].iloc[0] == r2["Close"].iloc[0] == 55.0
    assert len(provider.history_calls) == 1  # servido desde caché de éxito, sin cambios


def test_quote_not_found_error_tambien_se_cachea_como_negativo():
    """QuoteNotFoundError es subclase de ProviderError -- mismo criterio de
    caché negativo, evita repetir una búsqueda que ya se sabe sin resultado."""
    provider = _FakeProvider()
    provider.next_error = QuoteNotFoundError("XXXX")
    collector = DataCollector(provider, history_error_ttl=90.0)

    with pytest.raises(QuoteNotFoundError):
        collector.get_history("XXXX", period="1d", interval="5m")
    with pytest.raises(QuoteNotFoundError):
        collector.get_history("XXXX", period="1d", interval="5m")

    assert len(provider.history_calls) == 1


def test_get_quote_fallo_no_afecta_otro_symbol():
    provider = _FakeProvider()
    provider.next_error = ProviderError("Too Many Requests")
    collector = DataCollector(provider, quote_error_ttl=90.0)

    with pytest.raises(ProviderError):
        collector.get_quote("AAPL")
    with pytest.raises(ProviderError):
        collector.get_quote("NVDA")
    assert provider.quote_calls == ["AAPL", "NVDA"]  # ambos tocaron la red, son símbolos distintos


def test_get_quote_exito_se_sigue_cacheando_normalmente():
    provider = _FakeProvider()
    collector = DataCollector(provider)
    q1 = collector.get_quote("AAPL")
    q2 = collector.get_quote("AAPL")
    assert q1.symbol == q2.symbol == "AAPL"
    assert provider.quote_calls == ["AAPL"]  # segundo servido desde caché de éxito, sin cambios


def test_get_quote_tambien_tiene_cache_negativo_2026_09_29():
    """Extensión el mismo día (autorizado explícitamente) -- get_quote()
    gana el MISMO caché negativo que get_history(), tras confirmar en
    producción que Yahoo Y Finnhub pueden rate-limitar cotizaciones al
    mismo tiempo. get_quotes() (el lote) NO se toca -- ver docstring de
    DataCollector.get_quotes(), el circuit breaker de MultiProvider ya
    protege ese camino."""

    class _QuoteProvider:
        def __init__(self):
            self.calls = 0

        def get_quote(self, symbol):
            self.calls += 1
            raise ProviderError("boom")

        def get_quotes(self, symbols):
            raise NotImplementedError

        def get_history(self, symbol, period="6mo", interval="1d"):
            raise NotImplementedError

    provider = _QuoteProvider()
    collector = DataCollector(provider, quote_error_ttl=90.0)
    with pytest.raises(ProviderError):
        collector.get_quote("AAPL")
    with pytest.raises(ProviderError):
        collector.get_quote("AAPL")
    assert provider.calls == 1  # el segundo se sirvió del caché negativo, sin red


def test_get_quotes_lote_sin_cambios_de_comportamiento():
    """get_quotes() (el LOTE, distinto de get_quote()) no gana caché
    negativo propio en esta extensión -- el circuit breaker de
    MultiProvider ya cubre la redundancia entre ciclos para el camino de
    lote, sin necesitar una segunda capa acá."""

    class _QuotesProvider:
        def __init__(self):
            self.calls = 0

        def get_quote(self, symbol):
            raise NotImplementedError

        def get_quotes(self, symbols):
            self.calls += 1
            raise ProviderError("boom")

        def get_history(self, symbol, period="6mo", interval="1d"):
            raise NotImplementedError

    provider = _QuotesProvider()
    collector = DataCollector(provider)
    with pytest.raises(ProviderError):
        collector.get_quotes(["AAPL"])
    with pytest.raises(ProviderError):
        collector.get_quotes(["AAPL"])
    assert provider.calls == 2  # sin cambios -- comportamiento intacto


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
            p += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
            traceback.print_exc()
            f += 1
    print(f"--- {p} passed, {f} failed ---")
