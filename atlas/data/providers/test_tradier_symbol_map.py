"""Tests de la capa de normalización de símbolos para Tradier (2026-08-14).

Sin red -- solo lógica pura (regex + lookup en los JSON de overrides). Los
casos vienen directo de la investigación real contra la Production API de
Tradier (ver tradier_symbol_overrides.json para la evidencia completa).
"""

from atlas.data.providers import tradier_symbol_map as tsm


def test_clase_de_accion_confirmada_usa_override_explicito():
    n = tsm.normalize("BRK.B")
    assert n.query_symbol == "BRK/B"
    assert n.state == "ACTIVE"
    assert n.rule == "override_explicito"
    assert n.reason is not None


def test_renombrado_confirmado_devuelve_ticker_actual():
    n = tsm.normalize("ADGI")
    assert n.query_symbol == "IVVD"
    assert n.state == "ACTIVE"

    n2 = tsm.normalize("DWAC")
    assert n2.query_symbol == "DJT"
    assert n2.state == "ACTIVE"


def test_sufijo_old_se_limpia_via_override():
    n = tsm.normalize("POAHY.OLD")
    assert n.query_symbol == "POAHY"
    assert n.state == "ACTIVE"

    n2 = tsm.normalize("TKO.OLD")
    assert n2.query_symbol == "TKO"


def test_obsoleto_no_tiene_reemplazo_pero_se_intenta_igual():
    n = tsm.normalize("K")  # Kellanova, delistada -- ver override
    assert n.state == "OBSOLETE"
    assert n.query_symbol == "K"  # sin reemplazo real -- se pasa tal cual, no se excluye
    assert n.reason is not None and "Mars" in n.reason


def test_especial_etf_de_nicho():
    n = tsm.normalize("SPLG")
    assert n.state == "SPECIAL"
    assert n.query_symbol == "SPLG"


def test_exclusivo_de_racional():
    n = tsm.normalize("CFINASDAQ")
    assert n.state == "SPECIAL"
    assert "Racional" in n.reason


def test_renombrado_segunda_pasada_bk_a_bny():
    """Segunda pasada de investigación (2026-08-14): BK parecía "no cubierto"
    en la primera pasada, pero Bank of New York Mellon cambió su ticker a
    BNY -- no era un vacío de proveedor, era un renombre no detectado."""
    n = tsm.normalize("BK")
    assert n.query_symbol == "BNY"
    assert n.state == "ACTIVE"


def test_obsoleto_segunda_pasada_fusion_real():
    """CyberArk (CYBR) parecía "no cubierto" en la primera pasada -- en
    realidad fue adquirida por Palo Alto Networks y delistada (2026-02-11),
    confirmado con fuente. No es un vacío de Tradier/Yahoo, es un delisting real."""
    n = tsm.normalize("CYBR")
    assert n.state == "OBSOLETE"


def test_no_cubierto_confirmado():
    n = tsm.normalize("CIVI")
    assert n.state == "UNSUPPORTED"
    assert n.query_symbol == "CIVI"


def test_no_determinado_cae_en_unresolved():
    n = tsm.normalize("APLS")  # categoría 7, no investigado individualmente
    assert n.state == "UNRESOLVED"
    assert n.query_symbol == "APLS"


def test_simbolo_normal_sin_override_es_active_sin_reescritura():
    n = tsm.normalize("AAPL")
    assert n.state == "ACTIVE"
    assert n.query_symbol == "AAPL"
    assert n.rule == "sin_normalizacion"
    assert n.reason is None


def test_regla_general_de_clase_de_accion_no_solo_los_probados():
    """La regla dot->slash es general (no solo los 6 símbolos que se
    probaron en vivo) -- un símbolo hipotético con el mismo patrón que no
    esté en el override explícito también debe normalizarse, trazado como
    'regla_clase_accion' en vez de 'override_explicito'."""
    n = tsm.normalize("ZZZZ.C")
    assert n.query_symbol == "ZZZZ/C"
    assert n.rule == "regla_clase_accion"
    assert n.state == "ACTIVE"


def test_todas_las_equivalencias_tienen_estado_valido():
    import json
    from pathlib import Path

    overrides = json.loads((Path(tsm.__file__).parent / "tradier_symbol_overrides.json").read_text(encoding="utf-8"))
    assert len(overrides) == 78  # 70 de la 1ra pasada + 16 renombres reales - 2 reclasificados a OBSOLETE + 1 movido desde no_determinado (CIVI) = 78, ver segunda pasada 2026-08-14
    for symbol, entry in overrides.items():
        assert entry["state"] in tsm.VALID_STATES, f"{symbol}: estado inválido {entry['state']}"
        assert entry["reason"], f"{symbol}: sin motivo -- toda equivalencia debe ser trazable"


def test_conteo_por_estado_coincide_con_la_investigacion():
    """Segunda pasada (2026-08-14): buscar por NOMBRE de empresa en Tradier
    reveló 16 renombres de ticker reales que la primera pasada (solo por
    símbolo) no detectó, y 2 casos que en realidad son OBSOLETE (fusión/
    quiebra real) en vez de "no cubierto"."""
    counts = tsm.known_symbols_count()
    assert counts.get("ACTIVE") == 27         # 9 formato + 2 renombrado (1ra) + 16 renombrado (2da pasada)
    assert counts.get("OBSOLETE") == 8         # 6 (1ra) + 2 reclasificados (CYBR, ZYXI)
    assert counts.get("SPECIAL") == 23         # sin cambios
    assert counts.get("UNSUPPORTED") == 20     # 30 - 9 renombrados - 2 reclasificados + 1 (CIVI, confirmado individualmente)
    assert counts.get("UNRESOLVED") == 47      # 55 - 7 renombrados - 1 (CIVI, ahora en UNSUPPORTED)
    assert sum(counts.values()) == 125


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
            p += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
            traceback.print_exc()
            f += 1
    print(f"--- {p} passed, {f} failed ---")
