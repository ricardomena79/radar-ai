"""PM-RVOL Fase 1 (2026-08-25) -- señales de volumen premarket, capa de
OBSERVABILIDAD pura, autorizada explícitamente por el usuario. Puro, sin
DB/red -- exactamente el mismo estilo que `test_candidate_gates.py`.

Deliberadamente NUNCA se llaman "RVOL": no usan `average_volume` en
absoluto (confirmado por test explícito abajo) -- `relative_volume`/
`MIN_RVOL`/`gate_relative_volume` quedan intactos, sin relación con estas
dos funciones nuevas.
"""

import inspect

from atlas_live.radar import candidate_gates as gates
from atlas_live.radar.sweep_history import SweepSnapshot


def _snap(volume, session="premarket", change_pct=0.0, price=10.0, relative_volume=None):
    return SweepSnapshot(
        sweep_id="s", observed_at="2026-08-25T09:00:00+00:00", price=price,
        change_pct=change_pct, volume=volume, average_volume=None,
        relative_volume=relative_volume, dollar_volume=(price * volume if volume is not None else None),
        session=session,
    )


# ---------------------------------------------------------------------------
# premarket_volume_percentile
# ---------------------------------------------------------------------------

def test_1_percentil_con_universo_sintetico_conocido():
    """100 símbolos con dollar_volume 1..100 -- un símbolo con valor 50
    debe caer exactamente en el percentil 50."""
    universo = [float(v) for v in range(1, 101)]
    r = gates.premarket_volume_percentile(50.0, universo, "premarket")
    assert r.validation_state == "VALID"
    assert r.value == 50.0


def test_2_percentil_con_simbolos_nulos_y_negativos_se_excluyen():
    universo = [10.0, None, -5.0, 20.0] * 60  # 240 entradas, solo 120 válidas (10.0/20.0 x60)
    r = gates.premarket_volume_percentile(15.0, universo, "premarket")
    assert r.validation_state == "VALID"
    # de los 120 válidos, la mitad (10.0 x60) es <= 15.0
    assert r.value == 50.0


def test_3_percentil_con_universo_insuficiente_da_none():
    universo = [float(v) for v in range(1, 50)]  # 49 < MIN_UNIVERSE_SIZE_FOR_PM_PERCENTILE (100)
    r = gates.premarket_volume_percentile(25.0, universo, "premarket")
    assert r.value is None
    assert r.validation_state == "INSUFFICIENT_UNIVERSE"


def test_4_simbolo_en_percentil_alto():
    universo = [float(v) for v in range(1, 101)]
    r = gates.premarket_volume_percentile(99.0, universo, "premarket")
    assert r.validation_state == "VALID"
    assert r.value >= 95.0


def test_5_simbolo_en_percentil_bajo():
    universo = [float(v) for v in range(1, 101)]
    r = gates.premarket_volume_percentile(1.0, universo, "premarket")
    assert r.validation_state == "VALID"
    assert r.value <= 5.0


def test_percentil_sin_dato_propio_da_no_data():
    universo = [float(v) for v in range(1, 101)]
    r = gates.premarket_volume_percentile(None, universo, "premarket")
    assert r.value is None
    assert r.validation_state == "NO_DATA"


def test_percentil_dollar_volume_negativo_da_no_data():
    universo = [float(v) for v in range(1, 101)]
    r = gates.premarket_volume_percentile(-10.0, universo, "premarket")
    assert r.value is None
    assert r.validation_state == "NO_DATA"


def test_11_percentil_fuera_de_premarket_no_aplica():
    universo = [float(v) for v in range(1, 101)]
    r = gates.premarket_volume_percentile(50.0, universo, "regular")
    assert r.value is None
    assert r.validation_state == "NOT_PREMARKET"


def test_percentil_nunca_divide_por_cero_universo_vacio():
    r = gates.premarket_volume_percentile(50.0, [], "premarket")
    assert r.value is None
    assert r.validation_state == "INSUFFICIENT_UNIVERSE"


# ---------------------------------------------------------------------------
# premarket_volume_acceleration
# ---------------------------------------------------------------------------

def test_6_aceleracion_con_dos_ventanas_validas():
    """K=4 (default): ventana previa = 1000 acciones (500->1500), ventana
    reciente = 2000 acciones (1500->3500) -- aceleración x2."""
    history = [_snap(500 + i * 250) for i in range(8)]  # 500,750,...,2250 -- 8 snapshots
    current = _snap(2250 + 2000)  # 4250
    r = gates.premarket_volume_acceleration(current, history, "premarket")
    assert r.validation_state == "VALID"
    assert r.value is not None and r.value > 0


def test_7_aceleracion_sin_historia_suficiente():
    history = [_snap(100 + i * 10) for i in range(3)]  # < 2*K=8
    current = _snap(500)
    r = gates.premarket_volume_acceleration(current, history, "premarket")
    assert r.value is None
    assert r.validation_state == "INSUFFICIENT_HISTORY"


def test_8_aceleracion_con_volumen_previo_bajo_el_piso():
    """Ejemplo literal del pedido: 2 -> 4 acciones -- vol_previo=2, muy
    por debajo de MIN_SHARES_PRIOR_WINDOW (500)."""
    history = [_snap(v) for v in [0, 0, 0, 0, 2, 2, 2, 2]]  # ref_previo=0 (idx0), ref_reciente=2 (idx4)
    current = _snap(4)
    r = gates.premarket_volume_acceleration(current, history, "premarket")
    assert r.value is None
    assert r.validation_state == "INSUFFICIENT_VOLUME"


