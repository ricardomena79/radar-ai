"""Tests de identidad del universo (FASE 11, 2026-08-10). Offline, deterministas.

NOTA: escritos sin poder ejecutarse en la máquina de desarrollo (sin Python);
la ejecución real corre en Railway/CI o cuando el usuario los lance.
"""

import json
import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.market_study import universe

_NASDAQ_SAMPLE = (
    "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
    "AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N\n"
    "TSTZ|Zombie Test Issue|Q|Y|N|100|N|N\n"  # test issue -> descartado
    "File Creation Time: 08102026\n"
)

_OTHER_SAMPLE = (
    "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
    "DNN|Denison Mines Corp|A|DNN|N|100|N|DNN\n"
    "SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY\n"
    "File Creation Time: 08102026\n"
)


def test_parse_pipe_meta_nasdaq():
    recs = universe._parse_pipe_meta(_NASDAQ_SAMPLE, symbol_col=0, name_col=1, test_col=3, exchange="NASDAQ")
    by = {r["symbol"]: r for r in recs}
    assert "AAPL" in by and "TSTZ" not in by  # test issue descartado
    assert by["AAPL"]["exchange"] == "NASDAQ"
    assert by["AAPL"]["name"] == "Apple Inc. - Common Stock"


def test_parse_pipe_meta_other_exchange_code():
    recs = universe._parse_pipe_meta(_OTHER_SAMPLE, symbol_col=0, name_col=1, test_col=6, exchange_col=2)
    by = {r["symbol"]: r for r in recs}
    assert by["DNN"]["exchange"] == "NYSE American"   # código A -> etiqueta
    assert by["DNN"]["name"] == "Denison Mines Corp"
    assert by["SPY"]["exchange"] == "NYSE ARCA"       # código P


def test_tradingview_symbol():
    assert universe.tradingview_symbol("NASDAQ", "AAPL") == "NASDAQ:AAPL"
    assert universe.tradingview_symbol("NYSE American", "DNN") == "AMEX:DNN"
    assert universe.tradingview_symbol("NYSE", "GE") == "NYSE:GE"
    # exchange desconocido o ausente -> sin prefijo inventado
    assert universe.tradingview_symbol(None, "XYZ") == "XYZ"
    assert universe.tradingview_symbol("Bolsa Marte", "XYZ") == "XYZ"


def test_lookup_identity_from_cache():
    orig = universe._CACHE_META
    tmp = Path(tempfile.gettempdir()) / f"atlas_test_meta_{_uuid.uuid4().hex}.json"
    tmp.write_text(json.dumps({"DNN": {"exchange": "NYSE American", "name": "Denison Mines Corp"}}), encoding="utf-8")
    universe._CACHE_META = tmp
    try:
        ident = universe.lookup_identity("dnn")  # case-insensitive
        assert ident["exchange"] == "NYSE American"
        assert ident["name"] == "Denison Mines Corp"
        # símbolo desconocido -> None, nunca inventado
        assert universe.lookup_identity("NOPE") == {"exchange": None, "name": None}
    finally:
        universe._CACHE_META = orig
        tmp.unlink(missing_ok=True)


def test_lookup_identity_sin_cache():
    orig = universe._CACHE_META
    universe._CACHE_META = Path(tempfile.gettempdir()) / f"atlas_test_missing_{_uuid.uuid4().hex}.json"
    try:
        assert universe.lookup_identity("AAPL") == {"exchange": None, "name": None}
    finally:
        universe._CACHE_META = orig
