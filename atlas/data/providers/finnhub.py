"""Proveedor de datos de mercado basado en Finnhub (https://finnhub.io), nivel gratuito.

╔══════════════════════════════════════════════════════════════════════╗
║ VALIDADO CONTRA DATOS REALES (2026-08-05), REGISTRADO EN              ║
║ _PROVIDERS. get_quote/get_quotes confirmados con AAPL/MSFT/TSLA/SPY   ║
║ reales. get_history falla con 403 "You don't have access to this     ║
║ resource" -- el nivel gratuito de Finnhub no incluye /stock/candle    ║
║ para acciones de EE.UU. (ya sospechado, ahora confirmado). Falla      ║
║ como ProviderError, así que MultiProvider hace failover a Yahoo       ║
║ para históricos automáticamente.                                      ║
║                                                                        ║
║ Nota sobre la key: las keys de Finnhub NO tienen una longitud fija    ║
║ -- se vieron keys reales de 20 y de 40 caracteres, ambas válidas.     ║
║ No asumir un largo fijo al diagnosticar problemas de autenticación.   ║
║ Failover Yahoo->Finnhub demostrado con evidencia real (2026-08-05):   ║
║ Yahoo forzado a fallar en memoria (sin tocar código), MultiProvider   ║
║ pasó automáticamente a Finnhub, un ciclo de escaneo real completo     ║
║ actualizó el ranking (57 símbolos vía Finnhub, de 300 escaneados),    ║
║ y Yahoo volvió a ser el proveedor preferido al reactivarlo.           ║
╚══════════════════════════════════════════════════════════════════════╝

Elegido como segundo proveedor (por delante de Twelve Data y Alpha
Vantage) por: límite gratuito más generoso (60 llamadas/minuto, sin tope
diario documentado, contra 8/min-800/día de Twelve Data y 5/min-25/día de
Alpha Vantage), cobertura amplia de acciones y ETFs de EE.UU., y una API
REST simple sin SDK propio.

Limitación conocida y aceptada de entrada (nivel gratuito):
- El endpoint `/quote` no incluye volumen ni nombre del instrumento --
  `Quote.volume`, `Quote.average_volume`, `Quote.relative_volume` y
  `Quote.name` quedan en `None`. Ningún motor de Atlas Core exige estos
  campos (son `Optional`), pero degradan la calidad del dato frente a
  Yahoo Finance cuando Finnhub actúa como respaldo.
- PENDIENTE DE VALIDAR: si `/stock/candle` (histórico) está disponible en
  el nivel gratuito para acciones de EE.UU. -- hay reportes públicos de
  desarrolladores de que Finnhub restringió ese endpoint a planes pagos
  para acciones (sigue gratis para forex/crypto), pero no hay forma de
  confirmarlo sin una key real. Si el nivel gratuito lo bloquea,
  `get_history()` debe fallar con un `ProviderError` claro para que
  `MultiProvider` haga failover a Yahoo Finance, no quedar la duda.

Responsabilidad única: obtener datos crudos de Finnhub y normalizarlos a
Quote. No calcula indicadores, no aplica scoring ni filtra símbolos.
"""

import os
from datetime import datetime, time as dt_time, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from atlas.data.models.quote import (
    SESSION_AFTERHOURS,
    SESSION_CLOSED,
    SESSION_PREMARKET,
    SESSION_REGULAR,
    Quote,
)
from atlas.data.providers.base import (
    DataProvider,
    ProviderError,
    QuoteNotFoundError,
    RateLimitError,
)
from atlas.data.providers.base import call_with_timeout as _call_with_timeout

BASE_URL = "https://finnhub.io/api/v1"
NETWORK_TIMEOUT_SECONDS = 15.0

