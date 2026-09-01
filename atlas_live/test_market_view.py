"""Tests de Mercado (2026-08-29, autorizado explícitamente; ampliado a
EQUITY+ETF+ETN el 2026-08-30). Sin red -- `TradierProvider`/`load_universe()`
se reemplazan por versiones falsas dentro del módulo
(`mv.build_tradier_provider`/`mv.load_universe`), mismo patrón de
monkey-patching ya usado en `test_scan_stability.py`."""

import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import atlas_live.market_view as mv
from atlas.data.universe.universe import Asset


class _FakeQuote:
    def __init__(self, symbol, last_price, change_percent, timestamp=None, price_is_stale=False, previous_close=None):
        self.symbol = symbol
        self.last_price = last_price
        self.change_percent = change_percent
        self.timestamp = timestamp or datetime.now(timezone.utc)
        self.price_is_stale = price_is_stale
        self.previous_close = previous_close


class _FakeTradierProvider:
    """`get_quotes(symbols)` -- misma firma real, sin red. Cada llamada
    representa UN chunk. `fail_on_calls` (set de índices, 0-based) simula
    un chunk que lanza una excepción real."""

    def __init__(self, quotes_by_symbol, fail_on_calls=None):
        self.quotes_by_symbol = quotes_by_symbol
        self.fail_on_calls = fail_on_calls or set()
        self.calls = []

    def get_quotes(self, symbols):
        idx = len(self.calls)
        self.calls.append(list(symbols))
        if idx in self.fail_on_calls:
            raise RuntimeError("fallo simulado del proveedor para este chunk")
        return [self.quotes_by_symbol[s] for s in symbols if s in self.quotes_by_symbol]


def _asset(symbol, type_="EQUITY"):
    return Asset(symbol=symbol, name=f"{symbol} Inc.", type=type_)


def _fake_normalize(symbol):
    # 1:1, sin remapeo -- suficiente para estos tests (la normalización
    # uno-a-muchos ya está probada en universe_quotes.py, no se reimplementa acá).
    return SimpleNamespace(query_symbol=symbol, state="ACTIVE")


def _patch(monkeypatch, universe_symbols, quotes_by_symbol, fail_on_calls=None, chunk_size=None,
           types_by_symbol=None, yahoo_quotes_by_symbol=None, finnhub_quotes_by_symbol=None,
           session="regular"):
    """Por defecto, Yahoo/Finnhub quedan como no-op (nunca red real, nunca
    fallback) -- exactamente el mismo comportamiento que antes del
    multi-fuente para todos los tests que no pasan
    `yahoo_quotes_by_symbol`/`finnhub_quotes_by_symbol` explícitamente.
    `session` (default "regular", una sesión activa real) evita que estos
    tests dependan del reloj real -- sin este patch, `market_hours.get_session()`
    real podría devolver "closed" según el día/hora en que corran los
    tests, y el guard nuevo de sesión (2026-09-01) bloquearía el fallback
    en tests que no tienen nada que ver con esa regla."""
    types_by_symbol = types_by_symbol or {}
    assets = [_asset(s, types_by_symbol.get(s, "EQUITY")) for s in universe_symbols]
    universe_dict = {a.symbol: a for a in assets}
    provider = _FakeTradierProvider(quotes_by_symbol, fail_on_calls=fail_on_calls)
    monkeypatch.setattr(mv, "load_universe", lambda: universe_dict)
    monkeypatch.setattr(mv, "build_tradier_provider", lambda: provider)
    monkeypatch.setattr(mv, "normalize", _fake_normalize)
    # Solo se fuerza la sesión "actual" (llamada sin `now`, usada por
    # `_run_cycle_body()` para decidir si el fallback puede activarse) --
    # la clasificación de sesión de un timestamp puntual (`get_session(now=...)`,
    # usada por el resolver para etiquetar el dato ganador) sigue siendo
    # la función real, sin esto los tests de sesión del dato (H/I/J) se
    # rompen (mismo módulo `market_hours` compartido por ambos).
    real_get_session = mv.market_hours.get_session

    def _fake_get_session(now=None):
        if now is not None:
            return real_get_session(now=now)
        return session

    monkeypatch.setattr(mv.market_hours, "get_session", _fake_get_session)
    if chunk_size is not None:
        monkeypatch.setattr(mv, "TRADIER_CHUNK_SIZE", chunk_size)

    yahoo_quotes_by_symbol = yahoo_quotes_by_symbol or {}
    finnhub_quotes_by_symbol = finnhub_quotes_by_symbol or {}

    def _stats(attempted, success):
        return {"attempted": attempted, "success": success, "errors": attempted - success, "aborted": 0}

    def _fake_yahoo_batch(symbols):
        found = {s: yahoo_quotes_by_symbol[s] for s in symbols if s in yahoo_quotes_by_symbol}
        return found, _stats(len(symbols), len(found))

    def _fake_finnhub_batch(symbols, finnhub_provider):
        found = {s: finnhub_quotes_by_symbol[s] for s in symbols if s in finnhub_quotes_by_symbol}
        return found, _stats(len(symbols), len(found))

    monkeypatch.setattr(mv, "_fetch_yahoo_batch", _fake_yahoo_batch)
    monkeypatch.setattr(mv, "_fetch_finnhub_batch", _fake_finnhub_batch)
    monkeypatch.setattr(mv, "_build_finnhub_provider", lambda: object() if finnhub_quotes_by_symbol else None)
    return provider


# --- A: universo exacto -- solo lo que load_universe() devuelve, sin agregar nada ---

def test_A_universo_es_exactamente_load_universe(monkeypatch):
    mv._last_known_by_symbol.clear()
    quotes = {"AAA": _FakeQuote("AAA", 10.0, 5.0), "BBB": _FakeQuote("BBB", 20.0, -2.0)}
    _patch(monkeypatch, ["AAA", "BBB"], quotes)
    duration = mv.run_market_cycle_once()
    assert duration is not None
    snap = mv.get_market_snapshot()
    assert snap["total_universe"] == 2
    assert {r["symbol"] for r in snap["rows"]} == {"AAA", "BBB"}


# --- A2: EQUITY + ETF + ETN conviven en el MISMO ranking (2026-08-30) ---

def test_A2_equity_etf_etn_conviven_en_un_solo_ranking(monkeypatch):
    mv._last_known_by_symbol.clear()
    quotes = {
        "EQ1": _FakeQuote("EQ1", 10.0, 15.0),
        "ETF1": _FakeQuote("ETF1", 20.0, 8.0),
        "ETN1": _FakeQuote("ETN1", 30.0, -4.0),
    }
    _patch(monkeypatch, ["EQ1", "ETF1", "ETN1"], quotes,
           types_by_symbol={"EQ1": "EQUITY", "ETF1": "ETF", "ETN1": "ETN"})
    mv.run_market_cycle_once()
    rows = mv.get_market_snapshot()["rows"]
    # un solo ranking, ordenado por change_pct sin importar el tipo de instrumento
    assert [r["symbol"] for r in rows] == ["EQ1", "ETF1", "ETN1"]


