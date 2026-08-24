"""Modelo de datos: catalizador/noticia normalizado, independiente del proveedor.

Hermano de `quote.py::Quote` -- misma idea (transporte proveedor ->
collector -> motor), nunca la fila cruda que persiste `catalyst_registry.py`
(esa vive como dict, mismo estilo que el resto de `atlas_live/radar/`)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class CatalystEvent:
    """Catalizador/noticia estandarizado, devuelto por el collector tras
    clasificar un item crudo del proveedor (ver `atlas_live/catalyst/catalyst_collector.py`)."""

    ticker: str
    catalyst_type: str          # EARNINGS | FDA_PDUFA | CLINICAL_TRIAL | MA_ACQUISITION |
                                 # CONTRACT_AWARD | GUIDANCE | ANALYST_ACTION |
                                 # FINANCING_DILUTION | PARTNERSHIP | PRODUCT_LAUNCH | OTHER_MATERIAL
    headline: str
    summary: Optional[str]
    source: str                  # "finnhub_company_news" | "finnhub_earnings_calendar"
    source_id: Optional[str]     # id real del proveedor -- clave de dedup cuando existe
    url: Optional[str]           # trazabilidad a la fuente original (nunca oculta)
    published_at: Optional[datetime]
    event_date: Optional[str]    # YYYY-MM-DD -- cuándo ocurre/ocurrió el catalizador en sí
    event_time: Optional[str]    # "BMO" | "AMC" | "TBD" | "HH:MM" | None
    importance: str              # "alta" | "media" | "baja"
    direction: str                # "ALCISTA" | "BAJISTA" | "NEUTRAL" | "INDEFINIDA"
    confidence: float             # 0.0-1.0 -- confianza del match de clasificación, nunca inventada
    lifecycle_state: str          # FUTURO | INMINENTE | EN_ANTICIPACION | OCURRIDO | EXTENDIDA
    is_future: bool
    is_imminent: bool
    is_already_occurred: bool
    retrieved_at: datetime        # cuándo Atlas trajo este registro (bookkeeping de cache/TTL)
