"""Clasificador de catalizadores (2026-08-23, Fases 3/4 del Motor de
Catalizadores) -- reglas explícitas por keyword/regex, NUNCA machine
learning (mismo espíritu que `atlas_live/radar/alert_stage.py`/
`phase_classifier.py`: reglas documentadas, ordenadas, el primer match
gana). Puro -- sin DB, sin red, testeable con headlines sintéticos.

Dos responsabilidades separadas:
  1. `classify_catalyst_type()` -- QUÉ tipo de catalizador es (taxonomía
     Fase 3) + su dirección probable + una confianza explícita.
  2. `classify_catalyst_lifecycle()` -- EN QUÉ MOMENTO de su ciclo de
     vida está (Fase 4, el fix directo de la queja real de MRNA: "estuve
     mirando dos horas y nunca apareció... vendí temprano").
"""

import re
from typing import NamedTuple, Optional

# ---------------------------------------------------------------------------
# 1. Taxonomía de catalyst_type (Fase 3)
# ---------------------------------------------------------------------------

CATALYST_TYPES = (
    "EARNINGS", "FDA_PDUFA", "CLINICAL_TRIAL", "MA_ACQUISITION",
    "CONTRACT_AWARD", "GUIDANCE", "ANALYST_ACTION", "FINANCING_DILUTION",
    "PARTNERSHIP", "PRODUCT_LAUNCH", "OTHER_MATERIAL",
)

# (nombre, regex de disparo, confianza si matchea en headline, si matchea solo en summary)
_TYPE_RULES = (
    ("FDA_PDUFA", re.compile(r"\bFDA\b|\bPDUFA\b|\bpriority review\b|\bcomplete response letter\b|\bCRL\b", re.I)),
    ("CLINICAL_TRIAL", re.compile(r"\bphase\s*(1|2|3|i|ii|iii)\b|\bclinical trial\b|\btopline\b|\btrial results?\b|\bendpoint\b", re.I)),
    ("MA_ACQUISITION", re.compile(r"\bacquir(e|es|ed|ing|isition)\b|\bmerger\b|\bto be acquired\b|\btakeover\b", re.I)),
    ("FINANCING_DILUTION", re.compile(r"\boffering\b|\bdilution\b|\bregistered direct\b|\bsecondary offering\b|\bwarrants?\b|\bATM (program|offering)\b", re.I)),
    ("CONTRACT_AWARD", re.compile(r"\bcontract\b.*\bawarded?\b|\bawarded?\b.*\bcontract\b|\bwins? (a )?contract\b|\bDoD\b.*\bcontract\b", re.I)),
    ("GUIDANCE", re.compile(r"\bguidance\b|\braises? (full[- ]year )?(guidance|outlook)\b|\bcuts? (guidance|outlook)\b|\blowers? (guidance|outlook)\b", re.I)),
    ("ANALYST_ACTION", re.compile(r"\bupgrades?\b|\bdowngrades?\b|\bprice target\b|\binitiates? coverage\b", re.I)),
    ("PARTNERSHIP", re.compile(r"\bpartnership\b|\bcollaborat(e|es|ed|ion)\b|\bstrategic alliance\b|\blicensing (deal|agreement)\b", re.I)),
    ("PRODUCT_LAUNCH", re.compile(r"\blaunch(es|ed)?\b|\bunveils?\b|\bnew product\b", re.I)),
    ("EARNINGS", re.compile(r"\bearnings\b|\bq[1-4]\s*(20\d\d)?\s*results\b|\bquarterly results\b", re.I)),
)

