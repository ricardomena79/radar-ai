"""Hito 6, Fase 6.4-D1 (2026-09-05, autorizado explícitamente): DRY-RUN de
retención para `shadow_candidate_detection` -- exclusivamente diagnóstico,
CERO DELETE/VACUUM/ALTER TABLE, cero cambio de estado real.

Diseño aprobado en la fase de diseño de H6.4 (ver
`.claude/plans/ethereal-mixing-anchor.md`): la infraestructura segura ya
existe desde Hito 2 (`raw_data_consolidation_registry.py`, máquina de
estados `provisional -> verified -> compaction_authorized -> compacted`,
nunca escrita más allá de `verified` por ningún código existente). Este
módulo NO avanza esa máquina de estados -- solo LEE el manifiesto ya
persistido para reportar qué SERÍA elegible si, en una fase futura y
separada, existiera código que compacta.

GARANTÍA ESTRUCTURAL (no solo una promesa): `dry_run_retention_report()`
nunca importa `atlas_live.radar.shadow_detector_registry` ni abre
`shadow_unified_detector.db` -- toda su información sale de
`raw_data_consolidation.db` (el manifiesto), que ya contiene
`row_count_covered`/`min_timestamp_covered`/`max_timestamp_covered` por
bloque. Confirmado por test estructural (ver
`test_shadow_retention_dry_run.py`).

`verify_block_checksum_still_matches()` es la ÚNICA función de este
módulo que SÍ abre `shadow_unified_detector.db` (reutilizando
`raw_data_consolidation.analyze_block()`, ya existente, sin
modificarlo) -- deliberadamente separada del dry-run, preparada para que
una FASE FUTURA Y DISTINTA (la compactación real, con su propia
autorización) la use como última verificación de seguridad justo antes
de cualquier DELETE real. Nunca se llama desde `dry_run_retention_report()`.

Política de elegibilidad (la ÚNICA que este módulo implementa, puramente
de LECTURA):
  1. `source_table == "shadow_candidate_detection"` -- ningún otro.
  2. `status == "compacted"` EXCLUSIVAMENTE -- `"verified"` NO alcanza
     (pedido explícito: "NO considerar verified suficiente para
     borrar"). Como ningún código existente escribe jamás el estado
     `"compacted"` (confirmado leyendo `raw_data_consolidation_pipeline.py`
     completo -- se detiene en `"verified"`), este dry-run reportará HOY,
     honestamente, cero bloques elegibles -- eso es la salvaguarda
     funcionando como se diseñó, no un error.
  3. `max_timestamp_covered` anterior a `retention_days` (mínimo 180,
     impuesto por código, no solo documentado)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from atlas_live.radar import raw_data_consolidation as rdc
from atlas_live.radar import raw_data_consolidation_registry as registry

SOURCE_TABLE = "shadow_candidate_detection"

# Retención mínima -- decisión explícita de diseño (ver informe de H6.4):
# purgar agresivamente ahora podría destruir la evidencia que una futura
# auditoría real del Unified Detector (H6.3) todavía necesita. Impuesto
# como piso duro, no un default que se pueda bajar por accidente.
RETENTION_MIN_DAYS = 180

# Estimación de bytes/fila -- medida localmente sobre datos reales de
# `shadow_unified_detector.db` (55.421 filas ~= 34MB, ver informe de
# diseño de H6.4). Declarado explícitamente como ESTIMACIÓN, nunca una
# medición exacta de producción (que este módulo no puede obtener sin
# abrir `shadow_unified_detector.db`, algo que el dry-run nunca hace).
ESTIMATED_BYTES_PER_ROW = 630

ELIGIBLE_STATUS = "compacted"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def dry_run_retention_report(retention_days: int = RETENTION_MIN_DAYS) -> Dict[str, Any]:
    """Reporta, SOLO A PARTIR del manifiesto ya persistido en
    `raw_data_consolidation.db`, qué bloques de `shadow_candidate_detection`
    serían elegibles para una futura compactación real. Nunca escribe
    nada, nunca abre `shadow_unified_detector.db`. `retention_days` no
    puede ser menor a `RETENTION_MIN_DAYS` -- se rechaza explícitamente,
    nunca se aplica un valor menor en silencio."""
    if retention_days < RETENTION_MIN_DAYS:
        raise ValueError(
            f"retention_days={retention_days} es menor al piso de seguridad "
            f"RETENTION_MIN_DAYS={RETENTION_MIN_DAYS} -- rechazado explícitamente."
        )

    todos = registry.list_blocks(source_table=SOURCE_TABLE)
    cutoff = _now() - timedelta(days=retention_days)

    elegibles: List[Dict[str, Any]] = []
    motivo_no_elegible: Dict[str, int] = {
        "estado_no_compacted": 0,
        "sin_max_timestamp": 0,
        "demasiado_reciente": 0,
    }

    for bloque in todos:
        if bloque["status"] != ELIGIBLE_STATUS:
            motivo_no_elegible["estado_no_compacted"] += 1
            continue
        max_ts_raw = bloque.get("max_timestamp_covered")
        if not max_ts_raw:
            motivo_no_elegible["sin_max_timestamp"] += 1
            continue
        if _parse_iso(max_ts_raw) >= cutoff:
            motivo_no_elegible["demasiado_reciente"] += 1
            continue
        elegibles.append(bloque)

    n_filas_elegibles = sum(b["row_count_covered"] for b in elegibles)
    rango_temporal: Optional[Dict[str, str]] = None
    if elegibles:
        mins = [b["min_timestamp_covered"] for b in elegibles if b.get("min_timestamp_covered")]
        maxs = [b["max_timestamp_covered"] for b in elegibles if b.get("max_timestamp_covered")]
        rango_temporal = {
            "min_timestamp": min(mins) if mins else None,
            "max_timestamp": max(maxs) if maxs else None,
        }

    return {
        "source_table": SOURCE_TABLE,
        "retention_days": retention_days,
        "cutoff_utc": cutoff.isoformat(),
        "generated_at_utc": _now().isoformat(),
        "total_blocks_scanned": len(todos),
        "n_eligible_blocks": len(elegibles),
        "eligible_blocks": [
            {
                "block_key": b["block_key"],
                "row_count_covered": b["row_count_covered"],
                "min_timestamp_covered": b.get("min_timestamp_covered"),
                "max_timestamp_covered": b.get("max_timestamp_covered"),
                "raw_data_checksum": b["raw_data_checksum"],
                "methodology_version": b["methodology_version"],
            }
            for b in elegibles
        ],
        "n_rows_eligible": n_filas_elegibles,
        "estimated_bytes_recoverable": n_filas_elegibles * ESTIMATED_BYTES_PER_ROW,
        "estimated_bytes_per_row": ESTIMATED_BYTES_PER_ROW,
        "eligible_time_range": rango_temporal,
        "not_eligible_breakdown": motivo_no_elegible,
        "note": (
            "Reporte puramente informativo -- ningún DELETE/VACUUM/ALTER se "
            "ejecuta en ningún punto de este módulo. Si n_eligible_blocks=0, "
            "es porque ningún bloque alcanzó el estado 'compacted' todavía "
            "(nada lo escribe hoy) -- esa es la salvaguarda funcionando, no "
            "un error de este reporte."
        ),
    }


def verify_block_checksum_still_matches(block: Dict[str, Any]) -> Dict[str, Any]:
    """SOLO para uso de una fase destructiva FUTURA y separada, antes de
    cualquier DELETE real -- NUNCA llamada desde `dry_run_retention_report()`.
    Es la ÚNICA función de este módulo que abre `shadow_unified_detector.db`
    (vía `raw_data_consolidation.analyze_block()`, ya existente, sin
    modificar). Recalcula el checksum del bloque AHORA MISMO y lo compara
    contra el que quedó guardado en el manifiesto -- si no coinciden
    (el dato crudo cambió desde que se consolidó, o desapareció), lo
    declara explícitamente en vez de asumir que sigue siendo seguro
    borrar."""
    ticker, _, market_date = block["block_key"].partition("|")
    analisis_actual = rdc.analyze_block(block["source_table"], ticker, market_date)

    if analisis_actual is None:
        return {
            "block_key": block["block_key"],
            "matches": False,
            "reason": "el bloque ya no tiene ninguna fila cruda -- no se puede reverificar",
        }

    coincide = (
        analisis_actual["raw_data_checksum"] == block["raw_data_checksum"]
        and analisis_actual["row_count_covered"] == block["row_count_covered"]
    )
    return {
        "block_key": block["block_key"],
        "matches": coincide,
        "checksum_manifiesto": block["raw_data_checksum"],
        "checksum_actual": analisis_actual["raw_data_checksum"],
        "row_count_manifiesto": block["row_count_covered"],
        "row_count_actual": analisis_actual["row_count_covered"],
        "reason": None if coincide else "el dato crudo cambió desde que se consolidó -- NO seguro para borrar",
    }