# --- B: ningún símbolo fuera del universo provisto, sin importar el tipo ---

def test_B_no_incluye_simbolos_fuera_del_universo_provisto(monkeypatch):
    mv._last_known_by_symbol.clear()
    quotes = {"AAA": _FakeQuote("AAA", 10.0, 5.0), "NO_DEBERIA_APARECER": _FakeQuote("NO_DEBERIA_APARECER", 1.0, 1.0)}
    _patch(monkeypatch, ["AAA"], quotes)  # el universo (load_universe) NO incluye el segundo símbolo
    mv.run_market_cycle_once()
    snap = mv.get_market_snapshot()
    assert {r["symbol"] for r in snap["rows"]} == {"AAA"}


# --- C: ranking descendente correcto ---

def test_C_ranking_descendente_correcto(monkeypatch):
    mv._last_known_by_symbol.clear()
    quotes = {
        "LOW": _FakeQuote("LOW", 10.0, -5.0),
        "HIGH": _FakeQuote("HIGH", 10.0, 18.5),
        "MID": _FakeQuote("MID", 10.0, 3.2),
    }
    _patch(monkeypatch, ["LOW", "HIGH", "MID"], quotes)
    mv.run_market_cycle_once()
    rows = mv.get_market_snapshot()["rows"]
    assert [r["symbol"] for r in rows] == ["HIGH", "MID", "LOW"]
    assert [r["rank"] for r in rows] == [1, 2, 3]


# --- D: el ranking cambia cuando cambian los precios ---

def test_D_ranking_se_recalcula_con_precios_nuevos(monkeypatch):
    mv._last_known_by_symbol.clear()
    quotes_ciclo1 = {"A": _FakeQuote("A", 10.0, 2.0), "B": _FakeQuote("B", 10.0, 9.0)}
    provider = _patch(monkeypatch, ["A", "B"], quotes_ciclo1)
    mv.run_market_cycle_once()
    assert [r["symbol"] for r in mv.get_market_snapshot()["rows"]] == ["B", "A"]

    # "A" sube fuerte -> debe pasar al frente en el ciclo siguiente, sin
    # arrastre ni posición fija (caso real pedido: #35 con +2% que sube a
    # +9% debe subir de inmediato, no esperar).
    provider.quotes_by_symbol = {"A": _FakeQuote("A", 10.0, 25.0), "B": _FakeQuote("B", 10.0, 9.0)}
    mv.run_market_cycle_once()
    assert [r["symbol"] for r in mv.get_market_snapshot()["rows"]] == ["A", "B"]


# --- E: batch dividido correctamente en chunks ---

def test_E_batch_dividido_en_chunks_correctos(monkeypatch):
    mv._last_known_by_symbol.clear()
    symbols = [f"SYM{i}" for i in range(7)]
    quotes = {s: _FakeQuote(s, 10.0, 1.0) for s in symbols}
    provider = _patch(monkeypatch, symbols, quotes, chunk_size=3)
    mv.run_market_cycle_once()
    # 7 símbolos, chunks de 3 -> 3 llamadas (3+3+1), ningún símbolo repetido entre chunks.
    assert len(provider.calls) == 3
    assert sorted(len(c) for c in provider.calls) == [1, 3, 3]
    todos = [s for chunk in provider.calls for s in chunk]
    assert sorted(todos) == sorted(symbols)  # cada símbolo pedido exactamente una vez
    assert mv.get_market_snapshot()["chunks_total"] == 3


# --- F: la paralelización no genera duplicados ---

def test_F_paralelizacion_no_genera_duplicados(monkeypatch):
    mv._last_known_by_symbol.clear()
    symbols = [f"SYM{i}" for i in range(20)]
    quotes = {s: _FakeQuote(s, 10.0, float(i)) for i, s in enumerate(symbols)}
    _patch(monkeypatch, symbols, quotes, chunk_size=4)  # 5 chunks en paralelo
    mv.run_market_cycle_once()
    rows = mv.get_market_snapshot()["rows"]
    simbolos_en_filas = [r["symbol"] for r in rows]
    assert len(simbolos_en_filas) == len(set(simbolos_en_filas))  # sin duplicados
    assert len(rows) == 20


# --- G: error de un chunk no destruye todo el snapshot ---

def test_G_error_de_un_chunk_no_destruye_el_snapshot(monkeypatch):
    mv._last_known_by_symbol.clear()
    symbols = [f"SYM{i}" for i in range(9)]
    quotes = {s: _FakeQuote(s, 10.0, float(i)) for i, s in enumerate(symbols)}
    # chunk_size=3 -> 3 chunks; el chunk en la posición 1 (orden de
    # ENVÍO, no necesariamente de finalización) falla.
    provider = _patch(monkeypatch, symbols, quotes, chunk_size=3)

    # Falla determinísticamente el chunk que contiene "SYM3" (segundo
    # chunk armado con chunk_size=3), sin importar el orden real de
    # finalización entre los workers paralelos.
    def _get_quotes_fail_second(syms):
        provider.calls.append(list(syms))
        if "SYM3" in syms:
            raise RuntimeError("chunk roto")
        return [quotes[s] for s in syms if s in quotes]

    provider.get_quotes = _get_quotes_fail_second
    provider.calls = []

    duration = mv.run_market_cycle_once()
    assert duration is not None  # el ciclo completo NO se cae por un chunk roto
    snap = mv.get_market_snapshot()
    assert snap["chunks_error"] == 1
    assert snap["chunks_total"] == 3
    # Los otros 2 chunks (6 símbolos) sí llegaron frescos al snapshot.
    assert snap["resueltos"] == 6
    # El chunk roto (SYM3/4/5) NO desaparece del universo -- este es el
    # primer ciclo (sin cache todavía), así que quedan como SIN_DATO, al
    # final del ranking, nunca eliminados.
    rows_by_symbol = {r["symbol"]: r for r in snap["rows"]}
    assert len(rows_by_symbol) == 9
    for roto in ("SYM3", "SYM4", "SYM5"):
        assert rows_by_symbol[roto]["data_status"] == "SIN_DATO"
        assert rows_by_symbol[roto]["price"] is None
    assert snap["cycles_error"] == 0  # el CICLO en sí terminó bien, solo un chunk falló


# --- sparkline funciona ---

def test_sparkline_acumula_puntos_reales_entre_ciclos(monkeypatch):
    mv._sparkline_by_symbol.clear()
    quotes1 = {"AAA": _FakeQuote("AAA", 10.0, 1.0)}
    provider = _patch(monkeypatch, ["AAA"], quotes1)
    mv.run_market_cycle_once()
    fila1 = mv.get_market_snapshot()["rows"][0]
    assert fila1["sparkline"] == [10.0]

    provider.quotes_by_symbol = {"AAA": _FakeQuote("AAA", 11.5, 15.0)}
    mv.run_market_cycle_once()
    fila2 = mv.get_market_snapshot()["rows"][0]
    assert fila2["sparkline"] == [10.0, 11.5]  # acumula, no reemplaza


