"""Premarket Scanner: recorre el Universo Racional y calcula el Atlas Score de cada instrumento.

Para cada símbolo: obtiene datos vía DataCollector, calcula indicadores y el
Atlas Score (ambos ya implementados en atlas.engine.atlas_score), y guarda
el resultado. Al finalizar, ordena todo de mayor a menor Atlas Score.

No decide qué comprar, no aplica filtros de negocio (precio mínimo, float,
etc.) y no implementa IA, dashboard ni Explosion Index: solo recorre y
puntúa.
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
class ScanResult:
    """Atlas Score de un instrumento, junto con sus metadatos del Universo Racional."""

    symbol: str
    name: str
    type: str
    atlas_score: float
    detail: AtlasScore


class PremarketScanner:
    """Recorre un universo de instrumentos y calcula el Atlas Score de cada uno.

    Por defecto escanea el Universo Racional completo (atlas.data.universe).
    `universe_provider` es un punto de inyección de dependencias (igual que
    DataCollector recibe un DataProvider) para poder escanear un subconjunto
    en pruebas, sin agregar lógica de filtrado al scanner en sí.
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
        self._results: List[ScanResult] = []
        self.last_scan_duration_seconds: Optional[float] = None
        self.last_scan_errors: int = 0

    def _score_one(self, asset: Asset) -> Optional[ScanResult]:
        try:
            score = calculate_atlas_score(asset.symbol, self._collector)
        except Exception:
            # El universo tiene miles de símbolos con datos poco confiables
            # (delistados, sin historial suficiente, respuestas vacías de
            # Yahoo Finance). Un fallo puntual de Atlas Score para un
            # símbolo no debe interrumpir el escaneo del resto.
            return None

        return ScanResult(
            symbol=asset.symbol,
            name=asset.name,
            type=asset.type,
            atlas_score=score.total,
            detail=score,
        )

    def scan(self) -> List[ScanResult]:
        """Calcula el Atlas Score de cada instrumento del universo y lo guarda.

        Al finalizar, los resultados quedan ordenados de mayor a menor Atlas Score.

        Optimizaciones sobre la versión secuencial (mismos resultados funcionales):
        - Precarga las cotizaciones de todo el universo en un único lote
          (DataCollector.get_quotes), para que cada símbolo no dispare su
          propia consulta individual de cotización cuando se calcule su score.
        - Calcula los Atlas Score en paralelo con un pool de `max_workers`
          hilos (I/O-bound: la espera de red se solapa entre símbolos).
        - El propio DataCollector cachea en memoria (MemoryCache), así que
          una segunda llamada a scan() dentro del TTL reutiliza los datos.
        """
        universe = self._universe_provider()
        assets = list(universe.values())

        start = time.monotonic()

        self._collector.get_quotes([asset.symbol for asset in assets])

        results: List[ScanResult] = []
        errors = 0

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = [executor.submit(self._score_one, asset) for asset in assets]
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

    def top(self, n: int = 20) -> List[ScanResult]:
        """Devuelve los `n` instrumentos con mayor Atlas Score del último scan()."""
        return self._results[:n]

    def export_dataframe(self) -> pd.DataFrame:
        """Exporta los resultados del último scan() como DataFrame, ordenado por Atlas Score."""
        return pd.DataFrame(
            [
                {
                    "symbol": r.symbol,
                    "name": r.name,
                    "type": r.type,
                    "atlas_score": r.atlas_score,
                }
                for r in self._results
            ]
        )
