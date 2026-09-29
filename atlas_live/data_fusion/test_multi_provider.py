"""Tests de MultiProvider -- failover (ya existente, cero cambios de
comportamiento) + circuit breaker de get_history() (2026-09-29, autorizado
explícitamente, ver `_history_breaker_*`/`CIRCUIT_BREAKER_*` en
multi_provider.py). Sin red -- proveedores falsos, control total del
tiempo vía monkeypatch de `time.monotonic`."""

import pandas as pd
import pytest

import atlas_live.data_fusion.multi_provider as mp
from atlas.data.providers.base import ProviderError, QuoteNotFoundError
from atlas_live.data_fusion.multi_provider import MultiProvider


class _FakeProvider:
    """Nombre de clase ÚNICO por instancia de test (ver `_named_provider_class`)
    -- el breaker es global por `type(provider).__name__`, así que 2 tests
    con la misma clase se pisarían entre sí si corrieran en paralelo."""

    def __init__(self):
        self.history_calls = 0
        self.quote_calls = 0
        self.next_error = None
        self.next_result = None

    def get_history(self, symbol, period="6mo", interval="1d"):
        self.history_calls += 1
        if self.next_error is not None:
            raise self.next_error
        return self.next_result if self.next_result is not None else pd.DataFrame({"Close": [1.0]})

    def get_quote(self, symbol):
        self.quote_calls += 1
        if self.next_error is not None:
            raise self.next_error
        raise NotImplementedError

    def get_quotes(self, symbols):
        raise NotImplementedError


_counter = [0]


def _named_provider_class():
    """Crea una subclase de _FakeProvider con nombre único -- aísla el
    estado del circuit breaker (global por nombre de clase) entre tests."""
    _counter[0] += 1
    return type(f"_FakeProvider{_counter[0]}", (_FakeProvider,), {})


@pytest.fixture(autouse=True)
def _reset_breaker_state():
    """El breaker es intencionalmente global (ver docstring del módulo) --
    se limpia antes/después de cada test para que no haya fugas entre tests."""
    mp._history_consecutive_failures.clear()
    mp._history_breaker_open_until.clear()
    yield
    mp._history_consecutive_failures.clear()
    mp._history_breaker_open_until.clear()


def test_failover_get_quote_sin_cambios():
    """Regresión pura -- comportamiento ya existente, sin tocar."""
    ClsA, ClsB = _named_provider_class(), _named_provider_class()
    a, b = ClsA(), ClsB()
    a.next_error = ProviderError("boom")
    b.next_result = None
    provider = MultiProvider([a, b])
    with pytest.raises(NotImplementedError):
        provider.get_quote("AAPL")
    assert a.quote_calls == 1


def test_get_history_exitoso_no_activa_breaker():
    Cls = _named_provider_class()
    fake = Cls()
    fake.next_result = pd.DataFrame({"Close": [42.0]})
    provider = MultiProvider([fake])
    r = provider.get_history("AAPL")
    assert r["Close"].iloc[0] == 42.0
    assert fake.history_calls == 1


def test_breaker_se_abre_tras_N_fallos_seguidos_y_deja_de_intentar():
    Cls = _named_provider_class()
    fake = Cls()
    fake.next_error = ProviderError("Too Many Requests")
    provider = MultiProvider([fake])

    for _ in range(mp.CIRCUIT_BREAKER_FAILURES):
        with pytest.raises(ProviderError):
            provider.get_history("AAPL")
    assert fake.history_calls == mp.CIRCUIT_BREAKER_FAILURES

    # Con el breaker ya abierto, el ÚNICO proveedor se salta -- nunca toca la red.
    with pytest.raises(ProviderError, match="cooldown"):
        provider.get_history("AAPL")
    assert fake.history_calls == mp.CIRCUIT_BREAKER_FAILURES  # sin incremento


def test_breaker_se_cierra_solo_tras_el_cooldown(monkeypatch):
    Cls = _named_provider_class()
    fake = Cls()
    fake.next_error = ProviderError("Too Many Requests")
    provider = MultiProvider([fake])

    fake_now = [1000.0]
    monkeypatch.setattr(mp.time, "monotonic", lambda: fake_now[0])

    for _ in range(mp.CIRCUIT_BREAKER_FAILURES):
        with pytest.raises(ProviderError):
            provider.get_history("AAPL")

    with pytest.raises(ProviderError, match="cooldown"):
        provider.get_history("AAPL")
    assert fake.history_calls == mp.CIRCUIT_BREAKER_FAILURES  # todavía en cooldown

    fake_now[0] += mp.CIRCUIT_BREAKER_COOLDOWN_SECONDS + 1  # avanza el reloj más allá del cooldown
    fake.next_error = None
    fake.next_result = pd.DataFrame({"Close": [7.0]})
    r = provider.get_history("AAPL")
    assert r["Close"].iloc[0] == 7.0
    assert fake.history_calls == mp.CIRCUIT_BREAKER_FAILURES + 1  # volvió a intentar, real


