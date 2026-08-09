"""Tests del Marcador Histórico de Explosiones (2026-08-09). Lógica verificada
con trayectorias sintéticas controladas en un Exit Journal temporal (jamás la
base real) + un smoke test sobre datos reales con n explícito.
"""

import tempfile
from pathlib import Path

from atlas_live.memory import exit_journal as ej
from atlas_live.memory import explosion_history as eh
from atlas_live.memory import store

_ORIG_EJ = ej.DB_PATH
_ORIG_STORE = store.DB_PATH
_TMP = Path(tempfile.mkdtemp(prefix="atlas_test_eh_"))


def _use_tmp():
    ej.DB_PATH = _TMP / "exit_journal.db"
    store.DB_PATH = _TMP / "memory_store.db"
    for base in (ej.DB_PATH, store.DB_PATH):
        for s in ("", "-wal", "-shm"):
            p = Path(str(base) + s)
            if p.exists():
                p.unlink()


def _restore():
    ej.DB_PATH = _ORIG_EJ
    store.DB_PATH = _ORIG_STORE


def _traj(symbol, date, returns, start="2026-07-01T13:30:00+00:00"):
    """Siembra una trayectoria: `returns` es la lista de return_pct a 5 min."""
    from datetime import datetime, timedelta
    t0 = datetime.fromisoformat(start)
    for i, r in enumerate(returns):
        ej.record_trajectory_sample(
            symbol=symbol, date=date,
            sampled_at=(t0 + timedelta(minutes=5 * i)).isoformat(),
            return_pct=r, score=50.0, eligible=True,
        )


def test_explosion_limpia_hitos_y_calidad():
    _use_tmp()
    try:
        # 0 -> 10 -> 30 -> 55 (pico) -> 20 (retroceso sostenido)
        _traj("CLEAN", "2026-07-01", [0, 10, 30, 55, 20, 18])
        reg = eh.build_registry()
        assert reg["calidad"]["limpias_start_observado"] == 1
        e = reg["eventos"][0]
        assert e["quality"] == "limpia"
        assert e["max_return_pct"] == 55.0
        assert e["hitos"]["10"]["alcanzado"] and e["hitos"]["10"]["hora_et"]
        assert e["hitos"]["30"]["alcanzado"] and e["hitos"]["30"]["hora_et"]
        assert e["hitos"]["100"]["alcanzado"] is False  # no alcanzado -> honesto
        # fin de impulso: 55 -> cae a 20 (>=20% bajo el pico), sostenido
        assert e["fin_impulso_hora_et"] is not None
    finally:
        _restore()


def test_lead_time_10_a_30():
    _use_tmp()
    try:
        # +10% en el paso 1 (t+5min), +30% en el paso 3 (t+15min) -> lead 10 min
        _traj("LEAD", "2026-07-01", [0, 10, 20, 30, 40])
        lt = eh.lead_time_stats()
        assert lt["n"] == 1
        assert lt["mediana_min"] == 10.0  # 2 pasos de 5 min entre +10 y +30
    finally:
        _restore()


def test_pre_iniciada_hito_anterior_a_la_ventana():
    _use_tmp()
    try:
        # arranca ya en +40% -> pre_iniciada; +30 "anterior a la ventana"
        _traj("PRESTART", "2026-07-01", [40, 45, 60, 50])
        reg = eh.build_registry()
        e = reg["eventos"][0]
        assert e["quality"] == "pre_iniciada"
        assert e["hitos"]["30"]["alcanzado"] is True
        assert e["hitos"]["30"]["hora_et"] is None  # no se inventa la hora
        assert e["hitos"]["30"].get("nota") == "anterior a la ventana"
        # las pre-iniciadas NO cuentan para anticipación
        assert eh.lead_time_stats(reg)["n"] == 0
    finally:
        _restore()


