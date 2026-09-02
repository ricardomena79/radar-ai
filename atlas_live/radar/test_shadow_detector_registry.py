"""Tests de `atlas_live/radar/shadow_detector_registry.py::list_shadow_market_dates()`
(2026-09-02, autorizado explícitamente, para U3-C3) -- DB temporal aislada,
mismo patrón que `test_unified_detector.py`. Confirma que la función es
puramente de lectura y que devuelve exactamente las fechas distintas reales,
ordenadas."""

import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live.radar import shadow_detector_registry as sreg

_ORIG_DB_PATH = sreg.DB_PATH


def _fresh():
    sreg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_shadow_dates_{_uuid.uuid4().hex}.db"


def _restore():
    sreg.DB_PATH = _ORIG_DB_PATH


def _record(ticker, market_date, session="regular"):
    sreg.record_shadow_detection(
        ticker=ticker, market_date=market_date, session=session,
        price=10.0, change_pct=5.0, volume=1000, average_volume=200,
        relative_volume=5.0, dollar_volume=10000.0,
        price_source="tradier", price_basis="tradier_last", price_is_stale=False,
        universe_source="piggyback_radar", gates_fired=[{"gate": "price_change", "reason": "x", "value": 5.0}],
        snapshot={"price": 10.0},
    )


def test_list_shadow_market_dates_vacio_sin_ninguna_deteccion():
    _fresh()
    try:
        assert sreg.list_shadow_market_dates() == []
    finally:
        _restore()


def test_list_shadow_market_dates_distintas_y_ordenadas_ascendente():
    _fresh()
    try:
        # Insertadas fuera de orden, con duplicados dentro de la misma fecha.
        _record("AAA", "2026-08-28")
        _record("BBB", "2026-08-26")
        _record("CCC", "2026-08-28")  # misma fecha que AAA -- no debe duplicar
        _record("DDD", "2026-08-27")

        assert sreg.list_shadow_market_dates() == ["2026-08-26", "2026-08-27", "2026-08-28"]
    finally:
        _restore()


def test_list_shadow_market_dates_no_escribe_nada_en_la_base():
    _fresh()
    try:
        _record("AAA", "2026-08-26")
        db_path = sreg.DB_PATH
        size_antes = db_path.stat().st_size

        sreg.list_shadow_market_dates()
        sreg.list_shadow_market_dates()  # dos veces -- confirma idempotencia real

        assert db_path.stat().st_size == size_antes
        # La fila original sigue exactamente igual -- nada se tocó.
        assert sreg.count_shadow_detections("2026-08-26") == 1
    finally:
        _restore()
