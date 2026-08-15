"""CAPA 1 -- cobertura amplia del universo completo (Fase Tradier, 2026-08-14).

Arquitectura aprobada (ver informe de sesión 2026-08-14):

    UNIVERSO COMPLETO -> NORMALIZACIÓN -> TRADIER -> (si no resuelve) -> FALLBACK

Tradier es el proveedor PRINCIPAL de quotes. Antes de consultarlo, cada
símbolo pasa por `tradier_symbol_map.normalize()` (la única capa autorizada
a reescribir un símbolo -- nunca se reformatea nada acá ni en el scanner).
Lo que Tradier no resuelve cae al proveedor de respaldo YA EXISTENTE
(`atlas_live.data_fusion.registry.get_default_provider()`, Yahoo+Finnhub)
sin reinventar su mecanismo de failover.

Ningún símbolo se excluye por su estado (ACTIVE/SPECIAL/OBSOLETE/
UNSUPPORTED/UNRESOLVED) -- todos se intentan igual; el estado es
diagnóstico, no un filtro. No se elimina nada del universo acá.

Esta es SOLO la capa de quotes (precio, cambio, volumen, average_volume).
No calcula rankings, no detecta candidatos, no hace análisis intradía --
eso es Capa 2/Capa 3, todavía no integradas.
"""

import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from dotenv import load_dotenv

from atlas.data.models.quote import Quote
from atlas.data.providers.base import DataProvider, ProviderError
from atlas.data.providers.tradier_provider import TRADIER_CHUNK_SIZE, TradierProvider
from atlas.data.providers.tradier_symbol_map import normalize
from atlas_live.data_fusion.registry import get_default_provider

_load_dotenv_done = False


def _ensure_env_loaded() -> None:
    global _load_dotenv_done
    if not _load_dotenv_done:
        load_dotenv()
        _load_dotenv_done = True


def build_tradier_provider() -> Optional[TradierProvider]:
    """Construye el TradierProvider desde el entorno (`TRADIER_API_TOKEN`,
    `TRADIER_BASE_URL`). Nunca imprime ni loguea el token. Devuelve None
    (degradación segura, mismo criterio que `registry.get_default_provider()`
    con Finnhub) si no hay token configurado -- el orquestador cae
    directamente al proveedor de respaldo para el universo completo."""
    _ensure_env_loaded()
    token = os.environ.get("TRADIER_API_TOKEN")
    if not token:
        return None
    base_url = os.environ.get("TRADIER_BASE_URL", "https://api.tradier.com")
    return TradierProvider(token, base_url=base_url)


@dataclass
class UniverseQuotesDiagnostics:
    """Contadores del ciclo -- sin datos sensibles (ni token, ni URLs con
    credenciales, ni cuerpos crudos de respuesta)."""

    total_solicitados: int = 0
    resueltos_por_tradier: int = 0
    resueltos_por_normalizacion: int = 0  # subconjunto de los de arriba que necesitó normalize()
    enviados_a_fallback: int = 0
    resueltos_por_fallback: int = 0
    sin_datos_final: int = 0
    tradier_chunks: int = 0
    tradier_disponible: bool = False
    tradier_error: Optional[str] = None
    fallback_error: Optional[str] = None
    tiempo_tradier_segundos: float = 0.0
    tiempo_fallback_segundos: float = 0.0
    tiempo_total_segundos: float = 0.0
    estados: Dict[str, int] = field(default_factory=dict)  # conteo de símbolos por estado

    def to_dict(self) -> Dict[str, object]:
        return {
            "total_solicitados": self.total_solicitados,
            "resueltos_por_tradier": self.resueltos_por_tradier,
            "resueltos_por_normalizacion": self.resueltos_por_normalizacion,
            "enviados_a_fallback": self.enviados_a_fallback,
            "resueltos_por_fallback": self.resueltos_por_fallback,
            "sin_datos_final": self.sin_datos_final,
            "tradier_chunks": self.tradier_chunks,
            "tradier_disponible": self.tradier_disponible,
            "tradier_error": self.tradier_error,
            "fallback_error": self.fallback_error,
            "tiempo_tradier_segundos": self.tiempo_tradier_segundos,
            "tiempo_fallback_segundos": self.tiempo_fallback_segundos,
            "tiempo_total_segundos": self.tiempo_total_segundos,
            "estados": dict(self.estados),
        }


