"""Universo AMPLIO de acciones US para el estudio (2026-08-10).

Fuente: nasdaqtrader.com (`nasdaqlisted.txt` + `otherlisted.txt`) -- lista
oficial de símbolos listados en NASDAQ/NYSE/AMEX, gratis, SIN API key,
datacenter-friendly (HTTP simple). ~13.000 símbolos antes de filtrar.

Se cachea a un archivo para no re-descargar en cada corrida. `racional_symbols`
devuelve el universo de Racional existente (get_symbols()) para el cruce de
operabilidad -- NO se toca ese universo, solo se lee.
"""

import json
from pathlib import Path
from typing import List, Set

import requests

from atlas.config.config import data_dir

_NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
_OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
_CACHE = data_dir(default=Path(__file__).parent) / "broad_universe.json"
_TIMEOUT = 20


def _parse_pipe(text: str, symbol_col: int, test_col: int) -> List[str]:
    """Parsea un archivo pipe-delimitado, saltando header y el footer de
    'File Creation Time'. Descarta test issues y símbolos con caracteres raros."""
    syms = []
    lines = text.strip().split("\n")
    for line in lines[1:]:  # saltar header
        if line.startswith("File Creation Time") or not line.strip():
            continue
        parts = line.split("|")
        if len(parts) <= max(symbol_col, test_col):
            continue
        sym = parts[symbol_col].strip().upper()
        test = parts[test_col].strip().upper()
        if test == "Y":  # test issue -> fuera
            continue
        # yfinance no maneja bien símbolos con $, espacios o clases con "."
        if not sym or not sym.replace(".", "").replace("-", "").isalnum():
            continue
        syms.append(sym)
    return syms


def fetch_broad_universe(use_cache: bool = True) -> List[str]:
    """Descarga (o lee de caché) el universo amplio US. Ordenado y sin
    duplicados. Si la red falla y hay caché, usa la caché."""
    if use_cache and _CACHE.exists():
        try:
            return json.loads(_CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    syms: Set[str] = set()
    try:
        r1 = requests.get(_NASDAQ_URL, timeout=_TIMEOUT)
        if r1.status_code == 200:
            # nasdaqlisted: Symbol(0) ... Test Issue(3)
            syms.update(_parse_pipe(r1.text, symbol_col=0, test_col=3))
        r2 = requests.get(_OTHER_URL, timeout=_TIMEOUT)
        if r2.status_code == 200:
            # otherlisted: ACT Symbol(0) ... Test Issue(6)
            syms.update(_parse_pipe(r2.text, symbol_col=0, test_col=6))
    except Exception:
        if _CACHE.exists():
            return json.loads(_CACHE.read_text(encoding="utf-8"))
        raise
    result = sorted(syms)
    if result:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(json.dumps(result), encoding="utf-8")
    return result


def racional_symbols() -> Set[str]:
    """Universo de Racional existente (solo lectura) para el cruce de
    operabilidad `available_in_racional`."""
    from atlas.data.universe import get_symbols
    return {s.upper() for s in get_symbols()}
