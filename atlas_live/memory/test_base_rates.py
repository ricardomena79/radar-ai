"""Pruebas unitarias del motor de tasas base (Entregable Nº4), con datos
100% sintéticos -- NUNCA toca el Memory Store real ni `store.py`. Se
corren antes de conectar el motor a la base real poblada por el
Entregable 3 (ver criterio explícito del plan: no ejecutar el análisis
sobre datos reales hasta que el backfill termine).

Uso: `python -m atlas_live.memory.test_base_rates`
"""

from typing import Any, Dict, List

from atlas_live.memory import base_rates as br


def _obs(date: str, category: str, relative_volume: float = 0.0, sector: str = "Technology") -> Dict[str, Any]:
    return {
        "date": date,
        "category": category,
        "relative_volume": relative_volume,
        "sector": sector,
        "market_cap_bucket": None,
        "session": "regular",
    }


# ---------------------------------------------------------------------------
# 1. _wilson_lower_bound -- propiedades conocidas de la fórmula
# ---------------------------------------------------------------------------

def test_wilson_lower_bound_n_cero() -> None:
    assert br._wilson_lower_bound(0, 0) == 0.0


def test_wilson_lower_bound_cero_exitos() -> None:
    assert br._wilson_lower_bound(0, 100) == 0.0


def test_wilson_lower_bound_menor_que_estimador_puntual() -> None:
    # Con éxito parcial (ni 0 ni 100%), el límite inferior siempre es
    # menor que la proporción observada -- es la esencia del intervalo.
    lower = br._wilson_lower_bound(80, 100)
    assert 0.0 < lower < 0.80


def test_wilson_lower_bound_monotono_en_exitos() -> None:
    # A igual n, más éxitos -> límite inferior mayor.
    lower_50 = br._wilson_lower_bound(50, 100)
    lower_80 = br._wilson_lower_bound(80, 100)
    assert lower_80 > lower_50


def test_wilson_lower_bound_mas_muestra_acerca_al_estimador() -> None:
    # Misma proporción (80%), más muestra -> intervalo más angosto ->
    # límite inferior más cerca del 0.80 observado.
    lower_n20 = br._wilson_lower_bound(16, 20)
    lower_n2000 = br._wilson_lower_bound(1600, 2000)
    assert lower_n2000 > lower_n20
    assert abs(0.80 - lower_n2000) < abs(0.80 - lower_n20)


# ---------------------------------------------------------------------------
# 2. Condition.matches
# ---------------------------------------------------------------------------

def test_condition_rango_de_metrica() -> None:
    cond = br.Condition(metric_ranges={"relative_volume": (5.0, None)}, label="rvol>=5")
    assert cond.matches(_obs("2026-01-01", "EXPLOSION", relative_volume=8.0))
    assert not cond.matches(_obs("2026-01-01", "NORMAL", relative_volume=1.0))


def test_condition_metrica_faltante_no_matchea() -> None:
    cond = br.Condition(metric_ranges={"gap_pct": (2.0, None)}, label="gap>=2")
    obs = _obs("2026-01-01", "NORMAL")
    obs["gap_pct"] = None
    assert not cond.matches(obs)


def test_condition_sector_y_session() -> None:
    cond = br.Condition(sector="Technology", session="regular", label="tech-regular")
    assert cond.matches(_obs("2026-01-01", "EXPLOSION"))
    obs_otro_sector = _obs("2026-01-01", "EXPLOSION", sector="Energy")
    assert not cond.matches(obs_otro_sector)


def test_condition_sin_restricciones_matchea_todo() -> None:
    cond = br.Condition(label="todo")
    assert cond.matches(_obs("2026-01-01", "LOSER", relative_volume=999))


# ---------------------------------------------------------------------------
# 3. compute_population_base_rate
# ---------------------------------------------------------------------------

def test_population_base_rate() -> None:
    obs = [_obs("2026-01-01", "EXPLOSION")] * 3 + [_obs("2026-01-01", "NORMAL")] * 7
    assert br.compute_population_base_rate(obs, "EXPLOSION") == 0.3


def test_population_base_rate_lista_vacia() -> None:
    assert br.compute_population_base_rate([], "EXPLOSION") == 0.0


# ---------------------------------------------------------------------------
# 4. compute_base_rate -- las tres condiciones de confiabilidad
# ---------------------------------------------------------------------------

def _poblacion_sintetica() -> List[Dict[str, Any]]:
    """100 observaciones, 4 fechas (25/día). 20 tienen relative_volume=8.0
    (5 por fecha) y SIEMPRE son EXPLOSION -- señal real y fuerte. De las
    80 restantes (relative_volume=0.5), 4 son EXPLOSION (ruido de fondo) y
    76 son NORMAL. Tasa base poblacional de EXPLOSION = 24/100 = 0.24."""
    fechas = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
    obs: List[Dict[str, Any]] = []
    for fecha in fechas:
        for _ in range(5):
            obs.append(_obs(fecha, "EXPLOSION", relative_volume=8.0))
        for _ in range(1):
            obs.append(_obs(fecha, "EXPLOSION", relative_volume=0.5))
        for _ in range(19):
            obs.append(_obs(fecha, "NORMAL", relative_volume=0.5))
    assert len(obs) == 100
    return obs


