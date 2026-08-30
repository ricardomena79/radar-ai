"""Tests de Mercado (2026-08-29, autorizado explícitamente). Sin red --
`TradierProvider`/`get_equities()` se reemplazan por versiones falsas
dentro del módulo (`mv.build_tradier_provider`/`mv.get_equities`), mismo
patrón de monkey-patching ya usado en `test_scan_stability.py`."""

from datetime import datetime, timezone
from types import SimpleNamespace

import atlas_live.market_view as mv
from atlas.data.universe.universe import Asset


class _FakeQuote:
    def __init__(self, symbol, last_price, change_percent, timestamp=None, price_is_stale=False):
        self.symbol = symbol
        self.last_price = last_price
        self.change_percent = change_percent
        self.timestamp = timestamp or datetime.now(timezone.utc)
        self.price_is_stale = price_is_stale


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


def _asset(symbol, name="Empresa " + "X"):
    return Asset(symbol=symbol, name=f"{symbol} Inc.", type="EQUITY")


def _fake_normalize(symbol):
    # 1:1, sin remapeo -- suficiente para estos tests (la normalización
    # uno-a-muchos ya está probada en universe_quotes.py, no se reimplementa acá).
    return SimpleNamespace(query_symbol=symbol, state="ACTIVE")


def _patch(monkeypatch, universe_symbols, quotes_by_symbol, fail_on_calls=None, chunk_size=None):
    assets = [_asset(s) for s in universe_symbols]
    provider = _FakeTradierProvider(quotes_by_symbol, fail_on_calls=fail_on_calls)
    monkeypatch.setattr(mv, "get_equities", lambda: assets)
    monkeypatch.setattr(mv, "build_tradier_provider", lambda: provider)
    monkeypatch.setattr(mv, "normalize", _fake_normalize)
    if chunk_size is not None:
        monkeypatch.setattr(mv, "TRADIER_CHUNK_SIZE", chunk_size)
    return provider


# --- A: universo exacto -- solo lo que get_equities() devuelve, sin agregar nada ---

def test_A_universo_es_exactamente_get_equities(monkeypatch):
    mv._last_known_by_symbol.clear()
    quotes = {"AAA": _FakeQuote("AAA", 10.0, 5.0), "BBB": _FakeQuote("BBB", 20.0, -2.0)}
    _patch(monkeypatch, ["AAA", "BBB"], quotes)
    duration = mv.run_market_cycle_once()
    assert duration is not None
    snap = mv.get_market_snapshot()
    assert snap["total_universe"] == 2
    assert {r["symbol"] for r in snap["rows"]} == {"AAA", "BBB"}


# --- B: ningún ETF/ETN -- get_equities() ya filtra, se confirma que Mercado no agrega nada más ---

def test_B_no_incluye_simbolos_fuera_del_universo_provisto(monkeypatch):
    mv._last_known_by_symbol.clear()
    quotes = {"AAA": _FakeQuote("AAA", 10.0, 5.0), "SPY_ETF_NO_DEBERIA_APARECER": _FakeQuote("SPY_ETF_NO_DEBERIA_APARECER", 1.0, 1.0)}
    _patch(monkeypatch, ["AAA"], quotes)  # el universo (get_equities) NO incluye el ETF
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
    assert mv._is_active_session("closed") is False
    assert mv._is_active_session(None) is False


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
