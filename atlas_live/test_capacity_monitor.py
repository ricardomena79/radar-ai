"""Tests de `capacity_monitor.py` (2026-09-07, autorizado explícitamente
-- Capacity Monitor). DB de snapshots aislada por test; `disk_usage_info`/
`directory_inventory` monkeypatcheados para resultados deterministas (no
depende del disco real de la máquina que corre los tests)."""

import tempfile
import uuid as _uuid
from pathlib import Path

from atlas_live import capacity_monitor as cm
from atlas_live import capacity_registry as creg

_ORIG_DB = creg.DB_PATH


def _fresh_db():
    creg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_capacity_mon_{_uuid.uuid4().hex}.db"


def _restore_db():
    creg.DB_PATH = _ORIG_DB


def _fake_usage(total, used, free):
    return {"total_bytes": total, "used_bytes": used, "free_bytes": free}


def _fake_inventory(entries):
    return {"entries": entries, "total_files_found": len(entries), "total_accounted_bytes": sum(e["size_bytes"] for e in entries)}


# --- used/free/pct -------------------------------------------------------

def test_used_free_pct_calculados_correctamente(monkeypatch):
    _fresh_db()
    monkeypatch.setattr(cm, "disk_usage_info", lambda root: _fake_usage(10_000_000_000, 6_500_000_000, 3_500_000_000))
    monkeypatch.setattr(cm, "directory_inventory", lambda root, top_n: _fake_inventory([]))
    try:
        r = cm.capacity_report()
        assert r["total_bytes"] == 10_000_000_000
        assert r["used_bytes"] == 6_500_000_000
        assert r["free_bytes"] == 3_500_000_000
        assert r["used_pct"] == 65.0
        assert r["free_pct"] == 35.0
    finally:
        _restore_db()


