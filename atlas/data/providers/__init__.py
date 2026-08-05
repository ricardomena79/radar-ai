"""Registro de proveedores de datos disponibles. Agregar nuevos proveedores aquí, no en el resto del sistema."""

import os
from typing import Dict, List, Type

from dotenv import load_dotenv

from atlas.data.providers.base import DataProvider
from atlas.data.providers.finnhub import FinnhubProvider
from atlas.data.providers.multi_provider import MultiProvider
from atlas.data.providers.yahoo_finance import YahooFinanceProvider

# Carga .env (FINNHUB_API_KEY, ATLAS_DATA_PROVIDERS, etc.) apenas se
# importa este módulo -- antes, solo se cargaba a mano en scripts sueltos,
# así que el proceso real de Atlas Live nunca veía las variables del
# archivo. No pisa variables ya seteadas en el entorno real (override
# implícito en False, default de python-dotenv).
load_dotenv()

_PROVIDERS: Dict[str, Type[DataProvider]] = {
    "yahoo_finance": YahooFinanceProvider,
    "finnhub": FinnhubProvider,
    # Agregar acá cada proveedor nuevo a medida que se conecte
    # (Alpaca, Twelve Data, Alpha Vantage, ...).
}

DEFAULT_PROVIDER_NAME = "yahoo_finance"


def get_provider(name: str = DEFAULT_PROVIDER_NAME) -> DataProvider:
    """Instancia el proveedor registrado bajo `name`."""
    try:
        provider_cls = _PROVIDERS[name]
    except KeyError as exc:
        raise ValueError(f"Proveedor desconocido: '{name}'. Disponibles: {list(_PROVIDERS)}") from exc
    return provider_cls()


def _configured_provider_names() -> List[str]:
    """Lista ordenada de nombres de proveedor a usar, de la variable de entorno.

    ATLAS_DATA_PROVIDERS (plural) define la prioridad de failover, separada
    por comas -- ej. "yahoo_finance,alpaca,finnhub". Si no está definida,
    se usa ATLAS_DATA_PROVIDER (singular, un solo proveedor) por
    compatibilidad; si tampoco está definida, el default de siempre.
    """
    plural = os.environ.get("ATLAS_DATA_PROVIDERS")
    if plural:
        return [name.strip() for name in plural.split(",") if name.strip()]

    singular = os.environ.get("ATLAS_DATA_PROVIDER")
    if singular:
        return [singular.strip()]

    return [DEFAULT_PROVIDER_NAME]


def get_default_provider() -> DataProvider:
    """Instancia el proveedor (o los proveedores) configurados vía variable de entorno.

    Único punto que el resto de Atlas Core necesita conocer: para cambiar
    de proveedor por defecto, o agregar failover entre varios (Yahoo
    Finance -> Alpaca -> Finnhub -> Twelve Data -> Alpha Vantage -> ...),
    alcanza con registrar el proveedor nuevo arriba en `_PROVIDERS` y
    ajustar ATLAS_DATA_PROVIDERS -- nada más en el sistema cambia.

    Con un solo proveedor configurado (el caso de hoy) devuelve ese
    proveedor directamente, sin envolverlo -- comportamiento idéntico al
    de antes de que existiera MultiProvider. Con dos o más, devuelve un
    MultiProvider que hace failover automático entre ellos, en el orden
    dado.
    """
    names = _configured_provider_names()
    providers = [get_provider(name) for name in names]
    if len(providers) == 1:
        return providers[0]
    return MultiProvider(providers)
