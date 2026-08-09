"""Tests del canal de actualización rápida (Plan A + Plan B) -- lógica pura,
sin red ni servidor (ver DECISION_LOG.md "Optimización de latencia").

Todo corre contra un DataProvider de mentira (stub), nunca contra Yahoo ni
Finnhub: son tests offline, deterministas, aptos para la suite base.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pandas as pd

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.models.quote import Quote
from atlas.data.providers.base import (
    DataProvider,
    ProviderError,
    QuoteNotFoundError,
    RateLimitError,
)
from atlas_live import hot_quote


def _quote(symbol: str) -> Quote:
    return Quote(
        symbol=symbol,
        name=symbol,
        last_price=100.0,
        change_percent=1.5,
        volume=1_000,
        open=99.0,
        high=101.0,
        low=98.0,
        previous_close=99.0,
        timestamp=datetime(2026, 8, 7, 13, 45, 7, tzinfo=timezone.utc),
        source="finnhub",
        price_type="regular",
        market_state="REGULAR",
    )


class _StubProvider(DataProvider):
    """Devuelve un Quote fijo, salvo para los símbolos que se le indiquen que
    deben fallar (con la excepción pedida)."""

    def __init__(self, fail: dict | None = None) -> None:
        self._fail = fail or {}
        self.calls: List[str] = []

    def get_quote(self, symbol: str) -> Quote:
        self.calls.append(symbol)
        if symbol in self._fail:
            raise self._fail[symbol]
        return _quote(symbol)

    def get_quotes(self, symbols):
        return [self.get_quote(s) for s in symbols]

    def get_history(self, symbol, period="6mo", interval="1d") -> pd.DataFrame:
        raise NotImplementedError


class _FlakyProvider(DataProvider):
    """Falla las primeras `fail_times` llamadas de un símbolo con la excepción
    dada, y a partir de ahí devuelve un Quote normal -- para probar el
    reintento del canal rápido ante fallos transitorios."""

    def __init__(self, fail_times: int, exc: Exception) -> None:
        self._fail_times = fail_times
        self._exc = exc
        self.calls: List[str] = []

    def get_quote(self, symbol: str) -> Quote:
        self.calls.append(symbol)
        if len(self.calls) <= self._fail_times:
            raise self._exc
        return _quote(symbol)

    def get_quotes(self, symbols):
        return [self.get_quote(s) for s in symbols]

    def get_history(self, symbol, period="6mo", interval="1d") -> pd.DataFrame:
        raise NotImplementedError


# --------------------------- parse_symbols ---------------------------

def test_parse_symbols_caps_at_two():
    assert hot_quote.parse_symbols("AAA,BBB,CCC") == ["AAA", "BBB"]


def test_parse_symbols_dedupes_and_uppercases():
    assert hot_quote.parse_symbols(" spy , spy , qqq ") == ["SPY", "QQQ"]


def test_parse_symbols_empty():
    assert hot_quote.parse_symbols("") == []
    assert hot_quote.parse_symbols(None) == []
    assert hot_quote.parse_symbols("  ,  ,") == []


# --------------------------- collect_hot_quotes ---------------------------

def test_collect_ok_two_symbols():
    collector = DataCollector(_StubProvider())
    now = datetime(2026, 8, 7, 13, 45, 10, tzinfo=timezone.utc)
    out = hot_quote.collect_hot_quotes(["SPY", "QQQ"], collector, now=now)

    assert out["server_time"] == now.isoformat()
    assert len(out["quotes"]) == 2
    spy = out["quotes"][0]
    assert spy["symbol"] == "SPY"
    assert spy["status"] == "ok"
    assert spy["price"] == 100.0
    assert spy["change_pct"] == 1.5
    assert spy["price_type"] == "regular"
    assert spy["market_state"] == "REGULAR"
    assert spy["source"] == "finnhub"
    # price_as_of debe ser el timestamp real del proveedor, en ISO -- es lo
    # que el frontend usa para calcular la antigüedad exacta.
    assert spy["price_as_of"] == "2026-08-07T13:45:07+00:00"


def test_missing_symbol_does_not_break_channel():
    # QQQ no existe para el proveedor: sale "unavailable", pero SPY sigue OK.
    collector = DataCollector(_StubProvider(fail={"QQQ": QuoteNotFoundError("QQQ")}))
    out = hot_quote.collect_hot_quotes(["SPY", "QQQ"], collector)

    by_symbol = {q["symbol"]: q for q in out["quotes"]}
    assert by_symbol["SPY"]["status"] == "ok"
    assert by_symbol["QQQ"]["status"] == "unavailable"
    assert by_symbol["QQQ"]["reason"] == "QuoteNotFoundError"


def test_rate_limit_marked_unavailable():
    # RateLimitError (subclase de ProviderError) también se captura por
    # símbolo, sin tumbar el canal ni requerir un `except Exception`.
    collector = DataCollector(_StubProvider(fail={"SPY": RateLimitError("rate limit")}))
    out = hot_quote.collect_hot_quotes(["SPY"], collector)

    assert out["quotes"][0]["status"] == "unavailable"
    assert out["quotes"][0]["reason"] == "RateLimitError"


def test_generic_provider_error_marked_unavailable():
    collector = DataCollector(_StubProvider(fail={"SPY": ProviderError("boom")}))
    out = hot_quote.collect_hot_quotes(["SPY"], collector)
    assert out["quotes"][0]["status"] == "unavailable"
    assert out["quotes"][0]["reason"] == "ProviderError"


def test_empty_symbols_returns_server_time_and_no_quotes():
    now = datetime(2026, 8, 7, 13, 45, 10, tzinfo=timezone.utc)
    # Con lista vacía no se toca el collector (puede ser None, como en el
    # endpoint cuando no llegan símbolos válidos).
    out = hot_quote.collect_hot_quotes([], None, now=now)
    assert out["server_time"] == now.isoformat()
    assert out["quotes"] == []


# --------------------------- reintento del canal rápido ---------------------------

def _noop_sleep(_seconds):
    return None


def test_retry_recovers_transient_provider_error():
    # Falla la 1ra vez (ProviderError transitorio: timeout/SSL) y se recupera
    # en el 2do intento -> el símbolo termina OK gracias al reintento.
    provider = _FlakyProvider(fail_times=1, exc=ProviderError("timeout"))
    collector = DataCollector(provider)
    out = hot_quote.collect_hot_quotes(
        ["SPY"], collector, max_attempts=3, retry_backoff_seconds=0.3, sleep=_noop_sleep,
    )
    assert out["quotes"][0]["status"] == "ok"
    assert len(provider.calls) == 2  # 1 fallo + 1 éxito


def test_retry_gives_up_after_max_attempts():
    # ProviderError persistente en los 3 intentos -> unavailable, exactamente
    # 3 llamadas (no un bucle infinito).
    provider = _FlakyProvider(fail_times=99, exc=ProviderError("caído"))
    collector = DataCollector(provider)
    out = hot_quote.collect_hot_quotes(
        ["SPY"], collector, max_attempts=3, retry_backoff_seconds=0.3, sleep=_noop_sleep,
    )
    assert out["quotes"][0]["status"] == "unavailable"
    assert out["quotes"][0]["reason"] == "ProviderError"
    assert len(provider.calls) == 3


def test_retry_recovers_transient_quote_not_found():
    # Bajo throttling, Yahoo devuelve "no encontrado" para un símbolo real de
    # forma transitoria (verificado en prod con BTC-USD). Con reintento, un
    # QuoteNotFoundError que se recupera al 2do intento termina OK.
    provider = _FlakyProvider(fail_times=1, exc=QuoteNotFoundError("BTC-USD"))
    collector = DataCollector(provider)
    out = hot_quote.collect_hot_quotes(
        ["BTC-USD"], collector, max_attempts=3, retry_backoff_seconds=0.3, sleep=_noop_sleep,
    )
    assert out["quotes"][0]["status"] == "ok"
    assert len(provider.calls) == 2


def test_default_does_not_retry_quote_not_found():
    # Sin reintento (default), un símbolo inexistente sale "unavailable" en
    # una sola llamada -- comportamiento del canal intacto.
    provider = _FlakyProvider(fail_times=99, exc=QuoteNotFoundError("XXX"))
    collector = DataCollector(provider)
    out = hot_quote.collect_hot_quotes(["XXX"], collector)
    assert out["quotes"][0]["status"] == "unavailable"
    assert out["quotes"][0]["reason"] == "QuoteNotFoundError"
    assert len(provider.calls) == 1


def test_retry_does_not_retry_rate_limit():
    # RateLimitError no se reintenta dentro del mismo request (1 sola llamada).
    provider = _FlakyProvider(fail_times=99, exc=RateLimitError("429"))
    collector = DataCollector(provider)
    out = hot_quote.collect_hot_quotes(
        ["SPY"], collector, max_attempts=3, retry_backoff_seconds=0.3, sleep=_noop_sleep,
    )
    assert out["quotes"][0]["status"] == "unavailable"
    assert out["quotes"][0]["reason"] == "RateLimitError"
    assert len(provider.calls) == 1


def test_default_is_no_retry():
    # Sin especificar max_attempts, el comportamiento es idéntico al anterior:
    # un solo intento, sin reintento.
    provider = _FlakyProvider(fail_times=99, exc=ProviderError("x"))
    collector = DataCollector(provider)
    out = hot_quote.collect_hot_quotes(["SPY"], collector)
    assert out["quotes"][0]["status"] == "unavailable"
    assert len(provider.calls) == 1


# --------------------------- garantía de aislamiento ---------------------------

def test_hot_quote_runs_no_scanner_or_scoring_logic():
    """El canal rápido NO debe correr el scanner del universo, ni Radar, ni
    Memory, ni el Motor Predictivo -- solo la cotización cruda. Se verifica
    que el módulo no depende de ninguno de esos componentes."""
    source = Path(hot_quote.__file__).read_text(encoding="utf-8")
    for forbidden in ("scan_worker", "explosive_engine", "predictive_engine",
                      "live_integration", "run_scan_once"):
        assert forbidden not in source, f"hot_quote no debe referenciar {forbidden}"
