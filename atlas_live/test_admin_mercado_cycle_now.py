"""Tests de POST /api/admin/mercado-cycle-now (2026-08-31, autorizado
explícitamente) -- mismo patrón que test_admin_backfill_close_return.py:
sin red, sin arrancar hilos de fondo reales al importar server. El ciclo
real de Mercado se ejercita contra un universo/proveedor falso (mismo
estilo que test_market_view.py) -- nunca contra Tradier real."""

import os
from types import SimpleNamespace

import atlas_live.backtest.seed_import as _si
import atlas_live.radar.radar_worker as _rw
import atlas_live.scan_worker as _sw

_orig_seed = _si.import_all_seeds
_orig_refresh = _sw.start_background_refresh
_orig_radar = _rw.start_universe_radar
_si.import_all_seeds = lambda *a, **k: None
_sw.start_background_refresh = lambda *a, **k: None
_rw.start_universe_radar = lambda *a, **k: None
try:
    from atlas_live import server  # noqa: E402
finally:
    _si.import_all_seeds = _orig_seed
    _sw.start_background_refresh = _orig_refresh
    _rw.start_universe_radar = _orig_radar

import atlas_live.market_view as mv  # noqa: E402
from atlas.data.universe.universe import Asset  # noqa: E402


def _client():
    return server.app.test_client()


class _FakeQuote:
    def __init__(self, symbol, last_price, change_percent, price_is_stale=False):
        from datetime import datetime, timezone
        self.symbol = symbol
        self.last_price = last_price
        self.change_percent = change_percent
        self.timestamp = datetime.now(timezone.utc)
        self.price_is_stale = price_is_stale
        self.previous_close = last_price - change_percent if change_percent else last_price


class _FakeTradierProvider:
    def __init__(self):
        self.calls = 0

    def get_quotes(self, symbols):
        self.calls += 1
        return [_FakeQuote(s, 10.0, 1.0, price_is_stale=False) for s in symbols]


def _patch_universo_falso(monkeypatch, provider):
    universo = {"AAA": Asset(symbol="AAA", name="AAA Inc.", type="EQUITY"),
                "BBB": Asset(symbol="BBB", name="BBB Inc.", type="EQUITY")}
    monkeypatch.setattr(mv, "load_universe", lambda: universo)
    monkeypatch.setattr(mv, "build_tradier_provider", lambda: provider)
    monkeypatch.setattr(mv, "normalize", lambda s: SimpleNamespace(query_symbol=s, state="ACTIVE"))
    monkeypatch.setattr(server, "market_view", mv)


def test_sin_token_rechaza_siempre():
    old = os.environ.pop("ATLAS_ADMIN_TOKEN", None)
    try:
        r = _client().post("/api/admin/mercado-cycle-now")
        assert r.status_code == 403
    finally:
        if old is not None:
            os.environ["ATLAS_ADMIN_TOKEN"] = old


def test_token_incorrecto_rechaza(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    try:
        r = _client().post("/api/admin/mercado-cycle-now?token=incorrecto")
        assert r.status_code == 403
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_token_correcto_ejecuta_un_solo_ciclo_real(monkeypatch):
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    mv._last_known_by_symbol.clear()
    provider = _FakeTradierProvider()
    _patch_universo_falso(monkeypatch, provider)
    snap_antes = mv.get_market_snapshot()
    cycles_antes = snap_antes["cycles_total"]
    try:
        r = _client().post("/api/admin/mercado-cycle-now?token=secreto-real")
        assert r.status_code == 200
        body = r.get_json()
        assert body["disparado"] is True
        assert body["total_universe"] == 2
        assert body["filas_mostradas"] == 2
        assert body["cycles_total"] == cycles_antes + 1  # exactamente UN ciclo mas
        assert body["cycles_error"] == snap_antes["cycles_error"]
        assert provider.calls == 1  # Tradier se consultó una sola vez -- un solo ciclo real

        # GET /api/mercado ahora refleja el ciclo recién disparado
        r2 = _client().get("/api/mercado")
        assert r2.status_code == 200
        snap = r2.get_json()
        assert snap["total_universe"] == 2
        assert len(snap["rows"]) == 2
    finally:
        del os.environ["ATLAS_ADMIN_TOKEN"]


def test_no_dispara_un_segundo_ciclo_si_ya_hay_uno_en_curso(monkeypatch):
    """No-reentrancia real: si el lock ya está tomado (ciclo en curso, real
    o simulado), el endpoint NUNCA intenta un segundo ciclo -- ni siquiera
    llega a consultar Tradier."""
    os.environ["ATLAS_ADMIN_TOKEN"] = "secreto-real"
    provider = _FakeTradierProvider()
    _patch_universo_falso(monkeypatch, provider)
    assert mv._lock.acquire(blocking=False)  # simula un ciclo real ya en curso
    try:
        r = _client().post("/api/admin/mercado-cycle-now?token=secreto-real")
        assert r.status_code == 202
        body = r.get_json()
        assert body["disparado"] is False
        assert body["motivo"] == "ciclo_ya_en_curso_no_se_disparo_otro"
        assert provider.calls == 0  # nunca se llegó a llamar a Tradier -- ningún ciclo nuevo arrancó
    finally:
        mv._lock.release()
        del os.environ["ATLAS_ADMIN_TOKEN"]
