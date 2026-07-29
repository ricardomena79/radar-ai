"""Registro de proveedores de datos disponibles. Agregar nuevos proveedores aquí, no en el resto del sistema."""

from typing import Dict, Type

from atlas.data.providers.base import DataProvider
from atlas.data.providers.yahoo_finance import YahooFinanceProvider

_PROVIDERS: Dict[str, Type[DataProvider]] = {
    "yahoo_finance": YahooFinanceProvider,
}


def get_provider(name: str = "yahoo_finance") -> DataProvider:
    """Instancia el proveedor registrado bajo `name`."""
    try:
        provider_cls = _PROVIDERS[name]
    except KeyError as exc:
        raise ValueError(f"Proveedor desconocido: '{name}'. Disponibles: {list(_PROVIDERS)}") from exc
    return provider_cls()
