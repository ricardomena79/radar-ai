"""Veredicto BASE vs INFORMADA (2026-09-11, autorizado explícitamente en
Plan Mode -- misión "HACER OPERATIVO EL APRENDIZAJE REAL").

La auditoría de esta sesión (ver plan) encontró que la infraestructura
para comparar la decisión BASE (`atlas_decision.decision`, ciega a
`learned_evidence`) contra la decisión INFORMADA (`shadow_decision
.decision_shadow`) YA existe y YA reconstruye, por lectura pura de
`decision_knowledge_snapshot` (Hito 3.0, histórico completo, no solo
hacia adelante), un universo A (sin conocimiento elegible) / B (elegible,
sin divergencia) / C (elegible, con divergencia) con el veredicto
ACIERTO/ERROR/AMBIGUO/PENDIENTE de cada decisión contra el outcome real
(`shadow_observation_registry.construir_universo_abc()`). Lo que faltaba
-- y lo que agrega este módulo -- es la capa de rigor estadístico exigida
por la misión: comparación APAREADA (mismo caso, no dos muestras
independientes), intervalo de Wilson, y un veredicto en el vocabulario
exacto pedido, nunca más fuerte que la evidencia.

Puro: sin DB, sin red, sin ninguna lectura del reloj del sistema -- el
walk-forward ya fue garantizado, de forma independiente, por 3 capas
anteriores (`learned_evidence.py`, `knowledge_eligibility.py`,
`decision_knowledge_registry`/`construir_universo_abc()`) antes de que
cualquier evento llegue acá; este módulo solo agrega conteos ya filtrados,
nunca reevalúa fechas.

Metodología (comparación apareada, mismo caso en ambos brazos):
- Grupo C ("elegible, con divergencia") es la única población donde
  BASE != INFORMADA -- ahí, y solo ahí, tiene sentido preguntar "¿a cuál
  de las dos le fue mejor?".
- Para cada caso evaluable de C (outcome real disponible), se compara el
  veredicto de BASE contra el de INFORMADA. Un caso es "discordante" si
  uno acertó y el otro no (`mejora` = BASE erró, INFORMADA acertó;
  `empeoramiento` = lo inverso). Los casos donde ambos acertaron, ambos
  erraron, o alguno quedó AMBIGUO, son "empates" -- no aportan evidencia
  de diferencia (mismo principio que un test de McNemar: solo los pares
  discordantes informan sobre la dirección del efecto).
- Sobre los discordantes, se calcula el intervalo de Wilson (reutilizado
  de `candidate_registry.wilson_confidence_interval()`, sin reimplementar)
  de la proporción que favorece a INFORMADA. Si ese intervalo queda
  enteramente por encima de 50% Y el número de casos evaluables alcanza
  el mismo piso oficial que ya usa el resto del proyecto para hablar de
  evidencia "robusta" (`candidate_registry.META_MUESTRA_MINIMA`, 500 --
  ningún umbral nuevo), se declara aprendizaje operativo demostrado."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

VERDICTS = (
    "APRENDIZAJE_OPERATIVO_DEMOSTRADO",
    "CAMBIO_OBSERVADO_MEJORA_NO_DEMOSTRADA",
    "CONOCIMIENTO_CONSULTADO_SIN_EFECTO_DECISIONAL",
    "NO_EVALUABLE",
)

_GRUPO_A = "A_sin_elegible"
_GRUPO_B = "B_elegible_sin_divergencia"
_GRUPO_C = "C_elegible_con_divergencia"

_VEREDICTOS_ACIERTO = ("ACIERTO",)
_VEREDICTOS_ERROR = ("ERROR",)


def _eventos(universo: Dict[str, Any], grupo: str) -> List[Dict[str, Any]]:
    bloque = universo.get(grupo) if isinstance(universo, dict) else None
    if not isinstance(bloque, dict):
        return []
    eventos = bloque.get("eventos")
    return eventos if isinstance(eventos, list) else []


def _clasificar_par(baseline_veredicto: str, shadow_veredicto: str) -> str:
    """Un par evaluable -> "mejora" (INFORMADA acertó, BASE no), "empeoramiento"
    (inverso), o "empate" (cualquier otra combinación, incluido AMBIGUO)."""
    if baseline_veredicto in _VEREDICTOS_ERROR and shadow_veredicto in _VEREDICTOS_ACIERTO:
        return "mejora"
    if baseline_veredicto in _VEREDICTOS_ACIERTO and shadow_veredicto in _VEREDICTOS_ERROR:
        return "empeoramiento"
    return "empate"


def build_verdict(
    universo_conocimiento: Dict[str, Any],
    *,
    piso_muestra_minima: int = 500,
) -> Dict[str, Any]:
    """Construye el veredicto BASE vs INFORMADA a partir del
    `universo_conocimiento` YA calculado por
    `shadow_observation_registry.construir_universo_abc()` (o del mismo
    dict embebido en `full_shadow_observation_report()["universo_conocimiento"]`)
    -- nunca recalcula elegibilidad/walk-forward/outcome, solo agrega.

    `piso_muestra_minima` por defecto es
    `candidate_registry.META_MUESTRA_MINIMA` (500) -- se recibe como
    parámetro en vez de importarlo directamente para mantener este módulo
    sin ninguna dependencia de I/O; el caller real (`learning_safety_summary`)
    pasa el valor oficial.

    Nunca lanza: cualquier estructura inesperada en `universo_conocimiento`
    se trata como "sin datos" (conteos en 0), nunca como excepción."""
    n_a = len(_eventos(universo_conocimiento, _GRUPO_A))
    n_b = len(_eventos(universo_conocimiento, _GRUPO_B))
    eventos_c = _eventos(universo_conocimiento, _GRUPO_C)
    n_c = len(eventos_c)

    n_evaluable = 0
    n_pendiente = 0
    mejoras = 0
    empeoramientos = 0
    empates = 0
    aciertos_baseline = 0
    aciertos_shadow = 0

    for evento in eventos_c:
        baseline_v = evento.get("decision_baseline_veredicto")
        shadow_v = evento.get("decision_shadow_veredicto")
        if baseline_v == "PENDIENTE" or shadow_v == "PENDIENTE" or baseline_v is None or shadow_v is None:
            n_pendiente += 1
            continue
        n_evaluable += 1
        if baseline_v in _VEREDICTOS_ACIERTO:
            aciertos_baseline += 1
        if shadow_v in _VEREDICTOS_ACIERTO:
            aciertos_shadow += 1
        clasificacion = _clasificar_par(baseline_v, shadow_v)
        if clasificacion == "mejora":
            mejoras += 1
        elif clasificacion == "empeoramiento":
            empeoramientos += 1
        else:
            empates += 1

    discordantes = mejoras + empeoramientos

    hit_rate_baseline_pct = round(100.0 * aciertos_baseline / n_evaluable, 2) if n_evaluable else None
    hit_rate_shadow_pct = round(100.0 * aciertos_shadow / n_evaluable, 2) if n_evaluable else None

    from atlas_live.radar.candidate_registry import wilson_confidence_interval

    wilson_ci_baseline = wilson_confidence_interval(aciertos_baseline, n_evaluable) if n_evaluable else None
    wilson_ci_shadow = wilson_confidence_interval(aciertos_shadow, n_evaluable) if n_evaluable else None
    wilson_ci_diferencia_apareada = (
        wilson_confidence_interval(mejoras, discordantes) if discordantes else None
    )

    veredicto, motivo = _determinar_veredicto(
        n_c=n_c,
        n_evaluable=n_evaluable,
        discordantes=discordantes,
        wilson_ci_diferencia_apareada=wilson_ci_diferencia_apareada,
        piso_muestra_minima=piso_muestra_minima,
    )

    return {
        "veredicto": veredicto,
        "motivo": motivo,
        "piso_muestra_minima": piso_muestra_minima,
        "universo": {"n_A_sin_elegible": n_a, "n_B_elegible_sin_divergencia": n_b, "n_C_elegible_con_divergencia": n_c},
        "grupo_C_detalle": {
            "n_evaluable": n_evaluable,
            "n_pendiente": n_pendiente,
            "mejoras": mejoras,
            "empeoramientos": empeoramientos,
            "empates": empates,
            "discordantes": discordantes,
        },
        "hit_rate_baseline_pct": hit_rate_baseline_pct,
        "hit_rate_shadow_pct": hit_rate_shadow_pct,
        "wilson_ci_baseline": list(wilson_ci_baseline) if wilson_ci_baseline else None,
        "wilson_ci_shadow": list(wilson_ci_shadow) if wilson_ci_shadow else None,
        "wilson_ci_diferencia_apareada_pct_favorable_a_shadow": (
            list(wilson_ci_diferencia_apareada) if wilson_ci_diferencia_apareada else None
        ),
    }


def _determinar_veredicto(
    *,
    n_c: int,
    n_evaluable: int,
    discordantes: int,
    wilson_ci_diferencia_apareada: Optional[Tuple[float, float]],
    piso_muestra_minima: int,
) -> Tuple[str, str]:
    if n_c == 0:
        return (
            "CONOCIMIENTO_CONSULTADO_SIN_EFECTO_DECISIONAL",
            "0 casos donde la decisión informada por conocimiento elegible difiera de la decisión base "
            "(grupo C vacío) -- el conocimiento se consulta pero nunca cambia una decisión.",
        )
    if n_evaluable < piso_muestra_minima:
        return (
            "NO_EVALUABLE",
            f"{n_c} decisión(es) cambiaron por conocimiento elegible, pero solo {n_evaluable} tienen outcome "
            f"real evaluable -- por debajo del piso de muestra ({piso_muestra_minima}) para declarar cualquier "
            "veredicto de mejora/empeoramiento.",
        )
    if discordantes == 0:
        return (
            "CAMBIO_OBSERVADO_MEJORA_NO_DEMOSTRADA",
            f"{n_evaluable} casos evaluables con decisión cambiada, pero 0 pares discordantes (ningún caso "
            "donde una decisión acertó y la otra no) -- no hay evidencia de que el cambio de decisión haya "
            "afectado el resultado.",
        )
    if wilson_ci_diferencia_apareada is not None and wilson_ci_diferencia_apareada[0] > 50.0:
        return (
            "APRENDIZAJE_OPERATIVO_DEMOSTRADO",
            f"De {discordantes} pares discordantes, el intervalo de Wilson de la proporción favorable a la "
            f"decisión informada ({wilson_ci_diferencia_apareada[0]}%-{wilson_ci_diferencia_apareada[1]}%) "
            "queda enteramente por encima de 50%, con muestra evaluable sobre el piso oficial.",
        )
    return (
        "CAMBIO_OBSERVADO_MEJORA_NO_DEMOSTRADA",
        f"De {discordantes} pares discordantes, el intervalo de Wilson de la proporción favorable a la "
        f"decisión informada no queda enteramente por encima de 50% -- el cambio de decisión no muestra una "
        "ventaja estadísticamente defendible.",
    )