def test_9_sesiones_mezcladas_ignora_sesiones_anteriores():
    """8 snapshots de 'regular' (que solos alcanzarían para 2*K) + solo 2
    de 'premarket' -- deben filtrarse, y quedar con historial insuficiente
    para la sesión premarket actual."""
    history = [_snap(100 + i * 50, session="regular") for i in range(8)] + [
        _snap(100, session="premarket"), _snap(150, session="premarket"),
    ]
    current = _snap(300, session="premarket")
    r = gates.premarket_volume_acceleration(current, history, "premarket")
    assert r.validation_state == "INSUFFICIENT_HISTORY"  # solo 2 de la sesión correcta, no 8


def test_10_premarket_da_senal_valida_con_suficiente_evidencia():
    history = [_snap(500 + i * 500, session="premarket") for i in range(8)]
    current = _snap(500 + 8 * 500 + 3000, session="premarket")
    r = gates.premarket_volume_acceleration(current, history, "premarket")
    assert r.validation_state == "VALID"


def test_11b_aceleracion_fuera_de_premarket_no_aplica():
    history = [_snap(500 + i * 500) for i in range(8)]
    current = _snap(9000)
    r = gates.premarket_volume_acceleration(current, history, "regular")
    assert r.value is None
    assert r.validation_state == "NOT_PREMARKET"


def test_12_nunca_divide_por_cero_volumen_previo_cero():
    """vol_previo=0 exacto (sin piso, caería en división por cero si no
    se guardara) -- debe interceptarse como INSUFFICIENT_VOLUME antes de
    llegar a dividir."""
    history = [_snap(100) for _ in range(8)]  # volumen constante -> vol_previo=0
    current = _snap(100)
    r = gates.premarket_volume_acceleration(current, history, "premarket")
    assert r.value is None
    assert r.validation_state == "INSUFFICIENT_VOLUME"  # nunca ZeroDivisionError


def test_13_nunca_produce_valores_infinitos():
    """vol_previo=2000 (>= piso, VALID) y un salto reciente enorme --
    el cociente debe ser un número grande pero finito, nunca inf/NaN."""
    history = [_snap(500 + i * 500) for i in range(8)]  # 500..4000, vol_previo=2500-500=2000
    current = _snap(10_000_000)
    r = gates.premarket_volume_acceleration(current, history, "premarket")
    assert r.validation_state == "VALID"
    assert r.value not in (float("inf"), float("-inf"))
    assert r.value == r.value  # nunca NaN
    assert r.value > 1000  # aceleración real, enorme pero finita


def test_aceleracion_volumen_retrocede_da_no_data_nunca_inventa():
    """El volumen acumulado NUNCA debería bajar dentro del mismo día --
    si el proveedor entrega un dato inconsistente, se declara NO_DATA,
    nunca se inventa una aceleración (ni positiva ni negativa) con él."""
    # ref_previo (idx0)=2000, ref_reciente (idx4)=1000 -- retrocede exactamente
    # en los 2 puntos que la función compara (idx0 vs idx4).
    history = [_snap(v) for v in [2000, 2000, 2000, 2000, 1000, 1000, 1000, 1000]]
    current = _snap(1500)
    r = gates.premarket_volume_acceleration(current, history, "premarket")
    assert r.value is None
    assert r.validation_state == "NO_DATA"


def test_aceleracion_sin_current_volume_da_no_data():
    history = [_snap(500 + i * 500) for i in range(8)]
    current = _snap(None)
    r = gates.premarket_volume_acceleration(current, history, "premarket")
    assert r.value is None
    assert r.validation_state == "NO_DATA"


# ---------------------------------------------------------------------------
# Naming / pureza (punto 8 del pedido -- nunca "RVOL")
# ---------------------------------------------------------------------------

def test_ninguna_de_las_2_senales_usa_average_volume():
    """Chequea el CÓDIGO (nunca `.average_volume`/`.relative_volume` como
    acceso a atributo) -- las docstrings sí nombran esas palabras en
    prosa, a propósito, para documentar justamente que no se usan."""
    src_pct = inspect.getsource(gates.premarket_volume_percentile)
    src_acc = inspect.getsource(gates.premarket_volume_acceleration)
    assert ".average_volume" not in src_pct
    assert ".average_volume" not in src_acc
    assert ".relative_volume" not in src_pct
    assert ".relative_volume" not in src_acc


def test_todos_los_estados_de_validacion_estan_declarados():
    assert set(gates.PM_VALIDATION_STATES) == {
        "VALID", "INSUFFICIENT_UNIVERSE", "INSUFFICIENT_HISTORY",
        "INSUFFICIENT_VOLUME", "NOT_PREMARKET", "NO_DATA",
    }


def test_pm_signals_nunca_estan_en_all_gates():
    """No se convirtieron en gates -- ALL_GATES debe seguir teniendo
    exactamente las 7 puertas de siempre, ninguna nueva."""
    assert len(gates.ALL_GATES) == 7
    assert gates.premarket_volume_percentile not in gates.ALL_GATES
    assert gates.premarket_volume_acceleration not in gates.ALL_GATES
