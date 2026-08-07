"""Panel de Desempeño de Atlas (2026-08-07, ver DECISION_LOG.md).

Dos niveles, aprobados explícitamente por el usuario, que nunca se
mezclan:
  - **Nivel 1 -- Oportunidad Oficial del Día**: la fila `rank=1` del
    ranking sellado de hoy (Prediction Journal). Un único registro.
  - **Nivel 2 -- Rendimiento histórico de Atlas**: todo el top-20
    sellado que ya cerró su trayectoria (Exit Journal) -- muestra mucho
    más grande, la fuente oficial de las estadísticas globales.

**Principio explícito del usuario, no negociable**: "acierto del
modelo" y "rentabilidad obtenida" son dos conceptos separados, nunca
mezclados en la misma métrica.
  - Acierto del modelo = `category == "EXPLOSION"` -- el mismo umbral
    que ya usa el Clasificador (`classifier.py`) en todo el proyecto,
    reclasificando `final_return_pct` (el Exit Journal no guarda
    categoría, solo el retorno crudo -- no se inventa una nueva regla,
    se reutiliza la que ya existe).
  - Rentabilidad = `final_return_pct`, tratado como métrica financiera
    aparte (Win Rate financiero, Profit Factor, ganancia/pérdida
    promedio, expectativa, drawdown).

**Atlas Score**: combinación configurable de ambos grupos, con pesos en
`performance_config.json` -- no fijos en código porque no hay evidencia
que justifique una ponderación específica sobre otra (mismo criterio ya
usado para los pesos de Radar Explosivo en `explosive_config.json`).
Gobernanza aprobada: todo cambio a esos pesos se registra en
`DECISION_LOG.md` con fecha, pesos anteriores, nuevos y justificación.

**Drawdown**: no representa capital real -- Atlas no gestiona una
cuenta, evalúa oportunidades independientes. Es una curva hipotética
(capital inicial 100, una unidad fija sumada por operación, sin interés
compuesto), etiquetada como tal en cualquier lugar donde se muestre.

100% de solo lectura sobre Prediction Journal, Exit Journal y Memory
Store -- este módulo no escribe nada y no modifica ningún umbral ya
existente en `classifier.py`.
"""

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas_live.memory import classifier
from atlas_live.memory import exit_journal as ej
from atlas_live.memory import market_hours
from atlas_live.memory import prediction_journal as pj
from atlas_live.memory import store

CONFIG_PATH = Path(__file__).parent / "performance_config.json"

_DEFAULT_WEIGHTS = {
    "tasa_acierto": 0.30,
    "win_rate_financiero": 0.25,
    "profit_factor": 0.25,
    "expectativa": 0.10,
    "drawdown": 0.10,
}

# Categorías del Clasificador que implican que el símbolo fue elegible
# para Radar Explosivo en su momento (ver classifier.py) -- "detectada"
# se apoya en esa misma distinción ya existente, no en un campo nuevo.
_CATEGORIAS_DETECTADAS = {"EXPLOSION", "FALSE_BREAKOUT"}


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {"atlas_score_weights": dict(_DEFAULT_WEIGHTS)}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Nivel 1 -- Oportunidad Oficial del Día
# ---------------------------------------------------------------------------

def get_daily_opportunity(date: Optional[str] = None) -> Dict[str, Any]:
    """La fila `rank=1` del ranking sellado de `date` (hoy si no se
    especifica). "Resultado" y "Rentabilidad" son campos separados a
    propósito -- nunca el mismo número."""
    date = date or market_hours.market_date()
    sellados = pj.get_sealed_predictions(date)
    if not sellados:
        return {"date": date, "available": False}

    top = sellados[0]
    acierto = (top["result_category"] == "EXPLOSION") if top["result_category"] else None
    return {
        "date": date,
        "available": True,
        "symbol": top["symbol"],
        "graded": top["graded_at"] is not None,
        "resultado": ("Ganó" if acierto else "Perdió") if acierto is not None else None,
        "categoria_real": top["result_category"],
        "rentabilidad_pct": top["result_change_pct"],
        "tiempo_hasta_objetivo_min": top["anticipation_minutes"],
        "motivo": top["explanation"],
    }


# ---------------------------------------------------------------------------
# Nivel 2 -- Rendimiento histórico de Atlas (Exit Journal)
# ---------------------------------------------------------------------------