@dataclass
class SymbolTrace:
    """Diagnóstico completo de UN símbolo -- se construye para los 2.575,
    no solo para los que fallan, para que nunca haya una pérdida silenciosa
    (requisito explícito de la arquitectura aprobada, 2026-08-14)."""

    original: str
    query_symbol: str
    state: str                     # ACTIVE | SPECIAL | OBSOLETE | UNSUPPORTED | UNRESOLVED
    tradier_intentado: bool = False
    tradier_resuelto: bool = False
    fallback_intentado: bool = False
    fallback_resuelto: bool = False
    resultado_final: str = "sin_datos"   # "tradier" | "fallback" | "sin_datos"
    motivo_fallo: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "original": self.original,
            "query_symbol": self.query_symbol,
            "state": self.state,
            "tradier_intentado": self.tradier_intentado,
            "tradier_resuelto": self.tradier_resuelto,
            "fallback_intentado": self.fallback_intentado,
            "fallback_resuelto": self.fallback_resuelto,
            "resultado_final": self.resultado_final,
            "motivo_fallo": self.motivo_fallo,
        }


@dataclass
class UniverseQuotesResult:
    quotes: Dict[str, Quote]       # símbolo ORIGINAL del universo Racional -> Quote
    states: Dict[str, str]         # símbolo original -> estado (ACTIVE/SPECIAL/OBSOLETE/UNSUPPORTED/UNRESOLVED)
    diagnostics: UniverseQuotesDiagnostics
    traces: Dict[str, SymbolTrace] = field(default_factory=dict)  # símbolo original -> trace completo

    def unresolved_traces(self) -> List[SymbolTrace]:
        """Atajo: solo los símbolos que terminaron sin datos, con su motivo."""
        return [t for t in self.traces.values() if t.resultado_final == "sin_datos"]


_UNSET = object()  # centinela: distingue "no se pasó nada" (usar default real) de
                    # `fallback_provider=None` explícito (desactivar el fallback a propósito,
                    # ej. para medir SOLO la cobertura de Tradier en un diagnóstico).


