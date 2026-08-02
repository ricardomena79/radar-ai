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


def test_closed_fuera_de_horario() -> None:
    assert mh.get_session(_et(2026, 8, 3, 20, 0)) == "closed"
    assert mh.get_session(_et(2026, 8, 3, 2, 0)) == "closed"
    assert mh.get_session(_et(2026, 8, 3, 3, 59)) == "closed"


def test_closed_fin_de_semana() -> None:
    assert mh.get_session(_et(2026, 8, 1, 10, 0)) == "closed"  # sábado, en pleno horario regular
    assert mh.get_session(_et(2026, 8, 2, 6, 0)) == "closed"   # domingo, en pleno premarket


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