# Finnhub no manda un campo de sesión listo para usar en /quote --
# PENDIENTE DE VALIDAR si existe uno mejor una vez que se pueda inspeccionar
# una respuesta real. Misma aproximación por horario que AlpacaProvider,
# para no duplicar lógica de negocio nueva sin datos reales que la respalden.
_EASTERN = ZoneInfo("America/New_York")
_PREMARKET_START = dt_time(4, 0)
_REGULAR_START = dt_time(9, 30)
_REGULAR_END = dt_time(16, 0)
_AFTERHOURS_END = dt_time(20, 0)


def _resolve_session(now_utc: Optional[datetime] = None) -> str:
    now = (now_utc or datetime.now(timezone.utc)).astimezone(_EASTERN)
    if now.weekday() >= 5:  # sábado/domingo
        return SESSION_CLOSED
    current = now.time()
    if _PREMARKET_START <= current < _REGULAR_START:
        return SESSION_PREMARKET
    if _REGULAR_START <= current < _REGULAR_END:
        return SESSION_REGULAR
    if _REGULAR_END <= current < _AFTERHOURS_END:
        return SESSION_AFTERHOURS
    return SESSION_CLOSED


# Resoluciones que acepta /stock/candle. PENDIENTE DE VALIDAR contra una
# respuesta real -- tomado directamente de la documentación pública.
_INTERVAL_TO_RESOLUTION = {
    "1d": "D",
    "1wk": "W",
    "1mo": "M",
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "60m": "60",
    "1h": "60",
}

# Ventanas aproximadas para traducir `period` (vocabulario de yfinance) a
# un rango from/to en segundos, que es lo que pide /stock/candle.
_PERIOD_TO_SECONDS = {
    "1d": 1 * 86400,
    "5d": 5 * 86400,
    "1mo": 31 * 86400,
    "3mo": 93 * 86400,
    "6mo": 186 * 86400,
    "1y": 366 * 86400,
    "2y": 731 * 86400,
    "5y": 1826 * 86400,
}


