"""Tests de historical_scoring.py (2026-08-17, Fase 3). Filas sintéticas
controladas (no aleatorias) para poder verificar exactamente los números
de evidencia -- n, aciertos, % -- que produce el módulo."""

from atlas_live.learning import experiments as exp
from atlas_live.learning import historical_scoring as hs


def _row(symbol, date, direction, timing, volatility_14d_pct, max_advance_pct, max_drawdown_pct=None):
    return {
        "symbol": symbol, "date": date, "direction": direction, "timing_deteccion": timing,
        "volatility_14d_pct": volatility_14d_pct, "max_advance_pct": max_advance_pct,
        "max_drawdown_pct": max_drawdown_pct,
    }


def _make_controlled_group(direction="ALCISTA", timing="expansion_temprana", n=45):
    """45 filas con volatility_14d_pct = 1..45 (valores distintos, spread
    conocido). Cortes de tercil resultantes: lo=s[15], hi=s[30] -- ver
    _tercile_cuts en experiments.py. bajo = vol<=lo (16 filas: 1..16),
    medio = lo<vol<=hi (15 filas: 17..31), alto = vol>hi (14 filas: 32..45).
    max_advance_pct: bajo -> solo 2/16 llegan a +20 (vol 1,2 -> 25%); alto
    -> 12/14 llegan a +20, los 2 fallos (vol 44,45) llevan drawdown real
    conocido (-15 y -25) para probar false_positive_report."""
    rows = []
    for vol in range(1, n + 1):
        if vol <= 2:
            advance = 25.0  # los 2 primeros "bajo" SÍ llegan a +20 (ruido real, no perfecto)
        elif vol >= 44:
            advance = 10.0  # los 2 últimos "alto" NO llegan a +20 -- son el falso positivo
        elif vol > 31:
            advance = 60.0  # resto de "alto": llega bien arriba (+50 también)
        else:
            advance = 5.0   # resto: no llega a +20
        drawdown = -15.0 if vol == 44 else (-25.0 if vol == 45 else -3.0)
        rows.append(_row(f"SIM{vol}", "2026-06-01", direction, timing, float(vol), advance, drawdown))
    return rows


def test_compute_reference_table_agrupa_por_direction_y_timing():
    rows = _make_controlled_group() + [
        _row("X", "2026-06-01", "BAJISTA", "demasiado_tarde", 10.0, 5.0),
    ]
    table = hs.compute_reference_table(rows, ["volatility_14d_pct"], min_rows=30)
    assert set(table.keys()) == {("ALCISTA", "expansion_temprana"), ("BAJISTA", "demasiado_tarde")}


def test_compute_reference_table_sin_piso_de_muestra_da_cuts_none():
    rows = [_row(f"S{i}", "2026-06-01", "ALCISTA", "al_comienzo", float(i), 5.0) for i in range(10)]
    table = hs.compute_reference_table(rows, ["volatility_14d_pct"], min_rows=30)
    ref = table[("ALCISTA", "al_comienzo")]
    assert ref.cuts["volatility_14d_pct"] is None
    assert ref.buckets["poblacion_total"].n == 10


def test_score_candidate_bucket_alto_tiene_mejor_tasa_que_bajo():
    rows = _make_controlled_group()
    table = hs.compute_reference_table(rows, ["volatility_14d_pct"], min_rows=30)

    alto = hs.score_candidate(table, "ALCISTA", "expansion_temprana", {"volatility_14d_pct": 40.0})
    bajo = hs.score_candidate(table, "ALCISTA", "expansion_temprana", {"volatility_14d_pct": 3.0})

    assert alto["grupo_existe"] is True
    assert alto["bucket"] == "alto"
    assert alto["n"] == 14                      # vol 32..45
    assert alto["aciertos_20"] == 12             # todos menos vol 44,45
    assert alto["pct_20"] == round(100 * 12 / 14, 1)

    assert bajo["bucket"] == "bajo"
    assert bajo["n"] == 16                       # vol 1..16
    assert bajo["aciertos_20"] == 2               # solo vol 1,2
    assert alto["pct_20"] > bajo["pct_20"]        # la condición "alto" es realmente mejor, con evidencia


def test_score_candidate_grupo_inexistente_no_inventa_evidencia():
    table = hs.compute_reference_table(_make_controlled_group(), ["volatility_14d_pct"], min_rows=30)
    result = hs.score_candidate(table, "BAJISTA", "agotamiento", {"volatility_14d_pct": 5.0})
    assert result == {
        "direction": "BAJISTA", "timing_deteccion": "agotamiento", "grupo_existe": False,
        "bucket": None, "n": 0, "aciertos_20": 0, "aciertos_50": 0, "aciertos_100": 0,
        "pct_20": None, "pct_50": None, "pct_100": None,
    }


def test_false_positive_report_reporta_fallos_y_drawdown_real():
    rows = _make_controlled_group()
    table = hs.compute_reference_table(rows, ["volatility_14d_pct"], min_rows=30)
    # min_rows acá es el piso de muestra del BUCKET "alto" (14 filas), un
    # umbral propio y más chico que el piso de 30 usado para validar los
    # cortes de tercil del grupo completo (45 filas) -- dos preguntas
    # distintas: "¿el grupo tiene base para calcular cortes?" vs. "¿el
    # bucket alto tiene base para reportar su propia tasa de fallo?".
    report = hs.false_positive_report(table, rows, min_rows=10)

    assert len(report) == 1
    r = report[0]
    assert r["direction"] == "ALCISTA" and r["timing_deteccion"] == "expansion_temprana"
    assert r["n"] == 14
    assert r["aciertos_20"] == 12
    assert r["fallos_20"] == 2                    # vol 44 y 45
    assert r["pct_fallo_20"] == round(100 * 2 / 14, 1)
    assert r["n_con_drawdown_dato"] == 2
    assert r["max_drawdown_pct_promedio_en_fallos"] == round((-15.0 + -25.0) / 2, 1)  # -20.0


def test_false_positive_report_omite_grupos_sin_piso_de_muestra():
    rows = [_row(f"S{i}", "2026-06-01", "ALCISTA", "al_comienzo", float(i), 25.0) for i in range(10)]
    table = hs.compute_reference_table(rows, ["volatility_14d_pct"], min_rows=30)
    assert hs.false_positive_report(table, rows, min_rows=30) == []


def test_reutiliza_min_prior_rows_for_cuts_de_experiments():
    assert hs.MIN_PRIOR_ROWS_FOR_CUTS == exp.MIN_PRIOR_ROWS_FOR_CUTS


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
