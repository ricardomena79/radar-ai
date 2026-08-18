"""Tests del cache en proceso `historical_scoring.get_cached_reference_table()`
(2026-08-18, cierre de arquitectura) -- respeta el TTL, no recalcula antes
de expirar, y `generate_report()` sigue reconstruyendo siempre desde cero
(sin cache), comportamiento intacto."""

from atlas_live.learning import historical_scoring as hs


def _row(symbol, direction="ALCISTA", timing="al_comienzo", vol=5.0, advance=25.0):
    return {
        "symbol": symbol, "date": "2026-06-01", "direction": direction,
        "timing_deteccion": timing, "volatility_14d_pct": vol, "max_advance_pct": advance,
        "max_drawdown_pct": -3.0,
    }


def test_get_cached_reference_table_no_recalcula_antes_de_expirar_ttl():
    hs._reset_cache_for_tests()
    calls = {"n": 0}
    orig = hs._load_rows_from_db

    def _fake_load():
        calls["n"] += 1
        return [_row(f"SIM{i}") for i in range(35)]

    hs._load_rows_from_db = _fake_load
    try:
        table1 = hs.get_cached_reference_table(ttl_seconds=3600)
        table2 = hs.get_cached_reference_table(ttl_seconds=3600)
        assert calls["n"] == 1, "la segunda llamada dentro del TTL no debe recalcular"
        assert table1 is table2
    finally:
        hs._load_rows_from_db = orig
        hs._reset_cache_for_tests()


def test_get_cached_reference_table_recalcula_tras_expirar_ttl():
    hs._reset_cache_for_tests()
    calls = {"n": 0}
    orig = hs._load_rows_from_db

    def _fake_load():
        calls["n"] += 1
        return [_row(f"SIM{i}") for i in range(35)]

    hs._load_rows_from_db = _fake_load
    try:
        hs.get_cached_reference_table(ttl_seconds=0)
        hs.get_cached_reference_table(ttl_seconds=0)
        assert calls["n"] == 2, "TTL=0 debe forzar recalculo en cada llamada"
    finally:
        hs._load_rows_from_db = orig
        hs._reset_cache_for_tests()


def test_generate_report_sigue_sin_cache_siempre_recalcula():
    calls = {"n": 0}
    orig = hs._load_rows_from_db

    def _fake_load():
        calls["n"] += 1
        return [_row(f"SIM{i}") for i in range(35)]

    hs._load_rows_from_db = _fake_load
    try:
        hs.generate_report()
        hs.generate_report()
        assert calls["n"] == 2, "generate_report() no debe usar el cache nuevo"
    finally:
        hs._load_rows_from_db = orig


def test_cache_vacio_no_rompe_devuelve_tabla_vacia():
    hs._reset_cache_for_tests()
    orig = hs._load_rows_from_db
    hs._load_rows_from_db = lambda: []
    try:
        table = hs.get_cached_reference_table()
        assert table == {}
    finally:
        hs._load_rows_from_db = orig
        hs._reset_cache_for_tests()
