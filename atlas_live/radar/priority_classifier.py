"""Capa de prioridad operativa final (2026-08-18, cierre de arquitectura).

Convierte las miles de detecciones que `candidate_registry.live_opportunities()`
expone en 4 categorías accionables para el usuario -- PURAMENTE una función
de PRESENTACIÓN sobre datos que `alert_stage.py`/`phase_classifier.py` YA
calculan. No define ningún umbral nuevo, no importa `candidate_gates.py`,
no toca `atlas/engine/decision_engine.py`.

Regla de datos confiables (2026-08-18, mismo espíritu que
`atlas_live/scan_worker.py::_apply_stale_fallback_guard` para el pipeline
Yahoo -- acá aplicado al pipeline Tradier): si el ticker no tiene precio en
el último barrido de `radar_worker`, no hay dato actual verificable -- nunca
se presenta como oportunidad, sin importar la etapa que tenga registrada.

Evidencia histórica (`historical_evidence`, de
`atlas_live/learning/historical_scoring.score_candidate()`): SOLO se anexa
al motivo como texto de apoyo para el criterio del usuario -- nunca cambia
`estado_final`. Cambiarlo equivaldría a inventar un umbral nuevo de mezcla
de señales sin evidencia de que haga falta (ver plan, sección "Decisiones
de diseño").

Cierre de la cadena de confiabilidad (2026-08-18, caso real SBLK/BATL --
el mismo problema del pipeline Yahoo, ahora cerrado también acá): tener un
precio en el último barrido NO basta -- ese precio puede estar vencido
(`radar_worker._last_quotes` no se limpia si Tradier empieza a fallar, ver
`atlas_live/server.py::api_radar_oportunidades`), sin timestamp válido, o
con un % de cambio aritméticamente incoherente con el precio/referencia
que trae. `estado_validacion` (calculado por el llamador, SIEMPRE al
servir el request, nunca en el momento del barrido -- ver
`scan_worker.compute_price_age_seconds()`/`is_price_stale()`, reutilizadas
tal cual, sin duplicar lógica) fuerza NO_TOCAR con prioridad sobre
CUALQUIER etapa -- una OPORTUNIDAD_PRIORITARIA nunca puede existir con un
`estado_validacion` distinto de OK."""

from typing import Any, Dict, Optional, Tuple

FINAL_STATES = ("OPORTUNIDAD_PRIORITARIA", "VIGILAR", "PREPARACION", "NO_TOCAR")

_ALCISTA_AVANZADO = ("INICIO", "CONFIRMACION")
_VIGILAR_STAGES = ("ALERTA_TEMPRANA", "ALERTA_FUERTE")
_PREPARACION_STAGES = ("PREPARACION", "DETECCION_TEMPRANA")
_NO_TOCAR_STAGES = ("NO_PERSEGUIR", "FLUJO_VENDEDOR")

SIN_PRECIO_ACTUAL_MOTIVO = (
    "Sin precio actual en el último barrido -- DATOS NO CONFIABLES, NO RECOMENDAR"
)

# Estados de validación del precio (2026-08-18, cierre de confiabilidad).
# "OK" es el único que permite seguir evaluando la etapa -- cualquier otro
# fuerza NO_TOCAR de inmediato, con prioridad sobre INICIO/CONFIRMACION/
# ALERTA_*/PREPARACION, sin importar qué tan buena sea la señal.
VALIDACION_OK = "OK"
VALIDACION_SIN_PRECIO_ACTUAL = "SIN_PRECIO_ACTUAL"
VALIDACION_SIN_TIMESTAMP = "SIN_TIMESTAMP"
VALIDACION_VENCIDO = "VENCIDO"
VALIDACION_CAMBIO_PCT_INCOHERENTE = "CAMBIO_PCT_INCOHERENTE"

_DATOS_NO_CONFIABLES_PREFIJO = "DATOS NO CONFIABLES -- NO RECOMENDAR"

