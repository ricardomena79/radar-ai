"""Tests de `live_experience_scoring.py` (2026-08-25, Fase 1/5). DB temporal
para los tests que ejercitan el JOIN/WHERE real de `_load_rows_from_db()`
(casos A/C) -- el resto (agrupación, baseline, win-rate, Wilson CI, lift,
leakage temporal, muestra insuficiente) se prueba con `rows` sintéticas
puras, sin tocar ninguna base de datos, mismo estilo que
`test_historical_scoring.py`. Sin red en ningún test."""

import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.learning import live_experience_scoring as les
from atlas_live.radar import candidate_registry as reg

_ORIG_DB_PATH = reg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_les_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None
    les.DB_PATH = reg.DB_PATH  # live_experience_scoring importó el mismo valor -- se re-sincroniza


def _restore():
    reg.DB_PATH = _ORIG_DB_PATH
    les.DB_PATH = _ORIG_DB_PATH


def _seed(ticker, market_date, direction, timing, volatility_14d_pct, max_advance_pct,
          confiable=True, is_final=True, dollar_volume=100_000.0):
    reg.record_detection(
        ticker, market_date, "regular", f"{market_date}T14:00:00Z", "s1",
        10.0, 5.0, 10000, 5000, 2.0, dollar_volume, [{"name": "cambio_de_precio", "reason": "x", "value": 5.0}],
    )
    reg.set_phase_tag(ticker, market_date, timing, direction_at_detection=direction)
    reg.set_experimental_signals(ticker, market_date, volatility_14d_pct=volatility_14d_pct)
    reached_20 = max_advance_pct >= 20
    reached_50 = max_advance_pct >= 50
    reached_100 = max_advance_pct >= 100
    reg.record_outcome(
        ticker, market_date, 0.0, 10.0 * (1 + max_advance_pct / 100), max_advance_pct, 30.0,
        reached_20, reached_50, reached_100, "EXPLOSION",
        confiable_para_aprendizaje=confiable, is_final=is_final,
    )


def _row(direction="ALCISTA", timing="al_comienzo", volatility_14d_pct=5.0, max_advance_pct=25.0,
         market_date="2026-08-01"):
    return {"direction": direction, "timing_deteccion": timing, "volatility_14d_pct": volatility_14d_pct,
            "max_advance_pct": max_advance_pct, "market_date": market_date}


# ---------------------------------------------------------------------------
# Casos A/C -- requieren el JOIN/WHERE real de _load_rows_from_db()
# ---------------------------------------------------------------------------

def test_A_solo_usa_outcomes_finales():
    _fresh()
    try:
        _seed("FINAL1", "2026-08-01", "ALCISTA", "al_comienzo", 5.0, 30.0, is_final=True)
        _seed("ENCURSO1", "2026-08-01", "ALCISTA", "al_comienzo", 5.0, 90.0, is_final=False)
        rows = les._load_rows_from_db("2026-08-02")
        tickers = {r["ticker"] for r in rows}
        assert "FINAL1" in tickers
        assert "ENCURSO1" not in tickers
    finally:
        _restore()


def test_C_excluye_experiencias_no_confiables():
    _fresh()
    try:
        _seed("CONFIABLE1", "2026-08-01", "ALCISTA", "al_comienzo", 5.0, 30.0, confiable=True)
        _seed("SOSPECHOSO1", "2026-08-01", "ALCISTA", "al_comienzo", 5.0, 90.0, confiable=False)
        rows = les._load_rows_from_db("2026-08-02")
        tickers = {r["ticker"] for r in rows}
        assert "CONFIABLE1" in tickers
        assert "SOSPECHOSO1" not in tickers
    finally:
        _restore()


def test_load_rows_mapea_columnas_correctamente():
    _fresh()
    try:
        _seed("MAP1", "2026-08-01", "BAJISTA", "recorrido_significativo_ya_hecho", 12.3, 45.6)
        rows = les._load_rows_from_db("2026-08-02")
        assert len(rows) == 1
        r = rows[0]
        assert r["direction"] == "BAJISTA"
        assert r["timing_deteccion"] == "recorrido_significativo_ya_hecho"
        assert r["volatility_14d_pct"] == 12.3
        assert r["max_advance_pct"] == 45.6
        assert r["market_date"] == "2026-08-01"
    finally:
        _restore()


# ---------------------------------------------------------------------------
# Resto -- rows sintéticas, puras, sin DB
# ---------------------------------------------------------------------------

def test_B_excluye_fechas_futuras_o_iguales_a_as_of_date():
    rows = [
        _row(market_date="2026-08-20", max_advance_pct=30.0),   # pasado -- entra
        _row(market_date="2026-08-24", max_advance_pct=90.0),   # == as_of_date -- excluida
        _row(market_date="2026-08-25", max_advance_pct=90.0),   # futuro -- excluida
    ]
    salida = les.compute_own_experience_table("2026-08-24", rows=rows, min_rows=1)
    n_total = sum(f["n_evaluables"] for f in salida)
    assert n_total == 1  # solo la fila del 08-20 contribuye


