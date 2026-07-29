"""Prueba manual del módulo atlas.data.universe."""

from atlas.data.universe import get_asset, get_equities, get_etfs, get_symbols, is_available, load_universe

CHECK_SYMBOLS = ["AAPL", "NVDA", "PLTR", "SOXL"]


def test_universe() -> None:
    universe = load_universe()
    symbols = get_symbols()
    equities = get_equities()
    etfs = get_etfs()

    assert len(universe) == len(symbols)
    assert len(equities) + len(etfs) <= len(symbols)

    for symbol in CHECK_SYMBOLS:
        assert is_available(symbol), f"{symbol} debería estar disponible"
        assert get_asset(symbol) is not None

    print("=" * 40)
    print("ATLAS - PRUEBA DE UNIVERSE (Racional)")
    print("=" * 40)
    print(f"Total de instrumentos : {len(symbols)}")
    print(f"Total de acciones     : {len(equities)}")
    print(f"Total de ETF          : {len(etfs)}")
    print("-" * 40)
    for symbol in CHECK_SYMBOLS:
        asset = get_asset(symbol)
        print(f"{symbol:6} disponible={is_available(symbol)}  {asset.type:6} {asset.name}")
    print("=" * 40)
    print("OK: universo cargado y verificado correctamente.")


if __name__ == "__main__":
    test_universe()
