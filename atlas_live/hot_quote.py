"""Canal de actualización rápida (Plan A + Plan B) -- lógica pura.

Optimización de latencia (2026-08-07, ver DECISION_LOG.md). Refresca SOLO
los 2 símbolos visibles (Oportunidad del Día = Hero, y Plan B) contra el
proveedor, para mantener esos precios con antigüedad <=3s cuando el
proveedor lo permite. NO corre el scanner del universo (~244), ni Radar, ni
Memory, ni el Motor Predictivo -- solo la cotización cruda con su timestamp.

Separado de `server.py` a propósito, con el mismo criterio ya declarado
para esa capa ("cero lógica de negocio en el servidor"): así esta lógica se
puede testear de forma aislada, sin arrancar el refresco en segundo plano
ni importar todo el servidor.

Presupuesto de API: exactamente 2 símbolos cada 3s ~= 40 req/min, dentro
del límite de Finnhub (60/min) e independiente del escaneo del universo --
no aumenta el consumo sobre ese escaneo.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.providers.base import ProviderError, QuoteNotFoundError

# El canal es EXCLUSIVO para Plan A + Plan B: como mucho 2 símbolos. Cualquier
# exceso se ignora, para que nadie pueda usar este endpoint como un escáner
# masivo que sí golpearía los límites del proveedor.
MAX_HOT_SYMBOLS = 2


def parse_symbols(raw: Optional[str]) -> List[str]:
    """Normaliza el parámetro `symbols` (coma-separado) a una lista de a lo
    sumo `MAX_HOT_SYMBOLS` tickers en mayúsculas, sin vacíos ni duplicados."""
    seen: List[str] = []
    for part in (raw or "").split(","):
        sym = part.strip().upper()
        if sym and sym not in seen:
            seen.append(sym)
        if len(seen) >= MAX_HOT_SYMBOLS:
            break
    return seen


def collect_hot_quotes(
    symbols: List[str],
    collector: DataCollector,
    now: Optional[datetime] = None,
) -> Dict:
    """Devuelve `{server_time, quotes}` con la cotización cruda de cada
    símbolo. Un fallo por símbolo (proveedor caído, rate-limit, o símbolo
    inexistente) NO tumba el canal ni los otros símbolos: ese símbolo sale
    con `status="unavailable"` y el frontend conserva el "último recibido",
    mostrando su antigüedad creciente. Se capturan explícitamente solo las
    excepciones que el MultiProvider puede lanzar (`ProviderError` -- incluye
    `RateLimitError` -- y `QuoteNotFoundError`), nunca un `except Exception`
    genérico."""
    server_time = (now or datetime.now(timezone.utc)).isoformat()
    quotes: List[Dict] = []
    for symbol in symbols:
        try:
            q = collector.get_quote(symbol)
            quotes.append({
                "symbol": q.symbol,
                "status": "ok",
                "price": q.last_price,
                "change_pct": q.change_percent,
                "price_type": q.price_type,
                "market_state": q.market_state,
                "source": q.source,
                "price_as_of": q.timestamp.isoformat() if q.timestamp else None,
            })
        except (ProviderError, QuoteNotFoundError) as exc:
            quotes.append({
                "symbol": symbol,
                "status": "unavailable",
                "reason": type(exc).__name__,
            })
    return {"server_time": server_time, "quotes": quotes}
