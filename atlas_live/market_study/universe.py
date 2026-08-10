"""Universo AMPLIO de acciones US para el estudio (2026-08-10).

Fuente: nasdaqtrader.com (`nasdaqlisted.txt` + `otherlisted.txt`) -- lista
oficial de símbolos listados en NASDAQ/NYSE/AMEX, gratis, SIN API key,
datacenter-friendly (HTTP simple). ~13.000 símbolos antes de filtrar.

Además del símbolo, la fuente trae la IDENTIDAD real del instrumento
(nombre + exchange). La guardamos para no confundir tickers homónimos: DNN
en NYSE American (Denison Mines) no es lo mismo que un DNN de otro mercado.
`fetch_broad_universe_meta` devuelve esa identidad; `fetch_broad_universe`
sigue devolviendo solo la lista de símbolos (compat). `lookup_identity` la
lee de la caché SIN red, para que la ruta operativa no dispare descargas.

Se cachea a un archivo para no re-descargar en cada corrida. `racional_symbols`
devuelve el universo de Racional existente (get_symbols()) para el cruce de
operabilidad -- NO se toca ese universo, solo se lee.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Set

import requests

from atlas.config.config import data_dir

_NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
_OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
_DATA_DIR = data_dir(default=Path(__file__).parent)
_CACHE = _DATA_DIR / "broad_universe.json"            # lista de símbolos (compat)
_CACHE_META = _DATA_DIR / "broad_universe_meta.json"  # símbolo -> {exchange, name}
_TIMEOUT = 20

# Códigos de exchange de otherlisted.txt (columna Exchange) -> etiqueta legible.
# nasdaqlisted.txt no trae columna de exchange: todo es NASDAQ.
_EXCHANGE_CODES = {
    "A": "NYSE American",
    "N": "NYSE",
    "P": "NYSE ARCA",
    "Z": "Cboe BZX",
    "V": "IEX",
}

# Etiqueta de exchange -> prefijo que entiende TradingView. Si no está acá,
# no se antepone prefijo (mejor sin prefijo que con uno equivocado).
_TRADINGVIEW_PREFIX = {
    "NASDAQ": "NASDAQ",
    "NYSE": "NYSE",
    "NYSE American": "AMEX",
    "NYSE ARCA": "AMEX",
    "Cboe BZX": "AMEX",
    "IEX": "AMEX",
}


def tradingview_symbol(exchange: Optional[str], ticker: str) -> str:
    """Símbolo para TradingView: `PREFIJO:TICKER` cuando se conoce el exchange,
    o solo el ticker si no (nunca inventa un prefijo)."""
    prefix = _TRADINGVIEW_PREFIX.get(exchange or "")
    return f"{prefix}:{ticker}" if prefix else ticker


def _valid_symbol(sym: str) -> bool:
    # yfinance no maneja bien símbolos con $, espacios o clases con "."
    return bool(sym) and sym.replace(".", "").replace("-", "").isalnum()


def _parse_pipe_meta(text: str, symbol_col: int, name_col: int, test_col: int,
                     exchange: Optional[str] = None,
                     exchange_col: Optional[int] = None) -> List[Dict[str, str]]:
    """Parsea un archivo pipe-delimitado en registros {symbol, name, exchange},
    saltando header y el footer de 'File Creation Time' y descartando test issues."""
    out: List[Dict[str, str]] = []
    needed = max(c for c in (symbol_col, name_col, test_col, exchange_col or 0))
    for line in text.strip().split("\n")[1:]:  # saltar header
        if line.startswith("File Creation Time") or not line.strip():
            continue
        parts = line.split("|")
        if len(parts) <= needed:
            continue
        sym = parts[symbol_col].strip().upper()
        if parts[test_col].strip().upper() == "Y":  # test issue -> fuera
            continue
        if not _valid_symbol(sym):
            continue
        exch = exchange
        if exchange_col is not None:
            exch = _EXCHANGE_CODES.get(parts[exchange_col].strip().upper(), parts[exchange_col].strip())
        out.append({"symbol": sym, "name": parts[name_col].strip(), "exchange": exch or ""})
    return out


def fetch_broad_universe_meta(use_cache: bool = True) -> Dict[str, Dict[str, str]]:
    """Descarga (o lee de caché) el universo amplio US CON identidad.
    Devuelve {symbol: {"exchange": ..., "name": ...}}. Si la red falla y hay
    caché, usa la caché. Ante símbolo duplicado, gana la primera aparición
    (NASDAQ antes que otherlisted)."""
    if use_cache and _CACHE_META.exists():
        try:
            return json.loads(_CACHE_META.read_text(encoding="utf-8"))
        except Exception:
            pass
    meta: Dict[str, Dict[str, str]] = {}
    try:
        r1 = requests.get(_NASDAQ_URL, timeout=_TIMEOUT)
        if r1.status_code == 200:
            # nasdaqlisted: Symbol(0)|Security Name(1)|...|Test Issue(3)
            for rec in _parse_pipe_meta(r1.text, symbol_col=0, name_col=1, test_col=3, exchange="NASDAQ"):
                meta.setdefault(rec["symbol"], {"exchange": rec["exchange"], "name": rec["name"]})
        r2 = requests.get(_OTHER_URL, timeout=_TIMEOUT)
        if r2.status_code == 200:
            # otherlisted: ACT Symbol(0)|Security Name(1)|Exchange(2)|...|Test Issue(6)
            for rec in _parse_pipe_meta(r2.text, symbol_col=0, name_col=1, test_col=6, exchange_col=2):
                meta.setdefault(rec["symbol"], {"exchange": rec["exchange"], "name": rec["name"]})
    except Exception:
        if _CACHE_META.exists():
            return json.loads(_CACHE_META.read_text(encoding="utf-8"))
        raise
    if meta:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_META.write_text(json.dumps(meta), encoding="utf-8")
        # Mantener también la caché legacy de solo-símbolos en sincronía.
        _CACHE.write_text(json.dumps(sorted(meta)), encoding="utf-8")
    return meta


def fetch_broad_universe(use_cache: bool = True) -> List[str]:
    """Lista de símbolos del universo amplio US, ordenada y sin duplicados.
    Deriva de la metadata (misma descarga/caché), preservando compatibilidad."""
    if use_cache and _CACHE.exists():
        try:
            return json.loads(_CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return sorted(fetch_broad_universe_meta(use_cache=use_cache))


def lookup_identity(symbol: str) -> Dict[str, Optional[str]]:
    """Identidad {exchange, name} de un símbolo desde la caché de metadata, SIN
    red (para la ruta operativa). Vacía si aún no se descargó -- nunca inventa."""
    sym = (symbol or "").upper()
    if not _CACHE_META.exists():
        return {"exchange": None, "name": None}
    try:
        meta = json.loads(_CACHE_META.read_text(encoding="utf-8"))
    except Exception:
        return {"exchange": None, "name": None}
    rec = meta.get(sym) or {}
    return {"exchange": rec.get("exchange") or None, "name": rec.get("name") or None}


def racional_symbols() -> Set[str]:
    """Universo de Racional existente (solo lectura) para el cruce de
    operabilidad `available_in_racional`."""
    from atlas.data.universe import get_symbols
    return {s.upper() for s in get_symbols()}
