"""Radar Global: primera etapa de un radar de dos etapas sobre el Universo Racional.

Recorre TODO el universo (acciones + ETFs, ~2.577 activos) para construir una
lista de candidatos, sin calcular Atlas Score ni tomar ninguna decisión. Es
deliberadamente liviano: usa solo lo que una única cotización (`Quote`) ya
trae -- cambio %, volumen relativo, gap, capitalización -- para que correr
sobre el universo completo sea rápido. La Etapa 2 (Atlas Score, Momentum,
Money Flow, Decision Engine, Learning Engine) sigue funcionando exactamente
igual que hoy, sobre los candidatos que este radar entrega.

No reemplaza ni modifica ningún motor existente. No decide qué comprar: solo
mide y filtra, igual que el resto de los scanners de Atlas.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from typing import Callable, Dict, List, Optional

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.providers.base import ProviderError, QuoteNotFoundError
from atlas.data.universe import Asset, get_equities, get_etfs
from atlas.indicators.flow import gap_percent
from atlas.scanners.leveraged_etf_families import leveraged_etfs_of, underlying_of

UniverseProvider = Callable[[], Dict[str, Asset]]

# --- Configuración, todo nombrado y ajustable, nada como número mágico ---

DEFAULT_MAX_WORKERS = 30
DEFAULT_TOP_N_PER_CATEGORY = 60

# Un activo se considera "microcap" por debajo de este umbral. Atlas Core no
# tiene una categoría de microcap propia; este es el criterio estándar de
# la industria ($300M), usado solo para priorizar candidatos en el radar.
MICROCAP_MARKET_CAP_THRESHOLD = 300_000_000.0

# Piso de actividad para que una microcap o un ETF apalancado entren como
# candidatos "activos" (no cualquier microcap/ETF apalancado, solo los que
# se están moviendo de verdad).
MIN_ACTIVE_RELATIVE_VOLUME = 1.3
MIN_ACTIVE_CHANGE_PERCENT = 3.0

# Heurística de texto para detectar ETFs apalancados/inversos: el Universo
# Racional no trae un campo de apalancamiento, así que esto es una
# aproximación por nombre, no un dato garantizado.
LEVERAGED_ETF_KEYWORDS = (
    "2X", "3X", "ULTRAPRO", "ULTRA", "DAILY TARGET", "BULL", "BEAR",
    "INVERSE", "LEVERAGED",
)


@dataclass(frozen=True)
class GlobalRadarResult:
    """Un activo evaluado por el Radar Global, con las señales livianas que lo hicieron candidato."""

    symbol: str
    name: Optional[str]
    asset_type: str
    price: float
    change_percent: float
    relative_volume: Optional[float]
    gap_percent: Optional[float]
    market_cap: Optional[float]
    is_leveraged_etf: bool
    reasons: List[str]


def _is_leveraged_etf(asset: Asset) -> bool:
    if asset.type != "ETF" or not asset.name:
        return False
    name_upper = asset.name.upper()
    return any(keyword in name_upper for keyword in LEVERAGED_ETF_KEYWORDS)


def _is_active(result: "GlobalRadarResult") -> bool:
    """Mismo piso de actividad usado para microcaps y ETFs apalancados,
    y reutilizado para decidir si un movimiento amerita traer también a
    su par de familia (ver `_expand_families`)."""
    return (
        (result.relative_volume is not None and result.relative_volume >= MIN_ACTIVE_RELATIVE_VOLUME)
        or abs(result.change_percent) >= MIN_ACTIVE_CHANGE_PERCENT
    )


class GlobalRadar:
    """Recorre el Universo Racional completo con un filtro liviano (sin Atlas Score).

    Reutiliza exclusivamente DataCollector y el indicador `gap_percent` ya
    existentes. No calcula Atlas Score, Momentum Score, Money Flow ni
    ninguna decisión -- eso sigue siendo responsabilidad exclusiva de la
    Etapa 2 (el pipeline actual de Atlas Core), sin cambios.
    """

    def __init__(
        self,
        collector: Optional[DataCollector] = None,
        universe_provider: Optional[UniverseProvider] = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        top_n_per_category: int = DEFAULT_TOP_N_PER_CATEGORY,
    ) -> None:
        self._collector = collector or DataCollector()
        self._universe_provider = universe_provider or self._default_universe
        self._max_workers = max_workers
        self._top_n = top_n_per_category
        self._candidates: List[GlobalRadarResult] = []
        self.last_scan_duration_seconds: Optional[float] = None
        self.last_scan_symbols_reviewed: int = 0
        self.last_scan_errors: int = 0

    @staticmethod
    def _default_universe() -> Dict[str, Asset]:
        assets = get_equities() + get_etfs()
        return {asset.symbol: asset for asset in assets}

    def _build_result(self, asset: Asset) -> Optional[GlobalRadarResult]:
        try:
            quote = self._collector.get_quote(asset.symbol)
        except (ProviderError, QuoteNotFoundError):
            return None

        try:
            gap = gap_percent(quote.open, quote.previous_close) if quote.open else None
        except ValueError:
            gap = None

        return GlobalRadarResult(
            symbol=asset.symbol,
            name=asset.name or quote.name,
            asset_type=asset.type,
            price=quote.last_price,
            change_percent=quote.change_percent or 0.0,
            relative_volume=quote.relative_volume,
            gap_percent=gap,
            market_cap=quote.market_cap,
            is_leveraged_etf=_is_leveraged_etf(asset),
            reasons=[],
        )

    def scan(self) -> List[GlobalRadarResult]:
        """Recorre el universo completo en paralelo y arma la lista de candidatos."""
        universe = self._universe_provider()
        assets = list(universe.values())

        start = time.monotonic()

        results: List[GlobalRadarResult] = []
        errors = 0
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = [executor.submit(self._build_result, asset) for asset in assets]
            for future in as_completed(futures):
                result = future.result()
                if result is None:
                    errors += 1
                else:
                    results.append(result)

        self._candidates = self._select_candidates(results)
        self.last_scan_errors = errors
        self.last_scan_symbols_reviewed = len(assets)
        self.last_scan_duration_seconds = time.monotonic() - start
        return self._candidates

    def _select_candidates(self, results: List[GlobalRadarResult]) -> List[GlobalRadarResult]:
        """Une el top-N de cada criterio en una sola lista sin duplicados.

        El objetivo no es analizar más símbolos: es no dejar afuera, por un
        muestreo fijo, la oportunidad que se está moviendo de verdad hoy --
        sea una microcap, una empresa grande o un ETF apalancado.
        """
        by_symbol: Dict[str, GlobalRadarResult] = {}

        def _add(pool: List[GlobalRadarResult], reason: str) -> None:
            for r in pool[: self._top_n]:
                existing = by_symbol.get(r.symbol)
                if existing is None:
                    by_symbol[r.symbol] = replace(r, reasons=[reason])
                elif reason not in existing.reasons:
                    existing.reasons.append(reason)

        _add(sorted(results, key=lambda r: abs(r.change_percent), reverse=True), "mayor_cambio_pct")

        with_rvol = [r for r in results if r.relative_volume is not None]
        _add(sorted(with_rvol, key=lambda r: r.relative_volume, reverse=True), "mayor_volumen_relativo")

        with_gap = [r for r in results if r.gap_percent is not None]
        _add(sorted(with_gap, key=lambda r: abs(r.gap_percent), reverse=True), "gap_importante")

        leveraged_active = [r for r in results if r.is_leveraged_etf and _is_active(r)]
        _add(sorted(leveraged_active, key=lambda r: abs(r.change_percent), reverse=True), "etf_apalancado_activo")

        active_microcaps = [
            r for r in results
            if r.market_cap is not None and r.market_cap < MICROCAP_MARKET_CAP_THRESHOLD and _is_active(r)
        ]
        _add(sorted(active_microcaps, key=lambda r: abs(r.change_percent), reverse=True), "microcap_activa")

        self._expand_families(by_symbol, results)

        return list(by_symbol.values())

    def _expand_families(
        self, by_symbol: Dict[str, "GlobalRadarResult"], all_results: List["GlobalRadarResult"]
    ) -> None:
        """Si un candidato ya seleccionado es un ETF apalancado o un subyacente conocido y
        tuvo movimiento extraordinario, trae también a su par de familia -- sin ninguna
        llamada de red nueva, `all_results` ya tiene los datos de todo el universo.

        No trata al ETF apalancado y a su subyacente como símbolos independientes: si uno
        entró por su propia actividad, el otro se revisa también, aunque no haya cruzado
        sus propios umbrales.
        """
        all_by_symbol = {r.symbol: r for r in all_results}
        additions: Dict[str, "GlobalRadarResult"] = {}

        for symbol, candidate in by_symbol.items():
            if not _is_active(candidate):
                continue

            underlying_symbol = underlying_of(symbol)
            if underlying_symbol and underlying_symbol not in by_symbol:
                underlying_result = all_by_symbol.get(underlying_symbol)
                if underlying_result:
                    additions[underlying_symbol] = replace(
                        underlying_result, reasons=["activo_subyacente_de_etf_apalancado"]
                    )

            for etf_symbol in leveraged_etfs_of(symbol):
                if etf_symbol not in by_symbol:
                    etf_result = all_by_symbol.get(etf_symbol)
                    if etf_result:
                        additions[etf_symbol] = replace(
                            etf_result, reasons=["etf_apalancado_de_activo_subyacente"]
                        )

        by_symbol.update(additions)

    def candidates(self) -> List[GlobalRadarResult]:
        """Devuelve los candidatos del último scan()."""
        return self._candidates
