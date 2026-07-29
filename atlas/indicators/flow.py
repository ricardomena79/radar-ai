"""Indicadores de flujo de precio: Gap %."""


def gap_percent(open_price: float, previous_close: float) -> float:
    """Gap % = (apertura - cierre anterior) / cierre anterior * 100."""
    if open_price is None or previous_close is None:
        raise ValueError("open_price y previous_close son obligatorios")
    if previous_close <= 0:
        raise ValueError("previous_close debe ser mayor que 0")
    return ((open_price - previous_close) / previous_close) * 100