# --- precio vencido: se muestra igual, change_pct=None va al final del ranking ---

def test_precio_vencido_se_muestra_sin_change_pct_al_final(monkeypatch):
    mv._last_known_by_symbol.clear()
    quotes = {
        "FRESCO": _FakeQuote("FRESCO", 10.0, 5.0, price_is_stale=False),
        "VENCIDO": _FakeQuote("VENCIDO", 20.0, None, price_is_stale=True),
    }
    _patch(monkeypatch, ["FRESCO", "VENCIDO"], quotes)
    mv.run_market_cycle_once()
    rows = mv.get_market_snapshot()["rows"]
    assert [r["symbol"] for r in rows] == ["FRESCO", "VENCIDO"]  # el vencido queda al final
    assert rows[1]["change_pct"] is None
    assert rows[1]["price_is_stale"] is True


# --- Cache de último dato conocido -- robustez ante fallos parciales de
# Tradier (autorizado 2026-08-30). Un batch roto NUNCA borra una acción
# del universo -- se conserva su último dato conocido, marcado stale. ---

def test_cache_A_ciclo1_recibe_datos_frescos(monkeypatch):
    mv._last_known_by_symbol.clear()
    symbols = [f"SYM{i}" for i in range(3)]
    quotes = {s: _FakeQuote(s, 10.0 + i, 1.0 + i) for i, s in enumerate(symbols)}
    _patch(monkeypatch, symbols, quotes)
    mv.run_market_cycle_once()
    rows = mv.get_market_snapshot()["rows"]
    assert len(rows) == 3
    assert all(r["data_status"] == "FRESCO" for r in rows)


def test_cache_B_batch_falla_completo_usa_ultimo_dato_conocido(monkeypatch):
    mv._last_known_by_symbol.clear()
    symbols = [f"SYM{i}" for i in range(3)]
    quotes = {s: _FakeQuote(s, 10.0, 5.0) for s in symbols}
    provider = _patch(monkeypatch, symbols, quotes)
    mv.run_market_cycle_once()  # ciclo 1 -- todo fresco

    def _fail(_syms):
        raise RuntimeError("batch caido")
    provider.get_quotes = _fail
    mv.run_market_cycle_once()  # ciclo 2 -- el batch entero falla

    snap = mv.get_market_snapshot()
    rows = {r["symbol"]: r for r in snap["rows"]}
    assert len(rows) == 3  # ninguna acción desaparece
    for s in symbols:
        assert rows[s]["data_status"] == "STALE"
        assert rows[s]["price"] == 10.0
        assert rows[s]["change_pct"] == 5.0
        assert rows[s]["data_age_seconds"] is not None and rows[s]["data_age_seconds"] >= 0


def test_cache_C_batch_se_recupera_vuelve_a_fresco(monkeypatch):
    mv._last_known_by_symbol.clear()
    symbols = ["AAA"]
    provider = _patch(monkeypatch, symbols, {"AAA": _FakeQuote("AAA", 10.0, 5.0)})
    mv.run_market_cycle_once()

    def _fail(_syms):
        raise RuntimeError("caido")
    provider.get_quotes = _fail
    mv.run_market_cycle_once()
    assert mv.get_market_snapshot()["rows"][0]["data_status"] == "STALE"

    quotes_recuperado = {"AAA": _FakeQuote("AAA", 12.0, 9.0)}
    provider.get_quotes = lambda syms: [quotes_recuperado[s] for s in syms if s in quotes_recuperado]
    mv.run_market_cycle_once()
    row = mv.get_market_snapshot()["rows"][0]
    assert row["data_status"] == "FRESCO"
    assert row["price"] == 12.0
    assert row["change_pct"] == 9.0


def test_cache_D_simbolo_sin_datos_nunca_permanece_al_final(monkeypatch):
    mv._last_known_by_symbol.clear()
    quotes = {"AAA": _FakeQuote("AAA", 10.0, 5.0)}
    _patch(monkeypatch, ["AAA", "SINDATOS"], quotes)  # SINDATOS nunca responde
    mv.run_market_cycle_once()
    rows = mv.get_market_snapshot()["rows"]
    assert [r["symbol"] for r in rows] == ["AAA", "SINDATOS"]
    sin = rows[1]
    assert sin["data_status"] == "SIN_DATO"
    assert sin["price"] is None
    assert sin["change_pct"] is None


def test_cache_E_nunca_inventa_change_pct(monkeypatch):
    mv._last_known_by_symbol.clear()
    quotes = {"AAA": _FakeQuote("AAA", 10.0, None, price_is_stale=True)}
    _patch(monkeypatch, ["AAA"], quotes)
    mv.run_market_cycle_once()
    row = mv.get_market_snapshot()["rows"][0]
    assert row["change_pct"] is None  # nunca inventado -- ni fresco ni cacheado


def test_cache_F_sin_duplicados(monkeypatch):
    mv._last_known_by_symbol.clear()
    symbols = [f"SYM{i}" for i in range(50)]
    quotes = {s: _FakeQuote(s, 10.0, float(i)) for i, s in enumerate(symbols)}
    provider = _patch(monkeypatch, symbols, quotes, chunk_size=10)
    mv.run_market_cycle_once()

    def _fail(_syms):
        raise RuntimeError("caido")
    provider.get_quotes = _fail
    mv.run_market_cycle_once()

    rows = mv.get_market_snapshot()["rows"]
    simbolos = [r["symbol"] for r in rows]
    assert len(simbolos) == len(set(simbolos))
    assert len(rows) == 50


def test_cache_G_universo_completo_representado_aunque_falten_datos(monkeypatch):
    mv._last_known_by_symbol.clear()
    symbols = [f"SYM{i}" for i in range(9)]
    quotes = {s: _FakeQuote(s, 10.0, float(i)) for i, s in enumerate(symbols)}

    def _fail_second(syms):
        if "SYM3" in syms:
            raise RuntimeError("chunk roto")
        return [quotes[s] for s in syms if s in quotes]

    provider = _patch(monkeypatch, symbols, quotes, chunk_size=3)
    provider.get_quotes = _fail_second
    mv.run_market_cycle_once()
    snap = mv.get_market_snapshot()
    assert snap["total_universe"] == 9
    assert len(snap["rows"]) == 9  # universo completo, aunque un chunk falló
    sin_datos = {r["symbol"] for r in snap["rows"] if r["data_status"] == "SIN_DATO"}
    assert {"SYM3", "SYM4", "SYM5"} <= sin_datos  # el chunk roto (ciclo 1, sin cache) queda SIN_DATO