# Segundo pase: dentro de un catalyst_type ya identificado, resuelve
# NEUTRAL -> ALCISTA/BAJISTA según el lenguaje real del titular. Nunca se
# aplica fuera de su tipo (ej. "approves" solo cuenta para FDA_PDUFA).
_DIRECTION_RESOLVERS = {
    "EARNINGS": (
        (re.compile(r"\bbeats?\b|\btops? estimates?\b", re.I), "ALCISTA"),
        (re.compile(r"\bmisses?\b|\bfalls? short\b", re.I), "BAJISTA"),
    ),
    "FDA_PDUFA": (
        (re.compile(r"\bapproves?\b|\bgrants?\b|\bclears?\b", re.I), "ALCISTA"),
        (re.compile(r"\brejects?\b|\bcomplete response letter\b|\bCRL\b", re.I), "BAJISTA"),
    ),
    "CLINICAL_TRIAL": (
        # BAJISTA primero: un titular como "Fails to Meet Endpoint" contiene
        # literalmente "meet endpoint" -- la negación ("fails to") tiene que
        # ganar, nunca el fragmento positivo que arrastra.
        (re.compile(r"\bfails?\b|\bmisses? endpoint\b|\bdiscontinu", re.I), "BAJISTA"),
        (re.compile(r"\bmeets?( primary)? endpoint\b|\bpositive\b", re.I), "ALCISTA"),
    ),
    "GUIDANCE": (
        (re.compile(r"\braises?\b", re.I), "ALCISTA"),
        (re.compile(r"\bcuts?\b|\blowers?\b", re.I), "BAJISTA"),
    ),
    "ANALYST_ACTION": (
        (re.compile(r"\bupgrades?\b", re.I), "ALCISTA"),
        (re.compile(r"\bdowngrades?\b", re.I), "BAJISTA"),
    ),
}

# Dirección por defecto de cada tipo cuando el resolver de arriba no
# matchea nada (nunca None -- siempre un valor explícito, documentado en
# el plan aprobado).
_DEFAULT_DIRECTION = {
    "EARNINGS": "NEUTRAL",
    "FDA_PDUFA": "NEUTRAL",
    "CLINICAL_TRIAL": "NEUTRAL",
    "MA_ACQUISITION": "ALCISTA",       # para el target, caso típico
    "CONTRACT_AWARD": "ALCISTA",
    "GUIDANCE": "NEUTRAL",
    "ANALYST_ACTION": "NEUTRAL",
    "FINANCING_DILUTION": "BAJISTA",   # SIEMPRE -- la dilución es estructuralmente negativa
    "PARTNERSHIP": "ALCISTA",
    "PRODUCT_LAUNCH": "ALCISTA",
    "OTHER_MATERIAL": "NEUTRAL",
}

# Importancia por defecto de cada tipo -- alimenta CATALYST_SCORE
# (catalyst_score.py), documentado acá para que quede junto a la
# taxonomía que lo define.
_DEFAULT_IMPORTANCE = {
    "FDA_PDUFA": "alta", "CLINICAL_TRIAL": "alta", "MA_ACQUISITION": "alta",
    "EARNINGS": "media", "GUIDANCE": "media", "CONTRACT_AWARD": "media",
    "FINANCING_DILUTION": "media", "PARTNERSHIP": "media",
    "ANALYST_ACTION": "baja", "PRODUCT_LAUNCH": "baja", "OTHER_MATERIAL": "baja",
}


def default_importance(catalyst_type: str) -> str:
    """Expone `_DEFAULT_IMPORTANCE` para catalizadores que no vienen de un
    headline clasificable (ej. filas de calendario de earnings, Fase de
    collector) -- una sola fuente de verdad, nunca un literal duplicado."""
    return _DEFAULT_IMPORTANCE.get(catalyst_type, "baja")


class ClassifiedCatalyst(NamedTuple):
    catalyst_type: str
    direction: str
    confidence: float
    importance: str


def classify_catalyst_type(headline: str, summary: Optional[str] = None) -> ClassifiedCatalyst:
    """Clasifica `catalyst_type`/`direction`/`confidence`/`importance` a
    partir del texto real de la noticia. Reglas ordenadas, el primer tipo
    que matchea gana (mismo estilo `classify_alert_stage`). `confidence`:
    1.0 si el tipo matcheó en el headline, 0.6 si solo en el summary, 0.3
    para el fallback OTHER_MATERIAL -- constantes documentadas, nunca
    inventadas sin criterio."""
    headline = headline or ""
    summary = summary or ""

    catalyst_type = "OTHER_MATERIAL"
    confidence = 0.3
    for tipo, patron in _TYPE_RULES:
        if patron.search(headline):
            catalyst_type, confidence = tipo, 1.0
            break
        if patron.search(summary):
            catalyst_type, confidence = tipo, 0.6
            break

    direction = _DEFAULT_DIRECTION.get(catalyst_type, "NEUTRAL")
    for patron, direccion_resuelta in _DIRECTION_RESOLVERS.get(catalyst_type, ()):
        if patron.search(headline) or patron.search(summary):
            direction = direccion_resuelta
            break

    importance = _DEFAULT_IMPORTANCE.get(catalyst_type, "baja")
    return ClassifiedCatalyst(catalyst_type=catalyst_type, direction=direction,
                               confidence=confidence, importance=importance)