def test_compute_base_rate_confiable() -> None:
    poblacion = _poblacion_sintetica()
    condicion = br.Condition(metric_ranges={"relative_volume": (5.0, None)}, label="rvol>=5")
    resultado = br.compute_base_rate(poblacion, condicion, "EXPLOSION")

    assert resultado.sample_size == 20
    assert resultado.successes == 20
    assert resultado.win_rate == 1.0
    assert resultado.baseline_win_rate == 0.24
    assert resultado.wilson_lower_bound is not None and resultado.wilson_lower_bound > resultado.baseline_win_rate
    assert resultado.recent_sample_size == 10  # mitad más reciente de las 4 fechas: 2 fechas x 5
    assert resultado.reliable is True, resultado.reason


def test_compute_base_rate_muestra_insuficiente() -> None:
    poblacion = _poblacion_sintetica()
    # Condición que matchea muy pocas filas (menos de MIN_SAMPLE_SIZE).
    condicion = br.Condition(metric_ranges={"relative_volume": (7.99, 8.01)}, label="rvol≈8, un solo día")
    # Restringimos la población a una sola fecha para forzar sample_size < 10.
    poblacion_chica = [o for o in poblacion if o["date"] == "2026-01-05"]
    resultado = br.compute_base_rate(poblacion_chica, condicion, "EXPLOSION")

    assert resultado.sample_size == 5
    assert resultado.reliable is False
    assert "insuficiente" in resultado.reason.lower()


def test_compute_base_rate_no_supera_baseline() -> None:
    poblacion = _poblacion_sintetica()
    # Condición sin filtro real (matchea toda la población) -- por diseño
    # su win_rate es idéntico al baseline poblacional, así que el límite
    # inferior de Wilson nunca puede superarlo.
    condicion = br.Condition(label="sin filtro (matchea todo)")
    resultado = br.compute_base_rate(poblacion, condicion, "EXPLOSION")

    assert resultado.sample_size == 100
    assert resultado.reliable is False
    assert "baseline" in resultado.reason.lower()


def test_compute_base_rate_falla_consistencia_temporal() -> None:
    # 15 observaciones, todas EXPLOSION, todas en la MISMA fecha -- señal
    # fuerte y muestra suficiente, pero concentrada en una sola ventana.
    obs_un_dia = [_obs("2026-02-01", "EXPLOSION", relative_volume=9.0) for _ in range(15)]
    obs_un_dia += [_obs("2026-02-01", "NORMAL", relative_volume=0.5) for _ in range(85)]
    condicion = br.Condition(metric_ranges={"relative_volume": (5.0, None)}, label="rvol>=5, un solo día")
    resultado = br.compute_base_rate(obs_un_dia, condicion, "EXPLOSION")

    assert resultado.sample_size == 15
    assert resultado.reliable is False
    assert "una sola fecha" in resultado.reason.lower() or "ventana temporal" in resultado.reason.lower()


def test_compute_base_rate_sobre_base_vacia_nunca_inventa_resultado() -> None:
    resultado = br.compute_base_rate([], br.Condition(label="vacío"), "EXPLOSION")
    assert resultado.sample_size == 0
    assert resultado.reliable is False
    assert resultado.win_rate is None


def test_compute_base_rates_for_conditions_usa_mismo_baseline() -> None:
    poblacion = _poblacion_sintetica()
    condiciones = [
        br.Condition(metric_ranges={"relative_volume": (5.0, None)}, label="rvol>=5"),
        br.Condition(label="sin filtro"),
    ]
    resultados = br.compute_base_rates_for_conditions(poblacion, condiciones, "EXPLOSION")
    assert len(resultados) == 2
    assert resultados[0].baseline_win_rate == resultados[1].baseline_win_rate == 0.24


ALL_TESTS = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]


if __name__ == "__main__":
    fallos = []
    for test_fn in ALL_TESTS:
        try:
            test_fn()
        except AssertionError as exc:
            fallos.append((test_fn.__name__, str(exc)))
    print(f"Pruebas corridas: {len(ALL_TESTS)}")
    if fallos:
        print(f"FALLÓ -- {len(fallos)} prueba(s):")
        for nombre, motivo in fallos:
            print(f"  {nombre}: {motivo}")
        raise SystemExit(1)
    print("OK -- todas las pruebas unitarias sintéticas del motor de tasas base pasaron.")
