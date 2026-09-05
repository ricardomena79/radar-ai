"""Hito 6, Fase 6.2 (2026-09-04, autorizado explícitamente): tests de
`classify_possible_split()` -- heurística defensiva, nunca consumida por
`candidate_gates.py`. Los 5 casos obligatorios pedidos por el usuario +
los de calibración documentados en el propio módulo."""

from atlas.data.price_integrity import POSSIBLE_SPLIT_FLAG, classify_possible_split

# --- 1) casos sintéticos de splits/corporate actions conocidos -> deben marcarse --

def test_split_2_a_1_forward_con_movimiento_genuino_residual_se_marca():
    # previous_close=100, post-split "justo" = 50, +2% de movimiento real encima = 51.0
    flag, ratio = classify_possible_split(previous_close=100.0, last_price=51.0, change_percent=-49.0)
    assert flag == POSSIBLE_SPLIT_FLAG
    assert abs(ratio - 0.51) < 1e-9


def test_reverse_split_1_a_10_se_marca():
    # previous_close=2.70, post-split "justo" = 27.0, con residual real -> 27.50
    flag, ratio = classify_possible_split(previous_close=2.70, last_price=27.50, change_percent=918.5)
    assert flag == POSSIBLE_SPLIT_FLAG
    # ratio real = 27.50/2.70 = 10.185185... -- classify_possible_split()
    # redondea a 4 decimales a propósito (ver docstring), por eso la
    # tolerancia acá es la del redondeo, no una comparación exacta.
    assert abs(ratio - (27.50 / 2.70)) < 1e-4


def test_split_3_a_1_forward_se_marca():
    # previous_close=90, post-split "justo" = 30, con residual real -> 30.6
    flag, ratio = classify_possible_split(previous_close=90.0, last_price=30.6, change_percent=-66.0)
    assert flag == POSSIBLE_SPLIT_FLAG
    assert abs(ratio - (30.6 / 90.0)) < 1e-9


def test_split_exacto_sin_residual_se_marca():
    # Caso "de libro": ratio exactamente 4.0 (reverse split 1:4).
    flag, ratio = classify_possible_split(previous_close=5.0, last_price=20.0, change_percent=300.0)
    assert flag == POSSIBLE_SPLIT_FLAG
    assert ratio == 4.0


# --- 2) movimientos genuinos extremos, incluido un caso tipo MRNA +170% -> NO deben marcarse --

def test_caso_real_mrna_maximo_170_pct_no_se_marca():
    # Datos reales ya persistidos: price_at_detection=$65.605,
    # max_return_after_detection_pct=170.6% -> ratio=2.706, a 9.8% del
    # ratio limpio más cercano (3.0) -- muy por fuera del 4% de tolerancia.
    previous_close = 65.605
    last_price = previous_close * 2.706
    flag, ratio = classify_possible_split(previous_close, last_price, change_percent=170.6)
    assert flag is None
    assert ratio is None


def test_caso_real_mrna_cambio_de_deteccion_49_91_pct_no_se_marca():
    # total_day_change_pct=49.91% real -> ratio≈1.4989, sin ningún ratio
    # limpio cercano en la lista (3:2 queda fuera a propósito).
    previous_close = 65.605
    last_price = previous_close * 1.4991
    flag, ratio = classify_possible_split(previous_close, last_price, change_percent=49.91)
    assert flag is None
    assert ratio is None


def test_movimiento_genuino_grande_pero_lejos_de_cualquier_ratio_limpio_no_se_marca():
    # +80% genuino -> ratio=1.8, a 10% de 2.0 -- fuera de tolerancia.
    flag, ratio = classify_possible_split(previous_close=50.0, last_price=90.0, change_percent=80.0)
    assert flag is None
    assert ratio is None


# --- 3) casos normales -> no marcar --

def test_movimiento_normal_pequeno_no_se_marca():
    flag, ratio = classify_possible_split(previous_close=50.0, last_price=54.0, change_percent=8.0)
    assert flag is None
    assert ratio is None


def test_movimiento_extremo_pero_por_debajo_del_piso_no_se_marca():
    # 35% está por debajo de EXTREME_CHANGE_PCT_THRESHOLD=40.0 -- ni se
    # calcula el ratio.
    flag, ratio = classify_possible_split(previous_close=100.0, last_price=135.0, change_percent=35.0)
    assert flag is None
    assert ratio is None


# --- 4) datos faltantes/None/0 -> comportamiento seguro --

def test_previous_close_none_no_se_marca():
    flag, ratio = classify_possible_split(previous_close=None, last_price=51.0, change_percent=-49.0)
    assert flag is None
    assert ratio is None


def test_last_price_none_no_se_marca():
    flag, ratio = classify_possible_split(previous_close=100.0, last_price=None, change_percent=-49.0)
    assert flag is None
    assert ratio is None


def test_change_percent_none_no_se_marca():
    flag, ratio = classify_possible_split(previous_close=100.0, last_price=51.0, change_percent=None)
    assert flag is None
    assert ratio is None


def test_previous_close_cero_no_se_marca_ni_divide_por_cero():
    flag, ratio = classify_possible_split(previous_close=0.0, last_price=51.0, change_percent=-49.0)
    assert flag is None
    assert ratio is None


def test_previous_close_negativo_no_se_marca():
    flag, ratio = classify_possible_split(previous_close=-10.0, last_price=51.0, change_percent=-49.0)
    assert flag is None
    assert ratio is None


def test_last_price_cero_no_se_marca():
    flag, ratio = classify_possible_split(previous_close=100.0, last_price=0.0, change_percent=-100.0)
    assert flag is None
    assert ratio is None


def test_price_is_stale_nunca_se_marca_aunque_el_resto_luzca_como_split():
    # Guard explícito: aunque los números por sí solos calificarían como
    # split (ratio=0.5, cambio=-50%), price_is_stale=True siempre gana --
    # un dato stale nunca es evidencia de un split real.
    flag, ratio = classify_possible_split(previous_close=100.0, last_price=50.0, change_percent=-50.0, price_is_stale=True)
    assert flag is None
    assert ratio is None
