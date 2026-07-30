"""Money Flow Engine: detecta hacia qué sectores e industrias está entrando el dinero.

Agrupa acciones del Universo Racional por sector e industria (Quote.sector,
Quote.industry) y calcula, para cada grupo, un Money Flow Score (0-100) a
partir de:
  - cambio porcentual promedio del grupo
  - relative volume promedio del grupo
  - proporción de acciones con cambio positivo
  - proporción de acciones con Momentum Score > 70

Reutiliza (sin modificarlos) DataCollector, el Universo Racional y
calculate_momentum_score() del Momentum Engine. No genera señales de
compra/venta, no usa IA y no decide nada: solo agrupa y puntúa.

Por defecto escanea solo acciones (get_equities()): la clasificación por
sector/industria de Yahoo Finance no aplica de forma significativa a ETFs,
que suelen devolver sector/industry vacíos.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import pandas as pd

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.providers.yahoo_finance import YahooFinanceProvider
from atlas.data.universe import Asset, get_equities
from atlas.engine.momentum_engine import calculate_momentum_score

MOMENTUM_THRESHOLD = 70.0
UNCLASSIFIED = "Sin clasificar"

UniverseProvider = Callable[[], Dict[str, Asset]]


def _default_equity_universe() -> Dict[str, Asset]:
    return {asset.symbol: asset for asset in get_equities()}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class StockFlow:
    """Datos de una acción individual usados para calcular el Money Flow de su grupo."""

    symbol: str
    name: str
    sector: str
    industry: str
    change_percent: float
    relative_volume: Optional[float]
    momentum_score: float


@dataclass(frozen=True)
class GroupFlow:
    """Money Flow Score de un grupo (sector o industria), con sus acciones."""

    name: str
    money_flow_score: float
    stock_count: int
    positive_count: int
    high_momentum_count: int
    avg_change_percent: float
    avg_relative_volume: float
    stocks: List[StockFlow]

    def top_stocks(self, n: int = 5) -> List[StockFlow]:
        """Principales acciones del grupo, ordenadas por Momentum Score."""
        return sorted(self.stocks, key=lambda s: s.momentum_score, reverse=True)[:n]


class MoneyFlowEngine:
    """Agrupa acciones por sector e industria y calcula un Money Flow Score por grupo."""

    def __init__(
        self,
        collector: Optional[DataCollector] = None,
        universe_provider: Optional[UniverseProvider] = None,
        max_workers: int = 8,
    ) -> None:
        self._collector = collector or DataCollector(YahooFinanceProvider())
        self._universe_provider = universe_provider or _default_equity_universe
        self._max_workers = max_workers
        self._sector_results: List[GroupFlow] = []
        self._industry_results: List[GroupFlow] = []
        self.last_scan_duration_seconds: Optional[float] = None
        self.last_scan_errors: int = 0

    def _build_stock_flow(self, asset: Asset) -> Optional[StockFlow]:
        try:
            quote = self._collector.get_quote(asset.symbol)
            momentum = calculate_momentum_score(asset.symbol, self._collector)
        except Exception:
            # Un fallo puntual de datos para un símbolo no debe interrumpir
            # el escaneo del resto del universo.
            return None

        return StockFlow(
            symbol=asset.symbol,
            name=asset.name,
            sector=quote.sector or UNCLASSIFIED,
            industry=quote.industry or UNCLASSIFIED,
            change_percent=quote.change_percent,
            relative_volume=quote.relative_volume,
            momentum_score=momentum.momentum_score,
        )

    @staticmethod
    def _score_group(name: str, stocks: List[StockFlow]) -> GroupFlow:
        count = len(stocks)
        avg_change = sum(s.change_percent for s in stocks) / count

        rvols = [s.relative_volume for s in stocks if s.relative_volume is not None]
        avg_rvol = sum(rvols) / len(rvols) if rvols else 0.0

        positive_count = sum(1 for s in stocks if s.change_percent > 0)
        high_momentum_count = sum(1 for s in stocks if s.momentum_score > MOMENTUM_THRESHOLD)

        change_score = _clamp(50 + avg_change * 10)
        rvol_score = _clamp(avg_rvol * 50)
        positive_ratio_score = _clamp((positive_count / count) * 100)
        momentum_ratio_score = _clamp((high_momentum_count / count) * 100)

        money_flow_score = round(
            change_score * 0.30 + rvol_score * 0.30 + positive_ratio_score * 0.20 + momentum_ratio_score * 0.20,
            2,
        )

        return GroupFlow(
            name=name,
            money_flow_score=money_flow_score,
            stock_count=count,
            positive_count=positive_count,
            high_momentum_count=high_momentum_count,
            avg_change_percent=round(avg_change, 2),
            avg_relative_volume=round(avg_rvol, 2),
            stocks=stocks,
        )

    @classmethod
    def _group_and_score(cls, stocks: List[StockFlow], key: Callable[[StockFlow], str]) -> List[GroupFlow]:
        groups: Dict[str, List[StockFlow]] = {}
        for stock in stocks:
            groups.setdefault(key(stock), []).append(stock)

        flows = [cls._score_group(name, group) for name, group in groups.items()]
        flows.sort(key=lambda g: g.money_flow_score, reverse=True)
        return flows

    def scan(self) -> List[GroupFlow]:
        """Escanea el universo configurado y devuelve el ranking de sectores por Money Flow Score."""
        universe = self._universe_provider()
        assets = list(universe.values())

        start = time.monotonic()

        self._collector.get_quotes([asset.symbol for asset in assets])

        stocks: List[StockFlow] = []
        errors = 0

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = [executor.submit(self._build_stock_flow, asset) for asset in assets]
            for future in as_completed(futures):
                result = future.result()
                if result is None:
                    errors += 1
                else:
                    stocks.append(result)

        self._sector_results = self._group_and_score(stocks, key=lambda s: s.sector)
        self._industry_results = self._group_and_score(stocks, key=lambda s: s.industry)

        self.last_scan_errors = errors
        self.last_scan_duration_seconds = time.monotonic() - start
        return self._sector_results

    def top(self, n: int = 10) -> List[GroupFlow]:
        """Top `n` sectores del último scan(), ordenados por Money Flow Score."""
        return self._sector_results[:n]

    def top_industries(self, n: int = 10) -> List[GroupFlow]:
        """Top `n` industrias del último scan(), ordenadas por Money Flow Score."""
        return self._industry_results[:n]

    def to_dataframe(self) -> pd.DataFrame:
        """Exporta el ranking de sectores del último scan() como DataFrame."""
        return pd.DataFrame(
            [
                {
                    "sector": g.name,
                    "money_flow_score": g.money_flow_score,
                    "stock_count": g.stock_count,
                    "positive_count": g.positive_count,
                    "high_momentum_count": g.high_momentum_count,
                    "avg_change_percent": g.avg_change_percent,
                    "avg_relative_volume": g.avg_relative_volume,
                }
                for g in self._sector_results
            ]
        )
