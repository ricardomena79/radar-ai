"""Marcador Racional en Aprendizaje en Vivo (2026-08-18, pedido explícito
del usuario): get_live_learning_summary() debe exponer un bloque "racional"
en paralelo al universal existente ("hoy"/"acumulada"/"reciente", que no se
tocan). DB temporal, sin red."""

import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.learning import live_summary
from atlas_live.radar import candidate_registry as reg

_ORIG = reg.DB_PATH


def _fresh():
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_live_summary_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None


def _restore():
    reg.DB_PATH = _ORIG


def test_racional_solo_cuenta_lo_disponible_en_racional(monkeypatch):
    _fresh()
    try:
        monkeypatch.setattr("atlas.data.universe.is_available", lambda t: t == "RAC")

        for ticker in ("RAC", "NORAC"):
            reg.record_detection(ticker, "2026-08-18", "regular", "2026-08-18T14:00:00Z", "s1",
                                  10.0, 3.0, 1000, 500, 2.0, 1_000_000, gates_fired=[])
            reg.record_outcome(ticker, "2026-08-18", run_up_before_detection_pct=3.0,
                                max_price_after_detection=13.0, max_return_after_detection_pct=30.0,
                                minutes_to_max=20.0, reached_20=True, reached_50=False, reached_100=False,
                                category="buena_oportunidad")
        reg.record_daily_summary("2026-08-18", 100, 2, 2, 2, 2, 0, 0, 2, 0, 0)

        resumen = live_summary.get_live_learning_summary(market_date="2026-08-18")

        # Bloque universal -- sin cambios, sigue contando ambos tickers
        assert resumen["hoy"]["evaluables"] == 2
        assert resumen["hoy"]["aciertos"] == 2

        # Bloque racional -- nuevo, solo cuenta RAC
        assert "racional" in resumen
        assert resumen["racional"]["hoy"]["evaluables"] == 1
        assert resumen["racional"]["hoy"]["aciertos"] == 1
        assert resumen["racional"]["hoy"]["precision"] == "1/1 = 100.0%"
        assert resumen["racional"]["acumulada"]["evaluables"] == 1
        assert resumen["racional"]["reciente"]["precision"] == "1/1 = 100.0%"
        assert resumen["racional"]["reciente"]["dias_incluidos"] == 1
    finally:
        _restore()


def test_racional_sin_datos_no_rompe(monkeypatch):
    _fresh()
    try:
        monkeypatch.setattr("atlas.data.universe.is_available", lambda t: False)
        resumen = live_summary.get_live_learning_summary(market_date="2026-08-18")
        assert resumen["racional"]["hoy"]["evaluables"] == 0
        assert resumen["racional"]["hoy"]["precision"] is None
    finally:
        _restore()
