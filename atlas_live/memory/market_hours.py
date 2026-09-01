"""Detección de sesión de mercado -- base de la integración en tiempo real.

Horario estándar de Nueva York (America/New_York, con cambio de horario de
verano manejado por `zoneinfo`, sin dependencias nuevas):
  premarket:  04:00 - 09:30
  regular:    09:30 - 16:00
  afterhours: 16:00 - 20:00
  overnight:  20:00 - 04:00 (lunes 00:00-03:59 incluido, cola del domingo
              20:00; nunca viernes 20:00 en adelante ni sábado ni domingo
              antes de las 20:00 -- ver `get_session()`, 2026-09-01,
              autorizado explícitamente para permitir que Mercado siga
              actualizándose vía Yahoo Finance después del cierre de
              after-hours, sin inventar un dato nuevo a partir de uno
              viejo -- ver `multi_source_resolver.py`)
  closed:     sábado completo, domingo antes de las 20:00, y viernes desde
              las 20:00 (el fin de semana real, sin sesión de extended
              hours en ningún proveedor)

**Limitación documentada, no resuelta en esta etapa**: no contempla
feriados de mercado (Acción de Gracias, Navidad, etc.) -- un feriado se
trata como día hábil. Riesgo conocido, aceptado para esta primera
integración; se puede corregir después con un calendario de feriados si
hace falta, sin cambiar la interfaz de este módulo.
"""

from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

PREMARKET_START = dtime(4, 0)
REGULAR_START = dtime(9, 30)
REGULAR_END = dtime(16, 0)
AFTERHOURS_END = dtime(20, 0)

# Ventana en la que se permite sellar el ranking oficial del día -- los
# últimos minutos antes de la apertura, nunca después.
SEAL_WINDOW_START = dtime(9, 25)


def _as_eastern(now: datetime = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(EASTERN)


def get_session(now: datetime = None) -> str:
    """Devuelve 'premarket', 'regular', 'afterhours', 'overnight' o 'closed'.

    Overnight (2026-09-01): cubre 20:00-04:00 ET todas las noches de
    lunes a jueves, más la cola del domingo (domingo 20:00 en adelante) y
    la cola de esa misma noche en la madrugada de cada día hábil
    siguiente (incluido el lunes 00:00-03:59, que es la cola del overnight
    iniciado el domingo). El viernes 20:00 en adelante NO es overnight --
    ahí arranca el fin de semana real (`closed`), igual que todo el sábado
    y el domingo antes de las 20:00 -- ningún proveedor real ofrece
    extended hours en ese tramo."""
    et = _as_eastern(now)
    weekday = et.weekday()  # 0=lunes .. 6=domingo
    t = et.time()

    if weekday == 5:  # sábado: cerrado todo el día
        return "closed"
    if weekday == 6:  # domingo
        return "overnight" if t >= AFTERHOURS_END else "closed"

    # lunes(0) .. viernes(4)
    if t < PREMARKET_START:
        return "overnight"  # cola del overnight de la noche anterior
    if t < REGULAR_START:
        return "premarket"
    if t < REGULAR_END:
        return "regular"
    if t < AFTERHOURS_END:
        return "afterhours"
    if weekday == 4:  # viernes 20:00+: arranca el fin de semana, no hay overnight
        return "closed"
    return "overnight"  # lunes-jueves 20:00+


def is_seal_window(now: datetime = None) -> bool:
    """True solo en los minutos finales del premarket, antes de la apertura."""
    et = _as_eastern(now)
    if et.weekday() >= 5:
        return False
    return SEAL_WINDOW_START <= et.time() < REGULAR_START


def market_date(now: datetime = None) -> str:
    """Fecha de mercado (huso horario de Nueva York) en formato YYYY-MM-DD."""
    return _as_eastern(now).strftime("%Y-%m-%d")
