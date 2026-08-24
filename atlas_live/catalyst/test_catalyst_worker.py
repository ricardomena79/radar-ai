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
    worker._cooldown_until = 0.0
    worker._cooldown_reason = None


def _restore():
    creg.DB_PATH = _ORIG_CREG_DB
    candreg.DB_PATH = _ORIG_CANDREG_DB


class _FakeProvider:
    def __init__(self, news_by_symbol=None, calendar=None, raise_for=(), error_message=None,
                 calendar_error_message=None):
        self.news_by_symbol = news_by_symbol or {}
        self.calendar = calendar or []
        self.raise_for = set(raise_for)
        self.error_message = error_message  # None -> mensaje genérico (no 401/429)
        self.calendar_error_message = calendar_error_message
        self.news_calls = []
        self.calendar_calls = 0

    def get_company_news(self, symbol, from_date, to_date):
        self.news_calls.append(symbol)
        if symbol in self.raise_for:
            raise ProviderError(self.error_message or f"fallo simulado para {symbol}")
        return self.news_by_symbol.get(symbol, [])

    def get_earnings_calendar(self, from_date, to_date, symbol=None):
        self.calendar_calls += 1
        if self.calendar_error_message:
            raise ProviderError(self.calendar_error_message)
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


# ---------------------------------------------------------------------------
# Cooldown/backoff real ante 401/429 (2026-08-24, hallazgo de verificación
# en vivo: la key configurada quedó devolviendo HTTP 401 en TODOS los
# endpoints tras una corrida sin ningún corte -- esto prueba que un 401/429
# real corta el lote AHORA, en vez de seguir probando el resto del universo
# contra una key bloqueada.)
# ---------------------------------------------------------------------------

def test_tier1_401_corta_el_lote_y_no_prueba_el_resto():
    _fresh()
    try:
        market_date = "2026-08-23"
        now = datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc)
        for ticker in ("AAA", "BBB", "CCC"):
            candreg.record_detection(
                ticker, market_date, "regular", now.isoformat(), "sweep-1",
                price_at_detection=10.0, change_pct_at_detection=5.0,
                volume_at_detection=1000, average_volume_at_detection=200,
                relative_volume_at_detection=5.0, dollar_volume_at_detection=10000.0,
                gates_fired=[],
            )
        provider = _FakeProvider(
            raise_for={"AAA"}, error_message='Finnhub devolvió HTTP 401 para noticias de \'AAA\': {"error":"Invalid API key"}',
        )
        resultado = worker.run_tier1_once(provider, market_date, now, inter_call_delay_seconds=0.0)
        assert resultado["cooldown_triggered"] is True
        assert "401" in resultado["cooldown_reason"]
        # AAA es la primera candidata (orden de detected_at) -- BBB/CCC NUNCA deben haberse llamado.
        assert provider.news_calls == ["AAA"]
    finally:
        _restore()


def test_tier1_error_generico_no_activa_cooldown_sigue_con_el_resto():
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
        provider = _FakeProvider(raise_for={"AAA"})  # mensaje genérico, no 401/429
        resultado = worker.run_tier1_once(provider, market_date, now, inter_call_delay_seconds=0.0)
        assert resultado["cooldown_triggered"] is False
        assert provider.news_calls == ["AAA", "BBB"]  # siguió con el resto, como antes
    finally:
        _restore()


def test_tier2_429_señala_cooldown():
    _fresh()
    try:
        now = datetime(2026, 8, 21, tzinfo=timezone.utc)
        provider = _FakeProvider(calendar_error_message="Finnhub devolvió HTTP 429 para calendario de earnings: rate limited")
        resultado = worker.run_tier2_once(provider, now)
        assert resultado["cooldown_triggered"] is True
        assert "429" in resultado["cooldown_reason"]
    finally:
        _restore()


def test_tier3_401_corta_el_lote_y_no_prueba_el_resto():
    _fresh()
    try:
        now = datetime(2026, 8, 23, tzinfo=timezone.utc)
        simbolos = ["AAA", "BBB", "CCC", "DDD"]
        provider = _FakeProvider(
            raise_for={"BBB"}, error_message='Finnhub devolvió HTTP 401 para noticias de \'BBB\': {"error":"Invalid API key"}',
        )
        resultado = worker.run_tier3_once(provider, now, symbols=simbolos, batch_size=20, inter_call_delay_seconds=0.0)
        assert resultado["cooldown_triggered"] is True
        assert provider.news_calls == ["AAA", "BBB"]  # CCC/DDD nunca se llamaron
    finally:
        _restore()


def test_in_cooldown_y_enter_cooldown_puros():
    _fresh()
    try:
        assert worker.in_cooldown() is False
        worker._enter_cooldown("prueba")
        assert worker.in_cooldown() is True
        estado = worker.cooldown_status()
        assert estado["in_cooldown"] is True
        assert estado["reason"] == "prueba"
        assert estado["cooldown_until_epoch"] is not None
    finally:
        _restore()


def test_run_cycle_no_llama_al_proveedor_si_esta_en_cooldown(monkeypatch):
    _fresh()
    try:
        worker._enter_cooldown("cooldown activo de una prueba anterior")

        llamado = {"n": 0}

        def _boom():
            llamado["n"] += 1
            raise AssertionError("build_catalyst_provider() NUNCA debe llamarse en cooldown")

        monkeypatch.setattr(worker.prov, "build_catalyst_provider", _boom)
        worker._run_cycle()  # no debe lanzar -- tiene que salir ANTES de tocar el proveedor
        assert llamado["n"] == 0
    finally:
        _restore()


def test_run_cycle_401_en_tier1_detiene_tier2_y_tier3_del_mismo_ciclo(monkeypatch):
    _fresh()
    try:
        market_date = "2026-08-23"
        candreg.record_detection(
            "AAA", market_date, "regular", datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc).isoformat(),
            "sweep-1", price_at_detection=10.0, change_pct_at_detection=5.0,
            volume_at_detection=1000, average_volume_at_detection=200,
            relative_volume_at_detection=5.0, dollar_volume_at_detection=10000.0, gates_fired=[],
        )
        provider = _FakeProvider(
            raise_for={"AAA"}, error_message='Finnhub devolvió HTTP 401 para noticias de \'AAA\': {"error":"Invalid API key"}',
        )
        monkeypatch.setattr(worker.prov, "build_catalyst_provider", lambda: provider)
        monkeypatch.setattr(worker.market_hours, "market_date", lambda now=None: market_date)
        monkeypatch.setattr(worker.radar_worker, "get_last_quotes", lambda: {})
        monkeypatch.setattr(worker, "INTER_TIER_DELAY_SECONDS", 0.0)

        worker._run_cycle()

        assert worker.in_cooldown() is True
        assert provider.calendar_calls == 0  # Tier 2 nunca se disparó en este ciclo
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