_VALIDACION_MOTIVOS = {
    VALIDACION_SIN_PRECIO_ACTUAL: SIN_PRECIO_ACTUAL_MOTIVO,
    VALIDACION_SIN_TIMESTAMP: f"{_DATOS_NO_CONFIABLES_PREFIJO} (precio sin timestamp válido de Tradier)",
    VALIDACION_VENCIDO: f"{_DATOS_NO_CONFIABLES_PREFIJO} (precio vencido -- supera la antigüedad máxima permitida)",
    VALIDACION_CAMBIO_PCT_INCOHERENTE: f"{_DATOS_NO_CONFIABLES_PREFIJO} (cambio % incoherente con precio/referencia)",
}


def _historical_evidence_note(historical_evidence: Optional[Dict[str, Any]]) -> str:
    if not historical_evidence or not historical_evidence.get("grupo_existe"):
        return ""
    pct_20 = historical_evidence.get("pct_20")
    n = historical_evidence.get("n")
    if pct_20 is None or not n:
        return ""
    return f" + evidencia histórica: {pct_20:.0f}% alcanzó +20% (n={n})"


def classify_final_priority(
    stage: Optional[str],
    direction: Optional[str],
    change_pct_confiable: Optional[bool],
    tiene_precio_actual: bool,
    sector_flow_active: Optional[bool] = None,
    historical_evidence: Optional[Dict[str, Any]] = None,
    estado_validacion: str = VALIDACION_OK,
) -> Tuple[str, str]:
    """Devuelve `(estado_final, motivo)`. `estado_final` es siempre uno de
    `FINAL_STATES`. Orden de evaluación (el primero que matchea gana):

    1. Sin precio actual en el último barrido -> NO_TOCAR (dato no confiable).
    2. `estado_validacion != VALIDACION_OK` (vencido, sin timestamp, % de
       cambio incoherente) -> NO_TOCAR, CON PRIORIDAD SOBRE CUALQUIER
       ETAPA -- una señal de INICIO/CONFIRMACION con precio vencido nunca
       llega a evaluarse como oportunidad.
    3. `stage` ya es NO_PERSEGUIR/FLUJO_VENDEDOR -> NO_TOCAR.
    4. `stage` en (INICIO, CONFIRMACION) con `direction=="ALCISTA"` ->
       OPORTUNIDAD_PRIORITARIA (ya exige dirección confirmada por
       `alert_stage.classify_alert_stage`, se revalida acá por claridad).
    5. `stage` en (ALERTA_TEMPRANA, ALERTA_FUERTE) -> VIGILAR.
    6. `stage` en (PREPARACION, DETECCION_TEMPRANA) -> PREPARACION.
    7. Cualquier otro caso (stage=None o desconocido) -> NO_TOCAR."""
    nota_historica = _historical_evidence_note(historical_evidence)

    if not tiene_precio_actual:
        return "NO_TOCAR", SIN_PRECIO_ACTUAL_MOTIVO

    if estado_validacion != VALIDACION_OK:
        motivo = _VALIDACION_MOTIVOS.get(estado_validacion, f"{_DATOS_NO_CONFIABLES_PREFIJO} ({estado_validacion})")
        return "NO_TOCAR", motivo

    if stage in _NO_TOCAR_STAGES:
        return "NO_TOCAR", f"Etapa {stage}"

    if stage in _ALCISTA_AVANZADO and direction == "ALCISTA":
        motivo = f"Etapa {stage}, dirección confirmada"
        if sector_flow_active:
            motivo += " + sector con flujo de dinero activo"
        return "OPORTUNIDAD_PRIORITARIA", motivo + nota_historica

    if stage in _VIGILAR_STAGES:
        motivo = f"Etapa {stage}, falta confirmación direccional"
        if sector_flow_active:
            motivo += " + sector con flujo de dinero activo"
        return "VIGILAR", motivo + nota_historica

    if stage in _PREPARACION_STAGES:
        return "PREPARACION", f"Etapa {stage}, sin movimiento fuerte todavía" + nota_historica

    if stage in _ALCISTA_AVANZADO:
        # INICIO/CONFIRMACION sin direction=="ALCISTA": no debería ocurrir
        # (alert_stage.py ya lo exige), pero si pasa, nunca se presenta
        # como prioritaria sin esa confirmación explícita.
        return "VIGILAR", f"Etapa {stage} sin dirección ALCISTA confirmada"

    return "NO_TOCAR", f"Etapa {stage!r} sin regla de prioridad definida"