def test_cache_H_batch_fallido_no_borra_info_valida_del_ciclo_anterior(monkeypatch):
    mv._last_known_by_symbol.clear()
    symbols = ["AAA", "BBB"]
    provider = _patch(monkeypatch, symbols, {
        "AAA": _FakeQuote("AAA", 10.0, 5.0),
        "BBB": _FakeQuote("BBB", 20.0, -3.0),
    }, chunk_size=1)  # cada símbolo en su propio batch -- solo el de BBB falla
    mv.run_market_cycle_once()  # ambos frescos

    def _only_aaa(syms):
        if "BBB" in syms:
            raise RuntimeError("bbb batch caido")
        return [_FakeQuote(s, 11.0, 6.0) for s in syms if s == "AAA"]

    provider.get_quotes = _only_aaa
    mv.run_market_cycle_once()
    rows = {r["symbol"]: r for r in mv.get_market_snapshot()["rows"]}
    assert rows["AAA"]["data_status"] == "FRESCO"
    assert rows["BBB"]["data_status"] == "STALE"
    assert rows["BBB"]["price"] == 20.0  # info válida del ciclo anterior conservada
    assert rows["BBB"]["change_pct"] == -3.0


# --- Sesión activa: premarket/regular/afterhours -- nunca depende solo de
# "regular" (autorizado 2026-08-30, misma fuente de verdad que el resto de
# Atlas: `market_hours.get_session()`, sin horario propio) ---

def test_sesion_activa_incluye_premarket_regular_afterhours():
    assert mv._is_active_session("premarket") is True
    assert mv._is_active_session("regular") is True
    assert mv._is_active_session("afterhours") is True
    assert mv._is_active_session("overnight") is True  # 2026-09-01, autorizado explícitamente
    assert mv._is_active_session("closed") is False
    assert mv._is_active_session(None) is False


# --- OVERNIGHT (2026-09-01, autorizado explícitamente): Yahoo como única
# fuente, Tradier nunca consultado, STALE conservado con evidencia real ---

def test_overnight_no_llama_a_tradier(monkeypatch):
    mv._last_known_by_symbol.clear()
    quotes = {"AAA": _FakeQuote("AAA", 10.0, 5.0)}  # Tradier "tendría" esto -- no debe llegar a pedirlo
    yq = _FakeQuote("AAA", 10.5, 5.5, timestamp=datetime.now(timezone.utc))
    provider = _patch(monkeypatch, ["AAA"], quotes, session="overnight", yahoo_quotes_by_symbol={"AAA": yq})
    mv.run_market_cycle_once()
    assert provider.calls == []  # Tradier NUNCA se llamó durante overnight


def test_overnight_usa_yahoo_como_fuente(monkeypatch):
    mv._last_known_by_symbol.clear()
    yq = _FakeQuote("AAA", 10.5, 5.5, timestamp=datetime.now(timezone.utc))
    _patch(monkeypatch, ["AAA"], {}, session="overnight", yahoo_quotes_by_symbol={"AAA": yq})
    mv.run_market_cycle_once()
    row = mv.get_market_snapshot()["rows"][0]
    assert row["source"] == "yahoo"
    assert row["data_status"] == "FRESCO"


def test_overnight_sin_yahoo_fresco_conserva_cache_y_marca_stale(monkeypatch):
    mv._last_known_by_symbol.clear()
    # Ciclo 1: sesión regular, Tradier fresco -- deja cache poblado.
    quotes = {"AAA": _FakeQuote("AAA", 10.0, 5.0, previous_close=9.5)}
    _patch(monkeypatch, ["AAA"], quotes, session="regular")
    mv.run_market_cycle_once()
    cached_row = mv.get_market_snapshot()["rows"][0]
    assert cached_row["data_status"] == "FRESCO"
    assert cached_row["price"] == 10.0

    # Ciclo 2: overnight, sin Yahoo fresco disponible para AAA -- debe
    # conservar el cache (mismo price/change_pct) y marcarlo STALE, nunca
    # inventar un movimiento nuevo a partir de un precio viejo.
    _patch(monkeypatch, ["AAA"], {}, session="overnight")
    mv.run_market_cycle_once()
    row = mv.get_market_snapshot()["rows"][0]
    assert row["data_status"] == "STALE"
    assert row["price"] == 10.0
    assert row["change_pct"] == cached_row["change_pct"]
    assert row["data_age_seconds"] is not None and row["data_age_seconds"] >= 0


def test_overnight_no_requiere_token_tradier(monkeypatch):
    mv._last_known_by_symbol.clear()
    yq = _FakeQuote("AAA", 10.5, 5.5, timestamp=datetime.now(timezone.utc))
    _patch(monkeypatch, ["AAA"], {}, session="overnight", yahoo_quotes_by_symbol={"AAA": yq})
    monkeypatch.setattr(mv, "build_tradier_provider", lambda: None)  # sin TRADIER_API_TOKEN
    duration = mv.run_market_cycle_once()
    assert duration is not None  # corrió igual -- nunca lanza RuntimeError en overnight
    row = mv.get_market_snapshot()["rows"][0]
    assert row["source"] == "yahoo"


def test_overnight_top_100_mantiene_orden(monkeypatch):
    mv._last_known_by_symbol.clear()
    now = datetime.now(timezone.utc)
    yahoo_quotes = {
        "LOW": _FakeQuote("LOW", 10.0, -5.0, timestamp=now),
        "HIGH": _FakeQuote("HIGH", 10.0, 18.5, timestamp=now),
        "MID": _FakeQuote("MID", 10.0, 3.2, timestamp=now),
    }
    _patch(monkeypatch, ["LOW", "HIGH", "MID"], {}, session="overnight", yahoo_quotes_by_symbol=yahoo_quotes)
    mv.run_market_cycle_once()
    rows = mv.get_market_snapshot()["rows"]
    assert [r["symbol"] for r in rows] == ["HIGH", "MID", "LOW"]


def test_overnight_no_duplica_ciclos(monkeypatch):
    mv._last_known_by_symbol.clear()
    yq = _FakeQuote("AAA", 10.0, 5.0, timestamp=datetime.now(timezone.utc))
    _patch(monkeypatch, ["AAA"], {}, session="overnight", yahoo_quotes_by_symbol={"AAA": yq})
    acquired = mv._lock.acquire(blocking=False)
    assert acquired
    try:
        result = mv.run_market_cycle_once()
        assert result is None  # el lock ya estaba tomado -- no corrió un segundo ciclo
    finally:
        mv._lock.release()


# --- verificación estructural: ningún archivo protegido tiene diff ---

def test_archivos_protegidos_sin_diff():
    import subprocess

    protegidos = [
        "atlas_live/core/atlas_decision_core.py",
        "atlas_live/core/current_top_opportunity.py",
        "atlas_live/core/top_opportunity_stability.py",
        "atlas_live/core/current_top_opportunity_registry.py",
        "atlas_live/scan_worker.py",
        "atlas_live/radar/radar_worker.py",
        "atlas_live/radar/candidate_gates.py",
        "atlas_live/radar/priority_classifier.py",
        "atlas/engine/decision_engine.py",
        "atlas_live/memory",
        "atlas_live/learning",
        "atlas/data/providers/tradier_provider.py",
        "atlas_live/data_fusion/universe_quotes.py",
        "atlas/data/universe/universe.py",
    ]
    resultado = subprocess.run(
        ["git", "diff", "--stat", "--"] + protegidos,
        capture_output=True, text=True, cwd=".",
    )
    assert resultado.stdout.strip() == "", f"archivos protegidos con diff pendiente: {resultado.stdout}"


