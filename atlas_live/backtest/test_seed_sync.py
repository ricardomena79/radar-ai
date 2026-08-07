"""Pruebas de la sincronización JSONL entre el entorno de desarrollo y la
base oficial (Investigación 4, Etapa 3, 2026-08-06, ver DECISION_LOG.md).
Con trayectorias sintéticas, sin red real -- `_remote_inventory` se
reemplaza por una función de prueba en vez de golpear un servidor.

Uso: `python -m atlas_live.backtest.test_seed_sync`
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from atlas_live.memory import exit_journal as ej

# Aislamiento de datos (mismo criterio que test_exit_journal.py /
# test_live_integration.py desde el incidente real de la Investigación 3):
# `ej.DB_PATH` se redirige a un directorio temporal propio al importar --
# estas pruebas nunca pueden tocar la base real, sin importar quién las
# corra ni cuándo.
ej.DB_PATH = Path(tempfile.mkdtemp(prefix="atlas_test_seed_sync_")) / "exit_journal.db"

from atlas_live.backtest import export_seed_delta as exp  # noqa: E402  (después de fijar DB_PATH)
from atlas_live.backtest import seed_import as si  # noqa: E402


def _reset_db() -> None:
    if os.path.exists(ej.DB_PATH):
        os.remove(ej.DB_PATH)
    for ext in ("-wal", "-shm"):
        p = str(ej.DB_PATH) + ext
        if os.path.exists(p):
            os.remove(p)


BASE = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)


def _seed_samples(symbol: str, date: str, n: int, offset: float = 0.0) -> None:
    for m in range(n):
        ej.record_trajectory_sample(
            symbol=symbol, date=date, sampled_at=(BASE + timedelta(minutes=m)).isoformat(),
            return_pct=offset + m, score=50.0, eligible=True,
        )


def test_export_delta_excluye_lo_que_ya_esta_en_la_oficial() -> None:
    _reset_db()
    _seed_samples("SYNC1", "2026-01-05", 5)
    _seed_samples("SYNC2", "2026-01-05", 3)

    # SYNC1 ya existe en la base oficial (simulada) -- solo SYNC2 debe entrar al delta.
    exp._remote_inventory = lambda base_url: {("SYNC1", "2026-01-05")}

    with tempfile.TemporaryDirectory() as tmp:
        seed_dir = Path(tmp)
        resultado = exp.export_delta(base_url="http://fake", out_dir=seed_dir)

        assert resultado["pares_locales"] == 2
        assert resultado["pares_ya_en_oficial"] == 1
        assert resultado["pares_en_delta"] == 1
        assert resultado["filas_escritas"] == 3

        with open(resultado["archivo"], encoding="utf-8") as f:
            filas = [json.loads(linea) for linea in f]
        assert {f["symbol"] for f in filas} == {"SYNC2"}
    print("OK - export_delta excluye pares que ya existen en la base oficial")


def test_export_delta_sin_nada_que_sincronizar() -> None:
    _reset_db()
    _seed_samples("SYNC3", "2026-01-05", 2)
    exp._remote_inventory = lambda base_url: {("SYNC3", "2026-01-05")}

    with tempfile.TemporaryDirectory() as tmp:
        resultado = exp.export_delta(base_url="http://fake", out_dir=Path(tmp))
        assert resultado["pares_en_delta"] == 0
        assert resultado["archivo"] is None
    print("OK - sin delta, no se escribe ningún archivo (nada que sincronizar)")


def test_seed_import_es_aditivo_e_idempotente() -> None:
    """El corazón de la garantía de la Investigación 4: importar nunca
    sobrescribe, e importar el mismo seed dos veces da el mismo resultado."""
    with tempfile.TemporaryDirectory() as tmp:
        seed_dir = Path(tmp)
        seed_path = seed_dir / "exit_journal_seed_test.jsonl"
        filas = [
            {"symbol": "SYNC4", "date": "2026-01-05", "sampled_at": (BASE + timedelta(minutes=i)).isoformat(),
             "return_pct": float(i), "score": 70.0, "eligible": True}
            for i in range(4)
        ]
        with open(seed_path, "w", encoding="utf-8") as f:
            for fila in filas:
                f.write(json.dumps(fila) + "\n")

        _reset_db()
        assert ej.get_trajectory("SYNC4", "2026-01-05") == []

        reporte1 = si.import_all_seeds(seed_dir=seed_dir)
        assert reporte1[0]["insertadas"] == 4
        assert reporte1[0]["ya_existian"] == 0
        assert len(ej.get_trajectory("SYNC4", "2026-01-05")) == 4

        # Reintentar el mismo seed -- idempotente, sin duplicar.
        reporte2 = si.import_all_seeds(seed_dir=seed_dir)
        assert reporte2[0]["insertadas"] == 0
        assert reporte2[0]["ya_existian"] == 4
        assert len(ej.get_trajectory("SYNC4", "2026-01-05")) == 4
    print("OK - seed_import es aditivo (solo INSERT) e idempotente (reintentar no duplica)")


def test_seed_import_nunca_sobrescribe_dato_en_vivo_mas_reciente() -> None:
    """Si la base oficial ya tiene una muestra real para una clave exacta
    (symbol, date, sampled_at), el seed nunca la toca -- ni siquiera con
    un valor distinto en el archivo importado."""
    _reset_db()
    ts = BASE.isoformat()
    ej.record_trajectory_sample(symbol="SYNC5", date="2026-01-05", sampled_at=ts,
                                 return_pct=999.0, score=1.0, eligible=False)

    with tempfile.TemporaryDirectory() as tmp:
        seed_dir = Path(tmp)
        with open(seed_dir / "seed.jsonl", "w", encoding="utf-8") as f:
            f.write(json.dumps({"symbol": "SYNC5", "date": "2026-01-05", "sampled_at": ts,
                                 "return_pct": 1.0, "score": 50.0, "eligible": True}) + "\n")

        reporte = si.import_all_seeds(seed_dir=seed_dir)
        assert reporte[0]["insertadas"] == 0
        assert reporte[0]["ya_existian"] == 1

        traj = ej.get_trajectory("SYNC5", "2026-01-05")
        assert len(traj) == 1
        assert traj[0]["return_pct"] == 999.0  # el dato "en vivo" original, sin tocar
    print("OK - una clave (symbol,date,sampled_at) ya existente nunca se sobrescribe")


def test_seed_import_directorio_ausente_no_falla() -> None:
    _reset_db()
    reportes = si.import_all_seeds(seed_dir=Path(tempfile.mkdtemp()) / "no_existe")
    assert reportes == []
    print("OK - directorio de seeds ausente no rompe el arranque del servidor")


def test_seed_import_fila_corrupta_no_bloquea_al_resto() -> None:
    _reset_db()
    with tempfile.TemporaryDirectory() as tmp:
        seed_dir = Path(tmp)
        with open(seed_dir / "seed.jsonl", "w", encoding="utf-8") as f:
            f.write(json.dumps({"symbol": "SYNC6", "date": "2026-01-05", "sampled_at": BASE.isoformat(),
                                 "return_pct": 1.0, "score": 50.0, "eligible": True}) + "\n")
            f.write("esto no es json valido\n")
            f.write(json.dumps({"symbol": "SYNC6", "date": "2026-01-05",
                                 "sampled_at": (BASE + timedelta(minutes=5)).isoformat(),
                                 "return_pct": 2.0, "score": 50.0, "eligible": True}) + "\n")

        reporte = si.import_all_seeds(seed_dir=seed_dir)
        assert reporte[0]["insertadas"] == 2
        assert reporte[0]["errores"] == 1
        assert len(ej.get_trajectory("SYNC6", "2026-01-05")) == 2
    print("OK - una fila corrupta se cuenta como error, sin bloquear las filas válidas")


ALL_TESTS = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]

if __name__ == "__main__":
    fallos = []
    for test_fn in ALL_TESTS:
        try:
            test_fn()
        except AssertionError as exc:
            fallos.append((test_fn.__name__, str(exc)))
    _reset_db()
    print(f"\nPruebas corridas: {len(ALL_TESTS)}")
    if fallos:
        print(f"FALLÓ -- {len(fallos)}:")
        for nombre, motivo in fallos:
            print(f"  {nombre}: {motivo}")
    else:
        print("OK -- todas las pruebas de sincronización de seeds pasaron.")
