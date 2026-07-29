"""Modelo de datos: cotización normalizada de un instrumento, independiente del proveedor."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Quote:
    """Cotización estandarizada devuelta por cualquier DataProvider."""

    symbol: str
    name: Optional[str]
    last_price: float
    change_percent: float
    volume: Optional[int]
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    previous_close: Optional[float]
    market_cap: Optional[float] = None
    sector: Optional[str] = None
    float_shares: Optional[int] = None
    average_volume: Optional[int] = None
    relative_volume: Optional[float] = None
    timestamp: Optional[datetime] = None
