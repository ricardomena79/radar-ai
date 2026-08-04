"""MultiProvider: failover automático entre varios DataProvider, transparente para el resto de Atlas.

Implementa la misma interfaz DataProvider que cualquier proveedor
individual (YahooFinanceProvider, y los que se agreguen después). Por
eso DataCollector -- y todo lo que hay por encima, GlobalRadar, Atlas
Score, Decision Engine -- nunca sabe si detrás hay un solo proveedor o
varios: recibe un DataProvider y punto.

Comportamiento (Fase 1 -- solo failover):
  - Prueba los proveedores en el orden en que se configuraron.
  - Si uno falla (error de red, timeout, rate limit) o responde con datos
    incompletos, sigue automáticamente con el siguiente.
  - Nunca deja un escaneo a medias por la falla de un solo proveedor: solo
    levanta un error si TODOS los proveedores configurados fallaron para
    ese símbolo.

Preparado para evolucionar (todavía NO implementado, a propósito): en vez
de aplicar la misma lista de proveedores a cualquier consulta, `_providers_for()`
es el único lugar que decide qué proveedores probar para un símbolo/tipo de
dato dado. Hoy siempre devuelve la lista completa en orden de prioridad
(puro failover). El día que se quiera enrutar por especialidad --
ej. Proveedor A para acciones, B para ETFs, C para opciones, D para
noticias, E para fundamentales -- ese cambio se hace ahí adentro, sin
tocar get_quote/get_quotes/get_history ni nada de Atlas Core.
"""

from typing import Dict, List, Optional

import pandas as pd

from atlas.data.models.quote import Quote
from atlas.data.providers.base import DataProvider, ProviderError


def _is_quote_complete(quote: Quote) -> bool:
    """Los dos campos que todo el resto de Atlas necesita sí o sí."""
    return quote.last_price is not None and quote.change_percent is not None


class MultiProvider(DataProvider):
    """Envuelve una lista ordenada de proveedores y hace failover automático entre ellos."""

    def __init__(self, providers: List[DataProvider]) -> None:
        if not providers:
            raise ValueError("MultiProvider necesita al menos un proveedor")
        self._providers = list(providers)

    @property
    def provider_names(self) -> List[str]:
        """Nombres de los proveedores configurados, en orden de prioridad (para diagnóstico/logs)."""
        return [type(p).__name__ for p in self._providers]

    def _providers_for(self, symbol: str, kind: str) -> List[DataProvider]:
        """Punto único de enrutamiento. Hoy: todos los proveedores, en orden, para cualquier
        símbolo/tipo de dato -- failover puro. Mañana: podría devolver una lista distinta
        según `symbol`/`kind` (ej. acciones vs. ETFs vs. opciones) sin cambiar nada más."""
        return self._providers

    def get_quote(self, symbol: str) -> Quote:
        errors: List[str] = []
        for provider in self._providers_for(symbol, "quote"):
            try:
                quote = provider.get_quote(symbol)
            except ProviderError as exc:
                errors.append(f"{type(provider).__name__}: {exc}")
                continue
            if not _is_quote_complete(quote):
                errors.append(f"{type(provider).__name__}: cotización incompleta para '{symbol}'")
                continue
            return quote

        raise ProviderError(
            f"Todos los proveedores fallaron para '{symbol}': " + "; ".join(errors)
        )

    def get_quotes(self, symbols: List[str]) -> List[Quote]:
        remaining = list(dict.fromkeys(symbols))  # únicos, preserva orden
        quotes_by_symbol: Dict[str, Quote] = {}

        for provider in self._providers_for("*", "quotes"):
            if not remaining:
                break
            try:
                batch = provider.get_quotes(remaining)
            except ProviderError:
                # Este proveedor falló para todo el lote pendiente; el
                # siguiente proveedor intenta con los mismos símbolos.
                continue

            for quote in batch:
                if _is_quote_complete(quote):
                    quotes_by_symbol[quote.symbol] = quote

            remaining = [s for s in remaining if s not in quotes_by_symbol]

        # El orden de salida sigue el de la lista pedida, no el de llegada.
        return [quotes_by_symbol[s] for s in symbols if s in quotes_by_symbol]

    def get_history(self, symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        errors: List[str] = []
        for provider in self._providers_for(symbol, "history"):
            try:
                history = provider.get_history(symbol, period=period, interval=interval)
            except ProviderError as exc:
                errors.append(f"{type(provider).__name__}: {exc}")
                continue
            if history is None or history.empty:
                errors.append(f"{type(provider).__name__}: historial vacío para '{symbol}'")
                continue
            return history

        raise ProviderError(
            f"Todos los proveedores fallaron para el historial de '{symbol}': " + "; ".join(errors)
        )
