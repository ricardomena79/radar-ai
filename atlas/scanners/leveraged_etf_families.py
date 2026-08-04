"""Familias de activos: ETF apalancado <-> activo subyacente que replica.

El Universo Racional no trae esta relación como dato (Asset solo tiene
symbol/name/type), y el nombre del ETF no es un formato confiable para
derivarla automáticamente (cada emisor lo escribe distinto). Por eso es
una lista curada a mano, pensada para crecer con el tiempo a medida que
se detectan casos como PTIR/PLTR.

GlobalRadar usa esto para no tratar a un ETF apalancado y su subyacente
como símbolos completamente independientes: si uno se mueve fuerte, el
otro se revisa también, aunque no haya cruzado sus propios umbrales de
actividad.
"""

from typing import Dict, List, Optional

# ETF apalancado -> símbolo subyacente que replica.
LEVERAGED_ETF_UNDERLYING: Dict[str, str] = {
    "PTIR": "PLTR",
    "NVDL": "NVDA",
    "TSLL": "TSLA",
    "AMUU": "AMD",
}


def underlying_of(leveraged_symbol: str) -> Optional[str]:
    """Símbolo subyacente de un ETF apalancado conocido, o None si no está en la lista."""
    return LEVERAGED_ETF_UNDERLYING.get(leveraged_symbol)


def leveraged_etfs_of(underlying_symbol: str) -> List[str]:
    """ETFs apalancados conocidos que replican a `underlying_symbol` (puede haber más de uno)."""
    return [etf for etf, underlying in LEVERAGED_ETF_UNDERLYING.items() if underlying == underlying_symbol]
