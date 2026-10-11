import json

from atlas.data.universe import extended_hours as eh
from atlas.data.universe import load_universe


def test_el_listado_trae_simbolos_unicos_y_conocidos():
    syms = eh.load_extended_hours_symbols()
    assert len(syms) == 665
    assert {"AAPL", "TSLA", "NVDA", "SPY"} <= syms
    raw = json.load(open(eh.EXTENDED_HOURS_FILE, encoding="utf-8"))["symbols"]
    assert len(raw) == len(set(raw))


def test_se_cruza_con_el_universo_y_solo_devuelve_simbolos_de_racional():
    assets = eh.get_extended_hours_assets()
    universo = load_universe()
    assert assets and all(a.symbol in universo for a in assets)
    assert all(eh.is_extended_hours(a.symbol) for a in assets)
    # BA/HOOD/WOLF figuran en el listado y en el universo operativo
    assert {"BA", "HOOD", "WOLF"} <= {a.symbol for a in assets}
    assert [a.symbol for a in assets] == sorted(a.symbol for a in assets)


def test_un_simbolo_fuera_del_listado_no_es_horario_extendido():
    assert eh.is_extended_hours("ZZZZZ") is False
