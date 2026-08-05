"""Detección de sesión de mercado -- base de la integración en tiempo real.

Horario estándar de Nueva York (America/New_York, con cambio de horario de
verano manejado por `zoneinfo`, sin dependencias nuevas):
  premarket:  04:00 - 09:30
  regular:    09:30 - 16:00
  afterhours: 16:00 - 20:00
  closed:     el resto, y todo sábado/domingo

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
    """Devuelve 'premarket', 'regular', 'afterhours' o 'closed'."""
    et = _as_eastern(now)
    if et.weekday() >= 5:  # 5=sábado, 6=domingo
        return "closed"
    t = et.time()
    if PREMARKET_START <= t < REGULAR_START:
        return "premarket"
    if REGULAR_START <= t < REGULAR_END:
        return "regular"
    if REGULAR_END <= t < AFTERHOURS_END:
        return "afterhours"
    return "closed"


def is_seal_window(now: datetime = None) -> bool:
    """True solo en los minutos finales del premarket, antes de la apertura."""
    et = _as_eastern(now)
    if et.weekday() >= 5:
        return False
    return SEAL_WINDOW_START <= et.time() < REGULAR_START


def market_date(now: datetime = None) -> str:
    """Fecha de mercado (huso horario de Nueva York) en formato YYYY-MM-DD."""
    return _as_eastern(now).strftime("%Y-%m-%d")


def minutes_since_open(now: datetime = None) -> int:
    """Minutos transcurridos desde las 09:30 ET -- 0 si todavía no abrió
    hoy (premarket) o si es fin de semana. Usado por `live_integration.py`
    para marcar el `checkpoint_minutes` de una observación nueva del
    Memory Store, agregada en el momento real en que se conoce el
    resultado (no un checkpoint fijo de backtest)."""
    et = _as_eastern(now)
    if et.weekday() >= 5:
        return 0
    open_today = et.replace(hour=REGULAR_START.hour, minute=REGULAR_START.minute, second=0, microsecond=0)
    delta_minutes = (et - open_today).total_seconds() / 60.0
    return max(0, int(delta_minutes))