# --- confirmación real: el universo de Racional EQUITY tiene 1.646 símbolos ---

def test_universo_real_racional_equity_es_1646():
    from atlas.data.universe import get_equities
    equities = get_equities()
    assert len(equities) == 1646
    assert all(a.type == "EQUITY" for a in equities)


# --- confirmación real (2026-08-30, autorizado): universo AMPLIADO de
# Mercado = EQUITY+ETF+ETN = 2.577, sin duplicados, y en 11 chunks de 250 ---

def test_universo_real_completo_es_2577_equity_etf_etn():
    from atlas.data.universe import load_universe
    instruments = load_universe()
    assert len(instruments) == 2577
    from collections import Counter
    counts = Counter(a.type for a in instruments.values())
    assert counts["EQUITY"] == 1646
    assert counts["ETF"] == 929
    assert counts["ETN"] == 2
    # ningún otro tipo se cuela (nunca cripto -- no existe en este loader)
    assert set(counts.keys()) == {"EQUITY", "ETF", "ETN"}


def test_universo_real_completo_normaliza_a_11_chunks_sin_duplicados():
    """Sin red -- `normalize()` es una función pura local. Confirma la
    cuenta real de chunks (2.567 query symbols únicos -> 11 de 250) medida
    en el diagnóstico previo a la implementación, y que ningún símbolo
    original se pierde ni se duplica en el mapeo."""
    from atlas.data.providers.tradier_provider import TRADIER_CHUNK_SIZE
    from atlas.data.providers.tradier_symbol_map import normalize
    from atlas.data.universe import load_universe

    originals = list(load_universe().keys())
    assert len(originals) == len(set(originals))  # el universo en sí no trae duplicados

    query_to_originals = {}
    for sym in originals:
        n = normalize(sym)
        query_to_originals.setdefault(n.query_symbol, []).append(sym)

    query_symbols = list(query_to_originals.keys())
    chunks = [query_symbols[i:i + TRADIER_CHUNK_SIZE] for i in range(0, len(query_symbols), TRADIER_CHUNK_SIZE)]
    assert len(query_symbols) == 2567
    assert len(chunks) == 11
    # cada original aparece en exactamente un query_symbol -- sin duplicados
    todos_los_originales = [s for originals_list in query_to_originals.values() for s in originals_list]
    assert sorted(todos_los_originales) == sorted(originals)
    assert len(todos_los_originales) == len(set(todos_los_originales))


def test_change_abs_se_calcula_desde_previous_close_real(monkeypatch):
    """Cambio $ = precio actual - previous_close real de Tradier -- nunca
    derivado de change_pct (evita inventar por redondeo inverso)."""
    mv._last_known_by_symbol.clear()
    q = _FakeQuote("AAA", 105.0, 5.0, previous_close=100.0)
    _patch(monkeypatch, ["AAA"], {"AAA": q})
    mv.run_market_cycle_once()
    row = mv.get_market_snapshot()["rows"][0]
    assert row["change_abs"] == 5.0


def test_change_abs_none_si_no_hay_previous_close(monkeypatch):
    mv._last_known_by_symbol.clear()
    q = _FakeQuote("AAA", 105.0, 5.0, previous_close=None)
    _patch(monkeypatch, ["AAA"], {"AAA": q})
    mv.run_market_cycle_once()
    row = mv.get_market_snapshot()["rows"][0]
    assert row["change_abs"] is None  # nunca inventado


# =============================================================================
# Multi-fuente Tradier -> Yahoo -> Finnhub (2026-08-31, autorizado explícitamente)
# =============================================================================

def test_multisource_tradier_fresco_no_dispara_fallback(monkeypatch):
    """M) 'no duplicar requests innecesariamente' -- si Tradier ya está
    fresco para TODOS los símbolos, Yahoo/Finnhub NUNCA se llaman."""
    mv._last_known_by_symbol.clear()
    yahoo_calls = []

    def _spy_yahoo(symbols):
        yahoo_calls.append(list(symbols))
        return {}, mv._empty_circuit_stats()

    q = _FakeQuote("AAA", 100.0, 5.0, previous_close=95.0, price_is_stale=False)
    _patch(monkeypatch, ["AAA"], {"AAA": q})
    monkeypatch.setattr(mv, "_fetch_yahoo_batch", _spy_yahoo)
    mv.run_market_cycle_once()
    assert yahoo_calls == []  # nunca se llamó -- Tradier ya estaba fresco
    row = mv.get_market_snapshot()["rows"][0]
    assert row["source"] == "tradier"
    snap = mv.get_market_snapshot()
    assert snap["yahoo_checked"] == 0


def test_multisource_tradier_stale_yahoo_fresco_gana_yahoo(monkeypatch):
    """Caso B end-to-end: Tradier stale, Yahoo fresco -> Mercado muestra Yahoo."""
    mv._last_known_by_symbol.clear()
    tq = _FakeQuote("AAA", 100.0, None, previous_close=95.0, price_is_stale=True)
    yq = _FakeQuote("AAA", 103.0, 8.42, previous_close=95.0,
                     timestamp=datetime.now(timezone.utc), price_is_stale=False)
    _patch(monkeypatch, ["AAA"], {"AAA": tq}, yahoo_quotes_by_symbol={"AAA": yq})
    mv.run_market_cycle_once()
    snap = mv.get_market_snapshot()
    row = snap["rows"][0]
    assert row["source"] == "yahoo"
    assert row["price"] == 103.0
    assert row["data_status"] == "FRESCO"
    assert snap["tradier_stale"] == 1
    assert snap["yahoo_checked"] == 1
    assert snap["yahoo_fresh"] == 1


def test_multisource_tradier_yahoo_stale_finnhub_fresco_gana_finnhub(monkeypatch):
    """Caso C end-to-end: Tradier y Yahoo stale -> Finnhub fresco gana."""
    mv._last_known_by_symbol.clear()
    tq = _FakeQuote("AAA", 100.0, None, previous_close=95.0, price_is_stale=True)
    yq_vieja = _FakeQuote("AAA", 99.0, None, previous_close=95.0,
                            timestamp=datetime.now(timezone.utc) - timedelta(hours=5))
    fq = _FakeQuote("AAA", 104.0, 9.47, previous_close=95.0,
                     timestamp=datetime.now(timezone.utc), price_is_stale=False)
    _patch(monkeypatch, ["AAA"], {"AAA": tq},
           yahoo_quotes_by_symbol={"AAA": yq_vieja}, finnhub_quotes_by_symbol={"AAA": fq})
    mv.run_market_cycle_once()
    snap = mv.get_market_snapshot()
    row = snap["rows"][0]
    assert row["source"] == "finnhub"
    assert row["price"] == 104.0
    assert snap["finnhub_checked"] == 1
    assert snap["finnhub_fresh"] == 1