class FinnhubProvider(DataProvider):
    """Obtiene cotizaciones consultando la API REST de Finnhub (nivel gratuito)."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key or os.environ.get("FINNHUB_API_KEY")
        if not self._api_key:
            raise ProviderError(
                "FinnhubProvider requiere FINNHUB_API_KEY (variable de entorno, "
                "o argumento al construirlo)."
            )
        self._session = requests.Session()

    def _get(self, path: str, params: Dict[str, Any]) -> Any:
        full_params = dict(params)
        full_params["token"] = self._api_key

        def _do_request() -> Any:
            response = self._session.get(
                f"{BASE_URL}{path}", params=full_params, timeout=NETWORK_TIMEOUT_SECONDS
            )
            if response.status_code == 429:
                raise RateLimitError(f"Finnhub limitó la tasa de consultas: {response.text}")
            if response.status_code in (401, 403):
                raise ProviderError(
                    f"Finnhub rechazó las credenciales o el acceso al recurso "
                    f"({response.status_code}): {response.text}"
                )
            if response.status_code >= 400:
                raise ProviderError(f"Finnhub devolvió {response.status_code}: {response.text}")
            return response.json()

        return _call_with_timeout(
            _do_request, "*", "consulta", timeout_seconds=NETWORK_TIMEOUT_SECONDS, provider_name="Finnhub"
        )

    def get_quote(self, symbol: str) -> Quote:
        """Consulta /quote y normaliza la respuesta a un Quote.

        Forma esperada de la respuesta (documentación pública, PENDIENTE
        DE VALIDAR contra una key real):
          {"c": precio actual, "d": cambio absoluto, "dp": cambio %,
           "h": máximo del día, "l": mínimo del día, "o": apertura del día,
           "pc": cierre anterior, "t": timestamp unix}
        Un símbolo inválido responde con todos los campos en 0 (no con un
        404) -- PENDIENTE DE VALIDAR; por eso se trata `c == 0` como
        "no encontrado" en vez de confiar únicamente en el código HTTP.
        """
        data = self._get("/quote", {"symbol": symbol})

        last_price = data.get("c")
        previous_close = data.get("pc")

        if not last_price or previous_close is None:
            raise QuoteNotFoundError(symbol)

        change_percent = data.get("dp")
        if change_percent is None and previous_close:
            change_percent = ((last_price - previous_close) / previous_close) * 100

        return Quote(
            symbol=symbol,
            # PENDIENTE DE VALIDAR / limitación conocida: /quote no incluye
            # el nombre del instrumento. Requeriría una segunda llamada a
            # /stock/profile2, que no se hace por defecto para no duplicar
            # el consumo del límite de tasa gratuito en cada cotización.
            name=None,
            last_price=last_price,
            change_percent=change_percent,
            volume=None,  # PENDIENTE DE VALIDAR: no disponible en /quote.
            open=data.get("o"),
            high=data.get("h"),
            low=data.get("l"),
            previous_close=previous_close,
            market_cap=None,
            sector=None,
            industry=None,
            float_shares=None,
            average_volume=None,
            relative_volume=None,
            timestamp=self._parse_timestamp(data.get("t")),
            session=_resolve_session(),
        )

    def get_quotes(self, symbols: List[str]) -> List[Quote]:
        """Finnhub no ofrece un endpoint de lote en el nivel gratuito -- una
        llamada HTTP por símbolo, igual que Yahoo Finance. A diferencia de
        Alpaca (pensado para lotes grandes), esto hace a Finnhub más
        apropiado como respaldo puntual (símbolo por símbolo, ej. cuando
        Yahoo falla para un ticker) que como reemplazo del pase liviano de
        GlobalRadar sobre el universo completo."""
        quotes: List[Quote] = []
        for symbol in symbols:
            try:
                quotes.append(self.get_quote(symbol))
            except RateLimitError:
                # Corta todo el lote: seguir símbolo por símbolo bajo rate
                # limit activo solo agotaría el resto del presupuesto sin
                # devolver nada útil. El siguiente proveedor en MultiProvider
                # se hace cargo de los símbolos restantes.
                raise
            except (ProviderError, QuoteNotFoundError):
                continue
        return quotes

    def get_history(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        """Descarga barras OHLCV de /stock/candle.

        PENDIENTE DE VALIDAR EN SU TOTALIDAD: si este endpoint responde
        datos reales para acciones de EE.UU. en el nivel gratuito, o si
        devuelve 403 / `{"s": "no_data"}` (ver aviso al inicio del
        archivo). Si falla, se levanta ProviderError para que MultiProvider
        haga failover a Yahoo Finance en vez de propagar un error confuso.
        """
        resolution = _INTERVAL_TO_RESOLUTION.get(interval, "D")
        window_seconds = _PERIOD_TO_SECONDS.get(period, _PERIOD_TO_SECONDS["6mo"])
        now = datetime.now(timezone.utc)
        params = {
            "symbol": symbol,
            "resolution": resolution,
            "from": int(now.timestamp()) - window_seconds,
            "to": int(now.timestamp()),
        }

        data = self._get("/stock/candle", params)

        if not isinstance(data, dict) or data.get("s") != "ok":
            raise ProviderError(
                f"Finnhub no devolvió historial utilizable para '{symbol}' "
                f"(posible restricción del nivel gratuito para acciones): {data}"
            )

        closes = data.get("c") or []
        if not closes:
            raise QuoteNotFoundError(symbol)

        frame = pd.DataFrame(
            {
                "Open": data.get("o") or [],
                "High": data.get("h") or [],
                "Low": data.get("l") or [],
                "Close": closes,
                "Volume": data.get("v") or [],
            },
            index=pd.to_datetime(data.get("t") or [], unit="s", utc=True),
        )
        return frame

    @staticmethod
    def _parse_timestamp(epoch: Optional[int]) -> datetime:
        if epoch:
            return datetime.fromtimestamp(epoch, tz=timezone.utc)
        return datetime.now(timezone.utc)
