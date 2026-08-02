"""Prueba de regresión permanente del Clasificador de Resultado (Entregable Nº2).

Congela un conjunto de casos de referencia ("golden dataset") a partir de
la validación real de 73.123 filas sobre los 30 días de
`atlas_live/backtest/results_v1/` que cerró el Entregable 2. No depende de
que esos archivos sigan existiendo ni de volver a correr el escaneo
completo -- los valores exactos (symbol, fecha, `ground_truth_change_pct`,
`eligible`) quedan copiados una sola vez, acá, con su categoría esperada
ya verificada en ese momento.

Debe ejecutarse en cada modificación futura de `classifier.py`, para
garantizar que ningún cambio de lógica rompa en silencio el comportamiento
ya validado. Uso:

    python -m atlas_live.memory.test_classifier_golden

o, si el proyecto corre pytest sobre atlas_live en el futuro, la función
`test_classifier_golden()` sigue la misma convención de `atlas/tests/`
(función `test_*`, aserciones simples, sin fixtures de framework).

Los casos están agrupados en tres orígenes, cada uno documentado por qué
existe -- ningún valor de este archivo es inventado sin decirlo:

  1. REALES -- filas verdaderas de `results_v1/`, con sus valores exactos
     tal como se descargaron. Cubren las 14 detecciones reales conocidas,
     los 5 artefactos de datos sospechosos ya documentados en
     VALIDATION_RESULTS.md (clasifican como EXPLOSION hoy -- límite de
     alcance esperado, no un error, ver docstring de `classifier.py`), y
     una muestra de cada una de las 5 categorías.
  2. SINTÉTICOS DE BORDE -- filas mínimas construidas a propósito (no de
     mercado real) para fijar el comportamiento exacto en cada umbral de
     `classifier_config.json` (inclusive/exclusivo) y en el orden de
     prioridad documentado entre reglas (ej. FALSE_BREAKOUT gana sobre
     LOSER cuando `eligible=True`, aunque la pérdida sea fuerte).
  3. DATO FALTANTE -- una fila sin `ground_truth_change_pct`, para fijar
     que el clasificador descarta (devuelve `None`) en vez de inventar
     una categoría.
"""

from typing import Any, Dict, List, Optional, Tuple

from atlas_live.memory import classifier


def _row(ground_truth_change_pct: Optional[float], eligible: bool = False) -> Dict[str, Any]:
    return {
        "ground_truth_change_pct": ground_truth_change_pct,
        "explosive": {"eligible": eligible},
    }


# ---------------------------------------------------------------------------
# 1. REALES -- valores exactos de atlas_live/backtest/results_v1/*.json,
#    congelados el 2026-08-02 al cerrar el Entregable 2.
# ---------------------------------------------------------------------------

_REAL_DETECTIONS = [
    # (date, symbol, ground_truth_change_pct, eligible) -- las 14 detecciones
    # reales conocidas de Validación 1 (ver RADAR_EXPLOSIVO_V2.md).
    ("2026-06-22", "SAGT", 32.32322526870857, True),
    ("2026-06-23", "BLZE", 43.59605925794037, True),
    ("2026-06-23", "SOXS", 23.85040726163082, True),
    ("2026-06-23", "UVIX", 10.670731955522024, True),
    ("2026-06-24", "WEN", 25.55910297336141, True),
    ("2026-06-25", "SAGT", 19.00826543986847, True),
    ("2026-07-01", "SOXS", 19.135792148929543, True),
    ("2026-07-02", "YRD", 59.645237387389685, True),
    ("2026-07-02", "SOXS", 16.83937890401174, True),
    ("2026-07-07", "BJDX", 25.00000770749739, True),
    ("2026-07-09", "WRAP", 48.42766324231117, True),
    ("2026-07-13", "AGEN", 82.68656894873345, True),
    ("2026-07-30", "NUWE", 135.44972714002594, True),
    ("2026-07-30", "XRX", 32.19696480517455, True),
]

_REAL_ARTIFACTS = [
    # Artefactos de datos sospechosos (splits no ajustados / tickers
    # ilíquidos), ya documentados en VALIDATION_RESULTS.md. Clasifican como
    # EXPLOSION con la regla actual -- se congela ESE comportamiento actual,
    # no lo que "debería" pasar tras filtrarlos (eso es Entregable 3).
    ("2026-07-06", "ENFY", 409.99999417923425, False),
    ("2026-07-20", "ENFY", 258.82352780048, False),
    ("2026-07-21", "CCG", 2537.3873619897427, False),
    ("2026-07-21", "PRPL", 2141.3793748397484, False),
    ("2026-07-23", "FFAI", 9542.85701551304, False),
]

