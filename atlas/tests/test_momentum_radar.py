"""Prueba manual del Radar Momentum PRO sobre 100 símbolos del Universo Racional.

Un escaneo del Universo Racional completo (~2577 símbolos) puede tardar
horas. Para verificar el radar en minutos, esta prueba inyecta un
subconjunto representativo y estratificado de 100 símbolos (acciones + ETF)
como universo de entrada. El radar en sí no filtra nada: por defecto (sin
argumentos) recorre atlas.data.universe.load_universe() completo.
"""

from atlas.data.universe import Asset, get_equities, get_etfs
from atlas.scanners.momentum_radar import MomentumRadar

REQUIRED_SYMBOLS = ["AAPL", "NVDA", "PLTR", "SOXL"]


def _sample(assets, count: int):
    if len(assets) <= count:
        return list(assets)
    step = max(1, len(assets) // count)
    return assets[::step][:count]


def _build_sample_universe(total: int = 100) -> dict:
    equities = _sample(get_equities(), int(total * 0.6))
    etfs = _sample(get_etfs(), total - len(equities))

    universe = {asset.symbol: asset for asset in equities + etfs}

    for symbol in REQUIRED_SYMBOLS:
        if symbol not in universe:
            universe[symbol] = Asset(symbol=symbol, name=symbol, type="EQUITY")

    return universe


def test_momentum_radar() -> None:
    sample_universe = _build_sample_universe(100)
    radar = MomentumRadar(universe_provider=lambda: sample_universe)

    results = radar.scan()

    assert radar.last_scan_duration_seconds is not None
    assert len(results) > 0
    scores = [r.atlas_score for r in results]
    assert scores == sorted(scores, reverse=True)

    df = radar.to_dataframe()
    assert len(df) == len(results)
    for column in ["symbol", "name", "asset_type", "price", "change_pct", "relative_volume", "atlas_score"]:
        assert column in df.columns

    print("=" * 60)
    print("ATLAS - RADAR MOMENTUM PRO")
    print("=" * 60)
    print(f"Símbolos en el universo de prueba : {len(sample_universe)}")
    print(f"Activos analizados con éxito       : {len(results)}")
    print(f"Símbolos sin datos suficientes     : {radar.last_scan_errors}")
    print(f"Tiempo total del escaneo           : {radar.last_scan_duration_seconds:.1f}s")

    print("\n--- TOP 10 ATLAS SCORE ---")
    for rank, r in enumerate(radar.top(10), start=1):
        print(
            f"{rank:2}. {r.symbol:6} {r.asset_type:6} score={r.atlas_score:6.2f}  "
            f"price={r.price:9.2f}  chg={r.change_pct:+6.2f}%  rvol={r.relative_volume or 0:.2f}  {r.name}"
        )

    print("\n--- VERIFICACIÓN AAPL / NVDA / PLTR / SOXL ---")
    for symbol in REQUIRED_SYMBOLS:
        result = radar.get(symbol)
        if result is None:
            print(f"{symbol:6} sin datos disponibles en este escaneo")
        else:
            print(
                f"{symbol:6} OK  score={result.atlas_score:6.2f}  price={result.price:9.2f}  "
                f"chg={result.change_pct:+6.2f}%  rvol={result.relative_volume or 0:.2f}"
            )
            assert result.symbol == symbol

    print("=" * 60)
    print("OK: Radar Momentum PRO funciona correctamente sobre el subconjunto de prueba.")


if __name__ == "__main__":
    test_momentum_radar()
