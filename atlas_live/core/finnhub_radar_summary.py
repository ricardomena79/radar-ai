"""HITO 2 -- Observabilidad real del presupuesto Finnhub + chunks del
Radar (2026-09-14, PLAN Radar/Finnhub, autorizado explícitamente).

Endpoint público de solo lectura, sin token: expone (a) el estado REAL en
memoria del presupuesto compartido de Finnhub (`finnhub_shared_budget`,
por consumidor, pisos protegidos, límite total seguro) y (b) el
diagnóstico del ÚLTIMO barrido REAL del radar (universo, chunks OK/error,
detalle de error por chunk, duración, timestamp) -- nunca inventado ni
reconstruido desde otra tabla, tal como exige el plan.

Mismo patrón exacto que `learning_safety_summary.py`: cada bloque en su
propio try/except, nunca lanza, nunca expone secretos (`TRADIER_API_TOKEN`/
`FINNHUB_API_KEY` no aparecen en ninguna de las 2 fuentes leídas acá)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get(d: Any, key: str, default: Any) -> Any:
    if not isinstance(d, dict):
        return default
    valor = d.get(key, default)
    return default if valor is None else valor


def _default_summary() -> Dict[str, Any]:
    return {
        "generated_at": _now(),
        "finnhub_budget": {"ok": False, "por_consumidor": {}, "pisos_protegidos_por_minuto": {}, "limite_total_seguro_por_minuto": None, "fail_safe_events": None},
        "ultimo_sweep_radar": {
            "ok": False,
            "disponible": False,
            "ultimo_sweep_at": None,
            "ultimo_sweep_duracion_s": None,
            "sweeps_total": None,
            "sweeps_ok": None,
            "sweeps_error": None,
            "ultimo_tradier_error": None,
            "diagnostics": None,
        },
    }


def build_finnhub_radar_summary() -> Dict[str, Any]:
    """Nunca lanza. Cada bloque se calcula en su propio try/except -- un
    fallo en uno no vacía al otro."""
    resultado = _default_summary()

    try:
        from atlas_live.data_fusion import finnhub_shared_budget as fsb

        metrics = fsb.get_metrics()
        resultado["finnhub_budget"] = {
            "ok": True,
            "por_consumidor": _get(metrics, "por_consumidor", {}),
            "pisos_protegidos_por_minuto": _get(metrics, "pisos_protegidos_por_minuto", {}),
            "limite_total_seguro_por_minuto": _get(metrics, "limite_total_seguro_por_minuto", None),
            "fail_safe_events": _get(metrics, "fail_safe_events", None),
        }
    except Exception:
        pass

    try:
        from atlas_live.radar import candidate_registry as reg
        from atlas_live.radar import radar_worker as rw

        meta = reg.get_meta()
        diag = rw.get_last_diagnostics()
        resultado["ultimo_sweep_radar"] = {
            "ok": True,
            "disponible": diag is not None,
            "ultimo_sweep_at": _get(meta, "ultimo_sweep_at", None),
            "ultimo_sweep_duracion_s": _get(meta, "ultimo_sweep_duracion_s", None),
            "sweeps_total": _get(meta, "sweeps_total", None),
            "sweeps_ok": _get(meta, "sweeps_ok", None),
            "sweeps_error": _get(meta, "sweeps_error", None),
            "ultimo_tradier_error": _get(meta, "ultimo_tradier_error", None),
            "diagnostics": diag.to_dict() if diag is not None and hasattr(diag, "to_dict") else None,
        }
    except Exception:
        pass

    return resultado
