"""Modelo de datos: cotización normalizada de un instrumento, independiente del proveedor."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# Sentinel para `Quote.executable_price` (Fase 1D, 2026-08-24) -- distingue
# "el proveedor no dijo nada" (se espeja `last_price`, comportamiento actual
# sin cambios) de "el proveedor dijo explícitamente que NO hay precio
# ejecutable" (queda `None` de verdad). Un default `None` normal no permite
# esa distinción -- ver `Quote.__post_init__` más abajo.
_EXECUTABLE_PRICE_UNSET = object()


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

    # Cuarta sesión (2026-08-02, ver DATA_FUSION_ENGINE_PROPUESTA.md,
    # "SESIÓN OVERNIGHT"): "Overnight" (ej. Blue Ocean ATS), que algunas
    # plataformas externas muestran y que ningún proveedor de Atlas
    # entrega hoy. Campo dejado listo a propósito -- siempre `None`
    # mientras no exista un proveedor que lo llene (ninguno lo hace
    # todavía) -- para que un futuro proveedor del Data Fusion Engine
    # pueda poblarlo sin tocar este archivo ni ningún consumidor.
    price_overnight: Optional[float] = None

    # STALE_SESSION_FALLBACK (Fase 8, 2026-08-18, caso real PTEN): True
    # cuando se esperaba precio de premarket/after-hours (según
    # `market_state`) pero el proveedor no lo tenía y el `price_type`
    # realmente usado terminó siendo "regular" -- el precio mostrado
    # pertenece a la sesión ANTERIOR, no a la actual. Nunca `True` para
    # Tradier (concepto exclusivo del pipeline Yahoo en vivo). Puramente
    # informativo acá -- quién decide qué hacer con esto vive en
    # `scan_worker.py`, no en este modelo.
    stale_session_fallback: bool = False

    # Precio de premarket vía bid/ask (2026-08-18, caso real: `last`/
    # `trade_date` de Tradier llegan congelados en el cierre de la sesión
    # anterior durante premarket -- verificado con evidencia real contra
    # Yahoo Finance, ver `atlas/data/providers/tradier_provider.py`).
    # `price_basis` distingue qué señal se usó para `last_price`/
    # `change_percent`/`timestamp` de arriba: "tradier_last" (el trade más
    # reciente, caso normal) o "tradier_bid_ask_mid" (punto medio bid/ask,
    # usado solo cuando `last` está vencido y bid/ask son frescos y
    # confiables). `bid`/`ask`/`bid_timestamp`/`ask_timestamp` quedan
    # expuestos siempre que Tradier los entregue, para trazabilidad --
    # nunca se usan por defecto salvo en el caso `tradier_bid_ask_mid`.
    # `None` para proveedores que no son Tradier (concepto exclusivo de
    # este pipeline, mismo criterio que `stale_session_fallback`).
    price_basis: Optional[str] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_timestamp: Optional[datetime] = None
    ask_timestamp: Optional[datetime] = None

    # `price_is_stale` (2026-08-24, Fase 1 -- corrección de datos premarket,
    # caso real NSSC: $38.09/0% congelado ~46 minutos seguidos mientras el
    # movimiento premarket real seguía). `True` únicamente cuando NINGUNA
    # señal fue rescatable (`last` vencido Y bid/ask no válidos/frescos/de
    # spread angosto) -- `price_basis` queda en
    # `"tradier_regular_close_stale"` en ese caso, y `change_percent` queda
    # `None` (nunca un "0%" calculado sobre un precio congelado, ver
    # `_resolve_current_price`). `last_price` SÍ se conserva (el último
    # cierre regular real, mostrado como referencia, nunca inventado) --
    # solo `change_percent` se descarta, porque ese específicamente sería
    # engañoso. Concepto exclusivo de Tradier, mismo criterio que
    # `price_basis`/`stale_session_fallback` -- `False` por defecto para
    # cualquier otro proveedor.
    price_is_stale: bool = False

    # BID_ONLY (2026-08-24, Fase 1C -- caso real NSSC: `bid`=$39.00 fresco
    # y válido, a 1.44% del precio real de Yahoo, pero `ask`=$61.76 con un
    # spread de 45.18%, muy por encima de `MAX_MIDPOINT_SPREAD_PCT` -- la
    # regla de `price_basis="tradier_bid_ask_mid"` descartaba el PAR
    # bid/ask completo, perdiendo también el bid que sí era confiable).
    # `price_basis="tradier_bid_only"` marca el caso en que SOLO el bid
    # (nunca el ask) fue usado para `last_price`/`change_percent` --
    # `bid_only_reason` documenta por qué se descartó el ask
    # (`"ask_ausente"`/`"ask_invalido"`/`"ask_vencido"`/`"ask_roto"`,
    # ver `_classify_ask()` en `tradier_provider.py`), `None` en cualquier
    # otro caso. Concepto exclusivo de Tradier, mismo criterio que
    # `price_basis`/`price_is_stale`.
    bid_only_reason: Optional[str] = None

    # `executable_price` (2026-08-24, Fase 1D -- auditoría de seguridad tras
    # Fase 1C): separa el precio de SEÑAL (`last_price`, arriba -- sirve para
    # detectar movimiento, nunca cambia de significado) del precio
    # EJECUTABLE (a qué precio existiría una contraparte real de compra
    # ahora mismo). Para cualquier Quote donde el proveedor no diga nada
    # (default, ver `__post_init__` abajo -- Yahoo/Finnhub, y los Casos A/B
    # de Tradier) `executable_price` espeja `last_price`, EXACTAMENTE el
    # comportamiento de antes de este campo -- nada se rompe. Solo
    # `TradierProvider` lo pone explícitamente en `None` cuando el precio
    # resuelto NO tiene una contraparte de compra verificable: caso real
    # NSSC, `price_basis="tradier_bid_only"` (el precio es el BID -- lo que
    # el mercado te compraría, no a lo que vos podrías comprar; el ask que
    # daría esa contraparte está roto/vencido/ausente, ver
    # `tradier_provider._classify_ask()`) y `price_basis=
    # "tradier_regular_close_stale"` (el precio ni siquiera es de ahora).
    # Ningún consumidor existente lee este campo todavía -- es puramente
    # aditivo hasta que se decida usarlo (endpoints/UI, Fase 1D).
    executable_price: Optional[float] = _EXECUTABLE_PRICE_UNSET  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.executable_price is _EXECUTABLE_PRICE_UNSET:
            object.__setattr__(self, "executable_price", self.last_price)
