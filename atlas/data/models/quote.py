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
    industry: Optional[str] = None
    float_shares: Optional[int] = None
    average_volume: Optional[int] = None
    relative_volume: Optional[float] = None
    timestamp: Optional[datetime] = None

    # Trazabilidad de precio (2026-08-02, ver DATA_FUSION_ENGINE_PROPUESTA.md,
    # addendum) -- aditivos, con default, no rompen ningún consumidor
    # existente. `last_price`/`change_percent`/`timestamp` arriba siguen
    # siendo "el precio que Atlas usa" -- estos campos solo documentan de
    # dónde salió y a qué sesión de mercado corresponde, y exponen las
    # otras variantes disponibles al mismo tiempo (Regular/Premarket/
    # After-hours) para que la interfaz nunca tenga que ocultar nada.
    source: str = "yahoo_finance"
    price_type: str = "unknown"  # "regular" | "premarket" | "afterhours" | "unknown"
    market_state: Optional[str] = None  # valor crudo del proveedor (ej. Yahoo: REGULAR/PRE/POST/CLOSED)
    price_regular: Optional[float] = None
    price_premarket: Optional[float] = None
    price_afterhours: Optional[float] = None