def fetch_universe_quotes(
    symbols: List[str],
    tradier_provider: Optional[TradierProvider] = None,
    fallback_provider: object = _UNSET,
) -> UniverseQuotesResult:
    """CAPA 1: quotes de TODO el universo pasado en `symbols` (pensado para
    los ~2.575 símbolos del universo Racional, pero funciona con cualquier
    lista). `tradier_provider`/`fallback_provider` son inyectables para
    pruebas; por defecto se construyen desde el entorno real. Pasar
    `fallback_provider=None` explícitamente lo desactiva (útil para medir
    solo la cobertura de Tradier); omitirlo usa el proveedor de respaldo
    real (Yahoo/Finnhub)."""
    t0 = time.time()

    if tradier_provider is None:
        tradier_provider = build_tradier_provider()
    if fallback_provider is _UNSET:
        fallback_provider = get_default_provider()

    diag = UniverseQuotesDiagnostics(total_solicitados=len(symbols), tradier_disponible=tradier_provider is not None)

    # --- Normalización: símbolo original -> símbolo a consultar en Tradier ---
    # `query_to_originals` es uno-a-MUCHOS a propósito: más de un símbolo del
    # universo puede normalizar al mismo query_symbol (caso real confirmado:
    # "PBR.A" y "PBRA" -- entrada duplicada en el snapshot de Racional para
    # el mismo instrumento -- ambos normalizan a "PBR/A"). Un mapeo uno-a-uno
    # perdería silenciosamente el resultado de todos menos el último.
    normalized: Dict[str, str] = {}   # original -> query_symbol
    query_to_originals: Dict[str, List[str]] = {}  # query_symbol -> [originales]
    states: Dict[str, str] = {}
    needed_normalization: set = set()
    traces: Dict[str, SymbolTrace] = {}

    for sym in symbols:
        n = normalize(sym)
        states[sym] = n.state
        diag.estados[n.state] = diag.estados.get(n.state, 0) + 1
        normalized[sym] = n.query_symbol
        query_to_originals.setdefault(n.query_symbol, []).append(sym)
        if n.query_symbol != sym:
            needed_normalization.add(sym)
        traces[sym] = SymbolTrace(original=sym, query_symbol=n.query_symbol, state=n.state)

    quotes_by_original: Dict[str, Quote] = {}

    # --- Tradier (proveedor principal) ---
    if tradier_provider is not None:
        t_tradier = time.time()
        query_symbols = list(query_to_originals.keys())
        diag.tradier_chunks = max(1, (len(query_symbols) + TRADIER_CHUNK_SIZE - 1) // TRADIER_CHUNK_SIZE)
        for sym in symbols:
            traces[sym].tradier_intentado = True
        try:
            tradier_quotes = tradier_provider.get_quotes(query_symbols)
            resolved_query_symbols = {q.symbol for q in tradier_quotes}
            for q in tradier_quotes:
                for original in query_to_originals.get(q.symbol, []):
                    quotes_by_original[original] = q
                    traces[original].tradier_resuelto = True
                    traces[original].resultado_final = "tradier"
                    if original in needed_normalization:
                        diag.resueltos_por_normalizacion += 1
            for sym in symbols:
                if sym not in quotes_by_original:
                    traces[sym].motivo_fallo = (
                        f"Tradier no devolvió cotización para '{traces[sym].query_symbol}' "
                        f"(símbolo ausente de la respuesta -- no matcheado, no error)."
                    )
        except ProviderError as exc:
            diag.tradier_error = str(exc)
            for sym in symbols:
                traces[sym].motivo_fallo = f"Tradier: fallo del proveedor completo -- {exc}"
        diag.tiempo_tradier_segundos = round(time.time() - t_tradier, 3)
    else:
        for sym in symbols:
            traces[sym].motivo_fallo = "Tradier no disponible (sin TRADIER_API_TOKEN configurado)."

    diag.resueltos_por_tradier = len(quotes_by_original)

    # --- Fallback existente (Yahoo/Finnhub) solo para lo que Tradier no resolvió ---
    unresolved_originals = [s for s in symbols if s not in quotes_by_original]
    diag.enviados_a_fallback = len(unresolved_originals)

    if unresolved_originals and fallback_provider is not None:
        t_fb = time.time()
        for sym in unresolved_originals:
            traces[sym].fallback_intentado = True
        try:
            fb_quotes = fallback_provider.get_quotes(unresolved_originals)
            for q in fb_quotes:
                quotes_by_original[q.symbol] = q
                traces[q.symbol].fallback_resuelto = True
                traces[q.symbol].resultado_final = "fallback"
                traces[q.symbol].motivo_fallo = None
            diag.resueltos_por_fallback = len(fb_quotes)
            for sym in unresolved_originals:
                if sym not in quotes_by_original:
                    traces[sym].motivo_fallo = (
                        (traces[sym].motivo_fallo or "") + " | Fallback (Yahoo/Finnhub): tampoco encontrado."
                    ).strip(" |")
        except ProviderError as exc:
            diag.fallback_error = str(exc)
            for sym in unresolved_originals:
                traces[sym].motivo_fallo = (
                    (traces[sym].motivo_fallo or "") + f" | Fallback: fallo del proveedor completo -- {exc}"
                ).strip(" |")
        diag.tiempo_fallback_segundos = round(time.time() - t_fb, 3)
    elif unresolved_originals and fallback_provider is None:
        for sym in unresolved_originals:
            traces[sym].motivo_fallo = (
                (traces[sym].motivo_fallo or "") + " | Fallback desactivado explícitamente en esta corrida."
            ).strip(" |")

    diag.sin_datos_final = len(symbols) - len(quotes_by_original)
    diag.tiempo_total_segundos = round(time.time() - t0, 3)

    return UniverseQuotesResult(quotes=quotes_by_original, states=states, diagnostics=diag, traces=traces)
