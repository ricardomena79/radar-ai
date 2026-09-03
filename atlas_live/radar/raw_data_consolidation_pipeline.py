"""Orquesta el ciclo RAW -> ANALYSIS -> CONSOLIDATED KNOWLEDGE/MANIFEST ->
PERSISTENCE VERIFICATION para UN bloque `(ticker, market_date)` a la vez
(2026-09-02, Hito 2, autorizado explícitamente).

Combina, sin duplicar su lógica:
  - `raw_data_consolidation.py` (análisis -- 1 consulta agregada por bloque).
  - `raw_data_consolidation_registry.py` (persistencia -- write-once,
    idempotente).

Implementa EXACTAMENTE los pasos 1-7 del mecanismo de consolidación
diseñado: tomar el bloque, analizarlo, generar el manifiesto, persistirlo,
releerlo, verificar que coincide, y solo entonces marcarlo `verified`.
NUNCA avanza a `compaction_authorized`/`compacted` -- esos estados no
existen en el vocabulario de este módulo, quedan para una fase futura y
separada. NUNCA borra ni modifica ninguna fila de
`candidate_observation`/`shadow_candidate_detection` -- este módulo es
puramente de lectura sobre esas tablas (vía `raw_data_consolidation.py`)
y de escritura únicamente sobre `raw_data_consolidation.db` (una base
nueva, chica, separada).

Aislado por diseño (mismo patrón que
`atlas_live/learning/live_experience_pipeline.py`): cualquier excepción
queda atrapada acá adentro -- el llamador (un endpoint admin) siempre
recibe un dict, nunca una excepción sin manejar."""

from datetime import datetime, timezone
from typing import Any, Dict

from atlas_live.radar import raw_data_consolidation as rdc
from atlas_live.radar import raw_data_consolidation_registry as registry


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def consolidate_block(source_table: str, ticker: str, market_date: str) -> Dict[str, Any]:
    """Corre el ciclo completo para UN bloque. Idempotente: una segunda
    llamada sobre el mismo bloque no vuelve a contarlo como experiencia
    nueva (`already_consolidated=True`), pero sigue verificando que el
    manifiesto ya persistido coincide con lo que el análisis calcula
    ahora mismo -- si el dato crudo cambiara de forma inconsistente con
    lo ya registrado, esto lo detectaría (`error` en la respuesta, nunca
    silenciado)."""
    resumen: Dict[str, Any] = {
        "source_table": source_table,
        "ticker": ticker,
        "market_date": market_date,
        "ejecutado_at": _now_iso(),
        "ok": False,
        "already_consolidated": False,
        "status": None,
        "row_count_covered": None,
        "error": None,
    }
    try:
        analisis = rdc.analyze_block(source_table, ticker, market_date)
        if analisis is None:
            resumen["ok"] = True
            resumen["error"] = "sin filas para este bloque -- nada que consolidar"
            return resumen

        inserto_nuevo = registry.record_provisional(
            source_table=analisis["source_table"],
            block_key=analisis["block_key"],
            block_granularity=analisis["block_granularity"],
            row_count_covered=analisis["row_count_covered"],
            min_timestamp_covered=analisis["min_timestamp_covered"],
            max_timestamp_covered=analisis["max_timestamp_covered"],
            summary=analisis["summary"],
            raw_data_checksum=analisis["raw_data_checksum"],
            methodology_version=analisis["methodology_version"],
        )
        resumen["already_consolidated"] = not inserto_nuevo

        # Paso 5: volver a leerlo.
        releido = registry.get_block(source_table, analisis["block_key"], analisis["methodology_version"])
        if releido is None:
            resumen["error"] = "no se pudo releer el bloque recién persistido"
            return resumen

        # Paso 6: verificar que coincide con lo calculado ahora mismo.
        coincide = (
            releido["row_count_covered"] == analisis["row_count_covered"]
            and releido["raw_data_checksum"] == analisis["raw_data_checksum"]
        )
        if not coincide:
            resumen["error"] = (
                "el manifiesto ya persistido no coincide con el análisis actual "
                "del bloque -- no se marca verified"
            )
            resumen["status"] = releido["status"]
            resumen["row_count_covered"] = releido["row_count_covered"]
            return resumen

        # Paso 7/8 (parcial -- solo hasta 'verified'): registrar cobertura
        # ya está en la misma fila (row_count_covered/min/max_timestamp);
        # marcar verified si todavía estaba provisional.
        if releido["status"] == "provisional":
            registry.mark_verified(source_table, analisis["block_key"], analisis["methodology_version"])
            releido = registry.get_block(source_table, analisis["block_key"], analisis["methodology_version"])

        resumen["status"] = releido["status"]
        resumen["row_count_covered"] = releido["row_count_covered"]
        resumen["ok"] = True
    except Exception as exc:  # la consolidación NUNCA puede tumbar al llamador
        resumen["error"] = f"{type(exc).__name__}: {exc}"
        resumen["ok"] = False
    return resumen
