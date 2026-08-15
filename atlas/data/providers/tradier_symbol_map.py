"""Capa de normalización de símbolos para Tradier (Fase Tradier, 2026-08-14).

Traduce el símbolo tal como aparece en el universo Racional al símbolo que
Tradier realmente reconoce, y adjunta el estado/motivo de cada equivalencia
de forma explícita y trazable. Ninguna conversión de símbolo debe vivir
dentro del scanner ni de ningún orquestador -- esta es la ÚNICA capa que
decide qué símbolo se le pasa a Tradier.

Origen de los datos: investigación real contra la Production API de Tradier
(sesión 2026-08-14) sobre los 2.575 símbolos del universo Racional. De 125
símbolos no reconocidos en la prueba cruda, se clasificaron:
  - 9 por formato corregible (notación de clase de acción con punto -> el
    slash que Tradier realmente usa; sufijo ".OLD" interno de Racional).
  - 2 por renombre confirmado (ticker actual sí resuelve en Tradier).
  - 6 por fusión/adquisición/delisting real (4 verificadas con fuente web).
  - 21 ETFs de nicho o instrumentos especiales (Rights, CVR, Warrants,
    unidades de SPAC).
  - 2 exclusivos del wrapper propio de Racional (no son tickers reales).
  - 30 confirmados como no cubiertos por Tradier, probados aislados.
  - 55 no investigados individualmente (categoría "no determinado").

Ver `tradier_symbol_overrides.json` (los primeros 70) y
`tradier_no_determinado.json` (los 55 restantes) para el detalle símbolo
por símbolo con su motivo y evidencia.
"""

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

_OVERRIDES_FILE = Path(__file__).parent / "tradier_symbol_overrides.json"
_UNDETERMINED_FILE = Path(__file__).parent / "tradier_no_determinado.json"

# Confirmado en vivo (2026-08-14): BRK.B/BF.B/AKO.B/PBR.A no resuelven,
# BRK/B/BF/B/AKO/B/PBR/A sí -- Tradier usa slash para clase de acción, no
# punto. Se aplica como regla general (no solo a los símbolos ya probados)
# porque es un patrón de nomenclatura, no una excepción puntual -- pero
# CUALQUIER símbolo que caiga acá queda trazado con rule="regla_clase_accion"
# en el resultado, nunca aplicado en silencio.
_SHARE_CLASS_RE = re.compile(r"^([A-Z]{1,5})\.([A-Z])$")

VALID_STATES = {"ACTIVE", "SPECIAL", "OBSOLETE", "UNSUPPORTED", "UNRESOLVED"}


@dataclass(frozen=True)
class NormalizedSymbol:
    """Resultado de normalizar un símbolo del universo Racional."""

    original: str            # símbolo tal como aparece en el universo Racional
    query_symbol: str        # símbolo que efectivamente se le pasa a Tradier
    state: str                # ACTIVE | SPECIAL | OBSOLETE | UNSUPPORTED | UNRESOLVED
    reason: Optional[str]     # explicación trazable, None si no hay override conocido
    rule: str                  # "override_explicito" | "regla_clase_accion" | "sin_normalizacion"


_lock = threading.Lock()
_overrides_cache: Optional[Dict[str, dict]] = None
_undetermined_cache: Optional[Dict[str, dict]] = None


def _load_json(path: Path) -> Dict[str, dict]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _overrides() -> Dict[str, dict]:
    global _overrides_cache
    with _lock:
        if _overrides_cache is None:
            _overrides_cache = _load_json(_OVERRIDES_FILE)
        return _overrides_cache


def _undetermined() -> Dict[str, dict]:
    global _undetermined_cache
    with _lock:
        if _undetermined_cache is None:
            _undetermined_cache = _load_json(_UNDETERMINED_FILE)
        return _undetermined_cache


def normalize(symbol: str) -> NormalizedSymbol:
    """Traduce `symbol` al símbolo que se debe consultar en Tradier.

    No excluye ningún símbolo: incluso los marcados OBSOLETE/SPECIAL/
    UNSUPPORTED devuelven un `query_symbol` (el original, si no hay
    reemplazo real) para que el orquestador los intente igual y la
    ausencia de datos quede documentada por el propio ciclo, no por una
    decisión de esta capa.
    """
    override = _overrides().get(symbol)
    if override is not None:
        query = override.get("tradier_symbol") or symbol
        return NormalizedSymbol(
            original=symbol,
            query_symbol=query,
            state=override["state"],
            reason=override["reason"],
            rule="override_explicito",
        )

    match = _SHARE_CLASS_RE.match(symbol)
    if match:
        query = f"{match.group(1)}/{match.group(2)}"
        return NormalizedSymbol(
            original=symbol,
            query_symbol=query,
            state="ACTIVE",
            reason="Regla general: notación de clase de acción con punto -- Tradier usa slash.",
            rule="regla_clase_accion",
        )

    if symbol in _undetermined():
        entry = _undetermined()[symbol]
        return NormalizedSymbol(
            original=symbol,
            query_symbol=symbol,
            state="UNRESOLVED",
            reason=f"No investigado individualmente (categoría 7, sesión 2026-08-14): {entry.get('note')}",
            rule="sin_normalizacion",
        )

    return NormalizedSymbol(
        original=symbol,
        query_symbol=symbol,
        state="ACTIVE",
        reason=None,
        rule="sin_normalizacion",
    )


def get_state(symbol: str) -> str:
    """Atajo para obtener solo el estado, sin construir todo el resultado."""
    return normalize(symbol).state


def known_symbols_count() -> Dict[str, int]:
    """Conteo de símbolos con clasificación explícita, por estado -- para
    diagnóstico/documentación, no se usa en el flujo de consulta."""
    counts: Dict[str, int] = {}
    for entry in _overrides().values():
        counts[entry["state"]] = counts.get(entry["state"], 0) + 1
    counts["UNRESOLVED"] = len(_undetermined())
    return counts