def _categoria_real(resumen: Dict[str, Any]) -> Optional[str]:
    if resumen.get("final_return_pct") is None:
        return None
    obs = {"ground_truth_change_pct": resumen["final_return_pct"], "explosive": {"eligible": True}}
    return classifier.classify_observation(obs)


def _financial_stats(retornos: List[float]) -> Dict[str, Any]:
    if not retornos:
        return {
            "win_rate_financiero_pct": None, "profit_factor": None,
            "ganancia_promedio_pct": None, "perdida_promedio_pct": None,
            "expectativa_pct": None,
        }
    ganadoras = [r for r in retornos if r > 0]
    perdedoras = [r for r in retornos if r <= 0]
    win_rate = len(ganadoras) / len(retornos) * 100
    ganancia_prom = statistics.mean(ganadoras) if ganadoras else 0.0
    perdida_prom = statistics.mean(perdedoras) if perdedoras else 0.0
    suma_ganancias = sum(ganadoras)
    suma_perdidas = abs(sum(perdedoras))
    if suma_perdidas > 0:
        profit_factor = suma_ganancias / suma_perdidas
    else:
        profit_factor = None if suma_ganancias == 0 else float("inf")
    wr = win_rate / 100
    expectativa = wr * ganancia_prom - (1 - wr) * abs(perdida_prom)
    return {
        "win_rate_financiero_pct": win_rate,
        "profit_factor": profit_factor,
        "ganancia_promedio_pct": ganancia_prom,
        "perdida_promedio_pct": perdida_prom,
        "expectativa_pct": expectativa,
    }


def _drawdown_hipotetico(retornos_ordenados: List[float]) -> Optional[float]:
    """Curva de capital hipotética -- empieza en 100, suma secuencialmente
    cada retorno (una unidad fija por operación, sin interés compuesto).
    Devuelve la mayor caída pico-a-valle, en puntos. NO representa
    capital real."""
    if not retornos_ordenados:
        return None
    capital = 100.0
    pico = capital
    max_caida = 0.0
    for r in retornos_ordenados:
        capital += r
        pico = max(pico, capital)
        max_caida = max(max_caida, pico - capital)
    return max_caida


