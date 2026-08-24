"""Tests de catalyst_worker.py (2026-08-23). Proveedor falso (sin red),
DBs temporales para candidate_registry y catalyst_registry -- testea los
3 tiers como funciones "run once" (mismo criterio que
`radar_worker.run_sweep_once()`, nunca se testea el hilo daemon en sí)."""

import tempfile
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

from atlas.data.providers.base import ProviderError
from atlas_live.catalyst import catalyst_registry as creg
from atlas_live.catalyst import catalyst_worker as worker
from atlas_live.radar import candidate_registry as candreg

_ORIG_CREG_DB = creg.DB_PATH
_ORIG_CANDREG_DB = candreg.DB_PATH


def _fresh():
    creg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_worker_catalyst_{_uuid.uuid4().hex}.db"
    creg._schema_ready_for = None
    candreg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_worker_candidate_{_uuid.uuid4().hex}.db"
    candreg._schema_ready_for = None
    worker._tier3_cursor = 0


def _restore():
    creg.DB_PATH = _ORIG_CREG_DB
    candreg.DB_PATH = _ORIG_CANDREG_DB


class _FakeProvider:
    def __init__(self, news_by_symbol=None, calendar=None, raise_for=()):
        self.news_by_symbol = news_by_symbol or {}
        self.calendar = calendar or []
        self.raise_for = set(raise_for)
        self.news_calls = []
        self.calendar_calls = 0

    def get_company_news(self, symbol, from_date, to_date):
        self.news_calls.append(symbol)
        if symbol in self.raise_for:
            raise ProviderError(f"fallo simulado para {symbol}")
        return self.news_by_symbol.get(symbol, [])

    def get_earnings_calendar(self, from_date, to_date, symbol=None):
        self.calendar_calls += 1
        return self.calendar


def test_tier1_procesa_candidatas_del_dia_y_registra_poll_state():
    _fresh()
    try:
        market_date = "2026-08-23"
        now = datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc)
        candreg.record_detection(
            "ZYME", market_date, "regular", now.isoformat(), "sweep-1",
            price_at_detection=10.0, change_pct_at_detection=5.0,
            volume_at_detection=1000, average_volume_at_detection=200,
            relative_volume_at_detection=5.0, dollar_volume_at_detection=10000.0,
            gates_fired=[{"gate": "acceleration"}],
        )
        provider = _FakeProvider(news_by_symbol={
            "ZYME": [{"id": 1, "headline": "ZYME Announces Positive Phase 3 Topline Results",
                      "datetime": int(now.timestamp())}],
        })
        resultado = worker.run_tier1_once(provider, market_date, now, inter_call_delay_seconds=0.0)
        assert resultado["candidatas"] == 1
        assert resultado["eventos_procesados"] == 1
        assert resultado["errores"] == 0
        assert provider.news_calls == ["ZYME"]

        eventos = creg.get_events_for_ticker("ZYME")
        assert len(eventos) == 1
        assert eventos[0]["catalyst_type"] == "CLINICAL_TRIAL"

        poll = creg.get_poll_state("ZYME")
        assert poll["last_poll_ok"] == 1
        assert poll["n_events_found"] == 1
    finally:
        _restore()


def test_tier1_error_de_proveedor_no_tumba_el_resto_del_lote():
    _fresh()
    try:
        market_date = "2026-08-23"
        now = datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc)
        for ticker in ("AAA", "BBB"):
            candreg.record_detection(
                ticker, market_date, "regular", now.isoformat(), "sweep-1",
                price_at_detection=10.0, change_pct_at_detection=5.0,
                volume_at_detection=1000, average_volume_at_detection=200,
                relative_volume_at_detection=5.0, dollar_volume_at_detection=10000.0,
                gates_fired=[],
            )
        provider = _FakeProvider(
            news_by_symbol={"BBB": [{"id": 9, "headline": "BBB Reports Earnings", "datetime": int(now.timestamp())}]},
            raise_for={"AAA"},
        )
        resultado = worker.run_tier1_once(provider, market_date, now, inter_call_delay_seconds=0.0)
        assert resultado["candidatas"] == 2
        assert resultado["errores"] == 1
        assert resultado["eventos_procesados"] == 1  # BBB sí se procesó

        assert creg.get_poll_state("AAA")["last_poll_ok"] == 0
        assert creg.get_poll_state("BBB")["last_poll_ok"] == 1
    finally:
        _restore()


def test_tier2_procesa_calendario_completo_en_una_sola_llamada():
    _fresh()
    try:
        now = datetime(2026, 8, 21, tzinfo=timezone.utc)
        provider = _FakeProvider(calendar=[
            {"symbol": "AAA", "date": "2026-08-25", "hour": "bmo"},
            {"symbol": "BBB", "date": "2026-08-26", "hour": "amc"},
        ])
        resultado = worker.run_tier2_once(provider, now)
        assert provider.calendar_calls == 1  # UNA sola llamada para todo el universo
        assert resultado["filas"] == 2
        assert resultado["procesadas"] == 2
        assert len(creg.get_events_for_ticker("AAA")) == 1
        assert len(creg.get_events_for_ticker("BBB")) == 1
    finally:
        _restore()


def test_tier2_error_de_proveedor_registra_poll_state_y_no_revienta():
    _fresh()
    try:
        now = datetime(2026, 8, 21, tzinfo=timezone.utc)

        class _BoomProvider(_FakeProvider):
            def get_earnings_calendar(self, from_date, to_date, symbol=None):
                raise ProviderError("caído")

        resultado = worker.run_tier2_once(_BoomProvider(), now)
        assert resultado["procesadas"] == 0
        assert "error" in resultado
        assert creg.get_poll_state(worker.TIER2_POLL_STATE_KEY)["last_poll_ok"] == 0
    finally:
        _restore()


def test_tier3_avanza_el_cursor_round_robin_entre_llamadas():
    _fresh()
    try:
        now = datetime(2026, 8, 23, tzinfo=timezone.utc)
        simbolos = [f"SYM{i}" for i in range(45)]
        provider = _FakeProvider()

        lote1 = worker.run_tier3_once(provider, now, symbols=simbolos, batch_size=20, inter_call_delay_seconds=0.0)["batch"]
        lote2 = worker.run_tier3_once(provider, now, symbols=simbolos, batch_size=20, inter_call_delay_seconds=0.0)["batch"]
        lote3 = worker.run_tier3_once(provider, now, symbols=simbolos, batch_size=20, inter_call_delay_seconds=0.0)["batch"]

        assert lote1 == simbolos[0:20]
        assert lote2 == simbolos[20:40]
        assert lote3 == simbolos[40:45] + simbolos[0:15]  # da la vuelta (round-robin real)
    finally:
        _restore()


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
