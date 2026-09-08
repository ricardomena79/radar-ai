"""Capacity Monitor de Atlas (2026-09-07, autorizado explícitamente).

Objetivo: visibilidad permanente de cuánto almacenamiento real queda en el
Volume de `ATLAS_DATA_DIR`, con crecimiento reciente y proyección a
umbrales críticos -- SIN modificar retención, SIN borrar nada, SIN tocar
el radar. Solo observabilidad.

Reutiliza en su totalidad, sin duplicar:
  - `atlas_live.data_dir_diagnostics.disk_usage_info()` -- `shutil.disk_usage()`
    real sobre el filesystem, NUNCA un tamaño hardcodeado (funciona igual
    si Railway pasa el Volume de 10GB a 20GB/50GB, porque lee el
    filesystem en cada llamada, no una constante).
  - `atlas_live.data_dir_diagnostics.directory_inventory()` -- top-N
    archivos más grandes, ya ordenados descendente, ya testeado.
  - `atlas_live.capacity_registry` -- persistencia mínima de snapshots
    (nueva, propia, ver ese módulo para el porqué de la separación).

NUNCA inventa una tasa de crecimiento: si no hay suficiente histórico real
(`MIN_SNAPSHOTS_FOR_GROWTH` snapshots separados por al menos
`MIN_SPAN_HOURS_FOR_GROWTH` horas), `growth.history_status` es
`"insufficient_history"` y `mb_per_hour`/`mb_per_day`/toda la proyección
quedan `None` explícitos -- nunca un número fabricado.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pathlib import Path

from atlas.config.config import data_dir
from atlas_live import capacity_registry as creg
from atlas_live.data_dir_diagnostics import directory_inventory, disk_usage_info


def _data_dir_path():
    """Misma resolución exacta que `data_dir_diagnostics._data_dir_path()`
    -- reutiliza `atlas.config.config.data_dir()` (la fuente real), en vez
    de importar el helper privado de otro módulo."""
    return data_dir(Path(__file__).parent)

MB = 1024 * 1024
GB = 1024 * 1024 * 1024

# Umbrales de estado (documentados acá, nunca dispersos en el código que
# los usa -- pedido explícito del usuario).
CAPACITY_WARNING_THRESHOLD_PCT = 80.0
CAPACITY_CRITICAL_THRESHOLD_PCT = 90.0

# Proyección: a qué porcentajes se calculan los "días hasta".
PROJECTION_TARGET_PCTS = (80.0, 90.0, 95.0, 100.0)

# Un snapshot nuevo solo se persiste si pasó al menos esto desde el
# último -- "frecuencia razonable, no por sweep" (pedido explícito).
SNAPSHOT_MIN_INTERVAL_SECONDS = 1800  # 30 min

# Piso de evidencia real antes de calcular CUALQUIER tasa de crecimiento
# -- nunca se infiere una tendencia de 2 puntos casi simultáneos (ruido).
MIN_SNAPSHOTS_FOR_GROWTH = 2
MIN_SPAN_HOURS_FOR_GROWTH = 1.0

# Cuántos consumidores devolver en el reporte (ya ordenados desc. por
# `directory_inventory`, que internamente ordena TODO antes de recortar).
TOP_CONSUMERS_LIMIT = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def compute_status(used_pct: float) -> str:
    """Único lugar del código que traduce un % a OK/WARNING/CRITICAL --
    reutilizado tanto por el reporte completo como por cualquier test."""
    if used_pct >= CAPACITY_CRITICAL_THRESHOLD_PCT:
        return "CRITICAL"
    if used_pct >= CAPACITY_WARNING_THRESHOLD_PCT:
        return "WARNING"
    return "OK"


def _linear_growth_bytes_per_hour(snapshots: List[Dict[str, Any]]) -> Optional[float]:
    """Pendiente por mínimos cuadrados de `used_bytes` vs. horas transcurridas
    desde el primer snapshot de la ventana -- más robusto ante ruido que
    comparar solo el primero y el último punto. `None` explícito (nunca 0
    disfrazado de "sin crecimiento") si no hay suficiente evidencia."""
    if len(snapshots) < MIN_SNAPSHOTS_FOR_GROWTH:
        return None
    t0 = _parse_iso(snapshots[0]["observed_at"])
    xs = [(_parse_iso(s["observed_at"]) - t0).total_seconds() / 3600.0 for s in snapshots]
    ys = [float(s["used_bytes"]) for s in snapshots]
    span_hours = xs[-1] - xs[0]
    if span_hours < MIN_SPAN_HOURS_FOR_GROWTH:
        return None
    n = len(xs)
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    den = sum((x - x_mean) ** 2 for x in xs)
    if den == 0:
        return None
    return num / den  # bytes por hora


def _days_to_threshold(used_bytes: int, total_bytes: int, target_pct: float, bytes_per_day: Optional[float]) -> Optional[float]:
    """`None` explícito si no hay tasa (insufficient_history) o si la tasa
    es <= 0 (estable/decreciente -- nunca "nunca" representado como un
    número inventado). `0.0` si el umbral YA está alcanzado."""
    if bytes_per_day is None or bytes_per_day <= 0:
        return None
    target_bytes = total_bytes * (target_pct / 100.0)
    if used_bytes >= target_bytes:
        return 0.0
    return round((target_bytes - used_bytes) / bytes_per_day, 2)


def capacity_report(path=None) -> Dict[str, Any]:
    """Reporte completo -- puramente de lectura salvo por el registro de UN
    snapshot nuevo, y SOLO si pasó `SNAPSHOT_MIN_INTERVAL_SECONDS` desde el
    último (nunca escribe más seguido que eso, sin importar cuántas veces
    se llame esta función)."""
    root = path if path is not None else _data_dir_path()
    usage = disk_usage_info(root)

    total_bytes = usage.get("total_bytes")
    used_bytes = usage.get("used_bytes")
    free_bytes = usage.get("free_bytes")

    result: Dict[str, Any] = {
        "generated_at": _now(),
        "total_bytes": total_bytes,
        "used_bytes": used_bytes,
        "free_bytes": free_bytes,
        "used_pct": None,
        "free_pct": None,
        "top_consumers": [],
        "growth": {"mb_per_hour": None, "mb_per_day": None, "history_status": "insufficient_history"},
        "projection": {f"days_to_{int(p)}_pct": None for p in PROJECTION_TARGET_PCTS},
        "status": "OK",
    }

    if "disk_usage_error" in usage:
        result["disk_usage_error"] = usage["disk_usage_error"]
        return result  # sin total/used/free reales, no se puede calcular nada más -- nunca se inventa

    used_pct = round(used_bytes / total_bytes * 100.0, 2) if total_bytes else None
    free_pct = round(free_bytes / total_bytes * 100.0, 2) if total_bytes else None
    result["used_pct"] = used_pct
    result["free_pct"] = free_pct
    result["status"] = compute_status(used_pct) if used_pct is not None else "OK"

    inventory = directory_inventory(root, top_n=TOP_CONSUMERS_LIMIT)
    result["top_consumers"] = [
        {"path": e["path"], "size_bytes": e["size_bytes"]} for e in inventory.get("entries", [])
    ]

    # --- snapshot throttleado (pedido explícito: "no por sweep") ---
    latest = creg.latest_snapshot()
    should_record = True
    if latest is not None:
        elapsed = (datetime.now(timezone.utc) - _parse_iso(latest["observed_at"])).total_seconds()
        should_record = elapsed >= SNAPSHOT_MIN_INTERVAL_SECONDS
    if should_record and total_bytes is not None:
        creg.record_snapshot(total_bytes, used_bytes, free_bytes)

    # --- crecimiento + proyección, con TODO el histórico disponible ---
    snapshots = creg.list_snapshots()
    bytes_per_hour = _linear_growth_bytes_per_hour(snapshots)
    if bytes_per_hour is not None:
        bytes_per_day = bytes_per_hour * 24
        result["growth"] = {
            "mb_per_hour": round(bytes_per_hour / MB, 4),
            "mb_per_day": round(bytes_per_day / MB, 2),
            "history_status": "ok",
        }
        result["projection"] = {
            f"days_to_{int(p)}_pct": _days_to_threshold(used_bytes, total_bytes, p, bytes_per_day)
            for p in PROJECTION_TARGET_PCTS
        }
    # si bytes_per_hour es None, result ya trae los defaults de
    # "insufficient_history" seteados arriba -- nunca se sobreescriben con
    # un valor fabricado.

    return result


def capacity_summary_public(path=None) -> Dict[str, Any]:
    """Subconjunto SEGURO para exponer sin token (Cabina) -- reutiliza
    `capacity_report()` tal cual (cero cómputo nuevo, cero snapshot extra:
    ya se registró arriba si correspondía) y **nunca** incluye
    `top_consumers` (rutas de archivo internas del Volume -- innecesario
    para la barra de Cabina, y evita exponer detalle de filesystem sin
    token, mismo criterio que `learning_safety_summary.py` con
    `full_*_report()`)."""
    full = capacity_report(path=path)
    return {
        "generated_at": full["generated_at"],
        "total_bytes": full["total_bytes"],
        "used_bytes": full["used_bytes"],
        "free_bytes": full["free_bytes"],
        "used_pct": full["used_pct"],
        "free_pct": full["free_pct"],
        "growth": full["growth"],
        "projection": full["projection"],
        "status": full["status"],
    }