def test_no_hardcodea_capacidad_funciona_con_volumen_distinto(monkeypatch):
    """El total nunca debe ser una constante -- probamos con 3 tamaños de
    volumen distintos (10GB, 20GB, 50GB) y confirmamos que el reporte
    refleja CADA uno tal cual, sin ningún número fijo de por medio."""
    _fresh_db()
    monkeypatch.setattr(cm, "directory_inventory", lambda root, top_n: _fake_inventory([]))
    try:
        for total_gb in (10, 20, 50):
            total = total_gb * cm.GB
            monkeypatch.setattr(cm, "disk_usage_info", lambda root, t=total: _fake_usage(t, t // 2, t // 2))
            r = cm.capacity_report()
            assert r["total_bytes"] == total
            assert r["used_pct"] == 50.0
    finally:
        _restore_db()


# --- umbrales de estado ---------------------------------------------------

def test_compute_status_ok_warning_critical():
    assert cm.compute_status(0.0) == "OK"
    assert cm.compute_status(79.99) == "OK"
    assert cm.compute_status(80.0) == "WARNING"
    assert cm.compute_status(89.99) == "WARNING"
    assert cm.compute_status(90.0) == "CRITICAL"
    assert cm.compute_status(100.0) == "CRITICAL"


def test_status_en_el_reporte_completo(monkeypatch):
    _fresh_db()
    monkeypatch.setattr(cm, "directory_inventory", lambda root, top_n: _fake_inventory([]))
    try:
        monkeypatch.setattr(cm, "disk_usage_info", lambda root: _fake_usage(100, 95, 5))
        assert cm.capacity_report()["status"] == "CRITICAL"
    finally:
        _restore_db()


# --- ranking de consumidores -----------------------------------------------

def test_top_consumers_respeta_el_orden_ya_dado_por_directory_inventory(monkeypatch):
    _fresh_db()
    monkeypatch.setattr(cm, "disk_usage_info", lambda root: _fake_usage(1000, 500, 500))
    entries = [
        {"path": "radar_candidates.db", "size_bytes": 300},
        {"path": "shadow_unified_detector.db-wal", "size_bytes": 200},
        {"path": "broad_universe_meta.json", "size_bytes": 50},
    ]
    monkeypatch.setattr(cm, "directory_inventory", lambda root, top_n: _fake_inventory(entries))
    try:
        r = cm.capacity_report()
        assert [c["path"] for c in r["top_consumers"]] == [
            "radar_candidates.db", "shadow_unified_detector.db-wal", "broad_universe_meta.json",
        ]
        assert r["top_consumers"][0]["size_bytes"] == 300
    finally:
        _restore_db()


# --- insufficient_history ---------------------------------------------------

def test_insufficient_history_sin_ningun_snapshot_previo(monkeypatch):
    _fresh_db()
    monkeypatch.setattr(cm, "disk_usage_info", lambda root: _fake_usage(1000, 500, 500))
    monkeypatch.setattr(cm, "directory_inventory", lambda root, top_n: _fake_inventory([]))
    try:
        r = cm.capacity_report()
        assert r["growth"]["history_status"] == "insufficient_history"
        assert r["growth"]["mb_per_hour"] is None
        assert r["growth"]["mb_per_day"] is None
        for k, v in r["projection"].items():
            assert v is None
    finally:
        _restore_db()


def test_insufficient_history_con_span_menor_al_minimo(monkeypatch):
    """2 snapshots reales pero separados por menos de MIN_SPAN_HOURS_FOR_GROWTH
    -- no alcanza para inferir una tendencia confiable. `record_snapshot`
    se neutraliza para que `capacity_report()` no agregue un 3er snapshot
    con el reloj REAL (que rompería el span exacto de 10 min que este test
    necesita controlar) -- la escritura throttleada ya tiene su propio
    test dedicado (`test_snapshot_no_se_registra_dos_veces...`)."""
    _fresh_db()
    monkeypatch.setattr(cm, "directory_inventory", lambda root, top_n: _fake_inventory([]))
    try:
        creg.record_snapshot(1000, 100, 900, observed_at="2026-09-07T10:00:00+00:00")
        creg.record_snapshot(1000, 110, 890, observed_at="2026-09-07T10:10:00+00:00")  # +10 min, no +1h
        monkeypatch.setattr(cm, "disk_usage_info", lambda root: _fake_usage(1000, 120, 880))
        monkeypatch.setattr(cm.creg, "record_snapshot", lambda *a, **k: None)
        r = cm.capacity_report()
        assert r["growth"]["history_status"] == "insufficient_history"
    finally:
        _restore_db()


# --- cálculo de crecimiento + proyección ------------------------------------

def test_crecimiento_calculado_con_pendiente_conocida(monkeypatch):
    """3 snapshots con crecimiento LINEAL exacto de 100MB/hora -- confirma
    que mb_per_hour/mb_per_day salen del cálculo real, no de una constante."""
    _fresh_db()
    monkeypatch.setattr(cm, "directory_inventory", lambda root, top_n: _fake_inventory([]))
    try:
        base_used = 1_000_000_000
        step = 100 * cm.MB
        creg.record_snapshot(10_000_000_000, base_used, 9_000_000_000, observed_at="2026-09-07T08:00:00+00:00")
        creg.record_snapshot(10_000_000_000, base_used + step, 9_000_000_000 - step, observed_at="2026-09-07T09:00:00+00:00")
        creg.record_snapshot(10_000_000_000, base_used + 2 * step, 9_000_000_000 - 2 * step, observed_at="2026-09-07T10:00:00+00:00")
        monkeypatch.setattr(cm, "disk_usage_info", lambda root: _fake_usage(10_000_000_000, base_used + 2 * step, 9_000_000_000 - 2 * step))
        # neutraliza el record_snapshot SOLO ahora -- si se hiciera antes,
        # tambien anularia las 3 siembras de arriba (`cm.creg` y el `creg`
        # importado en este archivo son EL MISMO objeto de modulo).
        monkeypatch.setattr(cm.creg, "record_snapshot", lambda *a, **k: None)
        r = cm.capacity_report()
        assert r["growth"]["history_status"] == "ok"
        assert abs(r["growth"]["mb_per_hour"] - 100.0) < 0.01
        assert abs(r["growth"]["mb_per_day"] - 2400.0) < 0.5
    finally:
        _restore_db()


def test_proyeccion_dias_a_umbral_coherente_con_la_tasa(monkeypatch):
    _fresh_db()
    monkeypatch.setattr(cm, "directory_inventory", lambda root, top_n: _fake_inventory([]))
    try:
        total = 10 * cm.GB
        step = 1 * cm.GB  # 1GB/hora -- tasa deliberadamente agresiva para un test simple
        creg.record_snapshot(total, 1 * cm.GB, total - 1 * cm.GB, observed_at="2026-09-07T08:00:00+00:00")
        creg.record_snapshot(total, 2 * cm.GB, total - 2 * cm.GB, observed_at="2026-09-07T09:00:00+00:00")
        used_now = 3 * cm.GB
        monkeypatch.setattr(cm, "disk_usage_info", lambda root: _fake_usage(total, used_now, total - used_now))
        monkeypatch.setattr(cm.creg, "record_snapshot", lambda *a, **k: None)
        r = cm.capacity_report()
        # a 1GB/hora = 24GB/dia; falta (8-3)=5GB para 80% -> 5/24 dias
        esperado_80 = round((total * 0.8 - used_now) / (24 * cm.GB), 2)
        assert r["projection"]["days_to_80_pct"] == esperado_80
        # 100% ya fue superado en la proyeccion (30GB objetivo > total real, pero
        # el calculo es sobre el mismo total) -- confirmar que ninguno es negativo
        for v in r["projection"].values():
            assert v is None or v >= 0
    finally:
        _restore_db()


def test_proyeccion_cero_si_ya_esta_sobre_el_umbral(monkeypatch):
    _fresh_db()
    monkeypatch.setattr(cm, "directory_inventory", lambda root, top_n: _fake_inventory([]))
    try:
        total = 10 * cm.GB
        step = 1 * cm.MB
        creg.record_snapshot(total, int(9.5 * cm.GB), total - int(9.5 * cm.GB), observed_at="2026-09-07T08:00:00+00:00")
        creg.record_snapshot(total, int(9.5 * cm.GB) + step, total - int(9.5 * cm.GB) - step, observed_at="2026-09-07T09:00:00+00:00")
        used_now = int(9.6 * cm.GB)
        monkeypatch.setattr(cm, "disk_usage_info", lambda root: _fake_usage(total, used_now, total - used_now))
        monkeypatch.setattr(cm.creg, "record_snapshot", lambda *a, **k: None)
        r = cm.capacity_report()
        assert r["projection"]["days_to_80_pct"] == 0.0
        assert r["projection"]["days_to_90_pct"] == 0.0
    finally:
        _restore_db()


def test_proyeccion_none_si_crecimiento_es_negativo_o_cero(monkeypatch):
    """Si el uso está BAJANDO (o estable), nunca se inventa un 'días
    hasta' -- se devuelve None explícito, no un número negativo ni cero
    fabricado por división."""
    _fresh_db()
    monkeypatch.setattr(cm, "directory_inventory", lambda root, top_n: _fake_inventory([]))
    try:
        total = 10 * cm.GB
        creg.record_snapshot(total, 5 * cm.GB, total - 5 * cm.GB, observed_at="2026-09-07T08:00:00+00:00")
        creg.record_snapshot(total, 4 * cm.GB, total - 4 * cm.GB, observed_at="2026-09-07T09:00:00+00:00")  # bajó
        monkeypatch.setattr(cm, "disk_usage_info", lambda root: _fake_usage(total, 4 * cm.GB, total - 4 * cm.GB))
        monkeypatch.setattr(cm.creg, "record_snapshot", lambda *a, **k: None)
        r = cm.capacity_report()
        assert r["growth"]["mb_per_day"] < 0
        for v in r["projection"].values():
            assert v is None
    finally:
        _restore_db()


# --- ausencia de datos falsos ------------------------------------------------

def test_disk_usage_error_nunca_fabrica_datos(monkeypatch):
    _fresh_db()
    monkeypatch.setattr(cm, "disk_usage_info", lambda root: {"disk_usage_error": "OSError: boom"})
    monkeypatch.setattr(cm, "directory_inventory", lambda root, top_n: _fake_inventory([]))
    try:
        r = cm.capacity_report()
        assert r["total_bytes"] is None
        assert r["used_bytes"] is None
        assert r["used_pct"] is None
        assert r["status"] == "OK"  # nunca CRITICAL/WARNING inventado sin dato real
        assert r["disk_usage_error"] == "OSError: boom"
        assert r["top_consumers"] == []
        assert r["growth"]["history_status"] == "insufficient_history"
    finally:
        _restore_db()


def test_capacity_summary_public_no_incluye_top_consumers(monkeypatch):
    _fresh_db()
    monkeypatch.setattr(cm, "disk_usage_info", lambda root: _fake_usage(1000, 500, 500))
    monkeypatch.setattr(cm, "directory_inventory", lambda root, top_n: _fake_inventory([{"path": "x.db", "size_bytes": 10}]))
    try:
        publico = cm.capacity_summary_public()
        assert "top_consumers" not in publico
        assert publico["used_pct"] == 50.0
        assert set(publico.keys()) == {
            "generated_at", "total_bytes", "used_bytes", "free_bytes",
            "used_pct", "free_pct", "growth", "projection", "status",
        }
    finally:
        _restore_db()


def test_snapshot_no_se_registra_dos_veces_dentro_del_intervalo_minimo(monkeypatch):
    """Pedido explícito: 'no por sweep' -- 2 llamadas seguidas a
    capacity_report() no deben producir 2 filas si no pasó el intervalo
    mínimo real de tiempo."""
    _fresh_db()
    monkeypatch.setattr(cm, "disk_usage_info", lambda root: _fake_usage(1000, 500, 500))
    monkeypatch.setattr(cm, "directory_inventory", lambda root, top_n: _fake_inventory([]))
    try:
        cm.capacity_report()
        cm.capacity_report()
        cm.capacity_report()
        assert len(creg.list_snapshots()) == 1
    finally:
        _restore_db()