def test_multisource_las_tres_stale_usa_cache_marca_source_cache(monkeypatch):
    """Caso D end-to-end: las 3 fuentes stale -> usa el último dato
    conocido de Mercado, marcado STALE con source='cache'."""
    mv._last_known_by_symbol.clear()
    # Ciclo 1: Tradier fresco, se guarda en cache.
    tq_fresco = _FakeQuote("AAA", 100.0, 5.0, previous_close=95.0, price_is_stale=False)
    provider = _patch(monkeypatch, ["AAA"], {"AAA": tq_fresco})
    mv.run_market_cycle_once()
    assert mv.get_market_snapshot()["rows"][0]["source"] == "tradier"

    # Ciclo 2: Tradier stale, Yahoo/Finnhub sin dato -> debe usar cache.
    tq_stale = _FakeQuote("AAA", 100.0, None, previous_close=95.0, price_is_stale=True)
    provider.quotes_by_symbol = {"AAA": tq_stale}
    mv.run_market_cycle_once()
    snap = mv.get_market_snapshot()
    row = snap["rows"][0]
    assert row["source"] == "cache"
    assert row["data_status"] == "STALE"
    assert row["price"] == 100.0  # el dato fresco del ciclo 1, conservado
    assert snap["cache_used"] == 1


def test_multisource_cache_se_actualiza_cuando_cambia_la_fuente(monkeypatch):
    """N) el cache de último dato conocido se actualiza con la fuente que
    gane, no solo con Tradier -- si Yahoo aporta el dato fresco, el
    siguiente ciclo (todo stale) debe conservar el precio de YAHOO."""
    mv._last_known_by_symbol.clear()
    tq = _FakeQuote("AAA", 100.0, None, previous_close=95.0, price_is_stale=True)
    yq = _FakeQuote("AAA", 107.5, 13.16, previous_close=95.0,
                     timestamp=datetime.now(timezone.utc), price_is_stale=False)
    provider = _patch(monkeypatch, ["AAA"], {"AAA": tq}, yahoo_quotes_by_symbol={"AAA": yq})
    mv.run_market_cycle_once()
    assert mv.get_market_snapshot()["rows"][0]["price"] == 107.5  # gano Yahoo

    # Ciclo 2: ahora ninguna fuente responde -- debe conservar 107.5 (de Yahoo), no 100.0 (de Tradier).
    provider.quotes_by_symbol = {"AAA": tq}
    monkeypatch.setattr(mv, "_fetch_yahoo_batch", lambda symbols: ({}, mv._empty_circuit_stats()))
    mv.run_market_cycle_once()
    row = mv.get_market_snapshot()["rows"][0]
    assert row["source"] == "cache"
    assert row["price"] == 107.5


def test_multisource_overnight_sin_proveedor_no_se_inventa(monkeypatch):
    """K) ninguna fuente entrega overnight hoy -> `overnight_disponible`
    siempre False, nunca inventado."""
    mv._last_known_by_symbol.clear()
    tq = _FakeQuote("AAA", 100.0, None, previous_close=95.0, price_is_stale=True)
    yq = _FakeQuote("AAA", 101.0, 6.32, previous_close=95.0,
                     timestamp=datetime.now(timezone.utc), price_is_stale=False)
    _patch(monkeypatch, ["AAA"], {"AAA": tq}, yahoo_quotes_by_symbol={"AAA": yq})
    mv.run_market_cycle_once()
    row = mv.get_market_snapshot()["rows"][0]
    assert row["overnight_disponible"] is False


def test_multisource_sesion_del_dato_ganador_se_expone(monkeypatch):
    """H/I/J) la sesión expuesta (`session_dato`) corresponde al timestamp
    real del dato ganador, no al reloj actual."""
    mv._last_known_by_symbol.clear()
    ts_regular = datetime(2026, 9, 1, 15, 0, 0, tzinfo=timezone.utc)  # 11:00 ET martes = regular
    tq = _FakeQuote("AAA", 100.0, 5.0, previous_close=95.0, price_is_stale=False, timestamp=ts_regular)
    _patch(monkeypatch, ["AAA"], {"AAA": tq})
    mv.run_market_cycle_once()
    row = mv.get_market_snapshot()["rows"][0]
    assert row["session_dato"] == "REGULAR"


# =============================================================================
# Top N por movimiento (2026-08-31, pedido explícito: "simplificar Mercado
# a lo que realmente necesitamos" -- Mercado muestra únicamente las
# primeras MERCADO_TOP_N filas del ranking ya ordenado, nunca el universo
# completo -- los conteos de auditoría siguen siendo sobre el universo
# completo).
# =============================================================================

def test_mercado_nunca_corre_dos_ciclos_simultaneos(monkeypatch):
    """Pedido explícito: 'nunca ejecutar ciclos simultáneos'. El lock ya
    existente (`_lock.acquire(blocking=False)`) debe rechazar una segunda
    llamada mientras la primera sigue "corriendo" (simulado reteniendo
    el lock manualmente)."""
    mv._last_known_by_symbol.clear()
    _patch(monkeypatch, ["AAA"], {"AAA": _FakeQuote("AAA", 10.0, 5.0)})
    assert mv._lock.acquire(blocking=False)  # simula un ciclo ya en curso
    try:
        resultado = mv.run_market_cycle_once()
        assert resultado is None  # rechazado, nunca corre en paralelo
    finally:
        mv._lock.release()
    # liberado el lock, un ciclo normal sí debe correr
    assert mv.run_market_cycle_once() is not None


def test_timeout_duro_evita_que_un_future_colgado_congele_el_ciclo(monkeypatch):
    """2026-08-31, fix real -- caso real confirmado en producción durante
    premarket real: un ciclo quedó colgado >14 minutos en el `wait()` sin
    límite de `_fetch_with_circuit_breaker()`. Una unidad más lenta que
    `CIRCUIT_UNIT_TIMEOUT_SECONDS` NUNCA debe bloquear la función --
    debe devolver control apenas se cumple el timeout, sin esperar a que
    la unidad lenta termine de verdad."""
    monkeypatch.setattr(mv, "CIRCUIT_UNIT_TIMEOUT_SECONDS", 0.3)

    def _lento(unit):
        time.sleep(2.0)  # más lento que el timeout -- simula el cuelgue real
        return {unit: object()}

    t0 = time.time()
    result, stats = mv._fetch_with_circuit_breaker(
        units=["AAA"], fetch_one=_lento, unit_len=lambda u: 1,
        max_workers=1, min_sample=1, error_rate_threshold=0.85,
    )
    elapsed = time.time() - t0
    assert elapsed < 1.0  # nunca esperó los 2s reales -- el timeout (0.3s) cortó antes
    assert result == {}  # el resultado de la unidad colgada nunca se usa
    assert stats["attempted"] == 1
    assert stats["errors"] == 1