_REAL_SAMPLE_BY_CATEGORY = [
    # Muestra real de cada una de las otras 4 categorías, tomada al azar
    # (semilla fija) de la corrida completa de 73.123 filas del Entregable 2.
    ("FALSE_BREAKOUT", "2026-06-18", "SOXS", -19.5067210833685, True),
    ("FALSE_BREAKOUT", "2026-06-25", "GNOM", 2.1867300949329316, True),
    ("FALSE_BREAKOUT", "2026-06-29", "UVIX", -7.428567068917411, True),
    ("LOSER", "2026-07-07", "UUUU", -6.608574951596009, False),
    ("LOSER", "2026-06-26", "AMAT", -6.161672626426834, False),
    ("LOSER", "2026-07-13", "RBOT", -9.444446652023792, False),
    ("WEAK", "2026-07-27", "IWO", 0.8940381547610364, False),
    ("WEAK", "2026-06-22", "VCR", -1.8081201811748764, False),
    ("WEAK", "2026-06-23", "WNW", 1.7142840794154575, False),
    ("NORMAL", "2026-07-28", "TALO", -3.2554850277520573, False),
    ("NORMAL", "2026-06-25", "KD", -2.7927964771443796, False),
    ("NORMAL", "2026-07-16", "CHH", 2.0073349881170954, False),
]


def _build_golden_cases() -> List[Tuple[str, Dict[str, Any], Optional[str]]]:
    cases: List[Tuple[str, Dict[str, Any], Optional[str]]] = []

    for date, symbol, pct, eligible in _REAL_DETECTIONS:
        cases.append((f"real:{date}:{symbol}", _row(pct, eligible), "EXPLOSION"))

    for date, symbol, pct, eligible in _REAL_ARTIFACTS:
        cases.append((f"real:{date}:{symbol}:artefacto_sospechoso", _row(pct, eligible), "EXPLOSION"))

    for expected, date, symbol, pct, eligible in _REAL_SAMPLE_BY_CATEGORY:
        cases.append((f"real:{date}:{symbol}", _row(pct, eligible), expected))

    # -----------------------------------------------------------------
    # 2. SINTÉTICOS DE BORDE -- fijan el comportamiento exacto de cada
    #    umbral de classifier_config.json (por defecto: explosion=10.0,
    #    false_breakout_ceiling=5.0, loser=-5.0, weak_ceiling=2.0).
    # -----------------------------------------------------------------
    cases += [
        ("sintético:borde_explosion_inclusivo", _row(10.0, False), "EXPLOSION"),
        ("sintético:justo_bajo_explosion_no_elegible", _row(9.9, False), "NORMAL"),
        ("sintético:borde_false_breakout_exclusivo", _row(5.0, True), "NORMAL"),
        ("sintético:justo_bajo_false_breakout_elegible", _row(4.9, True), "FALSE_BREAKOUT"),
        ("sintético:borde_loser_inclusivo", _row(-5.0, False), "LOSER"),
        ("sintético:justo_sobre_loser_no_elegible", _row(-4.9, False), "NORMAL"),
        ("sintético:justo_bajo_weak_ceiling_positivo", _row(1.9, False), "WEAK"),
        ("sintético:borde_weak_ceiling_exclusivo", _row(2.0, False), "NORMAL"),
        # Orden de prioridad documentado: FALSE_BREAKOUT gana sobre LOSER
        # cuando eligible=True, aunque el resultado sea una pérdida fuerte.
        ("sintético:false_breakout_gana_sobre_loser", _row(-5.0, True), "FALSE_BREAKOUT"),
    ]

    # -----------------------------------------------------------------
    # 3. DATO FALTANTE -- nunca se inventa una categoría.
    # -----------------------------------------------------------------
    cases.append(("sintético:sin_ground_truth", _row(None, False), None))

    return cases


GOLDEN_CASES = _build_golden_cases()


def run_golden_tests(config: Optional[Dict[str, float]] = None) -> List[Tuple[str, Optional[str], Optional[str]]]:
    """Corre todos los casos congelados. Devuelve la lista de fallos como
    (id_del_caso, esperado, obtenido) -- vacía si todo pasó."""
    fallos = []
    for case_id, row, expected in GOLDEN_CASES:
        obtenido = classifier.classify_observation(row, config)
        if obtenido != expected:
            fallos.append((case_id, expected, obtenido))
    return fallos


def test_classifier_golden() -> None:
    fallos = run_golden_tests()
    assert not fallos, f"El Clasificador rompió {len(fallos)} caso(s) congelado(s): {fallos}"


if __name__ == "__main__":
    fallos = run_golden_tests()
    print(f"Casos congelados: {len(GOLDEN_CASES)}")
    if fallos:
        print(f"FALLÓ -- {len(fallos)} caso(s) no coinciden con lo congelado:")
        for case_id, expected, obtenido in fallos:
            print(f"  {case_id}: esperado={expected!r} obtenido={obtenido!r}")
        raise SystemExit(1)
    print("OK -- todos los casos congelados coinciden con el comportamiento validado del Clasificador.")
