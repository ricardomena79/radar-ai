"""Capa de ESTABILIDAD sobre CURRENT TOP OPPORTUNITY (2026-08-27, Fase 4/5,
autorizado explícitamente, con la condición fundamental de que NO existan
dos sistemas de decisión).

`select_current_top_opportunity()` (Fase 1/5) sigue siendo la ÚNICA lógica
que determina QUIÉN sería el mejor candidato -- este módulo NUNCA la
modifica ni reimplementa su criterio de orden. Lo único que agrega es una
pregunta distinta: "¿ya hay evidencia SUFICIENTE para que el candidato que
el selector eligió esta vez reemplace al que ya está confirmado?" -- nunca
puede elegir a un candidato que el selector no haya elegido primero.

PARÁMETROS -- estudio antes de implementar (pedido explícito: "no inventes
los parámetros... si no se puede justificar con datos actuales, usa una
configuración conservadora y explícitamente documentada"):

    No existe HOY ningún dato real desplegado que mida cuánto fluctúan
    `atlas_score`/`momentum_score` de un mismo símbolo entre ciclos
    consecutivos (nada de Fase 1-3/5 corrió nunca en producción) -- por lo
    tanto NINGÚN margen numérico de diferencia de score puede justificarse
    empíricamente hoy. Se declara así explícitamente, en vez de inventar
    un número.

    En cambio, el mecanismo elegido -- CONFIRMACIÓN POR CICLOS
    CONSECUTIVOS -- no requiere ese dato: reutiliza tal cual el criterio
    YA determinista del selector (si el selector prefiere a X sobre el
    confirmado, es porque X genuinamente lo superó en su cascada de
    criterios -- decisión/ranking_score/atlas_score/momentum_score/alfabético,
    nunca empate). Exigir que esa preferencia se repita en
    `CONFIRMATION_CYCLES` ciclos consecutivos (no necesariamente el mismo
    valor de score, alcanza con que el MISMO ticker siga ganando) filtra
    exactamente el caso que motivó esta fase: una fluctuación de UN solo
    ciclo (orden de red, ruido de mercado momentáneo) nunca sobrevive 2
    ciclos seguidos si es genuinamente ruido.

    `CONFIRMATION_CYCLES = 2` -- conservador, documentado, no derivado de
    datos reales (no existen todavía). A la cadencia real de
    `scan_worker.py` (`REFRESH_INTERVAL_SECONDS=300`, 5 min), esto exige
    ~10 minutos de evidencia sostenida antes de aceptar un reemplazo --
    un piso bajo a propósito (nunca "casi nunca cambia"), ajustable con
    datos reales en una fase posterior, nunca decidido a ciegas acá.

    Tiempo mínimo de permanencia: NO se agrega como mecanismo separado --
    sería redundante con la confirmación por ciclos consecutivos (que ya
    exige tiempo real transcurrido, dado que corre en ciclos reales de 5
    min). Diferencia mínima de score: NO se implementa (ver arriba,
    ningún número sería justificable hoy).

    Empate: no requiere lógica propia -- el selector (Fase 1/5) YA lo
    resuelve de forma determinista (alfabético como último recurso); la
    capa de estabilidad solo aplica el mismo criterio de ciclos
    consecutivos sobre ese resultado ya determinista.

    Desaparición del Top-1 confirmado: NO se reemplaza de inmediato por
    "quien sea" -- el candidato que el selector elija ese ciclo entra al
    MISMO proceso de confirmación por ciclos consecutivos que cualquier
    otro candidato nuevo, nunca un atajo. El motivo queda registrado
    explícito (`TOP1_DESAPARECIO` vs. `TOP1_REEMPLAZADO_POR_SUPERACION`)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from atlas_live.core import current_top_opportunity as ctop
from atlas_live.core import current_top_opportunity_registry as ctop_reg

CONFIRMATION_CYCLES = 2

REASON_TOP1_MANTENIDO = "TOP1_MANTENIDO"
REASON_TOP1_PRIMERA_SELECCION = "TOP1_PRIMERA_SELECCION"
REASON_TOP1_REFORZADO = "TOP1_REFORZADO"  # el selector coincide con lo ya confirmado
REASON_TOP1_ACUMULANDO_CONFIRMACION = "TOP1_ACUMULANDO_CONFIRMACION"  # candidato nuevo, todavía sin suficientes ciclos
REASON_TOP1_REEMPLAZADO_POR_SUPERACION = "TOP1_REEMPLAZADO_POR_SUPERACION"
REASON_TOP1_DESAPARECIO = "TOP1_DESAPARECIO"


def apply_stability(
    candidates: List[ctop.CandidateForSelection], market_date: str,
) -> Dict[str, Any]:
    """Corre UNA vez por ciclo real. Llama a `select_current_top_opportunity()`
    (sin modificar) para saber quién ganaría HOY, compara contra el estado
    persistido (confirmado + pendiente, ambos en la DB de Fase 2/5 --
    sobrevive un restart), y decide mantener o confirmar -- NUNCA elige un
    candidato que el selector no haya elegido primero.

    Devuelve un resumen con `action`/`reason`/`confirmed_ticker`/
    `pending_ticker`/`pending_streak`/`raw_selection` -- para
    observabilidad, nunca se usa para decidir nada río abajo."""
    seleccion = ctop.select_current_top_opportunity(candidates)
    if seleccion is None:
        return {"action": "SIN_CANDIDATOS", "reason": None, "confirmed_ticker": None,
                "pending_ticker": None, "pending_streak": 0, "raw_selection": None}

    abierto = ctop_reg.get_open_top_opportunity(market_date)
    pendiente = ctop_reg.get_pending_state(market_date)

    if abierto is None:
        # CASO A -- nada confirmado todavía para esta sesión: se acepta
        # directo, no hay nada contra qué estabilizar.
        ctop_reg.register_top_opportunity(seleccion, market_date)
        ctop_reg.set_pending_state(market_date, None, 0)
        return {
            "action": "CONFIRMADO", "reason": REASON_TOP1_PRIMERA_SELECCION,
            "confirmed_ticker": seleccion.ticker, "pending_ticker": None, "pending_streak": 0,
            "raw_selection": seleccion.ticker,
        }

    confirmado_ticker = abierto["ticker"]

    if seleccion.ticker == confirmado_ticker:
        # El selector coincide con lo ya confirmado -- SIN CAMBIOS en el
        # registry (CASO B de Fase 2/5, ni un UPDATE), y se resetea
        # cualquier racha pendiente de otro candidato (perdió su chance).
        ctop_reg.set_pending_state(market_date, None, 0)
        return {
            "action": "MANTENIDO", "reason": REASON_TOP1_REFORZADO,
            "confirmed_ticker": confirmado_ticker, "pending_ticker": None, "pending_streak": 0,
            "raw_selection": seleccion.ticker,
        }

    # El selector eligió a alguien DISTINTO del confirmado.
    candidatos_presentes = {c.ticker for c in candidates}
    confirmado_sigue_presente = confirmado_ticker in candidatos_presentes

    if seleccion.ticker == pendiente["pending_ticker"]:
        nueva_racha = pendiente["pending_streak"] + 1
    else:
        nueva_racha = 1

    if nueva_racha >= CONFIRMATION_CYCLES:
        # Evidencia suficiente -- reemplazo real.
        motivo = REASON_TOP1_REEMPLAZADO_POR_SUPERACION if confirmado_sigue_presente else REASON_TOP1_DESAPARECIO
        ctop_reg.register_top_opportunity(seleccion, market_date)
        ctop_reg.set_pending_state(market_date, None, 0)
        return {
            "action": "CONFIRMADO", "reason": motivo,
            "confirmed_ticker": seleccion.ticker, "pending_ticker": None, "pending_streak": 0,
            "raw_selection": seleccion.ticker,
        }

    # Todavía no hay suficientes ciclos consecutivos -- se mantiene el
    # confirmado (aunque haya desaparecido de este ciclo, ver docstring
    # del módulo: nunca se reemplaza de inmediato por "quien sea"), se
    # acumula la racha del candidato pendiente.
    ctop_reg.set_pending_state(market_date, seleccion.ticker, nueva_racha)
    return {
        "action": "MANTENIDO" if confirmado_sigue_presente else "MANTENIDO_PESE_A_DESAPARICION",
        "reason": REASON_TOP1_ACUMULANDO_CONFIRMACION if confirmado_sigue_presente else REASON_TOP1_DESAPARECIO,
        "confirmed_ticker": confirmado_ticker, "pending_ticker": seleccion.ticker,
        "pending_streak": nueva_racha, "raw_selection": seleccion.ticker,
    }