def test_timeout_se_contabiliza_correctamente_y_abre_el_circuito(monkeypatch):
    """El timeout cuenta como error (nunca se pierde en silencio) y abre
    el circuito de inmediato -- las unidades que ni siquiera se llegaron a
    encolar quedan `aborted`, nunca se intentan."""
    monkeypatch.setattr(mv, "CIRCUIT_UNIT_TIMEOUT_SECONDS", 0.3)

    def _lento(unit):
        time.sleep(2.0)
        return {unit: object()}

    result, stats = mv._fetch_with_circuit_breaker(
        units=["AAA", "BBB", "CCC", "DDD"], fetch_one=_lento, unit_len=lambda u: 1,
        max_workers=1, min_sample=1, error_rate_threshold=0.85,
    )
    assert stats["attempted"] == 1  # solo la primera se llegó a intentar (max_workers=1)
    assert stats["errors"] == 1
    assert stats["success"] == 0
    assert stats["aborted"] == 3  # BBB, CCC, DDD -- nunca se intentaron, circuito ya abierto


def test_mercado_muestra_solo_top_n_pero_audita_universo_completo(monkeypatch):
    mv._last_known_by_symbol.clear()
    symbols = [f"SYM{i}" for i in range(150)]
    quotes = {s: _FakeQuote(s, 10.0, float(i), price_is_stale=False) for i, s in enumerate(symbols)}
    monkeypatch.setattr(mv, "MERCADO_TOP_N", 100)
    _patch(monkeypatch, symbols, quotes)
    mv.run_market_cycle_once()
    snap = mv.get_market_snapshot()

    assert len(snap["rows"]) == 100  # solo se muestran 100, aunque el universo tenga 150
    assert snap["total_universe"] == 150  # la auditoría sigue siendo del universo completo
    assert snap["frescos"] == 150  # el conteo de frescos NO se recorta -- son 150 símbolos frescos reales

    # Los 100 mostrados deben ser EXACTAMENTE los 100 de mayor change_pct
    # (SYM149..SYM50, change_pct de 149.0 a 50.0), en orden descendente.
    esperados = [f"SYM{i}" for i in range(149, 49, -1)]
    assert [r["symbol"] for r in snap["rows"]] == esperados
    assert [r["rank"] for r in snap["rows"]] == list(range(1, 101))


def test_mercado_top_n_configurable_por_env(monkeypatch):
    mv._last_known_by_symbol.clear()
    symbols = [f"SYM{i}" for i in range(10)]
    quotes = {s: _FakeQuote(s, 10.0, float(i), price_is_stale=False) for i, s in enumerate(symbols)}
    monkeypatch.setattr(mv, "MERCADO_TOP_N", 3)
    _patch(monkeypatch, symbols, quotes)
    mv.run_market_cycle_once()
    rows = mv.get_market_snapshot()["rows"]
    assert [r["symbol"] for r in rows] == ["SYM9", "SYM8", "SYM7"]


def test_mercado_reordena_top_n_cuando_cambian_precios_entre_ciclos(monkeypatch):
    """Verifica que, con universo grande (> top N), el ranking mostrado se
    recalcula correctamente cuando los precios cambian entre ciclos --
    mismo criterio que test_D, a escala del recorte top-N."""
    mv._last_known_by_symbol.clear()
    symbols = [f"SYM{i}" for i in range(120)]
    monkeypatch.setattr(mv, "MERCADO_TOP_N", 50)
    quotes_ciclo1 = {s: _FakeQuote(s, 10.0, float(i), price_is_stale=False) for i, s in enumerate(symbols)}
    provider = _patch(monkeypatch, symbols, quotes_ciclo1)
    mv.run_market_cycle_once()
    top1 = [r["symbol"] for r in mv.get_market_snapshot()["rows"]]
    assert top1[0] == "SYM119"  # el más alto del ciclo 1

    # SYM0 (antes el último) pega un salto fuerte -> debe pasar al frente
    # del top mostrado en el ciclo siguiente.
    quotes_ciclo2 = dict(quotes_ciclo1)
    quotes_ciclo2["SYM0"] = _FakeQuote("SYM0", 10.0, 500.0, price_is_stale=False)
    provider.quotes_by_symbol = quotes_ciclo2
    mv.run_market_cycle_once()
    top2 = [r["symbol"] for r in mv.get_market_snapshot()["rows"]]
    assert top2[0] == "SYM0"
    assert top2 != top1


def test_multisource_sin_finnhub_key_no_intenta_finnhub(monkeypatch):
    """Sin FINNHUB_API_KEY (provider=None) -- Finnhub nunca se intenta,
    degradación segura (mismo criterio que build_tradier_provider)."""
    mv._last_known_by_symbol.clear()
    finnhub_calls = []

    def _spy_finnhub_provider():
        finnhub_calls.append(1)
        return None

    tq = _FakeQuote("AAA", 100.0, None, previous_close=95.0, price_is_stale=True)
    yq_vieja = _FakeQuote("AAA", 99.0, None, previous_close=95.0,
                            timestamp=datetime.now(timezone.utc) - timedelta(hours=5))
    _patch(monkeypatch, ["AAA"], {"AAA": tq}, yahoo_quotes_by_symbol={"AAA": yq_vieja})
    monkeypatch.setattr(mv, "_build_finnhub_provider", _spy_finnhub_provider)
    mv.run_market_cycle_once()
    assert finnhub_calls == [1]  # se intento construir el provider
    row = mv.get_market_snapshot()["rows"][0]
    assert row["source"] != "finnhub"  # pero sin key, nunca puede ganar


# =============================================================================
# Optimización del fallback -- sesión cerrada + circuit breaker + batch real
# de Yahoo (2026-09-01, autorizado explícitamente tras medir 421s/2.577
# llamadas Yahoo/1.687 errores en la prueba real anterior con el mercado
# cerrado).
# =============================================================================

def test_cerrado_nunca_dispara_fallback_yahoo_finnhub(monkeypatch):
    """Test de seguridad pedido explícitamente (punto 11): session=='closed'
    -> CERO llamadas a Yahoo/Finnhub, sin importar cuántos símbolos de
    Tradier estén stale (acá los 30/30 lo están -- el peor caso real
    medido). El universo completo se sigue representando (último dato
    conocido / SIN_DATO), Tradier se sigue consultando (barato)."""
    mv._last_known_by_symbol.clear()
    yahoo_calls = []
    finnhub_calls = []

    def _spy_yahoo(symbols):
        yahoo_calls.append(list(symbols))
        return {}, mv._empty_circuit_stats()

    def _spy_finnhub(symbols, provider):
        finnhub_calls.append(list(symbols))
        return {}, mv._empty_circuit_stats()

    symbols = [f"SYM{i}" for i in range(30)]
    quotes = {s: _FakeQuote(s, 10.0, None, price_is_stale=True) for s in symbols}  # TODOS stale
    _patch(monkeypatch, symbols, quotes, session="closed")
    monkeypatch.setattr(mv, "_fetch_yahoo_batch", _spy_yahoo)
    monkeypatch.setattr(mv, "_fetch_finnhub_batch", _spy_finnhub)
    mv.run_market_cycle_once()

    assert yahoo_calls == []
    assert finnhub_calls == []
    snap = mv.get_market_snapshot()
    assert snap["yahoo_attempted"] == 0
    assert snap["finnhub_attempted"] == 0
    assert len(snap["rows"]) == 30  # el universo se sigue representando


