"""Tests de `atlas_live/universo_yahoo_ranking.py` -- TOP 100 EN MOVIMIENTO
(2026-09-06). Puro, sin red, sin DB -- dobles simples con los atributos
que el módulo lee (`change_percent`, `relative_volume`, `last_price`,
`volume`, `name`), sin depender del `Quote` real ni de ningún proveedor."""

import types

from atlas_live import universo_yahoo_ranking as ranking


def _q(change_percent=None, relative_volume=None, last_price=None, volume=None, name="X"):
    return types.SimpleNamespace(
        change_percent=change_percent, relative_volume=relative_volume,
        last_price=last_price, volume=volume, name=name,
    )


# --------------------------- filtros individuales ---------------------------

def test_excluye_change_percent_faltante():
    quotes = {"AAA": _q(change_percent=None, relative_volume=2.0, last_price=10, volume=100_000)}
    r = ranking.compute_top_movimiento(quotes)
    assert r["total_candidatos"] == 0
    assert r["top"] == []


def test_excluye_relative_volume_faltante():
    quotes = {"AAA": _q(change_percent=5.0, relative_volume=None, last_price=10, volume=100_000)}
    r = ranking.compute_top_movimiento(quotes)
    assert r["total_candidatos"] == 0


def test_excluye_dollar_volume_por_debajo_del_piso():
    # last_price*volume = 2 * 10_000 = 20_000 < 50_000
    quotes = {"AAA": _q(change_percent=5.0, relative_volume=2.0, last_price=2, volume=10_000)}
    r = ranking.compute_top_movimiento(quotes)
    assert r["total_candidatos"] == 0


def test_dollar_volume_exactamente_en_el_piso_pasa():
    # last_price*volume = 5 * 10_000 = 50_000 == piso -> pasa (>=)
    quotes = {"AAA": _q(change_percent=5.0, relative_volume=2.0, last_price=5, volume=10_000)}
    r = ranking.compute_top_movimiento(quotes)
    assert r["total_candidatos"] == 1


def test_excluye_change_percent_negativo():
    quotes = {"AAA": _q(change_percent=-3.0, relative_volume=5.0, last_price=10, volume=100_000)}
    r = ranking.compute_top_movimiento(quotes)
    assert r["total_candidatos"] == 0


def test_excluye_change_percent_exactamente_cero():
    quotes = {"AAA": _q(change_percent=0.0, relative_volume=5.0, last_price=10, volume=100_000)}
    r = ranking.compute_top_movimiento(quotes)
    assert r["total_candidatos"] == 0


# --------------------------- fórmula ---------------------------

def test_score_es_change_percent_por_relative_volume():
    quotes = {"AAA": _q(change_percent=8.0, relative_volume=4.0, last_price=50, volume=2_000_000)}
    r = ranking.compute_top_movimiento(quotes)
    assert r["top"][0]["score"] == 32.0


def test_ejemplo_del_informe_aprobado_orden_completo():
    """Reproduce exactamente el ejemplo de 5 tickers ya presentado y
    aprobado por el usuario antes de implementar."""
    quotes = {
        "AAA": _q(change_percent=8.0, relative_volume=4.0, last_price=50, volume=2_000_000),   # score 32.0
        "CCC": _q(change_percent=15.0, relative_volume=1.2, last_price=30, volume=500_000),      # score 18.0
        "DDD": _q(change_percent=2.0, relative_volume=6.0, last_price=10, volume=300_000),       # score 12.0
        "BBB": _q(change_percent=25.0, relative_volume=8.0, last_price=2, volume=10_000),        # dollar_volume=20_000 -> excluida
        "EEE": _q(change_percent=-3.0, relative_volume=5.0, last_price=5, volume=50_000),        # negativa -> excluida
    }
    r = ranking.compute_top_movimiento(quotes)
    assert r["total_evaluados"] == 5
    assert r["total_candidatos"] == 3
    simbolos = [c["symbol"] for c in r["top"]]
    assert simbolos == ["AAA", "CCC", "DDD"]
    assert [c["rank"] for c in r["top"]] == [1, 2, 3]