# ---------------------------------------------------------------------------
# 2. Ciclo de vida FUTURO/INMINENTE/EN_ANTICIPACION/OCURRIDO/EXTENDIDA (Fase 4)
# ---------------------------------------------------------------------------

LIFECYCLE_STATES = ("FUTURO", "INMINENTE", "EN_ANTICIPACION", "OCURRIDO", "EXTENDIDA")

# Corte "0-3 días" pedido explícitamente por el usuario en su spec (Fase 4).
IMMINENT_MAX_DAYS = 3.0

# Movimiento de precio desde la publicación que ya cuenta como "el mercado
# se está posicionando antes del evento" -- mismo orden de magnitud que
# DRAWDOWN_FROM_PEAK_THRESHOLD_PCT de alert_stage.py (8.0), reutilizado a
# propósito, no un número nuevo sin criterio.
ANTICIPATION_MIN_MOVE_PCT = 8.0

# Ancla directa al caso real MRNA (total_day_change_pct=49.91%,
# max_return_after_detection_pct=170.6%) -- un movimiento que ya cruzó
# este piso se considera "ya corrió la mayor parte", el fix literal de la
# queja real del usuario sobre MRNA.
EXTENDED_MOVE_PCT = 40.0

# Piso mínimo de tiempo transcurrido antes de poder llamar "EXTENDIDA" a un
# movimiento grande -- evita marcar como "ya agotado" un gap recién
# publicado que todavía puede estar en pleno desarrollo (ej. detectado hace
# 15 minutos). MRNA real: publicada 10:45 UTC, cierre del MISMO día 20:00
# UTC (~9h15min después, misma sesión) ya debía clasificar EXTENDIDA -- por
# eso el piso se mide en HORAS, no en "1 sesión completa": exigir un día
# entero nunca dispararía el mismo día que más importa saberlo.
EXTENDED_MIN_DAYS_SINCE = 6.0 / 24.0  # 6 horas


def classify_catalyst_lifecycle(
    event_date: Optional[str],
    published_at,
    now,
    price_change_since_published_pct: Optional[float],
) -> str:
    """Devuelve una de `LIFECYCLE_STATES`. `event_date`: "YYYY-MM-DD" o
    None. `published_at`/`now`: objetos `datetime` (o None para
    `published_at`). `price_change_since_published_pct`: % de cambio del
    precio desde `published_at` hasta ahora -- `None` si no hay dato
    (nunca se inventa; la clasificación cae a las ramas que solo usan
    fechas). Orden de evaluación, el primero que matchea gana."""
    dias_al_evento = None
    if event_date:
        try:
            from datetime import date
            event_d = date.fromisoformat(event_date)
            dias_al_evento = (event_d - now.date()).days
        except (ValueError, TypeError):
            dias_al_evento = None

    if dias_al_evento is not None and dias_al_evento >= 0:
        # Evento futuro -- pero si YA hay movimiento fuerte de precio
        # desde la publicación, el mercado ya se posicionó (gana sobre
        # FUTURO/INMINENTE).
        if price_change_since_published_pct is not None and price_change_since_published_pct >= ANTICIPATION_MIN_MOVE_PCT:
            return "EN_ANTICIPACION"
        if dias_al_evento <= IMMINENT_MAX_DAYS:
            return "INMINENTE"
        return "FUTURO"

    # Evento ya pasó, o no tiene event_date propio (típico de M&A/contrato
    # sin "fecha de evento" separada de la publicación).
    if price_change_since_published_pct is not None and price_change_since_published_pct >= EXTENDED_MOVE_PCT:
        dias_desde_publicacion = None
        if published_at is not None:
            try:
                dias_desde_publicacion = (now - published_at).total_seconds() / 86400.0
            except TypeError:
                dias_desde_publicacion = None
        if dias_desde_publicacion is None or dias_desde_publicacion >= EXTENDED_MIN_DAYS_SINCE:
            return "EXTENDIDA"

    return "OCURRIDO"
