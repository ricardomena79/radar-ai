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

Clasificación de instrumentos (2026-08-17, pedido explícito: no mezclar
acciones ordinarias con ETFs/warrants/units/rights/preferidas en la misma
población de aprendizaje). Ambos archivos fuente traen una columna `ETF`
(Y/N) explícita -- se captura tal cual, es dato real de la fuente, no una
heurística. Para el resto de los tipos (warrant, unit, right, preferida,
nota/deuda) esos archivos NO traen una columna dedicada: se clasifican por
patrones de texto sobre `Security Name`, el enfoque estándar para estos
feeds. Es una heurística basada en texto, documentada como tal -- por
defecto (ninguna señal reconocida) el símbolo queda como EQUITY, nunca se
inventa una categoría más específica sin evidencia en el nombre.
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

import requests

from atlas.config.config import data_dir

_NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
_OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
_DATA_DIR = data_dir(default=Path(__file__).parent)
_CACHE = _DATA_DIR / "broad_universe.json"            # lista de símbolos (compat)
_CACHE_META = _DATA_DIR / "broad_universe_meta.json"  # símbolo -> {exchange, name, type}
# Versión de las reglas de `classify_instrument_type()` (2026-08-20, bug
# real caso FUTU/ARM/BIDU/BILI): el caché de arriba guarda el `type` ya
# resuelto -- si las reglas de clasificación cambian pero el caché en
# disco sigue siendo válido según su propio chequeo (tiene "type"), nunca
# se reclasifica solo. Este archivo de versión, separado a propósito para
# no tocar el formato del caché principal, fuerza una redescarga+reclasificación
# cuando la lógica cambió, sin depender de borrar el archivo a mano.
_CACHE_META_VERSION_FILE = _DATA_DIR / "broad_universe_meta_version.txt"
_CLASSIFICATION_VERSION = "2026-08-20-ads-no-es-preferred"
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


# Patrones de texto sobre `Security Name` para instrumentos que NO son
# acciones ordinarias y que los archivos fuente no marcan con columna
# propia (a diferencia de ETF, que sí trae su columna real). Orden
# importa: se evalúan en secuencia, gana el primer patrón que matchee.
_NAME_TYPE_PATTERNS = (
    ("WARRANT", "WARRANT"),
    ("RIGHT", "RIGHT"),
    ("UNIT", "UNIT"),
    # "DEPOSITARY SHARES" (2026-08-20, bug real encontrado en vivo, caso
    # FUTU) se sacó de acá: capturaba tanto preferentes reales ("X% Series A
    # Preferred Depositary Shares") como "American Depositary Shares", el
    # mecanismo NORMAL para que empresas extranjeras coticen en EE.UU. --
    # nada que ver con acciones preferentes. Eso excluía del radar en vivo
    # (solo escanea EQUITY) a 273 empresas ordinarias reales, incluidas
    # ARM, BIDU, BILI, argenx. Los preferentes genuinos con "Depositary
    # Shares" en el nombre (verificado: 47 casos reales, ej. Arch Capital,
    # AGNC, Brighthouse) siguen bien clasificados por la regla de abajo,
    # porque también dicen "Preferred" explícitamente en el nombre.
    ("PREFERRED", "PREFERRED"),
    (" PFD", "PREFERRED"),
    ("DEBENTURE", "DEBT"),
    ("NOTES", "DEBT"),
    ("BOND", "DEBT"),
)


def classify_instrument_type(symbol: str, name: str, etf_flag: bool) -> str:
    """Clasifica un símbolo del universo amplio en EQUITY/ETF/WARRANT/UNIT/
    RIGHT/PREFERRED/DEBT -- para no mezclar acciones ordinarias con
    instrumentos que distorsionarían el aprendizaje de movimientos
    explosivos (2026-08-17). `etf_flag` es dato real de la fuente (columna
    ETF). El resto es heurística de texto sobre `name`, documentada como
    tal: si ninguna señal aplica, el símbolo queda como EQUITY -- nunca se
    inventa una categoría más específica sin evidencia en el nombre."""
    if etf_flag:
        return "ETF"
    upper_name = (name or "").upper()
    for pattern, label in _NAME_TYPE_PATTERNS:
        if pattern in upper_name:
            return label
    return "EQUITY"


# ETFs apalancados/inversos (2026-08-20, pedido explícito del usuario, caso
# real MSTU/ETHU/CONL/BITX): el radar en vivo excluye TODO tipo=ETF a
# propósito (evita mezclar ETFs pasivos -- bonos, índices amplios -- que
# casi nunca se mueven fuerte en un día, con el aprendizaje de movimientos
# explosivos). Pero un ETF apalancado 1x/2x/3x sobre una sola acción/cripto
# es justo lo opuesto: amplifica directamente el movimiento de su
# subyacente, y es la categoría exacta que causó una brecha real (Atlas no
# vio el rally de cripto de MSTU/ETHU/CONL/BITX porque son ETFs). Patrón de
# nombre verificado contra el universo real: "2X"/"3X" (palabra completa,
# no dentro de otro texto), "ULTRA", "DAILY TARGET", "LEVERAGED" -- mismo
# vocabulario que usan los emisores reales (Direxion, GraniteShares,
# T-Rex, Leverage Shares) en el nombre oficial del instrumento. Nunca
# cambia `classify_instrument_type()` ni el campo `type` guardado -- es un
# filtro ADICIONAL, aplicado solo al armar el universo que barre el radar
# en vivo (`radar_worker.py`), para no tocar la Base Histórica (que sigue
# excluyendo TODOS los ETFs, sin cambios)."""
_LEVERAGED_ETF_PATTERN = re.compile(r"\b[123]X\b|ULTRA|DAILY TARGET|LEVERAGED", re.IGNORECASE)


