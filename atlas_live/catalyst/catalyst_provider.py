"""Construcción del proveedor de catalizadores desde el entorno (2026-08-23).

Mismo patrón exacto que `atlas_live/data_fusion/universe_quotes.py::build_tradier_provider()`:
degradación segura -- si `FINNHUB_API_KEY` no está configurada, devuelve
`None` en vez de fallar, y el caller (catalyst_worker) queda apagado sin
tumbar el resto de Atlas. Reutiliza `FinnhubProvider` tal cual -- ninguna
key ni cliente HTTP nuevo (misma key que ya usa `registry.get_default_provider()`
para cotizaciones de respaldo)."""

import logging
import os
from typing import Optional

from dotenv import load_dotenv

from atlas_live.data_fusion.finnhub_provider import FinnhubProvider

logger = logging.getLogger(__name__)

_load_dotenv_done = False
_warned_no_finnhub = False


def _ensure_env_loaded() -> None:
    global _load_dotenv_done
    if not _load_dotenv_done:
        load_dotenv()
        _load_dotenv_done = True


def build_catalyst_provider() -> Optional[FinnhubProvider]:
    """`None` si `FINNHUB_API_KEY` no está configurada -- degradación
    segura, el catalyst_worker queda OFFLINE sin afectar el radar técnico
    (que no depende de este proveedor en ningún punto)."""
    global _warned_no_finnhub
    _ensure_env_loaded()
    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        if not _warned_no_finnhub:
            logger.warning(
                "FINNHUB_API_KEY no configurada -- Motor de Catalizadores OFFLINE "
                "(el radar técnico sigue funcionando sin cambios)."
            )
            _warned_no_finnhub = True
        return None
    return FinnhubProvider(api_key)
