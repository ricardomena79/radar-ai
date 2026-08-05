"""Registro de proveedores activos del Data Fusion Engine (Etapa 0, 2026-08-05).

Único lugar donde se decide qué proveedores están activos y en qué
orden -- ver DATA_FUSION_ENGINE_PROPUESTA.md, sección "DATA FUSION
ENGINE": "un solo lugar para agregar/quitar/reordenar proveedores en el
futuro, en vez de tocar los call sites cada vez". `scan_worker.py` (y
cualquier otro módulo futuro) debe construir su proveedor exclusivamente
llamando a `get_default_provider()` -- nunca instanciando
`YahooFinanceLiveProvider`/`FinnhubProvider` por su cuenta (regla de
arquitectura ya declarada, ver `yahoo_finance_live_provider.py`).

Orden de prioridad: Yahoo Finance primero (proveedor primario, sin
cambios de comportamiento respecto a antes de esta etapa), Finnhub como
respaldo -- solo entra si Yahoo falla con `ProviderError` (mecanismo ya
probado en `MultiProvider`, ver DECISION_LOG.md).

Degradación segura: si `FINNHUB_API_KEY` no está configurada en este
entorno (`.env` ausente o incompleto), Atlas sigue funcionando -- el
registro devuelve un `MultiProvider` de un solo proveedor (Yahoo), sin
respaldo, en vez de fallar al arrancar. Se registra una advertencia una
sola vez, no en cada ciclo de escaneo.
"""

import os

from dotenv import load_dotenv
from loguru import logger

from atlas.data.providers.base import DataProvider
from atlas_live.data_fusion.finnhub_provider import FinnhubProvider
from atlas_live.data_fusion.multi_provider import MultiProvider
from atlas_live.data_fusion.yahoo_finance_live_provider import YahooFinanceLiveProvider

_load_dotenv_done = False
_warned_no_finnhub = False


def _ensure_env_loaded() -> None:
    global _load_dotenv_done
    if not _load_dotenv_done:
        load_dotenv()
        _load_dotenv_done = True


def get_default_provider() -> DataProvider:
    """Construye el proveedor por defecto de Atlas Live: Yahoo primero,
    Finnhub como respaldo si hay una API key configurada. Se llama una
    vez por ciclo de escaneo (mismo patrón que ya usaban los call sites
    con `YahooFinanceLiveProvider()` directo) -- construir el objeto es
    barato, no abre conexiones hasta que se use."""
    global _warned_no_finnhub
    _ensure_env_loaded()

    providers = [YahooFinanceLiveProvider()]

    api_key = os.environ.get("FINNHUB_API_KEY")
    if api_key:
        providers.append(FinnhubProvider(api_key))
    elif not _warned_no_finnhub:
        logger.warning(
            "FINNHUB_API_KEY no configurada -- Atlas Live corre sin proveedor de respaldo "
            "(solo Yahoo Finance). Ver .env.example."
        )
        _warned_no_finnhub = True

    return MultiProvider(providers)
