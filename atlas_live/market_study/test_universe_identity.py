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

_NASDAQ_ETF_SAMPLE = (
    "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
    "QQQ|Invesco QQQ Trust Series 1|Q|N|N|100|Y|N\n"
    "AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N\n"
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


def test_parse_pipe_meta_captura_columna_etf_real():
    recs = universe._parse_pipe_meta(_NASDAQ_ETF_SAMPLE, symbol_col=0, name_col=1, test_col=3, exchange="NASDAQ", etf_col=6)
    by = {r["symbol"]: r for r in recs}
    assert by["QQQ"]["etf"] is True
    assert by["AAPL"]["etf"] is False


def test_parse_pipe_meta_sin_etf_col_default_false():
    recs = universe._parse_pipe_meta(_NASDAQ_SAMPLE, symbol_col=0, name_col=1, test_col=3, exchange="NASDAQ")
    assert recs[0]["etf"] is False


def test_classify_instrument_type_etf_flag_manda():
    assert universe.classify_instrument_type("QQQ", "Invesco QQQ Trust Series 1", True) == "ETF"


def test_classify_instrument_type_equity_por_defecto():
    assert universe.classify_instrument_type("AAPL", "Apple Inc. - Common Stock", False) == "EQUITY"


def test_classify_instrument_type_warrant():
    assert universe.classify_instrument_type("XYZW", "XYZ Corp Warrants", False) == "WARRANT"


def test_classify_instrument_type_unit():
    assert universe.classify_instrument_type("XYZU", "XYZ Corp Units", False) == "UNIT"


def test_classify_instrument_type_right():
    assert universe.classify_instrument_type("XYZR", "XYZ Corp Rights", False) == "RIGHT"


def test_classify_instrument_type_preferred():
    assert universe.classify_instrument_type("XYZP", "XYZ Corp 8% Preferred Stock", False) == "PREFERRED"
    # "Depositary Shares" con "Preferred" explícito en el nombre -- caso
    # real (Arch Capital, AGNC, Brighthouse) -- sigue siendo PREFERRED.
    assert universe.classify_instrument_type(
        "XYZP", "XYZ Corp Depositary Shares Each Representing a 1/1000th Interest in a Preferred Share", False
    ) == "PREFERRED"


def test_classify_instrument_type_american_depositary_shares_es_equity():
    """2026-08-20, bug real encontrado en vivo (caso FUTU): "American
    Depositary Shares" es el mecanismo NORMAL para que empresas
    extranjeras coticen en EE.UU. -- una acción ordinaria real, no una
    preferente. Antes del fix, este caso (y 272 empresas reales más,
    incluidas ARM/BIDU/BILI/argenx) quedaban mal clasificadas como
    PREFERRED y excluidas del radar en vivo (solo escanea EQUITY)."""
    assert universe.classify_instrument_type(
        "FUTU", "Futu Holdings Limited - American Depositary Shares", False
    ) == "EQUITY"
    assert universe.classify_instrument_type(
        "BIDU", "Baidu, Inc. - American Depositary Shares, each representing 8 ordinary shares", False
    ) == "EQUITY"


def test_classify_instrument_type_debt():
    assert universe.classify_instrument_type("XYZN", "XYZ Corp 5% Notes due 2030", False) == "DEBT"


def test_fetch_broad_universe_meta_incluye_type(monkeypatch):
    """Verifica que el resultado final de fetch_broad_universe_meta trae
    'type' por símbolo -- sin red, mockeando requests.get."""
    class _Resp:
        def __init__(self, text):
            self.status_code = 200
            self.text = text

    def _fake_get(url, timeout):
        if url == universe._NASDAQ_URL:
            return _Resp(_NASDAQ_ETF_SAMPLE)
        return _Resp(_OTHER_SAMPLE)

    orig_cache, orig_cache_meta = universe._CACHE, universe._CACHE_META
    universe._CACHE = Path(tempfile.gettempdir()) / f"atlas_test_cache_{_uuid.uuid4().hex}.json"
    universe._CACHE_META = Path(tempfile.gettempdir()) / f"atlas_test_cache_meta_{_uuid.uuid4().hex}.json"
    monkeypatch.setattr(universe.requests, "get", _fake_get)
    try:
        meta = universe.fetch_broad_universe_meta(use_cache=False)
        assert meta["QQQ"]["type"] == "ETF"
        assert meta["AAPL"]["type"] == "EQUITY"
        assert meta["SPY"]["type"] == "ETF"
        assert meta["DNN"]["type"] == "EQUITY"
    finally:
        universe._CACHE = orig_cache
        universe._CACHE_META = orig_cache_meta


def test_fetch_broad_universe_meta_descarta_cache_vieja_sin_type(monkeypatch):
    """Una caché de una versión anterior (sin 'type') se descarta y se
    re-descarga, en vez de devolver registros incompletos."""
    tmp = Path(tempfile.gettempdir()) / f"atlas_test_stale_cache_{_uuid.uuid4().hex}.json"
    tmp.write_text(json.dumps({"AAPL": {"exchange": "NASDAQ", "name": "Apple Inc."}}), encoding="utf-8")

    class _Resp:
        def __init__(self, text):
            self.status_code = 200
            self.text = text

    def _fake_get(url, timeout):
        if url == universe._NASDAQ_URL:
            return _Resp(_NASDAQ_ETF_SAMPLE)
        return _Resp(_OTHER_SAMPLE)

    orig_cache, orig_cache_meta = universe._CACHE, universe._CACHE_META
    universe._CACHE_META = tmp
    universe._CACHE = Path(tempfile.gettempdir()) / f"atlas_test_cache2_{_uuid.uuid4().hex}.json"
    monkeypatch.setattr(universe.requests, "get", _fake_get)
    try:
        meta = universe.fetch_broad_universe_meta(use_cache=True)
        assert "type" in meta["AAPL"]  # se re-descargó, no devolvió la caché incompleta
    finally:
        universe._CACHE = orig_cache
        universe._CACHE_META = orig_cache_meta
        tmp.unlink(missing_ok=True)


def test_fetch_broad_universe_meta_descarta_cache_de_clasificacion_vieja(monkeypatch):
    """2026-08-20, bug real FUTU/ARM/BIDU/BILI: una caché con "type" pero
    calculada con una versión ANTERIOR de las reglas de clasificación
    (sin el archivo de versión, o con una versión distinta) también se
    descarta -- si no, un fix a `classify_instrument_type()` nunca se
    aplicaría solo mientras el caché en disco siga "pareciendo" válido."""
    tmp_meta = Path(tempfile.gettempdir()) / f"atlas_test_stale_version_{_uuid.uuid4().hex}.json"
    # caché "completa" (tiene type) pero de una clasificación vieja --
    # FUTU quedó mal clasificado como PREFERRED, la regla ya no existe.
    tmp_meta.write_text(json.dumps({
        "FUTU": {"exchange": "NASDAQ", "name": "Futu Holdings - American Depositary Shares", "type": "PREFERRED"},
    }), encoding="utf-8")

    class _Resp:
        def __init__(self, text):
            self.status_code = 200
            self.text = text

    def _fake_get(url, timeout):
        if url == universe._NASDAQ_URL:
            return _Resp(_NASDAQ_ETF_SAMPLE)
        return _Resp(_OTHER_SAMPLE)

    orig_cache, orig_cache_meta = universe._CACHE, universe._CACHE_META
    orig_version_file = universe._CACHE_META_VERSION_FILE
    universe._CACHE_META = tmp_meta
    universe._CACHE = Path(tempfile.gettempdir()) / f"atlas_test_cache3_{_uuid.uuid4().hex}.json"
    # sin archivo de versión -- simula una caché escrita ANTES de que este
    # mecanismo existiera, o con una versión vieja distinta.
    universe._CACHE_META_VERSION_FILE = Path(tempfile.gettempdir()) / f"atlas_test_no_existe_{_uuid.uuid4().hex}.txt"
    monkeypatch.setattr(universe.requests, "get", _fake_get)
    try:
        meta = universe.fetch_broad_universe_meta(use_cache=True)
        # se re-descargó (la caché vieja tenía SOLO "FUTU", el sample real
        # trae AAPL/QQQ/SPY/DNN) -- confirma que no se devolvió tal cual.
        assert "AAPL" in meta
        assert universe._CACHE_META_VERSION_FILE.read_text(encoding="utf-8") == universe._CLASSIFICATION_VERSION
    finally:
        universe._CACHE = orig_cache
        universe._CACHE_META = orig_cache_meta
        universe._CACHE_META_VERSION_FILE.unlink(missing_ok=True)
        universe._CACHE_META_VERSION_FILE = orig_version_file
        tmp_meta.unlink(missing_ok=True)