def test_artefacto_excluido():
    _use_tmp()
    try:
        _traj("ARTIFACT", "2026-07-01", [5000, 6000, 5500])  # imposible -> artefacto
        _traj("REAL", "2026-07-01", [0, 15, 35, 40])          # explosión real
        reg = eh.build_registry()
        assert reg["calidad"]["artefactos_excluidos"] == 1
        symbols = {e["symbol"] for e in reg["eventos"]}
        assert "ARTIFACT" not in symbols  # excluido del estudio
        assert "REAL" in symbols
    finally:
        _restore()


def test_no_alcanza_umbral_no_entra():
    _use_tmp()
    try:
        _traj("SMALL", "2026-07-01", [0, 5, 12, 8])  # solo +12%, no llega a +30
        reg = eh.build_registry(min_band_pct=30)
        assert all(e["symbol"] != "SMALL" for e in reg["eventos"])
    finally:
        _restore()


def test_summary_acumulativo_con_n():
    _use_tmp()
    try:
        _traj("A", "2026-07-01", [0, 10, 35, 40])   # max 40 -> >=30
        _traj("B", "2026-07-02", [0, 10, 60, 55])   # max 60 -> >=30,>=50
        _traj("C", "2026-07-03", [0, 20, 110, 90])  # max 110 -> >=30,>=50,>=100
        s = eh.summarize_by_band()
        assert s["por_banda_acumulativa"]["30"]["n"] == 3
        assert s["por_banda_acumulativa"]["50"]["n"] == 2
        assert s["por_banda_acumulativa"]["100"]["n"] == 1
        assert s["por_banda_acumulativa"]["200"]["estado"] == "No disponible"
    finally:
        _restore()


def test_grupos_ABCD():
    _use_tmp()
    try:
        # Apertura = 13:30 UTC (09:30 ET). Arrancamos 30 min antes (13:00).
        pre = "2026-07-01T13:00:00+00:00"
        # A: premarket fuerte (pico 15% antes de apertura) + continuó (40% después)
        _traj("GA", "2026-07-01", [5, 12, 15, 14, 13, 12, 20, 40, 35], start=pre)
        # B: premarket fuerte (pico 35% antes) + perdió momentum (26% después)
        _traj("GB", "2026-07-02", [5, 15, 35, 30, 28, 25, 26, 24, 20], start="2026-07-02T13:00:00+00:00")
        # C: premarket tranquilo + empezó tras apertura (40%)
        _traj("GC", "2026-07-03", [1, 2, 3, 2, 1, 0, 10, 35, 40], start="2026-07-03T13:00:00+00:00")
        # D: pre-iniciada (arranca en 40%)
        _traj("GD", "2026-07-04", [40, 45, 50, 45], start="2026-07-04T13:00:00+00:00")
        reg = eh.build_registry()
        por_sym = {e["symbol"]: e["grupo"] for e in reg["eventos"]}
        assert por_sym["GA"] == "A", por_sym
        assert por_sym["GB"] == "B", por_sym
        assert por_sym["GC"] == "C", por_sym
        assert por_sym["GD"] == "D", por_sym
        gs = eh.group_study(reg)
        assert gs["grupos"]["A"]["n"] == 1
        assert gs["grupos"]["B"]["n"] == 1
        # B con n<10 -> advertencia honesta presente
        assert gs["discriminacion_A_vs_B"]["advertencia"] is not None
    finally:
        _restore()


def test_smoke_datos_reales():
    # Sin redirigir: corre sobre la memoria real, valida estructura + n explícito.
    reg = eh.build_registry()
    assert "calidad" in reg and "eventos" in reg
    assert reg["calidad"]["eventos_incluidos"] == len(reg["eventos"])
    lt = eh.lead_time_stats(reg)
    assert "n" in lt  # siempre reporta n, aunque sea 0


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__); p += 1
        except Exception as e:
            print("FAIL", fn.__name__, e); traceback.print_exc(); f += 1
    print(f"--- {p} passed, {f} failed ---")
