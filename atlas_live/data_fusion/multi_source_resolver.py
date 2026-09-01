"""Resolver multi-fuente para Mercado (2026-08-31, autorizado explícitamente).

Capa PURA y aislada -- sin I/O propio, sin conocer HTTP/proveedores. Recibe
los `Quote` YA obtenidos (o `None` si no se consultó/no hubo dato) de las 3
fuentes que Atlas ya tiene configuradas (Tradier/Yahoo/Finnhub) y decide
cuál usar, con una sola regla central: **FRESCO > STALE**, nunca al revés,
sin importar qué proveedor respondió primero.

No importa ni modifica `TradierProvider`, `fetch_universe_quotes`,
`radar_worker.py` ni `scan_worker.py` -- Radar sigue exactamente igual que
hoy. Este módulo es usado ÚNICAMENTE por `market_view.py`.

Regla de selección (Caso A-E, pedido explícito):
  A) Tradier fresco (`price_is_stale=False`, ya lo calcula TradierProvider
     internamente -- se confía en ese flag, es el único de los 3
     confiable) -> se usa Tradier siempre, tenga o no bid/ask, PORQUE
     cuando tiene bid/ask es la señal más completa (precio ejecutable
     real) -- nunca se prefiere Yahoo/Finnhub sobre un Tradier fresco.
  B) Tradier stale -> se evalúa Yahoo. Frescura de Yahoo/Finnhub se
     calcula SIEMPRE de forma independiente (`now - timestamp` contra el
     mismo umbral) -- el flag `price_is_stale` propio de Yahoo NO es
     confiable (comprobado con evidencia real 2026-08-31: 11/11 símbolos
     con `price_is_stale=False` pese a tener >54h de antigüedad) y nunca
     se usa acá.
  C) Tradier y Yahoo stale/no disponibles -> se evalúa Finnhub, mismo
     criterio de frescura independiente.
  D) Las 3 fuentes stale/no disponibles -> se conserva el último dato
     conocido de Mercado (cache ya existente, `_last_known_by_symbol`),
     marcado STALE con su antigüedad real.
  E) Ninguna fuente tiene precio y tampoco hay cache -> SIN_DATO. Nunca
     se inventa un precio.

Sesión: se determina por el TIMESTAMP REAL del dato ganador (nunca por la
hora actual) reutilizando `market_hours.get_session(now=<timestamp del
dato>)` -- la misma función ya usada en todo Atlas, sin duplicar su
lógica de horarios. Overnight/BOATS: `overnight_disponible` SOLO es
`True` si la fuente entrega explícitamente un valor no nulo en su propio
campo de overnight (hoy: `Quote.price_overnight` de Yahoo, siempre `None`
en la práctica -- comprobado con 11 símbolos reales) -- nunca se infiere
overnight a partir del horario del reloj. Si en el futuro una fuente
empieza a llenar ese campo, este resolver ya sabe usarlo sin que haga
falta reescribir `market_view.py`.
"""

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from atlas_live.memory import market_hours

# Mismo concepto y mismo default que `TradierProvider.BID_ASK_MAX_AGE_SECONDS`
# (180s) -- se REPLICA acá con su propia env var (nunca se importa ni se
# modifica tradier_provider.py, aislamiento total) para decidir frescura de
# Yahoo/Finnhub de forma independiente y consistente con el resto de Atlas.
FRESHNESS_MAX_AGE_SECONDS = float(os.environ.get("ATLAS_MERCADO_FALLBACK_MAX_AGE_SECONDS", "180.0"))

SOURCES = ("tradier", "yahoo", "finnhub", "cache", "sin_dato")
SESSIONS = ("PREMARKET", "REGULAR", "AFTERHOURS", "OVERNIGHT", "CLOSED_UNKNOWN", "SIN_DATO")


@dataclass
class PrecioResuelto:
    symbol: str
    price: Optional[float]
    previous_close: Optional[float]
    change_pct: Optional[float]
    source: str                 # "tradier" | "yahoo" | "finnhub" | "cache" | "sin_dato"
    timestamp: Optional[datetime]
    session: str                # uno de SESSIONS
    is_stale: bool
    price_basis: Optional[str]
    overnight_disponible: bool = False


def _to_aware(ts: Optional[datetime]) -> Optional[datetime]:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _age_seconds(ts: Optional[datetime], now: datetime) -> Optional[float]:
    ts = _to_aware(ts)
    if ts is None:
        return None
    return (now - ts).total_seconds()