def is_leveraged_etf_name(name: Optional[str]) -> bool:
    """True si `name` (Security Name real del feed) tiene el patrón de un
    ETF apalancado/inverso conocido. Solo tiene sentido llamarla sobre un
    símbolo YA clasificado como type=="ETF" -- no reclasifica nada por su
    cuenta."""
    return bool(name) and bool(_LEVERAGED_ETF_PATTERN.search(name))


def _parse_pipe_meta(text: str, symbol_col: int, name_col: int, test_col: int,
                     exchange: Optional[str] = None,
                     exchange_col: Optional[int] = None,
                     etf_col: Optional[int] = None) -> List[Dict[str, str]]:
    """Parsea un archivo pipe-delimitado en registros {symbol, name, exchange, etf},
    saltando header y el footer de 'File Creation Time' y descartando test issues."""
    out: List[Dict[str, str]] = []
    needed = max(c for c in (symbol_col, name_col, test_col, exchange_col or 0, etf_col or 0))
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
        etf = parts[etf_col].strip().upper() == "Y" if etf_col is not None else False
        out.append({"symbol": sym, "name": parts[name_col].strip(), "exchange": exch or "", "etf": etf})
    return out


def fetch_broad_universe_meta(use_cache: bool = True) -> Dict[str, Dict[str, str]]:
    """Descarga (o lee de caché) el universo amplio US CON identidad y
    clasificación de instrumento. Devuelve
    {symbol: {"exchange": ..., "name": ..., "type": ...}} -- `type` es
    EQUITY/ETF/WARRANT/UNIT/RIGHT/PREFERRED/DEBT (ver
    `classify_instrument_type`). Si la red falla y hay caché, usa la
    caché. Ante símbolo duplicado, gana la primera aparición (NASDAQ antes
    que otherlisted)."""
    if use_cache and _CACHE_META.exists():
        try:
            cached = json.loads(_CACHE_META.read_text(encoding="utf-8"))
            cached_version = (
                _CACHE_META_VERSION_FILE.read_text(encoding="utf-8").strip()
                if _CACHE_META_VERSION_FILE.exists() else None
            )
            # Caché de una versión anterior a la clasificación (2026-08-17)
            # no tiene "type" -- se descarta y se re-descarga en vez de
            # devolver registros incompletos que romperían el filtro EQUITY.
            # Caché con "type" pero de una versión VIEJA de las reglas de
            # clasificación (2026-08-20) también se descarta -- si no, un
            # fix como el de FUTU/ARM/BIDU/BILI nunca se aplicaría solo.
            if cached and all("type" in v for v in cached.values()) and cached_version == _CLASSIFICATION_VERSION:
                return cached
        except Exception:
            pass
    meta: Dict[str, Dict[str, str]] = {}
    try:
        r1 = requests.get(_NASDAQ_URL, timeout=_TIMEOUT)
        if r1.status_code == 200:
            # nasdaqlisted: Symbol(0)|Security Name(1)|Market Category(2)|Test Issue(3)|Financial Status(4)|Round Lot Size(5)|ETF(6)|NextShares(7)
            for rec in _parse_pipe_meta(r1.text, symbol_col=0, name_col=1, test_col=3, exchange="NASDAQ", etf_col=6):
                meta.setdefault(rec["symbol"], {
                    "exchange": rec["exchange"], "name": rec["name"],
                    "type": classify_instrument_type(rec["symbol"], rec["name"], rec["etf"]),
                })
        r2 = requests.get(_OTHER_URL, timeout=_TIMEOUT)
        if r2.status_code == 200:
            # otherlisted: ACT Symbol(0)|Security Name(1)|Exchange(2)|CQS Symbol(3)|ETF(4)|Round Lot Size(5)|Test Issue(6)|NASDAQ Symbol(7)
            for rec in _parse_pipe_meta(r2.text, symbol_col=0, name_col=1, test_col=6, exchange_col=2, etf_col=4):
                meta.setdefault(rec["symbol"], {
                    "exchange": rec["exchange"], "name": rec["name"],
                    "type": classify_instrument_type(rec["symbol"], rec["name"], rec["etf"]),
                })
    except Exception:
        if _CACHE_META.exists():
            return json.loads(_CACHE_META.read_text(encoding="utf-8"))
        raise
    if meta:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_META.write_text(json.dumps(meta), encoding="utf-8")
        _CACHE_META_VERSION_FILE.write_text(_CLASSIFICATION_VERSION, encoding="utf-8")
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
