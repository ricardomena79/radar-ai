"""Compaction segura de `candidate_observation` (2026-09-07, autorizado
explícitamente) -- cierra el hallazgo de la auditoría física de
almacenamiento (misma sesión): `candidate_observation` es la ÚNICA tabla
que crece sin límite por sweep (~1.302 filas/sweep, ~324KB/sweep,
~114MB/día a 120s, ~3,34GB/30 días, ~10GB/90 días), sin ningún mecanismo
de retención hoy.

Reutiliza ÍNTEGRAMENTE Hito 2 (`raw_data_consolidation.py`/
`raw_data_consolidation_registry.py`) -- NO es un segundo sistema de
consolidación. Completa la máquina de estados que Hito 2 dejó preparada
en el schema pero nunca implementó más allá de `verified`:

    candidate_observation (bloque = UN día de mercado completo)
        -> provisional        (run_provisional_for_date, analyze_daily_block())
        -> verified           (run_verification_for_date, re-checksum)
        -> compaction_authorized  (authorize_compaction_for_date -- paso
                                    HUMANO/separado, nunca automático)
        -> [DELETE real, con re-verificación de checksum inmediatamente antes]
        -> compacted          (mark_compacted, con deleted_row_count real)

GUARDS DUROS (cada uno independiente, cualquiera basta para abortar --
ninguno se puede saltear pasando un argumento distinto):
  1. Nunca compacta el market_date de HOY ni fechas futuras.
  2. Nunca compacta un market_date con menos de RETENTION_DAYS (90) de
     antigüedad respecto a `today`.
  3. Exige `status == "compaction_authorized"` en el manifiesto -- un
     bloque `provisional`/`verified`/`compacted` nunca se borra.
  4. Re-verifica el checksum INMEDIATAMENTE antes del DELETE (nunca
     confía en el checksum de cuando se autorizó, que puede ser viejo) --
     si cambió CUALQUIER dato, ABORTA sin borrar nada.
  5. El DELETE real está hardcodeado a `DELETE FROM candidate_observation
     WHERE market_date=?` en una única función (`_delete_block_rows()`) --
     nunca acepta un nombre de tabla externo, nunca puede tocar
     `candidate_detection`/`candidate_outcome`/ninguna otra tabla.
  6. Nunca ejecuta `VACUUM` -- ni acá ni en ningún punto de este módulo.

Todo el módulo es STATELESS entre llamadas -- cada función lee el estado
real del manifiesto (`raw_data_consolidation.db`) al empezar, nunca
depende de una variable en memoria. Esto es, en sí mismo, el mecanismo de
recuperación ante un reinicio a mitad de pipeline: retomar en cualquier
punto es simplemente volver a llamar a la función del siguiente paso --
nunca hay un estado "a medio camino" fuera de lo que el manifiesto ya
registró.

NO se conecta automáticamente al hilo del radar en este pase -- el
disparo real "después del EOD, no en cada sweep" queda como punto de
integración diseñado (ver docstring de `run_daily_pipeline()`), pendiente
de su propia autorización explícita para tocar `radar_worker.py`."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any, Dict, Optional, Tuple

from atlas_live.memory import market_hours
from atlas_live.radar import candidate_registry as reg
from atlas_live.radar import raw_data_consolidation as rdc
from atlas_live.radar import raw_data_consolidation_registry as rdc_registry

SOURCE_TABLE = "candidate_observation"

# Retención elegida explícitamente por el usuario (2026-09-07) -- constante
# documentada, nunca dispersa. Un market_date solo es elegible para
# compactarse si tiene AL MENOS esta antigüedad respecto a `today`.
RETENTION_DAYS = 90


def _today() -> str:
    return market_hours.market_date()


def _is_eligible_for_compaction(market_date: str, today: str) -> Tuple[bool, Optional[str]]:
    """Guards 1 y 2, juntos -- puros, sin DB, fáciles de testear en
    aislamiento. `(True, None)` si es elegible; `(False, motivo)` si no."""
    if market_date >= today:
        return False, "no_se_compacta_dia_actual_ni_futuro"
    dias_desde = (date.fromisoformat(today) - date.fromisoformat(market_date)).days
    if dias_desde < RETENTION_DAYS:
        return False, f"dentro_de_ventana_de_retencion_{RETENTION_DAYS}d_faltan_{RETENTION_DAYS - dias_desde}d"
    return True, None


def run_provisional_for_date(market_date: str, today: Optional[str] = None) -> Dict[str, Any]:
    """Fase 1 -- calcula el resumen+checksum real del día (`analyze_daily_block`)
    y lo persiste como `provisional`. `today` es inyectable para tests;
    en producción usa `market_hours.market_date()` real. Idempotente vía
    `record_provisional()` (write-once, ya probado en Hito 2)."""
    today = today or _today()
    elegible, motivo = _is_eligible_for_compaction(market_date, today)
    if not elegible:
        return {"ok": False, "stage": "provisional", "reason": motivo}

    block = rdc.analyze_daily_block(SOURCE_TABLE, market_date)
    if block is None:
        return {"ok": False, "stage": "provisional", "reason": "sin_filas_para_esa_fecha"}

    inserted = rdc_registry.record_provisional(
        source_table=block["source_table"], block_key=block["block_key"],
        block_granularity=block["block_granularity"], row_count_covered=block["row_count_covered"],
        min_timestamp_covered=block["min_timestamp_covered"], max_timestamp_covered=block["max_timestamp_covered"],
        summary=block["summary"], raw_data_checksum=block["raw_data_checksum"],
        methodology_version=block["methodology_version"],
    )
    return {"ok": True, "stage": "provisional", "inserted_new": inserted, "block_key": block["block_key"],
            "row_count_covered": block["row_count_covered"]}


def run_verification_for_date(market_date: str) -> Dict[str, Any]:
    """Fase 2 -- recalcula el checksum AHORA y lo compara contra el
    provisional ya guardado. Si coincide, avanza a `verified`. Si NO
    coincide (los datos crudos cambiaron desde que se registró el
    provisional), NUNCA verifica -- se queda en `provisional`, sin
    excepción."""
    block = rdc_registry.get_block(SOURCE_TABLE, market_date, rdc.METHODOLOGY_VERSION)
    if block is None:
        return {"ok": False, "stage": "verified", "reason": "sin_manifiesto_provisional"}
    if block["status"] != "provisional":
        return {"ok": False, "stage": "verified", "reason": f"status_actual_{block['status']}_se_esperaba_provisional"}

    fresh = rdc.analyze_daily_block(SOURCE_TABLE, market_date)
    if fresh is None or fresh["raw_data_checksum"] != block["raw_data_checksum"]:
        return {"ok": False, "stage": "verified", "reason": "checksum_no_coincide_datos_cambiaron"}

    verified = rdc_registry.mark_verified(SOURCE_TABLE, market_date, rdc.METHODOLOGY_VERSION)
    return {"ok": verified, "stage": "verified", "reason": None if verified else "no_estaba_en_provisional"}


def authorize_compaction_for_date(market_date: str) -> Dict[str, Any]:
    """Fase 3 -- autorización EXPLÍCITA y SEPARADA (nunca automática desde
    `run_verification_for_date`). Pensada para invocarse desde un paso
    humano/admin separado -- avanza `verified -> compaction_authorized`,
    nunca borra nada por sí sola."""
    authorized = rdc_registry.mark_compaction_authorized(SOURCE_TABLE, market_date, rdc.METHODOLOGY_VERSION)
    return {"ok": authorized, "stage": "compaction_authorized", "reason": None if authorized else "no_estaba_en_verified"}


def _delete_block_rows(market_date: str) -> int:
    """ÚNICA función de todo el módulo que ejecuta un DELETE real.
    Hardcodeado a `candidate_observation` -- el nombre de tabla NUNCA es
    un parámetro externo, no hay forma de invocar esto sobre otra tabla.
    Conexión propia y aislada (nunca importa/llama funciones internas de
    `candidate_registry.py`, solo reutiliza su `DB_PATH` -- mismo patrón
    de aislamiento que `raw_data_consolidation.py::analyze_block()` ya
    usa para lectura). Jamás ejecuta `VACUUM`."""
    with sqlite3.connect(reg.DB_PATH, timeout=15) as conn:
        conn.execute("PRAGMA busy_timeout=15000")
        cur = conn.execute("DELETE FROM candidate_observation WHERE market_date=?", (market_date,))
        conn.commit()
        return cur.rowcount


def compact_block(market_date: str, today: Optional[str] = None) -> Dict[str, Any]:
    """Fase 4 -- el ÚNICO punto de entrada que puede borrar filas reales.
    Re-chequea TODOS los guards desde cero en cada llamada (nunca confía
    en que un caller ya los haya validado) -- esto es lo que hace que
    llamar dos veces sea seguro (idempotente): la segunda vez, el
    manifiesto ya no está en `compaction_authorized` (pasó a `compacted`),
    así que el guard 3 aborta antes de intentar un segundo DELETE."""
    today = today or _today()

    elegible, motivo = _is_eligible_for_compaction(market_date, today)
    if not elegible:
        return {"ok": False, "deleted_row_count": 0, "reason": motivo}

    block = rdc_registry.get_block(SOURCE_TABLE, market_date, rdc.METHODOLOGY_VERSION)
    if block is None:
        return {"ok": False, "deleted_row_count": 0, "reason": "sin_manifiesto"}
    if block["status"] != "compaction_authorized":
        return {"ok": False, "deleted_row_count": 0, "reason": f"status_actual_{block['status']}_se_requiere_compaction_authorized"}

    # Guard 4 -- re-verificación INMEDIATA, nunca se confía en el checksum
    # guardado en la fase de autorización (puede ser viejo). `fresh is None`
    # (cero filas para este bloque) se trata DISTINTO de un checksum que no
    # coincide con filas presentes: cero filas significa, con más
    # probabilidad, que un intento anterior de `compact_block()` ya corrió
    # el DELETE pero el proceso murió ANTES de `mark_compacted()` -- es
    # exactamente el escenario de recuperación tras un reinicio a mitad de
    # camino, y debe poder completarse (idempotente), no abortar.
    fresh = rdc.analyze_daily_block(SOURCE_TABLE, market_date)
    if fresh is not None and fresh["raw_data_checksum"] != block["raw_data_checksum"]:
        return {"ok": False, "deleted_row_count": 0, "reason": "checksum_cambio_justo_antes_del_delete_abortado"}

    deleted = _delete_block_rows(market_date)
    rdc_registry.mark_compacted(SOURCE_TABLE, market_date, rdc.METHODOLOGY_VERSION, deleted_row_count=deleted)
    return {"ok": True, "deleted_row_count": deleted, "reason": None, "market_date": market_date}


def run_daily_pipeline(market_date: str, today: Optional[str] = None, auto_authorize: bool = False) -> Dict[str, Any]:
    """Orquesta provisional -> verified (siempre) y, SOLO si
    `auto_authorize=True` (nunca el default, nunca en producción sin una
    decisión explícita aparte), también compaction_authorized -> DELETE.
    Pensado como el único punto que un futuro disparador (ej. después del
    EOD, ver docstring del módulo) llamaría -- pero ESE disparador no se
    conecta a `radar_worker.py` en este pase, queda como diseño."""
    resultado_provisional = run_provisional_for_date(market_date, today=today)
    if not resultado_provisional["ok"]:
        return {"provisional": resultado_provisional, "verified": None, "authorized": None, "compacted": None}

    resultado_verificacion = run_verification_for_date(market_date)
    resultado_autorizacion = None
    resultado_compactacion = None
    if resultado_verificacion["ok"] and auto_authorize:
        resultado_autorizacion = authorize_compaction_for_date(market_date)
        if resultado_autorizacion["ok"]:
            resultado_compactacion = compact_block(market_date, today=today)

    return {
        "provisional": resultado_provisional, "verified": resultado_verificacion,
        "authorized": resultado_autorizacion, "compacted": resultado_compactacion,
    }