def _agrupar_por_dia(resumenes: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grupos: Dict[str, List[Dict[str, Any]]] = {}
    for r in resumenes:
        grupos.setdefault(r["date"], []).append(r)
    return grupos


def _detectadas_vs_acertadas(date: str) -> Dict[str, Any]:
    """Sobre TODO el universo escaneado ese día (Memory Store), no solo
    el top-20 sellado -- "detectada" es una pregunta de cobertura de
    detección, distinta de la rentabilidad de lo seleccionado."""
    obs_hoy = store.get_observations(date=date)
    detectadas = [o for o in obs_hoy if o["category"] in _CATEGORIAS_DETECTADAS]
    acertadas = [o for o in detectadas if o["category"] == "EXPLOSION"]
    return {
        "detectadas": len(detectadas),
        "acertadas": len(acertadas),
        "tasa_pct": (len(acertadas) / len(detectadas) * 100) if detectadas else None,
    }


def _atlas_score(tasa_acierto_pct: Optional[float], fin: Dict[str, Any], drawdown: Optional[float],
                  pesos: Dict[str, float]) -> Dict[str, Any]:
    """Combinación configurable -- cada componente normalizado a 0-100
    antes de ponderar. `None` en cualquier componente lo excluye del
    cálculo (no se fabrica un valor para un dato ausente); el score
    final es `None` si no hay ningún componente disponible."""
    def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
        return max(lo, min(hi, v))

    componentes: Dict[str, Optional[float]] = {}
    componentes["tasa_acierto"] = tasa_acierto_pct  # ya es 0-100

    if fin["win_rate_financiero_pct"] is not None:
        componentes["win_rate_financiero"] = fin["win_rate_financiero_pct"]
    else:
        componentes["win_rate_financiero"] = None

    pf = fin["profit_factor"]
    if pf is None:
        componentes["profit_factor"] = None
    else:
        pf_finito = min(pf, 3.0) if pf != float("inf") else 3.0
        componentes["profit_factor"] = _clamp(pf_finito / 3.0 * 100)

    exp = fin["expectativa_pct"]
    if exp is None:
        componentes["expectativa"] = None
    else:
        # -5% a +5% de expectativa mapeado linealmente a 0-100 -- rango
        # de partida, ajustable con evidencia acumulada (no una escala
        # "demostrada").
        componentes["expectativa"] = _clamp((exp + 5.0) / 10.0 * 100)

    if drawdown is None:
        componentes["drawdown"] = None
    else:
        # Menor drawdown = más puntos. 0 puntos de caída = 100 puntos de
        # score; 50 puntos de caída o más = 0. Rango de partida, mismo
        # criterio que arriba.
        componentes["drawdown"] = _clamp(100 - (drawdown / 50.0 * 100))

    disponibles = {k: v for k, v in componentes.items() if v is not None}
    if not disponibles:
        return {"score": None, "componentes": componentes, "pesos_usados": pesos}

    peso_total = sum(pesos.get(k, 0.0) for k in disponibles)
    if peso_total == 0:
        return {"score": None, "componentes": componentes, "pesos_usados": pesos}

    score = sum(disponibles[k] * pesos.get(k, 0.0) for k in disponibles) / peso_total
    return {"score": round(score, 1), "componentes": componentes, "pesos_usados": pesos}


def get_global_performance(now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    hoy = market_hours.market_date(now)

    todos = ej.get_summaries_between()
    cerrados = [r for r in todos if r.get("final_return_pct") is not None]
    retornos = [r["final_return_pct"] for r in cerrados]

    categorias = [_categoria_real(r) for r in cerrados]
    aciertos = sum(1 for c in categorias if c == "EXPLOSION")
    tasa_acierto = (aciertos / len(cerrados) * 100) if cerrados else None

    fin = _financial_stats(retornos)
    drawdown = _drawdown_hipotetico(retornos)

    por_dia = _agrupar_por_dia(cerrados)
    de_hoy = por_dia.get(hoy, [])
    mejor_hoy = max((r["final_return_pct"] for r in de_hoy), default=None)
    peor_hoy = min((r["final_return_pct"] for r in de_hoy), default=None)

    sellados_hoy = pj.get_sealed_predictions(hoy) if pj.is_sealed(hoy) else []
    cerrados_hoy_simbolos = {r["symbol"] for r in de_hoy}
    abiertas_hoy = len([s for s in sellados_hoy if s["symbol"] not in cerrados_hoy_simbolos])

    hoy_dt = datetime.strptime(hoy, "%Y-%m-%d").date()
    semana_actual = f"{hoy_dt.isocalendar()[0]}-W{hoy_dt.isocalendar()[1]:02d}"
    mes_actual = hoy_dt.strftime("%Y-%m")

    def _win_rate_periodo(filtro) -> Optional[float]:
        retornos_periodo = [r["final_return_pct"] for r in cerrados if filtro(r)]
        return _financial_stats(retornos_periodo)["win_rate_financiero_pct"]

    win_rate_semanal = _win_rate_periodo(
        lambda r: f"{datetime.strptime(r['date'], '%Y-%m-%d').date().isocalendar()[0]}-W{datetime.strptime(r['date'], '%Y-%m-%d').date().isocalendar()[1]:02d}" == semana_actual
    )
    win_rate_mensual = _win_rate_periodo(lambda r: r["date"][:7] == mes_actual)

    evolucion = [
        {
            "fecha": fecha,
            "win_rate_pct": _financial_stats([r["final_return_pct"] for r in filas])["win_rate_financiero_pct"],
            "n": len(filas),
        }
        for fecha, filas in sorted(por_dia.items())
    ]

    cfg = load_config()
    score = _atlas_score(tasa_acierto, fin, drawdown, cfg["atlas_score_weights"])

    return {
        "date": hoy,
        "recomendaciones_emitidas_hoy": len(sellados_hoy),
        "operaciones_abiertas_hoy": abiertas_hoy,
        "operaciones_cerradas_hoy": len(de_hoy),
        "precision_del_modelo": {
            "tasa_acierto_pct": tasa_acierto,
            "muestra": len(cerrados),
            "detectadas_vs_acertadas": _detectadas_vs_acertadas(hoy),
        },
        "rendimiento_financiero": {
            **fin,
            "drawdown_hipotetico_pct": drawdown,
            "mejor_operacion_hoy_pct": mejor_hoy,
            "peor_operacion_hoy_pct": peor_hoy,
        },
        "win_rate_periodos": {
            "diario_pct": _financial_stats([r["final_return_pct"] for r in de_hoy])["win_rate_financiero_pct"],
            "semanal_pct": win_rate_semanal,
            "mensual_pct": win_rate_mensual,
        },
        "evolucion": evolucion,
        "atlas_score": score,
    }
