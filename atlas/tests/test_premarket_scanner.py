"""Prueba manual del Premarket Scanner.

Un escaneo del Universo Racional completo (~2577 símbolos) implica ~3
llamadas a Yahoo Finance por símbolo y puede tardar horas. Para verificar
el pipeline end-to-end en minutos, esta prueba inyecta un subconjunto
representativo y estratificado (acciones + ETF, a lo largo de todo el
alfabeto) como universo de entrada. El scanner en sí no filtra nada: por
defecto (sin argumentos) recorre atlas.data.universe.load_universe()
completo.
"""

from atlas.data.universe import Asset, get_equities, get_etfs
from atlas.scanners.premarket import PremarketScanner

REQUIRED_SYMBOLS = ["AAPL", "NVDA", "PLTR", "SOXL"]

# Tiempo medido en la Fase 5 (versión secuencial, sin caché) sobre este mismo
# subconjunto de 104 símbolos: 98 analizados, 6 sin datos, 196.8s totales.
PREVIOUS_BENCHMARK_SECONDS = 196.8


def _sample(assets, count: int):
    if len(assets) <= count:
        return list(assets)
    step = max(1, len(assets) // count)
    return assets[::step][:count]


def _build_sample_universe() -> dict:
    equities = _sample(get_equities(), 60)
    etfs = _sample(get_etfs(), 40)

    universe = {asset.symbol: asset for asset in equities + etfs}

    for symbol in REQUIRED_SYMBOLS:
        if symbol not in universe:
            universe[symbol] = Asset(symbol=symbol, name=symbol, type="EQUITY")

    return universe


def test_premarket_scanner() -> None:
    sample_universe = _build_sample_universe()
    scanner = PremarketScanner(universe_provider=lambda: sample_universe)

    results = scanner.scan()

    assert scanner.last_scan_duration_seconds is not None
    assert len(results) > 0
    scores = [r.atlas_score for r in results]
    assert scores == sorted(scores, reverse=True)

    scanned_symbols = {r.symbol for r in results}
    for symbol in REQUIRED_SYMBOLS:
        assert symbol in scanned_symbols, f"{symbol} debería estar entre los resultados"

    df = scanner.export_dataframe()
    assert len(df) == len(results)

    print("=" * 60)
    print("ATLAS - PREMARKET SCANNER")
    print("=" * 60)
    print(f"Símbolos en el universo de prueba : {len(sample_universe)}")
    print(f"Activos analizados con éxito       : {len(results)}")
    print(f"Símbolos sin datos suficientes     : {scanner.last_scan_errors}")
    print(f"Tiempo total del escaneo           : {scanner.last_scan_duration_seconds:.1f}s")

    print("\n--- TOP 20 ATLAS SCORE (todos los tipos) ---")
    for rank, result in enumerate(scanner.top(20), start=1):
        print(f"{rank:2}. {result.symbol:6} {result.type:6} score={result.atlas_score:6.2f}  {result.name}")

    etfs_only = [r for r in results if r.type == "ETF"]
    print("\n--- TOP 20 ETF ---")
    for rank, result in enumerate(etfs_only[:20], start=1):
        print(f"{rank:2}. {result.symbol:6} score={result.atlas_score:6.2f}  {result.name}")

    equities_only = [r for r in results if r.type == "EQUITY"]
    print("\n--- TOP 20 ACCIONES ---")
    for rank, result in enumerate(equities_only[:20], start=1):
        print(f"{rank:2}. {result.symbol:6} score={result.atlas_score:6.2f}  {result.name}")

    new_time = scanner.last_scan_duration_seconds
    improvement_pct = (PREVIOUS_BENCHMARK_SECONDS - new_time) / PREVIOUS_BENCHMARK_SECONDS * 100

    print("\n--- BENCHMARK: FASE 5 (secuencial) vs FASE 6 (optimizado) ---")
    print(f"Tiempo anterior (Fase 5, secuencial) : {PREVIOUS_BENCHMARK_SECONDS:.1f}s")
    print(f"Tiempo nuevo (Fase 6, optimizado)    : {new_time:.1f}s")
    print(f"Mejora                               : {improvement_pct:.1f}%")

    print("=" * 60)
    print("OK: Premarket Scanner funciona correctamente sobre el subconjunto de prueba.")


if __name__ == "__main__":
    test_premarket_scanner()
