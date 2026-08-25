"""TradierProvider -- proveedor PRINCIPAL de datos de mercado del Data Fusion
Engine (Fase Tradier, 2026-08-14).

Implementa `atlas.data.providers.base.DataProvider` -- mismo contrato que
`YahooFinanceLiveProvider`/`FinnhubProvider`, así que nada que ya consuma un
`DataProvider` necesita cambiar.

Validado en vivo antes de escribir este archivo (ver informe de sesión
2026-08-14): cobertura 95.1% cruda / 96.7% tras normalización sobre el
universo neto de 2.575 símbolos del universo Racional; ciclo completo del
universo en ~15s vía 11 requests de 250 símbolos, cero HTTP 429 ni
degradación; historial diario e intradía (1 min, con VWAP y volumen por
minuto) confirmados con datos reales.

Batching real: a diferencia de Finnhub (que no tiene endpoint de lote y
por eso `FinnhubProvider.get_quotes()` itera símbolo por símbolo),
`/v1/markets/quotes` de Tradier acepta cientos de símbolos separados por
coma en un solo request -- `get_quotes()` lo aprovecha en chunks de
`TRADIER_CHUNK_SIZE`.

Símbolo no reconocido: Tradier responde HTTP 200 con el símbolo listado en
`quotes.unmatched_symbols` (o simplemente ausente de `quotes.quote`) -- NO
un 404. Se trata como ausencia normal del símbolo en ese proveedor, nunca
como `ProviderError`; el símbolo normalizado que se le pasa a Tradier viene
siempre de `tradier_symbol_map.normalize()`, nunca se reformatea acá.
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from atlas.data.models.quote import Quote
from atlas.data.providers.base import DataProvider, ProviderError, QuoteNotFoundError

SOURCE_NAME = "tradier"
QUOTES_PATH = "/v1/markets/quotes"
HISTORY_PATH = "/v1/markets/history"
TIMESALES_PATH = "/v1/markets/timesales"
REQUEST_TIMEOUT_SECONDS = 20

# Probado en vivo (2026-08-14): 2.575 símbolos en 11 chunks de 250 -- 200 OK
# en los 11, latencia 0.84-1.07s por chunk, sin rate-limit. No es un límite
# documentado de Tradier, es el tamaño validado empíricamente en esta sesión.
TRADIER_CHUNK_SIZE = 250

_DAYS_BY_PERIOD = {"1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "2y": 730, "5y": 1825}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# --- Precio de premarket vía bid/ask midpoint (2026-08-18) -----------------
#
# Caso real, auditado con evidencia (JSON crudo de Tradier + cruce contra
# Yahoo Finance): `last`/`trade_date` quedan CONGELADOS en el cierre de la
# sesión REGULAR anterior durante todo el premarket (idéntico para símbolos
# líquidos e ilíquidos por igual -- esta cuenta/feed no recibe trade prints
# de extended hours), mientras `bid`/`ask`/`bid_date`/`ask_date` SÍ se
# actualizan en tiempo real (verificado: NVDA bid=220.05 vs Yahoo premarket
# oficial $220.06; XOS bid/ask=4.59-4.60 vs Yahoo premarket oficial $4.585,
# +119.38% -- ambos casos coinciden casi exacto).
#
# Reutiliza EXACTAMENTE el mismo umbral que `scan_worker.PRICE_MAX_AGE_SECONDS`
# (misma variable de entorno `ATLAS_PRICE_MAX_AGE_SECONDS`, default 180s) --
# no se importa desde `atlas_live/` (violaría la capa: `atlas/` no depende de
# `atlas_live/`), se lee la MISMA env var acá a propósito para que nunca
# diverjan.
BID_ASK_MAX_AGE_SECONDS = _env_float("ATLAS_PRICE_MAX_AGE_SECONDS", 180.0)

# Umbral de INTEGRIDAD del dato (nunca de trading): por encima de este spread
# relativo, un par bid/ask ya no se considera representativo de un mercado
# activo -- ejemplos reales 2026-08-18: NVDA 0.02%, XOS 0.22%, SEZL 1.82%,
# todos muy por debajo. Configurable vía env var, mismo patrón que el resto
# del proyecto (`ATLAS_PRICE_MAX_AGE_SECONDS`, `ATLAS_SCAN_REQUEST_DELAY_MS`).
MAX_MIDPOINT_SPREAD_PCT = _env_float("ATLAS_MAX_MIDPOINT_SPREAD_PCT", 8.0)

# --- Fallback BID_ONLY (2026-08-24, Fase 1C) --------------------------------
#
# Caso real NSSC: `bid`=$39.00 fresco y válido (a 1.44% del precio real de
# Yahoo, $39.57) descartado junto con `ask`=$61.76 (spread=45.18%,
# `ask_date` vencido) porque la regla del Caso B exige que AMBOS lados
# pasen validez+frescura+spread antes de usar el punto medio -- un ask roto
# tira también al bid bueno. Este umbral decide cuándo el ask está lo
# bastante roto como para justificar usar el bid SOLO (nunca "cualquier
# spread grande" -- pedido explícito: "no asumir que cualquier spread
# grande significa que el ask está roto").
#
# Dos señales independientes, AMBAS deben cumplirse (evita que un mercado
# simplemente angosto pero real dispare el fallback por una sola métrica):
#   1. El spread ya debe estar MUY por encima del umbral de confianza del
#      punto medio (acá, 3x `MAX_MIDPOINT_SPREAD_PCT` = 24.0%) -- el caso
#      real NSSC (45.18%) lo supera con margen amplio, no es un ajuste fino
#      calibrado para apenas capturarlo.
#   2. El ask debe estar desproporcionadamente por encima del bid (ratio
#      ask/bid >= 1.25, es decir 25%+ por encima) -- un spread ancho pero
#      con ambos lados relativamente cercanos entre sí es menos sospechoso
#      que un ask que casi duplica al bid (NSSC real: ratio=1.584).
# Un spread entre `MAX_MIDPOINT_SPREAD_PCT` y `ASK_BROKEN_SPREAD_PCT`
# (8%-24%) es tratado como "ambiguo" -- ni confiable para el punto medio,
# ni evidencia suficiente para descartar el ask y usar bid-only -- cae al
# Caso C (ningún precio inventado), nunca a BID_ONLY sin evidencia clara.
ASK_BROKEN_SPREAD_PCT = 3 * MAX_MIDPOINT_SPREAD_PCT  # 24.0
ASK_BROKEN_MIN_RATIO = 1.25

# Frescura del BID cuando se usa SOLO (sin ask) -- deliberadamente más laxa
# que `BID_ASK_MAX_AGE_SECONDS` (180s), que exige a los DOS lados frescos
# para promediarlos. Evidencia real (NSSC, 2026-08-24): el bid usable
# ($39.00, a 1.44% de Yahoo) tenía 679s (11.3 min) de antigüedad -- YA
# vencido bajo el umbral de 180s, y sin embargo seguía siendo la mejor
# señal disponible, muy por delante del `last` congelado desde hace 61.5
# horas. Un bid aislado en un nombre poco líquido se actualiza con menos
# frecuencia que un par bid/ask activo -- exigirle el mismo umbral de 180s
# hubiera descartado exactamente el caso real que motivó este fallback.
# Elegido como múltiplo de `BID_ASK_MAX_AGE_SECONDS` (5x = 900s/15min) en
# vez de un número nuevo sin relación -- cubre el único caso real
# disponible (679s) con margen amplio, no un ajuste fino para apenas
# alcanzarlo, pero sigue siendo un límite real (un bid de horas de
# antigüedad no calificaría). Calibración provisional -- revisar con más
# evidencia real cuando esté disponible.
BID_ONLY_MAX_AGE_SECONDS = _env_float("ATLAS_BID_ONLY_MAX_AGE_SECONDS", 5 * BID_ASK_MAX_AGE_SECONDS)


def _epoch_ms_to_dt(epoch_ms: Optional[float]) -> Optional[datetime]:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc) if epoch_ms else None


def _age_seconds(ts: Optional[datetime], now: datetime) -> Optional[float]:
    return (now - ts).total_seconds() if ts is not None else None


def _classify_ask(bid: Optional[float], ask: Optional[float], ask_age: Optional[float]) -> str:
    """Clasifica el estado del ASK para decidir si el par bid/ask sigue
    siendo confiable (`"ok"`), o por qué NO lo es -- la razón exacta queda
    trazada en `Quote.bid_only_reason` cuando habilita el fallback BID_ONLY
    (Fase 1C, 2026-08-24). `"spread_ambiguo"` es its propio estado (ni
    confiable ni claramente roto) para que el llamador nunca lo trate como
    de ask roto sin más -- es un tercer estado propio."""
    if ask is None:
        return "ausente"
    if ask <= 0 or bid is None or bid <= 0 or ask < bid:
        return "invalido"
    if ask_age is None or ask_age > BID_ASK_MAX_AGE_SECONDS:
        return "vencido"
    mid = (bid + ask) / 2
    spread_pct = ((ask - bid) / mid * 100) if mid else None
    if spread_pct is None or spread_pct <= MAX_MIDPOINT_SPREAD_PCT:
        return "ok"
    if spread_pct > ASK_BROKEN_SPREAD_PCT and ask >= bid * ASK_BROKEN_MIN_RATIO:
        return "roto"
    return "spread_ambiguo"


def _resolve_current_price(data: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """Decide qué señal representa el "precio actual" real, en orden:

    Caso A (`LIVE_TRADE`, `price_basis="tradier_last"`): el último trade
    (`last`/`trade_date`) está fresco -- se usa tal cual.

    Caso B (`BID_ASK_MID`, `price_basis="tradier_bid_ask_mid"`): `last`
    vencido, pero bid Y ask están AMBOS frescos, válidos y de spread
    angosto (<=`MAX_MIDPOINT_SPREAD_PCT`) -- se usa el punto medio.

    Caso B2 (`BID_ONLY`, `price_basis="tradier_bid_only"` -- 2026-08-24,
    Fase 1C, caso real NSSC: bid=$39.00 fresco+válido a 1.44% de Yahoo,
    descartado junto con un ask=$61.76 roto, spread=45.18%): `last`
    vencido, el PAR bid/ask no califica para el Caso B, pero el bid POR SÍ
    SOLO es válido+fresco Y el ask fue descartado con evidencia clara
    (ausente/inválido/vencido/roto -- ver `_classify_ask()`), nunca solo
    porque "falta el ask". Un spread ancho pero no claramente roto
    (`"spread_ambiguo"`) NO habilita este caso -- cae al C. `change_percent`
    se calcula contra `prevclose`, igual que en el Caso B.

    Caso C (`STALE_REGULAR_CLOSE`, `price_is_stale=True` -- 2026-08-24,
    Fase 1, caso real NSSC: $38.09/0% congelado ~46 minutos seguidos
    mientras el movimiento premarket real seguía): ninguno de los
    anteriores es confiable -- `last_price` se conserva TAL CUAL (el
    cierre regular anterior, mostrado como referencia, nunca inventado)
    pero `change_percent` se descarta (`None`) -- Tradier lo calcula
    contra ese mismo `last` vencido, así que sería un "0%" engañoso, no un
    dato real.

    `price_basis`/`price_is_stale`/`bid_only_reason` quedan marcados
    explícitamente en los 4 casos -- nunca se inventa un `last_price`
    nuevo en ningún caso, solo se elige CUÁL señal ya real usar y se
    documenta de dónde salió. La cadena de confiabilidad ya existente
    (`scan_worker.compute_price_age_seconds`/`is_price_stale`/
    `estado_validacion=VENCIDO`) sigue intacta, sin cambios -- estos casos
    quedan trazables desde el Quote mismo."""
    last = data.get("last")
    prevclose = data.get("prevclose")
    change_pct_raw = data.get("change_percentage")

    trade_ts = _epoch_ms_to_dt(data.get("trade_date"))
    trade_age = _age_seconds(trade_ts, now)

    bid = data.get("bid")
    ask = data.get("ask")
    bid_ts = _epoch_ms_to_dt(data.get("bid_date"))
    ask_ts = _epoch_ms_to_dt(data.get("ask_date"))

    resolved = {
        "last_price": last, "change_percent": change_pct_raw, "timestamp": trade_ts,
        "price_basis": "tradier_last", "bid": bid, "ask": ask,
        "bid_timestamp": bid_ts, "ask_timestamp": ask_ts, "price_is_stale": False,
        "bid_only_reason": None,
        # `executable_price` (Fase 1D, 2026-08-24 -- separación señal/
        # ejecutable): Caso A por defecto -- un trade recién ejecutado es la
        # mejor aproximación disponible a "a este precio hay contraparte
        # real". Casos B2/C lo pisan a `None` explícitamente más abajo.
        "executable_price": last,
    }

    if trade_age is not None and trade_age <= BID_ASK_MAX_AGE_SECONDS:
        return resolved  # Caso A: `last` genuinamente fresco -- se usa tal cual.

    bid_age = _age_seconds(bid_ts, now)
    ask_age = _age_seconds(ask_ts, now)
    # La antigüedad de AMBOS lados debe estar dentro del umbral -- un bid
    # fresco no puede tapar un ask vencido (o viceversa): un mercado
    # confiable necesita los dos lados vivos, no solo uno.
    both_sides_fresh = (
        bid_age is not None and bid_age <= BID_ASK_MAX_AGE_SECONDS
        and ask_age is not None and ask_age <= BID_ASK_MAX_AGE_SECONDS
    )
    valid_pair = bid is not None and ask is not None and bid > 0 and ask > 0 and ask >= bid

    if valid_pair and both_sides_fresh:
        mid = (bid + ask) / 2
        spread_pct = ((ask - bid) / mid * 100) if mid else None
        if spread_pct is not None and spread_pct <= MAX_MIDPOINT_SPREAD_PCT:
            # Timestamp mostrado = el más reciente de los dos lados (pedido
            # explícito) -- la validez YA exigió que AMBOS estén frescos, así
            # que usar el más reciente acá no oculta ningún lado vencido.
            display_ts = max(bid_ts, ask_ts)
            change_pct = ((mid - prevclose) / prevclose * 100) if prevclose else None
            resolved.update({
                "last_price": mid, "change_percent": change_pct, "timestamp": display_ts,
                "price_basis": "tradier_bid_ask_mid", "price_is_stale": False,
                # Comportamiento SIN CAMBIOS respecto a antes de Fase 1D: el
                # punto medio bid/ask YA exigió ambos lados frescos+válidos+
                # spread angosto -- sigue siendo la mejor aproximación a un
                # precio ejecutable real, ahora solo declarada explícitamente.
                "executable_price": mid,
            })
            return resolved  # Caso B: `last` vencido, bid/ask frescos y confiables.

    # Caso B2: BID_ONLY -- el par bid/ask no calificó arriba (si hubiera
    # calificado, ya se habría retornado), pero el bid SOLO puede seguir
    # siendo confiable. Solo se acepta cuando el ask fue descartado con una
    # razón CLARA (ausente/inválido/vencido/roto) -- nunca en el caso
    # "spread_ambiguo" (evidencia insuficiente para descartar el ask, pero
    # tampoco para confiar en el punto medio) ni cuando el propio bid está
    # vencido/inválido (eso no es "no inventar con el ask roto", sería
    # inventar con el bid roto).
    ask_status = _classify_ask(bid, ask, ask_age)
    bid_fresh = bid is not None and bid > 0 and bid_age is not None and bid_age <= BID_ONLY_MAX_AGE_SECONDS
    if bid_fresh and ask_status in ("ausente", "invalido", "vencido", "roto"):
        change_pct = ((bid - prevclose) / prevclose * 100) if prevclose else None
        resolved.update({
            "last_price": bid, "change_percent": change_pct, "timestamp": bid_ts,
            "price_basis": "tradier_bid_only", "price_is_stale": False,
            "bid_only_reason": f"ask_{ask_status}",
            # Fase 1D (2026-08-24, auditoría de seguridad): el bid es un
            # precio de mercado válido para SEÑAL (por eso `last_price`/
            # `change_percent` SÍ se completan arriba, alimentan detección/
            # gates/momentum sin cambios) -- pero NUNCA es lo que un usuario
            # podría pagar para comprar (eso lo daría el ask, que acá está
            # roto/vencido/ausente por definición de este mismo Caso B2).
            # `executable_price=None` explícito: no hay contraparte de
            # compra verificable a NINGÚN precio confirmado ahora mismo.
            "executable_price": None,
        })
        return resolved

    # Caso C: ninguno confiable -- `last_price` se conserva tal cual (nunca
    # se inventa un precio nuevo), pero `change_percent` se descarta
    # explícitamente (era Tradier calculándolo contra ese mismo `last`
    # vencido) y `price_basis`/`price_is_stale` marcan el caso -- fix
    # directo del caso real NSSC (2026-08-24).
    resolved.update({
        "change_percent": None, "price_basis": "tradier_regular_close_stale", "price_is_stale": True,
        # Fase 1D: un precio vencido (potencialmente de días de antigüedad)
        # tampoco es ejecutable -- mismo criterio que BID_ONLY.
        "executable_price": None,
    })
    return resolved


def _to_quote(data: Dict[str, Any], symbol: str, now: Optional[datetime] = None) -> Quote:
    if data.get("last") is None:
        raise QuoteNotFoundError(symbol)

    now = now or datetime.now(timezone.utc)
    resolved = _resolve_current_price(data, now)

    volume = data.get("volume")
    average_volume = data.get("average_volume")
    relative_volume = None
    if volume is not None and average_volume:
        relative_volume = round(volume / average_volume, 4)

    return Quote(
        symbol=symbol,
        name=data.get("description"),
        last_price=resolved["last_price"],
        change_percent=resolved["change_percent"],
        volume=volume,
        open=data.get("open"),
        high=data.get("high"),
        low=data.get("low"),
        previous_close=data.get("prevclose"),
        average_volume=average_volume,
        relative_volume=relative_volume,
        timestamp=resolved["timestamp"],
        executable_price=resolved["executable_price"],
        source=SOURCE_NAME,
        price_basis=resolved["price_basis"],
        bid_only_reason=resolved["bid_only_reason"],
        bid=resolved["bid"],
        ask=resolved["ask"],
        bid_timestamp=resolved["bid_timestamp"],
        ask_timestamp=resolved["ask_timestamp"],
        price_is_stale=resolved["price_is_stale"],
    )


class TradierProvider(DataProvider):
    """Proveedor principal basado en Tradier -- ver docstring del módulo
    para la evidencia de validación y las limitaciones reales."""

    def __init__(self, api_token: str, base_url: str = "https://api.tradier.com") -> None:
        if not api_token:
            raise ValueError("TradierProvider requiere un api_token no vacío.")
        self._api_token = api_token
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json"}

    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            response = requests.get(
                f"{self._base_url}{path}", params=params, headers=self._headers, timeout=REQUEST_TIMEOUT_SECONDS
            )
        except requests.RequestException as exc:
            raise ProviderError(f"Fallo de red al consultar Tradier {path}: {exc}") from exc
        if response.status_code != 200:
            raise ProviderError(f"Tradier devolvió HTTP {response.status_code} para {path}: {response.text[:300]}")
        return response.json()

    def get_quote(self, symbol: str) -> Quote:
        data = self._get(QUOTES_PATH, {"symbols": symbol, "greeks": "false"})
        quote_obj = data.get("quotes", {}).get("quote")
        if isinstance(quote_obj, list):
            quote_obj = quote_obj[0] if quote_obj else None
        if not quote_obj:
            raise QuoteNotFoundError(symbol)
        return _to_quote(quote_obj, quote_obj.get("symbol", symbol))

    def get_quotes(self, symbols: List[str]) -> List[Quote]:
        """Usa el endpoint de lote real de Tradier en chunks de
        `TRADIER_CHUNK_SIZE`. Símbolos no reconocidos se omiten en
        silencio (mismo criterio que `FinnhubProvider.get_quotes()`) --
        detectar cuáles faltan es responsabilidad del orquestador
        (`atlas_live/data_fusion/universe_quotes.py`), comparando el
        resultado contra la lista pedida."""
        quotes: List[Quote] = []
        for i in range(0, len(symbols), TRADIER_CHUNK_SIZE):
            chunk = [s for s in symbols[i:i + TRADIER_CHUNK_SIZE] if s]
            if not chunk:
                continue
            data = self._get(QUOTES_PATH, {"symbols": ",".join(chunk), "greeks": "false"})
            quote_field = data.get("quotes", {}).get("quote")
            if isinstance(quote_field, list):
                for item in quote_field:
                    if isinstance(item, dict) and item.get("symbol") and item.get("last") is not None:
                        quotes.append(_to_quote(item, item["symbol"]))
            elif isinstance(quote_field, dict) and quote_field.get("symbol") and quote_field.get("last") is not None:
                quotes.append(_to_quote(quote_field, quote_field["symbol"]))
        return quotes

    def get_raw_quotes(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """Devuelve el JSON crudo de Tradier para cada símbolo, SIN pasar
        por `_to_quote()` -- expone todos los campos que Tradier realmente
        entrega (incluidos `bid`/`ask`/`bid_date`/`ask_date`, que
        `_to_quote()` hoy ignora) para poder auditar cuál campo refleja de
        verdad el premarket, en vez de asumirlo (2026-08-18, caso real:
        `last`/`trade_date` llegan congelados en el cierre de la sesión
        anterior para símbolos líquidos durante premarket)."""
        raw: List[Dict[str, Any]] = []
        for i in range(0, len(symbols), TRADIER_CHUNK_SIZE):
            chunk = [s for s in symbols[i:i + TRADIER_CHUNK_SIZE] if s]
            if not chunk:
                continue
            data = self._get(QUOTES_PATH, {"symbols": ",".join(chunk), "greeks": "false"})
            quote_field = data.get("quotes", {}).get("quote")
            if isinstance(quote_field, list):
                raw.extend(item for item in quote_field if isinstance(item, dict))
            elif isinstance(quote_field, dict):
                raw.append(quote_field)
        return raw

    def get_history(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        """Barras diarias vía `/v1/markets/history`. Formato verificado en
        vivo (2026-08-14): `history.day` es una lista de
        {date, open, high, low, close, volume}."""
        days = _DAYS_BY_PERIOD.get(period, 180)
        end = datetime.now(timezone.utc).date()
        start = end - pd.Timedelta(days=days)
        tradier_interval = "daily" if interval in ("1d", "daily", "D") else interval

        data = self._get(
            HISTORY_PATH,
            {"symbol": symbol, "interval": tradier_interval, "start": start.isoformat(), "end": end.isoformat()},
        )
        # `data.get("history", {})` no alcanza: para símbolos sin historial
        # (confirmados en la investigación de CAPA 1 -- ej. AL, ANZU, APLS)
        # Tradier devuelve literalmente `{"history": null}` -- la clave
        # EXISTE con valor None, así que el default de `.get()` nunca se
        # usa y `.get("day")` explota con AttributeError. Encontrado en
        # vivo (2026-08-15) corriendo el batch histórico real.
        history = data.get("history") or {}
        rows = history.get("day")
        if not rows:
            raise QuoteNotFoundError(symbol)
        if isinstance(rows, dict):
            rows = [rows]

        df = pd.DataFrame(
            {
                "Open": [r["open"] for r in rows],
                "High": [r["high"] for r in rows],
                "Low": [r["low"] for r in rows],
                "Close": [r["close"] for r in rows],
                "Volume": [r["volume"] for r in rows],
            },
            index=pd.to_datetime([r["date"] for r in rows], utc=True),
        )
        return df

    def get_intraday_timesales(
        self, symbol: str, interval: str = "1min", session_filter: str = "all",
        start: Optional[str] = None, end: Optional[str] = None,
    ) -> pd.DataFrame:
        """Velas intradía con volumen por minuto y VWAP vía
        `/v1/markets/timesales` -- formato verificado en vivo (2026-08-14).
        NO se usa todavía en ningún flujo de escaneo (CAPA 3, pendiente);
        expuesto acá para que Capa 3 lo reutilice sin reimplementar la
        llamada ni volver a validar el formato."""
        params: Dict[str, Any] = {"symbol": symbol, "interval": interval, "session_filter": session_filter}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        data = self._get(TIMESALES_PATH, params)
        rows = data.get("series", {}).get("data")
        if not rows:
            raise QuoteNotFoundError(symbol)
        if isinstance(rows, dict):
            rows = [rows]

        df = pd.DataFrame(
            {
                "Open": [r["open"] for r in rows],
                "High": [r["high"] for r in rows],
                "Low": [r["low"] for r in rows],
                "Close": [r["close"] for r in rows],
                "Volume": [r["volume"] for r in rows],
                "VWAP": [r.get("vwap") for r in rows],
            },
            index=pd.to_datetime([r["timestamp"] for r in rows], unit="s", utc=True),
        )
        return df
