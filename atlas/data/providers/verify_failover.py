"""Verificación puntual de la capa MultiProvider, contra proveedores reales.

No es un test de `pytest` -- es un script de diagnóstico/evidencia,
pensado para correrse a mano (`python -m atlas.data.providers.verify_failover`)
cada vez que haga falta reconfirmar que el failover Yahoo -> Finnhub
funciona con datos reales, sin tener que reconstruir la prueba a mano.

Cuatro chequeos independientes, cada uno con su propia evidencia:
  1. Yahoo Finance responde de forma aislada (oportunista -- depende de
     que Yahoo no esté rate-limited en el momento de correr esto).
  2. Finnhub responde de forma aislada (oportunista -- depende de que la
     API key en FINNHUB_API_KEY sea válida).
  3. El failover real: un proveedor que siempre falla + Finnhub real,
     envueltos en MultiProvider -- determinístico, no depende del humor
     de Yahoo en el momento de correr esto, prueba específicamente que
     MultiProvider pasa al segundo proveedor y que ESE proveedor responde
     con datos reales.
  4. Un ciclo de escaneo real completo actualiza el ranking de Atlas Live
     usando esta misma cadena (scan_worker.run_scan_once()).
"""

from typing import Any, Dict

from atlas.data.providers.base import DataProvider, ProviderError
from atlas.data.providers.finnhub import FinnhubProvider
from atlas.data.providers.multi_provider import MultiProvider
from atlas.data.providers.yahoo_finance import YahooFinanceProvider


class _AlwaysFailsProvider(DataProvider):
    """Proveedor de prueba que siempre falla -- para forzar el failover de
    MultiProvider de forma determinística, sin depender de si Yahoo está
    caído en este momento exacto o no."""

    def get_quote(self, symbol: str):
        raise ProviderError("Fallo forzado (proveedor de prueba, no es un proveedor real)")

    def get_history(self, symbol: str, period: str = "6mo", interval: str = "1d"):
        raise ProviderError("Fallo forzado (proveedor de prueba, no es un proveedor real)")


def check_yahoo_isolated(symbol: str = "AAPL") -> Dict[str, Any]:
    try:
        q = YahooFinanceProvider().get_quote(symbol)
        return {"ok": True, "last_price": q.last_price, "name": q.name}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def check_finnhub_isolated(symbol: str = "AAPL") -> Dict[str, Any]:
    try:
        q = FinnhubProvider().get_quote(symbol)
        return {"ok": True, "last_price": q.last_price, "name": q.name}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def check_forced_failover(symbol: str = "AAPL") -> Dict[str, Any]:
    """Prueba determinística: el primer proveedor SIEMPRE falla, así que
    cualquier éxito acá viene forzosamente del segundo (Finnhub real)."""
    provider = MultiProvider([_AlwaysFailsProvider(), FinnhubProvider()])
    try:
        q = provider.get_quote(symbol)
        return {"ok": True, "last_price": q.last_price, "vino_del_segundo_proveedor": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def check_real_scan_updates_ranking() -> Dict[str, Any]:
    """Corre un ciclo de escaneo real de Atlas Live y confirma que el
    ranking se actualiza (generated_at cambia respecto al anterior)."""
    from atlas_live import scan_worker as sw

    before = sw.STATE.snapshot().get("generated_at")
    sw.run_scan_once()
    after_snap = sw.STATE.snapshot()
    after = after_snap.get("generated_at")
    return {
        "generated_at_antes": before,
        "generated_at_despues": after,
        "cambio": before != after,
        "symbols_ok": after_snap.get("symbols_ok"),
        "last_error": after_snap.get("last_error"),
    }


def run_all() -> Dict[str, Any]:
    return {
        "1_yahoo_aislado": check_yahoo_isolated(),
        "2_finnhub_aislado": check_finnhub_isolated(),
        "3_failover_forzado_a_finnhub": check_forced_failover(),
        "4_escaneo_real_actualiza_ranking": check_real_scan_updates_ranking(),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run_all(), indent=2, ensure_ascii=False, default=str))