def test_activa_si_dispara_fallback_cuando_hay_stale(monkeypatch):
    """Contraprueba de la anterior: la MISMA situación (todo Tradier-stale)
    en sesión activa SÍ debe intentar el fallback -- confirma que el
    guard de 'closed' es específico, no una regresión general."""
    mv._last_known_by_symbol.clear()
    yahoo_calls = []

    def _spy_yahoo(symbols):
        yahoo_calls.append(list(symbols))
        return {}, mv._empty_circuit_stats()

    symbols = [f"SYM{i}" for i in range(5)]
    quotes = {s: _FakeQuote(s, 10.0, None, price_is_stale=True) for s in symbols}
    _patch(monkeypatch, symbols, quotes, session="premarket")
    monkeypatch.setattr(mv, "_fetch_yahoo_batch", _spy_yahoo)
    mv.run_market_cycle_once()
    assert len(yahoo_calls) == 1
    assert sorted(yahoo_calls[0]) == sorted(symbols)


def test_yahoo_usa_get_quotes_batch_no_una_sesion_por_simbolo(monkeypatch):
    """Punto 3 del pedido: reutilizar el batch YA EXISTENTE
    (`YahooFinanceProvider.get_quotes()`, sesión HTTP compartida vía
    `yf.Tickers`) en vez de una sesión nueva por símbolo
    (`yf.Ticker(symbol).info`). Se llama a la función REAL
    `_fetch_yahoo_batch` (sin mockear), reemplazando solo
    `get_quotes()` -- si el código siguiera pidiendo una cotización por
    símbolo, `calls` tendría 85 entradas, no 3."""
    calls = []

    def _fake_get_quotes(self, symbols):
        calls.append(list(symbols))
        return []

    monkeypatch.setattr(mv.YahooFinanceLiveProvider, "get_quotes", _fake_get_quotes)
    monkeypatch.setattr(mv, "YAHOO_BATCH_SIZE", 40)
    monkeypatch.setattr(mv, "YAHOO_BATCH_WORKERS", 4)
    symbols = [f"SYM{i}" for i in range(85)]  # 85 -> 3 chunks de <=40
    mv._fetch_yahoo_batch(symbols)
    assert len(calls) == 3
    assert sorted(len(c) for c in calls) == [5, 40, 40]
    assert sorted(s for c in calls for s in c) == sorted(symbols)


def test_circuit_breaker_yahoo_abre_tras_error_rate_alto_y_aborta_el_resto(monkeypatch):
    """Punto 4 del pedido: si Yahoo empieza a fallar sistemáticamente,
    Mercado deja de encolar chunks nuevos y cuenta el resto como
    'aborted' -- Yahoo no puede mantener bloqueado el ciclo entero."""
    calls = []

    def _always_fail(symbols):
        calls.append(list(symbols))
        raise RuntimeError("Yahoo caído")

    monkeypatch.setattr(mv, "_fetch_yahoo_chunk", _always_fail)
    monkeypatch.setattr(mv, "YAHOO_BATCH_SIZE", 10)
    monkeypatch.setattr(mv, "YAHOO_BATCH_WORKERS", 2)
    monkeypatch.setattr(mv, "CIRCUIT_MIN_SAMPLE", 20)
    monkeypatch.setattr(mv, "CIRCUIT_ERROR_RATE", 0.85)

    symbols = [f"SYM{i}" for i in range(200)]  # 20 chunks de 10
    result, stats = mv._fetch_yahoo_batch(symbols)
    assert result == {}
    assert stats["aborted"] > 0
    assert len(calls) < 20  # se frenó antes de intentar los 20 chunks
    assert stats["attempted"] + stats["aborted"] == 200


def test_circuit_breaker_yahoo_ratelimit_abre_de_inmediato(monkeypatch):
    """Un RateLimitError explícito (Yahoo ya está diciendo 'parame') abre
    el circuito de inmediato, sin esperar la muestra mínima."""
    calls = []

    def _rate_limited(symbols):
        calls.append(list(symbols))
        from atlas.data.providers.base import RateLimitError
        raise RateLimitError("rate limited")

    monkeypatch.setattr(mv, "_fetch_yahoo_chunk", _rate_limited)
    monkeypatch.setattr(mv, "YAHOO_BATCH_SIZE", 10)
    monkeypatch.setattr(mv, "YAHOO_BATCH_WORKERS", 1)  # secuencial, fácil de razonar
    monkeypatch.setattr(mv, "CIRCUIT_MIN_SAMPLE", 1000)  # si abre, fue por RateLimitError

    symbols = [f"SYM{i}" for i in range(100)]  # 10 chunks
    result, stats = mv._fetch_yahoo_batch(symbols)
    assert len(calls) == 1
    assert stats["aborted"] == 90


def test_circuit_breaker_finnhub_aborta_tras_errores(monkeypatch):
    """Mismo circuit breaker para Finnhub -- 'mejor esfuerzo', nunca
    bloquea Mercado esperando a que se resuelvan todos los símbolos."""
    monkeypatch.setattr(mv, "CIRCUIT_MIN_SAMPLE", 5)
    monkeypatch.setattr(mv, "CIRCUIT_ERROR_RATE", 0.8)
    monkeypatch.setattr(mv, "FINNHUB_BATCH_WORKERS", 2)

    class _FailingProvider:
        def get_quote(self, symbol):
            raise RuntimeError("Finnhub caído")

    symbols = [f"SYM{i}" for i in range(50)]
    result, stats = mv._fetch_finnhub_batch(symbols, _FailingProvider())
    assert result == {}
    assert stats["aborted"] > 0
    assert stats["attempted"] < 50


def test_symbols_override_limita_el_universo_del_ciclo(monkeypatch):
    """Solo para pruebas/diagnóstico reales de alcance acotado -- nunca
    usado por el hilo de fondo. Confirma que limita el universo sin
    romper el resto del ciclo."""
    mv._last_known_by_symbol.clear()
    quotes = {
        "AAA": _FakeQuote("AAA", 10.0, 5.0),
        "BBB": _FakeQuote("BBB", 20.0, -2.0),
        "CCC": _FakeQuote("CCC", 30.0, 1.0),
    }
    _patch(monkeypatch, ["AAA", "BBB", "CCC"], quotes)
    mv.run_market_cycle_once(symbols_override=["AAA", "CCC"])
    snap = mv.get_market_snapshot()
    assert snap["total_universe"] == 2
    assert {r["symbol"] for r in snap["rows"]} == {"AAA", "CCC"}