# --------------------------- orden y límite ---------------------------

def test_orden_descendente_por_score():
    quotes = {
        "LOW": _q(change_percent=1.0, relative_volume=1.0, last_price=10, volume=100_000),   # 1.0
        "HIGH": _q(change_percent=10.0, relative_volume=5.0, last_price=10, volume=100_000),  # 50.0
        "MID": _q(change_percent=4.0, relative_volume=2.0, last_price=10, volume=100_000),    # 8.0
    }
    r = ranking.compute_top_movimiento(quotes)
    assert [c["symbol"] for c in r["top"]] == ["HIGH", "MID", "LOW"]


def test_maximo_100_aunque_pasen_mas_candidatos():
    quotes = {
        f"T{i:04d}": _q(change_percent=1.0 + i * 0.001, relative_volume=2.0, last_price=10, volume=100_000)
        for i in range(150)
    }
    r = ranking.compute_top_movimiento(quotes)
    assert r["total_evaluados"] == 150
    assert r["total_candidatos"] == 150  # los 150 pasan los filtros
    assert len(r["top"]) == 100  # pero el TOP se recorta a 100
    # el mejor score (T0149, change_percent mas alto) debe quedar primero
    assert r["top"][0]["symbol"] == "T0149"


def test_empate_determinista_por_simbolo_ascendente():
    quotes = {
        "ZZZ": _q(change_percent=5.0, relative_volume=2.0, last_price=10, volume=100_000),  # score 10.0
        "AAA": _q(change_percent=5.0, relative_volume=2.0, last_price=10, volume=100_000),  # score 10.0 (empate exacto)
        "MMM": _q(change_percent=5.0, relative_volume=2.0, last_price=10, volume=100_000),  # score 10.0 (empate exacto)
    }
    r = ranking.compute_top_movimiento(quotes)
    assert [c["symbol"] for c in r["top"]] == ["AAA", "MMM", "ZZZ"]


def test_sin_quotes_no_fabrica_nada():
    r = ranking.compute_top_movimiento({})
    assert r == {"total_evaluados": 0, "total_candidatos": 0, "top": []}


def test_recalculo_refleja_datos_nuevos_sin_estado_interno():
    """El módulo no guarda ningún estado propio -- 2 llamadas sucesivas con
    datos distintos deben reflejar cada una su propia entrada (confirma
    'actualización del ranking': no hay caché oculto que devuelva lo
    mismo de antes)."""
    quotes_1 = {"AAA": _q(change_percent=2.0, relative_volume=2.0, last_price=10, volume=100_000)}
    quotes_2 = {"BBB": _q(change_percent=9.0, relative_volume=3.0, last_price=10, volume=100_000)}
    r1 = ranking.compute_top_movimiento(quotes_1)
    r2 = ranking.compute_top_movimiento(quotes_2)
    assert [c["symbol"] for c in r1["top"]] == ["AAA"]
    assert [c["symbol"] for c in r2["top"]] == ["BBB"]


def test_modulo_no_importa_ningun_proveedor_ni_motor_protegido():
    """Escaneo estático del propio archivo -- confirma, sin depender de
    que alguien recuerde no agregarlo después, que este módulo nunca
    importa Tradier/Yahoo/candidate_gates/priority_classifier/
    DecisionEngine/atlas_decision_core."""
    import ast
    import inspect

    source = inspect.getsource(ranking)
    tree = ast.parse(source)
    prohibidos = (
        "tradier", "yahoo", "candidate_gates", "priority_classifier",
        "decision_engine", "atlas_decision_core", "requests", "yfinance",
    )
    nombres_importados = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            nombres_importados += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            nombres_importados.append(node.module)

    for nombre in nombres_importados:
        for p in prohibidos:
            assert p not in nombre.lower(), f"import prohibido detectado: {nombre}"
