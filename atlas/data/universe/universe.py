"""Universo de instrumentos que Atlas puede analizar.

La fuente de datos (hoy: el snapshot de Racional derivado del PDF oficial)
se inyecta como un `loader`: una función sin argumentos que devuelve una
lista de registros {symbol, name, type}. Cambiar el origen en el futuro
(CSV, API, base de datos) solo requiere escribir un nuevo loader con esa
misma firma y pasarlo a `Universe`; el resto de Atlas sigue usando
load_universe(), get_symbols(), get_equities(), get_etfs(), is_available()
y get_asset() sin cambios.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from atlas.data.universe.racional_loader import load_assets

AssetLoader = Callable[[], List[Dict[str, str]]]


@dataclass(frozen=True)
class Asset:
    """Instrumento del universo, tal como aparece en la fuente."""

    symbol: str
    name: str
    type: str


class Universe:
    """Colección en memoria de los instrumentos disponibles, indexada por símbolo."""

    def __init__(self, loader: AssetLoader = load_assets) -> None:
        self._loader = loader
        self._assets: Optional[Dict[str, Asset]] = None

    def load(self) -> Dict[str, Asset]:
        """Carga (o recarga) el universo completo desde el loader configurado."""
        assets: Dict[str, Asset] = {}
        for record in self._loader():
            asset = Asset(symbol=record["symbol"], name=record["name"], type=record["type"])
            assets[asset.symbol] = asset
        self._assets = assets
        return self._assets

    def _ensure_loaded(self) -> Dict[str, Asset]:
        if self._assets is None:
            self.load()
        return self._assets

    def symbols(self) -> List[str]:
        """Todos los símbolos disponibles, ordenados alfabéticamente."""
        return sorted(self._ensure_loaded().keys())

    def equities(self) -> List[Asset]:
        """Todos los instrumentos de tipo EQUITY."""
        return [asset for asset in self._ensure_loaded().values() if asset.type == "EQUITY"]

    def etfs(self) -> List[Asset]:
        """Todos los instrumentos de tipo ETF."""
        return [asset for asset in self._ensure_loaded().values() if asset.type == "ETF"]

    def is_available(self, symbol: str) -> bool:
        """Indica si un símbolo existe en el universo."""
        return symbol.upper() in self._ensure_loaded()

    def asset(self, symbol: str) -> Optional[Asset]:
        """Devuelve el Asset asociado a un símbolo, o None si no existe."""
        return self._ensure_loaded().get(symbol.upper())


_universe = Universe()


def load_universe() -> Dict[str, Asset]:
    """Carga (o recarga) el universo completo desde la fuente configurada."""
    return _universe.load()


def get_symbols() -> List[str]:
    """Devuelve todos los símbolos disponibles, ordenados alfabéticamente."""
    return _universe.symbols()


def get_equities() -> List[Asset]:
    """Devuelve todos los instrumentos de tipo EQUITY."""
    return _universe.equities()


def get_etfs() -> List[Asset]:
    """Devuelve todos los instrumentos de tipo ETF."""
    return _universe.etfs()


def is_available(symbol: str) -> bool:
    """Indica si un símbolo existe en el universo."""
    return _universe.is_available(symbol)


def get_asset(symbol: str) -> Optional[Asset]:
    """Devuelve el Asset asociado a un símbolo, o None si no existe."""
    return _universe.asset(symbol)