def test_7_prueba_explicita_experiencia_futura_no_entra_en_conocimiento_pasado():
    """Caso explícito pedido: una experiencia de mañana no puede filtrarse
    en el conocimiento calculado para una fecha anterior, aunque venga
    mezclada en la misma lista de entrada."""
    rows_pasado_solo = [_row(market_date="2026-08-20", max_advance_pct=10.0 + i) for i in range(35)]
    rows_con_futuro = rows_pasado_solo + [
        _row(market_date="2026-08-25", max_advance_pct=999.0),  # "mañana" respecto al as_of_date de abajo
    ]
    salida_sin_futuro = les.compute_own_experience_table("2026-08-24", rows=rows_pasado_solo, min_rows=1)
    salida_con_futuro = les.compute_own_experience_table("2026-08-24", rows=rows_con_futuro, min_rows=1)

    def _sin_timestamp(salida):
        return [{k: v for k, v in f.items() if k != "computed_at"} for f in salida]

    # El resultado debe ser IDÉNTICO (salvo el timestamp de cómputo, que
    # varía entre dos llamadas separadas por diseño) -- la fila del futuro
    # nunca contribuye, ni a n, ni a pct_20, ni a ningún agregado.
    assert _sin_timestamp(salida_sin_futuro) == _sin_timestamp(salida_con_futuro)
    # "poblacion_total" es la única fila que representa el grupo completo sin
    # dividir por bucket -- las demás filas (alto/medio/bajo) son subconjuntos
    # de esa misma población, sumarlas todas duplicaría el conteo a propósito.
    poblacion = next(f for f in salida_con_futuro if f["bucket"] == "poblacion_total")
    assert poblacion["n_evaluables"] == 35  # nunca 36 -- la fila del 08-25 nunca entra


def test_D_calcula_baseline_correctamente():
    rows = (
        [_row(market_date="2026-08-01", direction="ALCISTA", max_advance_pct=25.0) for _ in range(6)]
        + [_row(market_date="2026-08-01", direction="ALCISTA", max_advance_pct=5.0) for _ in range(4)]
    )
    salida = les.compute_own_experience_table("2026-08-24", rows=rows, min_rows=1)
    # 6/10 >= 20% -> baseline 60.0%, igual en todas las filas de salida
    baselines = {f["baseline_pct_20"] for f in salida}
    assert baselines == {60.0}


def test_E_calcula_win_rate_correctamente():
    rows = (
        [_row(market_date="2026-08-01", direction="ALCISTA", timing="al_comienzo", max_advance_pct=25.0) for _ in range(3)]
        + [_row(market_date="2026-08-01", direction="ALCISTA", timing="al_comienzo", max_advance_pct=5.0) for _ in range(1)]
    )
    salida = les.compute_own_experience_table("2026-08-24", rows=rows, min_rows=1)
    poblacion = next(f for f in salida if f["direction"] == "ALCISTA" and f["bucket"] == "poblacion_total")
    assert poblacion["n_evaluables"] == 4
    assert poblacion["n_aciertos_20"] == 3
    assert poblacion["pct_20"] == 75.0


def test_F_calcula_wilson_ci_correctamente():
    """Contra la fórmula ya validada en candidate_registry -- mismo caso
    real usado en esa sesión: 4 aciertos de 5 da un intervalo ANCHO pese
    al 80% bruto (muestra chica)."""
    from atlas_live.radar.candidate_registry import wilson_confidence_interval

    rows = (
        [_row(market_date="2026-08-01", direction="ALCISTA", timing="al_comienzo", max_advance_pct=25.0) for _ in range(4)]
        + [_row(market_date="2026-08-01", direction="ALCISTA", timing="al_comienzo", max_advance_pct=5.0) for _ in range(1)]
    )
    salida = les.compute_own_experience_table("2026-08-24", rows=rows, min_rows=1)
    poblacion = next(f for f in salida if f["direction"] == "ALCISTA" and f["bucket"] == "poblacion_total")
    esperado_lower, esperado_upper = wilson_confidence_interval(4, 5)
    assert poblacion["wilson_lower_bound_20_pct"] == esperado_lower
    assert poblacion["wilson_upper_bound_20_pct"] == esperado_upper
    assert esperado_upper - esperado_lower > 30  # intervalo ancho, muestra chica


