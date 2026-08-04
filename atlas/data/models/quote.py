"""Modelo de datos: cotización normalizada de un instrumento, independiente del proveedor."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# Sesión de mercado a la que corresponde este precio. Un DataProvider debe
# normalizar el estado crudo de su fuente (ej. el `marketState` de Yahoo
# Finance) a una de estas cuatro constantes. Ningún motor de Atlas
# interpreta este valor para calcular nada -- es solo información de
# procedencia para que las capas de presentación (ej. Atlas Live) sepan
# qué sesión representa el precio que están mostrando.
SESSION_PREMARKET = "PREMARKET"
SESSION_REGULAR = "REGULAR"
SESSION_AFTERHOURS = "AFTERHOURS"
SESSION_CLOSED = "CLOSED"


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
    session: str = SESSION_REGULAR
