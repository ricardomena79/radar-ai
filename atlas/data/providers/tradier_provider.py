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


def _epoch_ms_to_dt(epoch_ms: Optional[float]) -> Optional[datetime]:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc) if epoch_ms else None


def _age_seconds(ts: Optional[datetime], now: datetime) -> Optional[float]:
    return (now - ts).total_seconds() if ts is not None else None


def _resolve_current_price(data: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """Decide qué señal representa el "precio actual" real: el último trade
    (`last`/`trade_date`) si está fresco, o el punto medio bid/ask si
    `last` está vencido pero bid/ask son frescos y confiables. Si ninguno
    de los dos es confiable, se conserva `last`/`trade_date` TAL CUAL
    (aunque estén vencidos) -- nunca se inventa un precio nuevo; la cadena
    de confiabilidad YA EXISTENTE (`scan_worker.compute_price_age_seconds`/
    `is_price_stale`/`estado_validacion=VENCIDO`) es la que decide mostrar
    "NO_TOCAR"/"NO RECOMENDAR" a partir de un `timestamp` vencido, exactamente
    como ya hace hoy -- este caso C no necesita ningún estado nuevo."""
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
        "bid_timestamp": bid_ts, "ask_timestamp": ask_ts,
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
                "price_basis": "tradier_bid_ask_mid",
            })
            return resolved  # Caso B: `last` vencido, bid/ask frescos y confiables.

    return resolved  # Caso C: ninguno confiable -- se devuelve `last`/`trade_date` sin tocar.


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
        source=SOURCE_NAME,
        price_basis=resolved["price_basis"],
        bid=resolved["bid"],
        ask=resolved["ask"],
        bid_timestamp=resolved["bid_timestamp"],
        ask_timestamp=resolved["ask_timestamp"],
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
