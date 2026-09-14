"""Tests de `magnitud_prediction_source.py` (2026-09-13, autorizado
explícitamente tras validación fuera de muestra). DB temporal aislada --
mismo patrón `_fresh()`/`_restore()` ya usado en
`test_live_experience_knowledge.py`."""

import tempfile
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

from atlas_live.learning import live_experience_knowledge as lek
from atlas_live.radar import magnitud_prediction_source as mps

_ORIG_DB_PATH = lek.DB_PATH


def _fresh():
    lek.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_lek_mps_{_uuid.uuid4().hex}.db"


def _restore():
    lek.DB_PATH = _ORIG_DB_PATH


def _fila(direction, stage, n, mediana, computed_as_of, bucket="poblacion_total"):
    return {
        "direction": direction, "timing_deteccion": stage, "bucket": bucket,
        "n_evaluables": n, "n_aciertos_20": 0, "pct_20": 0.0,
        "wilson_lower_bound_20_pct": 0.0, "wilson_upper_bound_20_pct": 1.0,
        "baseline_pct_20": 3.0, "lift_20": 0.0, "mediana_max_advance_pct": mediana,
        "n_aciertos_50": 0, "pct_50": 0.0, "n_aciertos_100": 0, "pct_100": 0.0,
        "validation_state": "VALIDACION_ROBUSTA" if n >= 500 else "EN_VALIDACION",
        "computed_as_of": computed_as_of,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


def test_devuelve_snapshot_cuando_n_supera_el_umbral_de_madurez():
    _fresh()
    try:
        lek.record_experience_knowledge(
            [_fila("ALCISTA", "CONFIRMACION", 787, 6.5, "2026-08-31")],
            methodology_version=lek.METHODOLOGY_VERSION_V2,
        )
        resultado = mps.resolve_predicted_pct_v2("ALCISTA", "CONFIRMACION", "2026-09-02")
        assert resultado is not None
        assert resultado["predicted_pct"] == 6.5
        assert resultado["muestra_n"] == 787
        assert resultado["fuente"] == "v2_own_experience"
        assert resultado["bucket"] == "poblacion_total"
    finally:
        _restore()


def test_devuelve_none_cuando_n_esta_por_debajo_del_umbral():
    # n=499, un punto por debajo del piso de 500 -- verificado con datos
    # reales que preservar este piso (no 100-499) es la decisión correcta.
    _fresh()
    try:
        lek.record_experience_knowledge(
            [_fila("ALCISTA", "CONFIRMACION", 499, 6.5, "2026-08-31")],
            methodology_version=lek.METHODOLOGY_VERSION_V2,
        )
        resultado = mps.resolve_predicted_pct_v2("ALCISTA", "CONFIRMACION", "2026-09-02")
        assert resultado is None
    finally:
        _restore()


def test_devuelve_none_cuando_n_es_exactamente_el_umbral_menos_uno_pero_si_al_umbral():
    _fresh()
    try:
        lek.record_experience_knowledge(
            [_fila("ALCISTA", "CONFIRMACION", 500, 6.5, "2026-08-31")],
            methodology_version=lek.METHODOLOGY_VERSION_V2,
        )
        resultado = mps.resolve_predicted_pct_v2("ALCISTA", "CONFIRMACION", "2026-09-02")
        assert resultado is not None  # n=500 exacto SI alcanza (umbral inclusivo)
    finally:
        _restore()


def test_devuelve_none_cuando_no_existe_el_grupo():
    _fresh()
    try:
        lek.record_experience_knowledge(
            [_fila("ALCISTA", "CONFIRMACION", 787, 6.5, "2026-08-31")],
            methodology_version=lek.METHODOLOGY_VERSION_V2,
        )
        resultado = mps.resolve_predicted_pct_v2("BAJISTA", "FLUJO_VENDEDOR", "2026-09-02")
        assert resultado is None
    finally:
        _restore()


def test_walk_forward_nunca_usa_snapshot_del_mismo_dia_ni_posterior():
    # El caso central de anti-leakage: un snapshot con computed_as_of ==
    # market_date de la candidata (o posterior) NUNCA debe usarse -- solo
    # el estrictamente anterior.
    _fresh()
    try:
        lek.record_experience_knowledge(
            [_fila("ALCISTA", "CONFIRMACION", 787, 6.5, "2026-09-02")],
            methodology_version=lek.METHODOLOGY_VERSION_V2,
        )
        # market_date == computed_as_of -> no debe verlo
        assert mps.resolve_predicted_pct_v2("ALCISTA", "CONFIRMACION", "2026-09-02") is None
        # market_date < computed_as_of -> tampoco (sería usar conocimiento futuro)
        assert mps.resolve_predicted_pct_v2("ALCISTA", "CONFIRMACION", "2026-09-01") is None
        # market_date > computed_as_of -> SI lo ve
        assert mps.resolve_predicted_pct_v2("ALCISTA", "CONFIRMACION", "2026-09-03") is not None
    finally:
        _restore()


def test_usa_el_snapshot_mas_reciente_disponible_antes_del_corte():
    _fresh()
    try:
        lek.record_experience_knowledge(
            [_fila("ALCISTA", "CONFIRMACION", 520, 6.3, "2026-08-31")],
            methodology_version=lek.METHODOLOGY_VERSION_V2,
        )
        lek.record_experience_knowledge(
            [_fila("ALCISTA", "CONFIRMACION", 787, 6.5, "2026-09-04")],
            methodology_version=lek.METHODOLOGY_VERSION_V2,
        )
        # Antes del segundo snapshot -- debe usar el primero (6.3)
        r1 = mps.resolve_predicted_pct_v2("ALCISTA", "CONFIRMACION", "2026-09-02")
        assert r1["predicted_pct"] == 6.3
        assert r1["computed_as_of"] == "2026-08-31"
        # Después de ambos -- debe usar el más reciente (6.5)
        r2 = mps.resolve_predicted_pct_v2("ALCISTA", "CONFIRMACION", "2026-09-05")
        assert r2["predicted_pct"] == 6.5
        assert r2["computed_as_of"] == "2026-09-04"
    finally:
        _restore()


def test_ignora_metodologia_v1_nunca_mezcla_con_v2():
    # Un snapshot v1 (timing_deteccion, no alert_stage) con el mismo nombre
    # de columna NO debe ser leído por esta fuente -- solo v2.
    _fresh()
    try:
        lek.record_experience_knowledge(
            [_fila("ALCISTA", "al_comienzo", 5000, 14.7, "2026-08-31")],
            methodology_version=lek.METHODOLOGY_VERSION,  # v1
        )
        resultado = mps.resolve_predicted_pct_v2("ALCISTA", "al_comienzo", "2026-09-02")
        assert resultado is None
    finally:
        _restore()


def test_ignora_buckets_distintos_de_poblacion_total():
    # La única estratificación validada fuera de muestra fue el agregado
    # sin tercil -- un bucket "alto"/"medio"/"bajo" con n>=500 no debe
    # usarse todavía (nunca se validó fuera de muestra para v2).
    _fresh()
    try:
        lek.record_experience_knowledge(
            [_fila("ALCISTA", "CONFIRMACION", 900, 10.3, "2026-08-31", bucket="alto")],
            methodology_version=lek.METHODOLOGY_VERSION_V2,
        )
        resultado = mps.resolve_predicted_pct_v2("ALCISTA", "CONFIRMACION", "2026-09-02")
        assert resultado is None
    finally:
        _restore()


def test_get_latest_v2_snapshot_devuelve_none_sin_datos():
    _fresh()
    try:
        assert mps.get_latest_v2_snapshot("ALCISTA", "CONFIRMACION", "2026-09-02") is None
    finally:
        _restore()