def es_tradier_fresco(quote: Optional[Any]) -> bool:
    """Tradier ya calcula su propia frescura internamente
    (`_resolve_current_price`, Caso A/B/B2 = fresco, Caso C = stale) --
    ese flag SÍ es confiable, se reutiliza tal cual. Pública -- reutilizada
    por `market_view.py` para decidir qué símbolos necesitan fallback."""
    if quote is None or quote.last_price is None:
        return False
    return not bool(getattr(quote, "price_is_stale", False))


_es_tradier_fresco = es_tradier_fresco


def es_fresco_independiente(quote: Optional[Any], now: datetime) -> bool:
    """Para Yahoo/Finnhub: frescura calculada SIEMPRE por nosotros mismos
    (`now - timestamp`), nunca confiando en el flag `price_is_stale`
    propio de la fuente (comprobado no confiable para Yahoo). Pública --
    `market_view.py` la reutiliza para decidir si hace falta seguir a
    Finnhub después de intentar Yahoo, sin duplicar el criterio."""
    if quote is None or quote.last_price is None:
        return False
    age = _age_seconds(quote.timestamp, now)
    return age is not None and age <= FRESHNESS_MAX_AGE_SECONDS


# Alias privado (compatibilidad interna con el resto de este módulo).
_es_fresco_independiente = es_fresco_independiente


def _calcular_change_pct(price: Optional[float], previous_close: Optional[float]) -> Optional[float]:
    """`change_pct` real, calculado a partir de `price`/`previous_close`
    -- reutilizable sin importar si el dato es fresco o STALE (2026-08-31,
    caso real: Caso D-bis descartaba un % perfectamente calculable con
    datos reales solo por estar marcado STALE, dejando el ranking sin
    poder ordenar símbolos que sí tenían un porcentaje real conocido).
    Nunca inventa: `None` si falta cualquiera de los dos datos o si
    `previous_close` es 0 (división inválida)."""
    if price is None or previous_close is None or previous_close == 0:
        return None
    return ((price - previous_close) / previous_close) * 100


def _clasificar_sesion(timestamp: Optional[datetime]) -> str:
    """Sesión del DATO, no del reloj actual -- reutiliza
    `market_hours.get_session(now=timestamp)` tal cual (sin duplicar su
    lógica de horarios), evaluada sobre el timestamp real de la fuente
    ganadora. 'closed' se expone como CLOSED_UNKNOWN acá (nunca
    "OVERNIGHT" inferido del horario -- overnight solo se marca si la
    fuente lo declaró explícitamente, ver `overnight_disponible`)."""
    ts = _to_aware(timestamp)
    if ts is None:
        return "SIN_DATO"
    try:
        session = market_hours.get_session(now=ts)
    except Exception:
        return "CLOSED_UNKNOWN"
    return {
        "premarket": "PREMARKET",
        "regular": "REGULAR",
        "afterhours": "AFTERHOURS",
        "overnight": "OVERNIGHT",
        "closed": "CLOSED_UNKNOWN",
    }.get(session, "CLOSED_UNKNOWN")


def _overnight_explicito(quote: Optional[Any]) -> Optional[float]:
    """Solo Yahoo tiene hoy un campo dedicado (`price_overnight`), y hoy
    siempre es `None` (comprobado con 11 símbolos reales, 2026-08-31).
    Se lee de forma genérica (`getattr`) para que si mañana OTRA fuente
    agrega un campo equivalente, alcance con extender esta función --
    nunca reescribir market_view.py."""
    return getattr(quote, "price_overnight", None) if quote is not None else None