def test_exito_resetea_el_contador_de_fallos_seguidos():
    Cls = _named_provider_class()
    fake = Cls()
    provider = MultiProvider([fake])

    fake.next_error = ProviderError("boom")
    for _ in range(mp.CIRCUIT_BREAKER_FAILURES - 1):  # uno menos del umbral
        with pytest.raises(ProviderError):
            provider.get_history("AAPL")

    fake.next_error = None
    fake.next_result = pd.DataFrame({"Close": [1.0]})
    provider.get_history("AAPL")  # éxito -- resetea el contador

    fake.next_error = ProviderError("boom de nuevo")
    for _ in range(mp.CIRCUIT_BREAKER_FAILURES - 1):
        with pytest.raises(ProviderError):
            provider.get_history("AAPL")
    # todavía no debería abrir -- el contador se reseteó con el éxito de arriba
    fake.next_result = pd.DataFrame({"Close": [2.0]})
    fake.next_error = None
    r = provider.get_history("AAPL")
    assert r["Close"].iloc[0] == 2.0


def test_breaker_por_proveedor_no_afecta_al_otro_en_el_failover():
    """Yahoo con el breaker abierto -- Finnhub (el siguiente en la lista)
    sigue intentándose con normalidad, sin ningún efecto cruzado."""
    ClsYahoo, ClsFinnhub = _named_provider_class(), _named_provider_class()
    yahoo, finnhub = ClsYahoo(), ClsFinnhub()
    yahoo.next_error = ProviderError("Too Many Requests")
    finnhub.next_result = pd.DataFrame({"Close": [99.0]})
    provider = MultiProvider([yahoo, finnhub])

    for _ in range(mp.CIRCUIT_BREAKER_FAILURES):
        provider.get_history("AAPL")  # cae a Finnhub cada vez, nunca lanza
    assert yahoo.history_calls == mp.CIRCUIT_BREAKER_FAILURES

    # Yahoo abierto -- se salta, Finnhub responde directo.
    r = provider.get_history("AAPL")
    assert r["Close"].iloc[0] == 99.0
    assert yahoo.history_calls == mp.CIRCUIT_BREAKER_FAILURES  # sin incremento -- se saltó


def test_quote_not_found_error_no_activa_el_breaker():
    """QuoteNotFoundError se propaga tal cual (comportamiento ya existente,
    símbolo puntual, no es una falla del proveedor completo) -- nunca debe
    contar como fallo del circuit breaker."""
    Cls = _named_provider_class()
    fake = Cls()
    fake.next_error = QuoteNotFoundError("XXXX")
    provider = MultiProvider([fake])

    for _ in range(mp.CIRCUIT_BREAKER_FAILURES + 2):
        with pytest.raises(QuoteNotFoundError):
            provider.get_history("XXXX")
    assert fake.history_calls == mp.CIRCUIT_BREAKER_FAILURES + 2  # nunca se saltó, breaker nunca se abrió


def test_breaker_es_global_entre_instancias_de_multiprovider():
    """Reproduce el escenario real: `get_default_provider()` construye un
    MultiProvider NUEVO cada ciclo de escaneo -- el breaker debe seguir
    protegiendo aunque la instancia cambie."""
    Cls = _named_provider_class()
    fake_a = Cls()
    fake_a.next_error = ProviderError("Too Many Requests")
    provider_ciclo_1 = MultiProvider([fake_a])
    for _ in range(mp.CIRCUIT_BREAKER_FAILURES):
        with pytest.raises(ProviderError):
            provider_ciclo_1.get_history("AAPL")

    fake_b = Cls()  # MISMA clase (mismo nombre) -- otra instancia, ciclo nuevo
    provider_ciclo_2 = MultiProvider([fake_b])
    with pytest.raises(ProviderError, match="cooldown"):
        provider_ciclo_2.get_history("AAPL")
    assert fake_b.history_calls == 0  # nunca llegó a tocar la red -- el breaker viajó con la clase


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        mp._history_consecutive_failures.clear()
        mp._history_breaker_open_until.clear()
        try:
            fn()
            print("PASS", fn.__name__)
            p += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
            traceback.print_exc()
            f += 1
    print(f"--- {p} passed, {f} failed ---")