def test_G_calcula_lift_correctamente():
    # Baseline poblacional = sobre TODAS las filas (todos los grupos juntos):
    # 2 aciertos (BAJISTA) + 5 aciertos (ALCISTA) = 7 de 15 -> 46.67%.
    rows = (
        [_row(market_date="2026-08-01", direction="BAJISTA", timing="agotamiento", max_advance_pct=25.0) for _ in range(2)]
        + [_row(market_date="2026-08-01", direction="BAJISTA", timing="agotamiento", max_advance_pct=5.0) for _ in range(8)]
        # Grupo ALCISTA/al_comienzo: 5 filas, 5 aciertos -> 100%, muy por encima del baseline
        + [_row(market_date="2026-08-01", direction="ALCISTA", timing="al_comienzo", max_advance_pct=30.0) for _ in range(5)]
    )
    salida = les.compute_own_experience_table("2026-08-24", rows=rows, min_rows=1)
    fuerte = next(f for f in salida if f["direction"] == "ALCISTA" and f["bucket"] == "poblacion_total")
    assert fuerte["pct_20"] == 100.0
    assert fuerte["baseline_pct_20"] == round(100 * 7 / 15, 2)
    assert fuerte["lift_20"] == round(100.0 / fuerte["baseline_pct_20"], 3)


def test_H_agrupa_por_direction_timing_y_tercil():
    rows = [
        _row(market_date="2026-08-01", direction="ALCISTA", timing="al_comienzo", volatility_14d_pct=v, max_advance_pct=30.0)
        for v in range(1, 41)  # 40 valores distintos -> terciles bien definidos
    ] + [
        _row(market_date="2026-08-01", direction="BAJISTA", timing="agotamiento", volatility_14d_pct=v, max_advance_pct=10.0)
        for v in range(1, 41)
    ]
    salida = les.compute_own_experience_table("2026-08-24", rows=rows, feature_cols=("volatility_14d_pct",), min_rows=30)
    grupos = {(f["direction"], f["timing_deteccion"]) for f in salida}
    assert ("ALCISTA", "al_comienzo") in grupos
    assert ("BAJISTA", "agotamiento") in grupos
    buckets_alcista = {f["bucket"] for f in salida if f["direction"] == "ALCISTA"}
    assert {"alto", "medio", "bajo", "poblacion_total"} <= buckets_alcista  # terciles reales, no solo población


def test_I_muestra_insuficiente_queda_marcada_explicitamente():
    rows = [_row(market_date="2026-08-01", direction="NEUTRAL", timing="indeterminado", max_advance_pct=10.0) for _ in range(5)]
    salida = les.compute_own_experience_table("2026-08-24", rows=rows, min_rows=1)
    poblacion = next(f for f in salida if f["direction"] == "NEUTRAL")
    assert poblacion["n_evaluables"] == 5
    assert poblacion["validation_state"] == "MUESTRA_INSUFICIENTE"  # n=5 <= 99


def test_muestra_robusta_queda_marcada_explicitamente():
    rows = [_row(market_date="2026-08-01", direction="NEUTRAL", timing="indeterminado", max_advance_pct=10.0) for _ in range(600)]
    salida = les.compute_own_experience_table("2026-08-24", rows=rows, min_rows=1)
    poblacion = next(f for f in salida if f["direction"] == "NEUTRAL")
    assert poblacion["validation_state"] == "VALIDACION_ROBUSTA"


def test_sin_evidencia_no_produce_ninguna_fila():
    """Sin filas, no se inventa ningún grupo -- lista vacía, nunca un
    grupo con n=0 fabricado."""
    salida = les.compute_own_experience_table("2026-08-24", rows=[], min_rows=1)
    assert salida == []


def test_salida_es_extensible_incluye_50_y_100_sin_costo_adicional():
    rows = [_row(market_date="2026-08-01", direction="ALCISTA", timing="al_comienzo", max_advance_pct=60.0) for _ in range(5)]
    salida = les.compute_own_experience_table("2026-08-24", rows=rows, min_rows=1)
    poblacion = next(f for f in salida if f["bucket"] == "poblacion_total")
    assert poblacion["n_aciertos_50"] == 5
    assert poblacion["pct_50"] == 100.0
    assert poblacion["n_aciertos_100"] == 0
    assert poblacion["pct_100"] == 0.0


# ---------------------------------------------------------------------------
# Caso J -- no toca ningún módulo protegido
# ---------------------------------------------------------------------------

def test_J_no_importa_ningun_modulo_protegido():
    """Chequea IMPORTS reales, no la prosa del docstring -- el módulo SÍ
    nombra estos archivos en su docstring para explicar qué NO toca, a
    propósito (mismo criterio ya usado en otras fases de esta sesión)."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(les))
    modulos_importados = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modulos_importados.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modulos_importados.add(node.module)
            modulos_importados.update(alias.name for alias in node.names)

    for prohibido in ("candidate_gates", "priority_classifier", "decision_engine", "candidate_tracker"):
        assert not any(prohibido in m for m in modulos_importados), (
            f"live_experience_scoring.py no debe importar nada relacionado con {prohibido}"
        )


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