def resolver_mejor_precio(
    symbol: str,
    tradier_quote: Optional[Any],
    yahoo_quote: Optional[Any],
    finnhub_quote: Optional[Any],
    now: datetime,
    cached: Optional[dict] = None,
) -> PrecioResuelto:
    """Función PURA -- ningún I/O acá. `cached` es la última entrada
    conocida de `market_view._last_known_by_symbol` (o `None`), mismo
    formato ya usado por Mercado."""

    # Caso especial: overnight explícito de una fuente (ej. Yahoo
    # price_overnight) -- si algún día existe, es más específico que
    # cualquier otro precio "fresco" genérico, porque describe la sesión
    # exacta que estamos buscando. Hoy: inerte (siempre None), pero deja
    # la arquitectura lista sin tocar market_view.py cuando exista.
    overnight_val = _overnight_explicito(yahoo_quote) or _overnight_explicito(finnhub_quote)
    if overnight_val is not None:
        fuente_overnight = yahoo_quote if _overnight_explicito(yahoo_quote) is not None else finnhub_quote
        return PrecioResuelto(
            symbol=symbol, price=overnight_val,
            previous_close=getattr(fuente_overnight, "previous_close", None),
            change_pct=None,  # nunca se inventa -- el campo overnight no trae % propio garantizado
            source="yahoo" if fuente_overnight is yahoo_quote else "finnhub",
            timestamp=getattr(fuente_overnight, "timestamp", None),
            session="OVERNIGHT", is_stale=False, price_basis="overnight_explicito",
            overnight_disponible=True,
        )

    # Caso A: Tradier fresco -- gana siempre, tenga o no bid/ask (cuando
    # los tiene, es la señal más completa; nunca se prefiere otra fuente
    # sobre un Tradier fresco).
    if _es_tradier_fresco(tradier_quote):
        return PrecioResuelto(
            symbol=symbol, price=tradier_quote.last_price,
            previous_close=tradier_quote.previous_close, change_pct=tradier_quote.change_percent,
            source="tradier", timestamp=tradier_quote.timestamp,
            session=_clasificar_sesion(tradier_quote.timestamp), is_stale=False,
            price_basis=getattr(tradier_quote, "price_basis", None),
        )

    # Caso B: Tradier stale -- se evalua Yahoo con frescura independiente.
    if _es_fresco_independiente(yahoo_quote, now):
        return PrecioResuelto(
            symbol=symbol, price=yahoo_quote.last_price,
            previous_close=yahoo_quote.previous_close, change_pct=yahoo_quote.change_percent,
            source="yahoo", timestamp=yahoo_quote.timestamp,
            session=_clasificar_sesion(yahoo_quote.timestamp), is_stale=False,
            price_basis="yahoo_fresh",
        )

    # Caso C: Tradier y Yahoo stale/no disponibles -- se evalua Finnhub.
    if _es_fresco_independiente(finnhub_quote, now):
        return PrecioResuelto(
            symbol=symbol, price=finnhub_quote.last_price,
            previous_close=finnhub_quote.previous_close, change_pct=finnhub_quote.change_percent,
            source="finnhub", timestamp=finnhub_quote.timestamp,
            session=_clasificar_sesion(finnhub_quote.timestamp), is_stale=False,
            price_basis="finnhub_fresh",
        )

    # Caso D: las 3 fuentes stale/no disponibles -- conservar el ultimo
    # dato conocido de Mercado, marcado STALE con su antigüedad real.
    if cached is not None and cached.get("price") is not None:
        return PrecioResuelto(
            symbol=symbol, price=cached.get("price"),
            previous_close=cached.get("previous_close"), change_pct=cached.get("change_pct"),
            source="cache", timestamp=cached.get("cached_at"),
            session=_clasificar_sesion(cached.get("cached_at")), is_stale=True,
            price_basis=cached.get("price_basis"),
        )

    # Caso D-bis: no hay cache, pero SÍ hay al menos un precio stale real
    # de alguna fuente (nunca se inventa -- se usa el dato stale más
    # reciente disponible, marcado explícitamente, en vez de "sin dato"
    # cuando en realidad SÍ hay un numero real, solo viejo).
    candidatos_stale = [
        (q, src, basis) for q, src, basis in (
            (tradier_quote, "tradier", getattr(tradier_quote, "price_basis", None)),
            (yahoo_quote, "yahoo", "yahoo_stale"),
            (finnhub_quote, "finnhub", "finnhub_stale"),
        ) if q is not None and q.last_price is not None
    ]
    if candidatos_stale:
        # el mas reciente de los stale disponibles (ordenar por antigüedad
        # ascendente -- edad `None` va al final, nunca gana por accidente)
        def _orden_por_frescura(item):
            edad = _age_seconds(item[0].timestamp, now)
            return edad if edad is not None else float("inf")

        q, src, basis = min(candidatos_stale, key=_orden_por_frescura)
        return PrecioResuelto(
            symbol=symbol, price=q.last_price, previous_close=q.previous_close,
            change_pct=_calcular_change_pct(q.last_price, q.previous_close),
            source=src, timestamp=q.timestamp,
            session=_clasificar_sesion(q.timestamp), is_stale=True, price_basis=basis,
        )

    # Caso E: ninguna fuente tiene precio y tampoco hay cache -- SIN_DATO,
    # nunca se inventa.
    return PrecioResuelto(
        symbol=symbol, price=None, previous_close=None, change_pct=None,
        source="sin_dato", timestamp=None, session="SIN_DATO", is_stale=False, price_basis=None,
    )
