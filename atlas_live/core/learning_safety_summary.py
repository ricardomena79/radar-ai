"""HITO 4 -- Fase 4.3 (2026-09-04, autorizado explícitamente en Plan Mode):
resumen agregado SEGURO y PÚBLICO del estado de las 4 capas de Hito 3
(elegibilidad/shadow observation/activación/evaluación continua) -- resuelve
la limitación repetida en la auditoría de cierre de Hito 3: "no verificable
sin ATLAS_ADMIN_TOKEN".

Reutiliza ÍNTEGRAMENTE los 4 `full_*_report()` ya existentes y ya
testeados (`knowledge_eligibility_registry`, `shadow_observation_registry`,
`activation_registry`, `continuous_evaluation_registry`) -- cero consultas
SQL nuevas. Extrae ÚNICAMENTE conteos agregados (`conteos_por_estado`/
`n_eventos`/`n_observaciones`) -- NUNCA copia la clave `eventos` (que trae
detalle por ticker/condición, incluyendo Wilson/baseline reales) a la
salida pública. Ver `test_learning_safety_summary.py` para la prueba
explícita, recursiva, de que ninguna fuga de detalle ocurre.

Puro orquestador de lectura -- nunca escribe nada, nunca activa nada,
nunca lanza (cada sub-bloque queda aislado en su propio try/except, un
fallo en una capa no puede vaciar las otras 3)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

_UNIVERSO_GRUPOS = ("A_sin_elegible", "B_elegible_sin_divergencia", "C_elegible_con_divergencia")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get(d: Any, key: str, default: Any) -> Any:
    """`dict.get(key, default)` NO protege contra un valor explícitamente
    `None` presente para esa clave (solo contra la clave ausente) -- si
    algún `full_*_report()` alguna vez devolviera `None` en vez de `{}`/`0`
    para un campo (hoy ninguno lo hace, confirmado leyendo las 4 fuentes,
    pero es una garantía externa, no propia), este resumen público NUNCA
    debe propagar ese `None` tal cual (hallazgo real durante la auditoría
    de validación de Fase 4.3, 2026-09-04, reproducido con un mock)."""
    if not isinstance(d, dict):
        return default
    valor = d.get(key, default)
    return default if valor is None else valor


def _default_summary() -> Dict[str, Any]:
    return {
        "generated_at": _now(),
        "activation_mechanism_state": "OFF",
        "eligibilidad": {"ok": False, "n_eventos": 0, "conteos_por_estado": {}},
        "shadow_observation": {"ok": False, "n_observaciones": 0, "universo_conocimiento_conteos": {}},
        "activacion": {"ok": False, "n_eventos": 0, "conteos_por_estado": {}, "n_revocaciones_registradas": 0},
        "evaluacion_continua": {
            "ok": False, "n_eventos": 0, "conteos_por_estado": {}, "n_revocaciones_disparadas": 0,
        },
    }


def build_safety_summary() -> Dict[str, Any]:
    """Nunca lanza. Cada uno de los 4 bloques se calcula en su propio
    try/except -- si una capa falla (DB inexistente, error de lectura),
    ese bloque queda en su default seguro (`ok=False`, conteos en 0) sin
    afectar a las demás."""
    resultado = _default_summary()

    try:
        from atlas_live.core import knowledge_eligibility_registry as ker

        rep = ker.full_eligibility_report()
        resultado["eligibilidad"] = {
            "ok": bool(_get(rep, "ok", False)),
            "n_eventos": _get(rep, "n_eventos", 0),
            "conteos_por_estado": _get(rep, "conteos_por_estado", {}),
        }
    except Exception:
        pass

    try:
        from atlas_live.core import shadow_observation_registry as sor

        rep = sor.full_shadow_observation_report()
        universo = _get(rep, "universo_conocimiento", {})
        resultado["shadow_observation"] = {
            "ok": bool(_get(rep, "ok", False)),
            "n_observaciones": _get(rep, "n_observaciones", 0),
            "universo_conocimiento_conteos": {
                grupo: _get(_get(universo, grupo, {}), "n_eventos", 0) for grupo in _UNIVERSO_GRUPOS
            },
        }
    except Exception:
        pass

    try:
        from atlas_live.core import activation_registry as areg

        resultado["activation_mechanism_state"] = areg.get_mechanism_state() or "OFF"
        rep = areg.full_activation_report()
        resultado["activacion"] = {
            "ok": bool(_get(rep, "ok", False)),
            "n_eventos": _get(rep, "n_eventos", 0),
            "conteos_por_estado": _get(rep, "conteos_por_estado", {}),
            "n_revocaciones_registradas": len(areg.list_revocations() or []),
        }
    except Exception:
        pass

    try:
        from atlas_live.core import continuous_evaluation_registry as cer

        rep = cer.full_continuous_evaluation_report()
        resultado["evaluacion_continua"] = {
            "ok": bool(_get(rep, "ok", False)),
            "n_eventos": _get(rep, "n_eventos", 0),
            "conteos_por_estado": _get(rep, "conteos_por_estado", {}),
            "n_revocaciones_disparadas": _get(rep, "n_revocaciones_disparadas", 0),
        }
    except Exception:
        pass

    return resultado
