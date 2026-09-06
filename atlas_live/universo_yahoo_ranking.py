"""TOP 100 EN MOVIMIENTO (2026-09-06, Universo Yahoo -- capa de
descubrimiento, autorizado explícitamente).

Ranking puramente mecánico y determinista sobre las quotes que el radar YA
obtiene (`atlas_live.radar.radar_worker.get_last_quotes()`, Tradier, ya en
memoria) -- este módulo nunca dispara ninguna consulta a ningún proveedor,
ni siquiera importa un cliente de red: recibe el dict de quotes ya resuelto
como parámetro y solo hace aritmética pura sobre él.

Deliberadamente AISLADO de `candidate_gates.py`, `priority_classifier.py`,
el pipeline de Oportunidades, `atlas_decision_core.py`/`DecisionEngine`, y
de cualquier scoring histórico o probabilístico de Atlas -- este archivo no
los importa, no los llama, no reutiliza ninguno de sus umbrales. Fórmula y
filtros exactos, aprobados explícitamente por el usuario, sin pesos, sin
normalización, sin capado, sin thresholds adicionales a los 4 listados:

FILTROS (en este orden, TODOS deben cumplirse para que un símbolo entre a
la etapa de score -- ninguno de los 4 es negociable ni se completa con un
valor supuesto):
  1. `change_percent is not None`
  2. `relative_volume is not None`
  3. `last_price * volume >= DOLLAR_VOLUME_FLOOR` ($50.000 -- mismo piso ya
     calibrado y usado en `candidate_registry.classify_learning_quality()`;
     se reutiliza acá EXCLUSIVAMENTE como filtro de liquidez, nunca como
     componente del score)
  4. `change_percent > 0` (solo alzas -- herramienta de descubrimiento de
     subas, no un listado general de movedores)

SCORE (solo sobre lo que pasó los 4 filtros):
  `score = change_percent * relative_volume`

ORDEN: `score` descendente; empate desambiguado por símbolo ascendente
(A-Z) -- únicamente para que el orden sea determinista y reproducible ante
un empate exacto de score, nunca un factor adicional del ranking en sí.
"""

from typing import Any, Dict, List

DOLLAR_VOLUME_FLOOR = 50_000.0
TOP_N = 100


def _passes_filters(quote: Any) -> bool:
    change_percent = getattr(quote, "change_percent", None)
    relative_volume = getattr(quote, "relative_volume", None)
    if change_percent is None or relative_volume is None:
        return False

    last_price = getattr(quote, "last_price", None)
    volume = getattr(quote, "volume", None)
    if last_price is None or volume is None:
        return False
    if last_price * volume < DOLLAR_VOLUME_FLOOR:
        return False

    if not (change_percent > 0):
        return False

    return True


def compute_top_movimiento(quotes: Dict[str, Any], limit: int = TOP_N) -> Dict[str, Any]:
    """Puro, determinista, sin I/O -- `quotes` es el dict símbolo->Quote ya
    obtenido por `radar_worker.get_last_quotes()` (o cualquier objeto con
    los mismos atributos, para poder testear con dobles simples).

    Devuelve `{"total_evaluados": len(quotes), "total_candidatos": <tras
    los 4 filtros, antes de recortar a `limit`>, "top": [...]}` -- nunca
    fabrica un resultado si `quotes` está vacío (total_evaluados=0,
    total_candidatos=0, top=[])."""
    candidatos: List[Dict[str, Any]] = []
    for symbol, quote in quotes.items():
        if not _passes_filters(quote):
            continue
        change_percent = quote.change_percent
        relative_volume = quote.relative_volume
        candidatos.append({
            "symbol": symbol,
            "name": getattr(quote, "name", None),
            "price": getattr(quote, "last_price", None),
            "change_percent": change_percent,
            "volume": getattr(quote, "volume", None),
            "relative_volume": relative_volume,
            "score": change_percent * relative_volume,
        })

    total_candidatos = len(candidatos)
    candidatos.sort(key=lambda c: (-c["score"], c["symbol"]))

    top = candidatos[:limit]
    for i, c in enumerate(top, start=1):
        c["rank"] = i

    return {
        "total_evaluados": len(quotes),
        "total_candidatos": total_candidatos,
        "top": top,
    }
