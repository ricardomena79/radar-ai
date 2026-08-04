"""Radar Momentum PRO: detecta oportunidades de momentum intradía en el Universo Racional.

A diferencia de un scanner genérico, este radar está pensado como el punto de
entrada especializado para detectar activos con mayor probabilidad de
movimientos explosivos en la apertura del mercado: recorre el Universo
Racional completo, obtiene datos vía DataCollector y ordena todo según el
Atlas Score ya implementado (sin recalcularlo ni modificarlo).

No genera señales de compra, no genera alertas y no usa IA: solo mide y
ordena, igual que el resto del motor de Atlas.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import pandas as pd

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.universe import Asset, load_universe
from atlas.engine.atlas_score import AtlasScore, calculate_atlas_score

UniverseProvider = Callable[[], Dict[str, Asset]]


@dataclass(frozen=True)
class MomentumRadarResult:
    """Instrumento evaluado por el Radar Momentum PRO."""

    symbol: str
    name: str
    asset_type: str
    price: float
    change_pct: float
    relative_volume: Optional[float]
    atlas_score: float
    detail: AtlasScore


class MomentumRadar:
    """Recorre el Universo Racional y ordena los instrumentos por Atlas Score.

    Usa exclusivamente módulos ya existentes y aprobados: DataCollector para
    los datos, calculate_atlas_score() para la puntuación, y el Universo
    Racional como fuente de instrumentos. No implementa lógica propia de
    scoring ni de filtrado.

    `universe_provider` es un punto de inyección de dependencias (igual que
    en PremarketScanner) para poder probar con un subconjunto sin cambiar
    el comportamiento por defecto, que es escanear el universo completo.
    """

    def __init__(
        self,
        collector: Optional[DataCollector] = None,
        universe_provider: Optional[UniverseProvider] = None,
        max_workers: int = 8,
    ) -> None:
        self._collector = collector or DataCollector()
        self._universe_provider = universe_provider or load_universe
        self._max_workers = max_workers
        self._results: List[MomentumRadarResult] = []
        self.last_scan_duration_seconds: Optional[float] = None
        self.last_scan_errors: int = 0

    def _build_result(self, asset: Asset) -> Optional[MomentumRadarResult]:
        try:
            quote = self._collector.get_quote(asset.symbol)
            score = calculate_atlas_score(asset.symbol, self._collector)
        except Exception:
            # El universo tiene miles de símbolos con datos poco confiables
            # (delistados, sin historial suficiente). Un fallo puntual no
            # debe interrumpir el escaneo del resto.
            return None

        return MomentumRadarResult(
            symbol=asset.symbol,
            name=asset.name,
            asset_type=asset.type,
            price=quote.last_price,
            change_pct=quote.change_percent,
            relative_volume=quote.relative_volume,
            atlas_score=score.total,
            detail=score,
        )

    def scan(self) -> List[MomentumRadarResult]:
        """Escanea el universo configurado y ordena los resultados por Atlas Score."""
        universe = self._universe_provider()
        assets = list(universe.values())

        start = time.monotonic()

        # Precarga las cotizaciones en un único lote (DataCollector.get_quotes),
        # así cada símbolo no dispara su propia consulta individual.
        self._collector.get_quotes([asset.symbol for asset in assets])

        results: List[MomentumRadarResult] = []
        errors = 0

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = [executor.submit(self._build_result, asset) for asset in assets]
            for future in as_completed(futures):
                result = future.result()
                if result is None:
                    errors += 1
                else:
                    results.append(result)

        results.sort(key=lambda r: r.atlas_score, reverse=True)

        self._results = results
        self.last_scan_errors = errors
        self.last_scan_duration_seconds = time.monotonic() - start
        return self._results

    def top(self, n: int = 20) -> List[MomentumRadarResult]:
        """Devuelve los `n` instrumentos con mayor Atlas Score del último scan()."""
        return self._results[:n]

    def get(self, symbol: str) -> Optional[MomentumRadarResult]:
        """Devuelve el resultado de un símbolo del último scan(), o None si no está."""
        symbol = symbol.upper()
        for result in self._results:
            if result.symbol == symbol:
                return result
        return None

    def to_dataframe(self) -> pd.DataFrame:
        """Exporta los resultados del último scan() como DataFrame, ordenado por Atlas Score."""
        return pd.DataFrame(
            [
                {
                    "symbol": r.symbol,
                    "name": r.name,
                    "asset_type": r.asset_type,
                    "price": r.price,
                    "change_pct": r.change_pct,
                    "relative_volume": r.relative_volume,
                    "atlas_score": r.atlas_score,
                }
                for r in self._results
            ]
        )
