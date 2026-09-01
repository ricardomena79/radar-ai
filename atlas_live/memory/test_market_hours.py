"""Pruebas unitarias de la detección de sesión de mercado, con horarios
sintéticos (no depende de la hora real ni de que el test corra en
premarket). Uso: `python -m atlas_live.memory.test_market_hours`"""

from datetime import datetime
from zoneinfo import ZoneInfo

from atlas_live.memory import market_hours as mh

ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


# 2026-08-03 es lunes; 2026-08-01 es sábado; 2026-08-02 es domingo.

def test_premarket() -> None:
    assert mh.get_session(_et(2026, 8, 3, 4, 0)) == "premarket"
    assert mh.get_session(_et(2026, 8, 3, 9, 29)) == "premarket"


def test_regular() -> None:
    assert mh.get_session(_et(2026, 8, 3, 9, 30)) == "regular"
    assert mh.get_session(_et(2026, 8, 3, 12, 0)) == "regular"
    assert mh.get_session(_et(2026, 8, 3, 15, 59)) == "regular"


def test_afterhours() -> None:
    assert mh.get_session(_et(2026, 8, 3, 16, 0)) == "afterhours"
    assert mh.get_session(_et(2026, 8, 3, 19, 59)) == "afterhours"


def test_overnight_reemplaza_al_viejo_closed_fuera_de_horario() -> None:
    # 2026-09-01, autorizado explícitamente: 20:00-04:00 ET en día hábil
    # ahora es "overnight" (antes era "closed") -- Mercado sigue
    # actualizándose vía Yahoo en esta ventana.
    assert mh.get_session(_et(2026, 8, 3, 20, 0)) == "overnight"
    assert mh.get_session(_et(2026, 8, 3, 2, 0)) == "overnight"
    assert mh.get_session(_et(2026, 8, 3, 3, 59)) == "overnight"


def test_closed_fin_de_semana() -> None:
    assert mh.get_session(_et(2026, 8, 1, 10, 0)) == "closed"  # sábado, en pleno horario regular
    assert mh.get_session(_et(2026, 8, 2, 6, 0)) == "closed"   # domingo, antes de las 20:00


# --- OVERNIGHT (2026-09-01, autorizado explícitamente) -----------------
# 2026-08-03 es lunes; 2026-08-06 es jueves; 2026-08-07 es viernes;
# 2026-08-08 es sábado; 2026-08-09 es domingo; 2026-08-10 es lunes.

def test_overnight_lunes_a_jueves() -> None:
    assert mh.get_session(_et(2026, 8, 4, 20, 0)) == "overnight"   # martes 20:00
    assert mh.get_session(_et(2026, 8, 4, 23, 59)) == "overnight"  # martes 23:59
    assert mh.get_session(_et(2026, 8, 5, 0, 0)) == "overnight"    # miércoles 00:00
    assert mh.get_session(_et(2026, 8, 5, 3, 59)) == "overnight"   # miércoles 03:59


def test_transicion_19_59_a_20_00_dia_habil() -> None:
    assert mh.get_session(_et(2026, 8, 3, 19, 59)) == "afterhours"
    assert mh.get_session(_et(2026, 8, 3, 20, 0)) == "overnight"


def test_transicion_23_59_a_00_00() -> None:
    assert mh.get_session(_et(2026, 8, 3, 23, 59)) == "overnight"
    assert mh.get_session(_et(2026, 8, 4, 0, 0)) == "overnight"


def test_transicion_03_59_a_04_00() -> None:
    assert mh.get_session(_et(2026, 8, 4, 3, 59)) == "overnight"
    assert mh.get_session(_et(2026, 8, 4, 4, 0)) == "premarket"


def test_viernes_20_00_es_cerrado_no_overnight() -> None:
    # 2026-08-07 es viernes -- nunca hay overnight hacia el sábado.
    assert mh.get_session(_et(2026, 8, 7, 19, 59)) == "afterhours"
    assert mh.get_session(_et(2026, 8, 7, 20, 0)) == "closed"
    assert mh.get_session(_et(2026, 8, 7, 23, 59)) == "closed"


def test_sabado_siempre_cerrado() -> None:
    # 2026-08-08 es sábado.
    assert mh.get_session(_et(2026, 8, 8, 0, 0)) == "closed"
    assert mh.get_session(_et(2026, 8, 8, 10, 0)) == "closed"
    assert mh.get_session(_et(2026, 8, 8, 23, 59)) == "closed"


def test_domingo_antes_de_20_00_cerrado_despues_overnight() -> None:
    # 2026-08-09 es domingo.
    assert mh.get_session(_et(2026, 8, 9, 19, 59)) == "closed"
    assert mh.get_session(_et(2026, 8, 9, 20, 0)) == "overnight"
    assert mh.get_session(_et(2026, 8, 9, 23, 0)) == "overnight"


def test_lunes_madrugada_es_cola_del_overnight_del_domingo() -> None:
    # 2026-08-10 es lunes -- 00:00-03:59 es la cola del overnight
    # iniciado el domingo 09/08 a las 20:00.
    assert mh.get_session(_et(2026, 8, 10, 0, 0)) == "overnight"
    assert mh.get_session(_et(2026, 8, 10, 3, 59)) == "overnight"
    assert mh.get_session(_et(2026, 8, 10, 4, 0)) == "premarket"


def test_market_date_no_cambia_con_overnight() -> None:
    # Mercado no usa market_date() en absoluto (confirmado por lectura de
    # market_view.py) -- este test confirma que la función en sí sigue
    # siendo pura fecha-de-calendario-ET, sin ninguna lógica nueva de
    # sesión, para que ese hecho quede protegido explícitamente.
    assert mh.market_date(_et(2026, 8, 3, 20, 0)) == "2026-08-03"    # lunes 20:00 (overnight)
    assert mh.market_date(_et(2026, 8, 4, 2, 0)) == "2026-08-04"     # martes 02:00 (overnight)
    assert mh.market_date(_et(2026, 8, 9, 21, 0)) == "2026-08-09"    # domingo 21:00 (overnight)


def test_seal_window() -> None:
    assert mh.is_seal_window(_et(2026, 8, 3, 9, 24)) is False
    assert mh.is_seal_window(_et(2026, 8, 3, 9, 25)) is True
    assert mh.is_seal_window(_et(2026, 8, 3, 9, 29)) is True
    assert mh.is_seal_window(_et(2026, 8, 3, 9, 30)) is False  # ya es apertura, no premarket
    assert mh.is_seal_window(_et(2026, 8, 1, 9, 27)) is False  # sábado, aunque la hora coincida


def test_market_date_usa_huso_horario_de_nueva_york() -> None:
    # 23:30 en Nueva York del lunes sigue siendo el lunes en NY, aunque en
    # UTC ya sea martes.
    assert mh.market_date(_et(2026, 8, 3, 23, 30)) == "2026-08-03"


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
        print(f"FALLÓ -- {len(fallos)}:")
        for nombre, motivo in fallos:
            print(f"  {nombre}: {motivo}")
        raise SystemExit(1)
    print("OK -- todas las pruebas de market_hours pasaron.")
