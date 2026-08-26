"""Orquesta el ciclo EXPERIENCIA → CONOCIMIENTO (2026-08-25, Fase 3/5 del
circuito de aprendizaje, autorizado explícitamente). NO conecta nada a
CONOCIMIENTO → DECISIÓN -- esa flecha sigue sin existir, deliberadamente.

Combina, sin duplicar su lógica:
  - `live_experience_scoring.py` (Fase 1) -- calcula la tabla de
    conocimiento a partir de `candidate_detection`/`candidate_outcome`.
  - `live_experience_knowledge.py` (Fase 2) -- la persiste, append-only.

Este módulo es la ÚNICA pieza nueva de Fase 3: un orquestador delgado,
aislado (nunca propaga una excepción -- "el aprendizaje no puede tumbar
Atlas"), que un disparador externo (Fase 3 también: el hilo del radar
después del EOD, o un endpoint admin manual) puede llamar con un solo
argumento (`as_of_date`) y obtener un resumen verificable de qué pasó."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence

from atlas_live.learning import experiments
from atlas_live.learning import live_experience_knowledge as lek
from atlas_live.learning import live_experience_scoring as les


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_experience_learning_cycle(
    as_of_date: str,
    feature_cols: Sequence[str] = ("volatility_14d_pct",),
    min_rows: int = experiments.MIN_PRIOR_ROWS_FOR_CUTS,
) -> Dict[str, Any]:
    """Corre UNA vez el ciclo completo: carga experiencias walk-forward-
    seguras (`market_date < as_of_date`, `is_final=1`,
    `confiable_para_aprendizaje=1` -- mismo criterio ya existente, no se
    inventa otro), calcula la tabla de conocimiento (Fase 1, sin
    modificarla) y la persiste (Fase 2, append-only, sin modificarla).

    Nunca deja que una experiencia recién insertada por ESTE MISMO cálculo
    contamine el cálculo -- `_load_rows_from_db()` (Fase 1) solo lee
    `candidate_detection`/`candidate_outcome` (experiencia cruda), nunca
    `live_experience_knowledge` (el conocimiento que este módulo genera);
    no existe ningún camino por el que el conocimiento recién calculado
    pueda retroalimentar su propio cálculo.

    Aislado por diseño: CUALQUIER excepción (consulta rota, SQLite
    inaccesible, fila inesperada) queda capturada acá adentro -- nunca se
    relanza. El llamador (el hilo del radar, o un endpoint manual) siempre
    recibe un dict, nunca una excepción sin atrapar.

    Sin experiencias válidas, termina limpiamente: `ok=True`,
    `n_experiencias=0`, nada se inserta -- no es un error, es un resultado
    real y honesto (todavía no hay evidencia)."""
    resumen: Dict[str, Any] = {
        "as_of_date": as_of_date,
        "ejecutado_at": _now_iso(),
        "ok": False,
        "n_experiencias": 0,
        "n_grupos": 0,
        "n_grupos_robustos": 0,
        "n_insertadas": 0,
        "methodology_version": lek.METHODOLOGY_VERSION,
        "error": None,
    }
    try:
        # Una sola lectura de experiencias -- se reutiliza tanto para el
        # conteo reportado como para el cálculo en sí (nunca dos consultas
        # que podrían divergir).
        rows = les._load_rows_from_db(as_of_date)
        resumen["n_experiencias"] = len(rows)

        if not rows:
            resumen["ok"] = True
            return resumen

        tabla = les.compute_own_experience_table(
            as_of_date, feature_cols=feature_cols, min_rows=min_rows, rows=rows,
        )
        resumen["n_grupos"] = len(tabla)
        resumen["n_grupos_robustos"] = sum(1 for f in tabla if f["validation_state"] == "VALIDACION_ROBUSTA")

        n_insertadas = lek.record_experience_knowledge(tabla)
        resumen["n_insertadas"] = n_insertadas
        resumen["ok"] = True
    except Exception as exc:  # el aprendizaje NUNCA puede tumbar al llamador
        resumen["error"] = f"{type(exc).__name__}: {exc}"
        resumen["ok"] = False
    return resumen
