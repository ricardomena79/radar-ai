"""Reinicio único del aprendizaje anterior (2026-08-15, "Reinicio v2").

Pedido explícito del usuario: el historial de aprendizaje anterior (73.123
observaciones "v1" del Memory Store, el seed de 89.786 muestras del Exit
Journal, señales/predicciones ya resueltas) puede no ser representativo de
la arquitectura actual (Tradier + radar de universo completo) y no debe
seguir acumulándose sobre él. Este módulo lo vacía UNA SOLA VEZ, de forma
auditable, sin tocar código/config/providers/Tradier/universo Racional.

Por qué es CÓDIGO y no un borrado manual de archivos: la base oficial en
producción vive en el Volume de Railway (`ATLAS_DATA_DIR`), al que esta
sesión no tiene acceso directo. La única forma confiable de vaciarla es
que el propio proceso lo haga al arrancar -- exactamente el mismo patrón ya
usado por `seed_import`/`observation_recovery` para RECONSTRUIR datos, acá
usado en sentido inverso para LIMPIARLOS, una vez, con backup.

Garantías:
  - Idempotente: una marca (`_LEARNING_RESET_MARKER`) en el mismo
    directorio de datos asegura que esto corre UNA sola vez por Volume/
    entorno, nunca de nuevo en reinicios futuros.
  - Con backup: antes de vaciar una tabla, se copia el archivo .db
    completo a `<nombre>.pre_reset_v2_<fecha>.bak` en el MISMO directorio
    -- nunca se destruye el dato viejo, solo deja de ser la base activa.
  - Vacía TABLAS (DELETE), nunca borra el archivo ni el esquema -- todo el
    código que ya sabe leer/escribir esas bases sigue funcionando igual,
    solo que empiezan en cero.
  - NO toca: código, configuración, providers, Tradier, universo Racional,
    normalización de símbolos, tests, credenciales, `atlas_live/radar/`
    (es la base NUEVA de esta etapa, no aprendizaje anterior),
    `mission_control/timeline.db` (log operativo, no resultados de
    aprendizaje).
"""

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from loguru import logger

from atlas.config.config import data_dir

RESET_VERSION = "learning_reset_v2_2026-08-15"

# (módulo que resuelve DB_PATH -> [tablas a vaciar]). Se importa perezosamente
# dentro de la función para no imponer un orden de import en server.py.
_TARGETS: Dict[str, List[str]] = {
    "atlas_live.memory.store": ["observations"],
    "atlas_live.memory.exit_journal": ["trajectory_samples", "exit_summary"],
    "atlas_live.memory.prediction_journal": ["dynamic_snapshots", "sealed_ranking_meta", "sealed_predictions"],
    "atlas_live.signals.signal_registry": ["signals", "signal_observations", "signal_results"],
    "atlas_live.predictive_engine.prediction_log": ["predictions"],
    "atlas_live.market_study.study_registry": [
        "study_checkpoint", "explosion_features", "explosion_outcome", "study_meta",
    ],
}


def _marker_path() -> Path:
    # Vive en el mismo directorio de datos que todo lo demás (Volume en
    # producción, `atlas_live/memory/` en local sin ATLAS_DATA_DIR) -- así
    # la marca viaja CON los datos que protege, no con el código.
    return data_dir(Path(__file__).parent / "memory") / f"_{RESET_VERSION}.marker"


def already_applied() -> bool:
    return _marker_path().exists()


def _backup_and_clear(module_name: str, tables: List[str], today: str) -> Dict[str, object]:
    import importlib

    mod = importlib.import_module(module_name)
    db_path: Path = mod.DB_PATH
    result = {"module": module_name, "db_path": str(db_path), "existed": db_path.exists(), "backup": None, "cleared": {}}

    if not db_path.exists():
        return result

    backup_path = db_path.with_name(f"{db_path.stem}.pre_reset_v2_{today}.bak{db_path.suffix}")
    if not backup_path.exists():
        shutil.copy2(db_path, backup_path)
    result["backup"] = str(backup_path)

    conn = sqlite3.connect(db_path, timeout=15)
    try:
        conn.execute("PRAGMA busy_timeout=15000")
        for table in tables:
            try:
                before = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                conn.execute(f'DELETE FROM "{table}"')
                result["cleared"][table] = before
            except sqlite3.OperationalError as exc:
                result["cleared"][table] = f"omitido ({exc})"
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()
    return result


def reset_learning_once() -> Dict[str, object]:
    """Punto de entrada único, llamado desde `server.py` al arrancar.
    No hace nada si ya se aplicó antes en este mismo Volume/entorno."""
    if already_applied():
        return {"applied": False, "reason": "ya aplicado antes", "marker": str(_marker_path())}

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    report = {"applied": True, "version": RESET_VERSION, "at": datetime.now(timezone.utc).isoformat(), "targets": []}

    for module_name, tables in _TARGETS.items():
        try:
            result = _backup_and_clear(module_name, tables, today)
            report["targets"].append(result)
        except Exception as exc:
            report["targets"].append({"module": module_name, "error": f"{type(exc).__name__}: {exc}"})
            logger.error(f"learning_reset: falló {module_name}: {exc}")

    # Invalida también la caché de evidencia EN MEMORIA del proceso
    # (`live_integration._evidence_cache`) -- hallazgo real del
    # 2026-08-15: vaciar las tablas en disco no alcanza si el proceso ya
    # había calculado `nivel_aprendizaje_pct`/`reliable_condition_count`
    # antes de que este reset corriera; sin esto, la Cabina seguiría
    # mostrando el número viejo hasta el próximo cambio de día de mercado.
    try:
        from atlas_live.memory import live_integration
        live_integration.reset_evidence_cache()
        report["evidence_cache_invalidada"] = True
    except Exception as exc:
        report["evidence_cache_invalidada"] = False
        logger.error(f"learning_reset: no se pudo invalidar _evidence_cache: {exc}")

    marker = _marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        f"{RESET_VERSION} aplicado {report['at']}\n"
        f"Reinicio explícito del aprendizaje anterior (arquitectura Tradier+radar). "
        f"Backups .bak junto a cada base original.\n",
        encoding="utf-8",
    )
    logger.info(f"learning_reset: {RESET_VERSION} aplicado -- {len(report['targets'])} bases procesadas.")
    return report
