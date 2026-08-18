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
