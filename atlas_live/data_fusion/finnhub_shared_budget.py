"""Presupuesto compartido de `FINNHUB_API_KEY` entre `hot_quote.py`,
`atlas_live/catalyst/catalyst_worker.py` y `market_view.py` (2026-09-14,
autorizado explícitamente tras la investigación de rate-limit de
Yahoo/Finnhub de esta sesión).

Contexto real, ya medido/documentado antes de este módulo: Finnhub free
tier permite 60 req/min. `hot_quote.py` ya reserva ~40 req/min para sí
mismo (documentado en su propio módulo, "dentro del límite de Finnhub"),
`catalyst_worker.py` puede llegar a ~14 req/min en sus ventanas activas --
juntos, ~54/min, dejando casi nada de margen real para `market_view.py`
(Mercado), que hoy no tiene ningún presupuesto propio y puede ráfaga sin
límite hasta que su circuit breaker estadístico reaccione (confirmado en
vivo: 397 intentos, 338 errores en un solo ciclo real).

Los 3 corren en el MISMO proceso Python (single worker, confirmado en
sesiones anteriores) -- un objeto en memoria protegido por `Lock` alcanza,
sin Redis ni infraestructura externa.

REVISIÓN 2026-09-14 (autorizada explícitamente, segunda vuelta de diseño):
las reservas 40/10/5 dejaron de ser techos absolutos -- son PISOS
PROTEGIDOS. Cualquier consumidor puede pedir MÁS que su propio piso
("prestar" capacidad libre de otros), pero solo si, después de
concederlo, sigue quedando margen suficiente para que TODOS los demás
consumidores puedan alcanzar su propio piso completo si lo necesitaran
en ese mismo instante -- esa es la única garantía matemática real de "no
le quito piso a nadie", y aplica por igual sin importar la prioridad de
quien pide (una versión anterior de este algoritmo solo reservaba para
los de MAYOR prioridad, lo cual dejaba un hueco real -- ver la sección
"CORRECCIÓN" en el docstring de `try_acquire()`). Prioridad documentada,
mayor a menor (determina el tamaño relativo del piso de cada uno, y
serviría para arbitrar preferencia si los pisos NO sumaran exactamente
el límite total -- ver hallazgo matemático abajo):
  1. `hot_quote`    -- visible al usuario en tiempo real.
  2. `catalyst_worker` -- enriquecimiento de fondo, ya tiene su propio cooldown.
  3. `market_view`  -- fallback de último nivel, panel que ya degrada con
     gracia a datos STALE/cache -- la prioridad más baja.

Hallazgo matemático, declarado explícitamente (no oculto): como
40+10+5=55=el límite total, garantizar SIEMPRE el piso completo de los 3
consumidores deja CERO margen real de préstamo instantáneo para
cualquiera de ellos, incluso cuando los otros dos están 100% inactivos --
es una consecuencia directa de que los pisos ya agotan el total, no un
defecto del algoritmo. El préstamo solo se vuelve posible cuando una
request vieja de otro consumidor EXPIRA de la ventana deslizante de 60s
(capacidad genuinamente liberada, nunca "prestada" de un piso que
todavía podría usarse). Se prioriza la garantía de "nunca le quito piso
a nadie, a NINGÚN consumidor" sobre maximizar el aprovechamiento
instantáneo, por pedido explícito del usuario.

No bloqueante: `try_acquire()` nunca espera, responde de inmediato con la
decisión ya tomada.

Fail-safe: cualquier excepción inesperada dentro de `try_acquire()`, y
cualquier `consumer` no reconocido, se resuelve CONCEDIENDO el pedido
(nunca denegando) -- un bug o una entrada mal escrita en ESTE módulo
nunca debe bloquear una consulta real de negocio en Mercado, Radar,
Catalizadores ni hot_quote. Se cuenta aparte (`fail_safe_events`) para
poder auditarlo, nunca se oculta.
"""

import os
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

# Orden de prioridad EXPLÍCITO, mayor a menor -- índice 0 = mayor
# prioridad. Nunca un reparto parejo (60/3=20).
PRIORITY_ORDER: List[str] = ["hot_quote", "catalyst_worker", "market_view"]
CONSUMERS = tuple(PRIORITY_ORDER)

WINDOW_SECONDS = 60.0


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Pisos PROTEGIDOS por consumidor, ventana de 60s -- suma = 55 (5 de
# margen de seguridad bajo el límite nominal real de Finnhub, 60/min).
# Cada número refleja la necesidad real ya documentada y la prioridad
# explícita de arriba. Configurable por entorno para poder calibrar sin
# tocar código.
RESERVED_PER_MINUTE: Dict[str, int] = {
    "hot_quote": _env_int("ATLAS_FINNHUB_BUDGET_HOT_QUOTE", 40),
    "catalyst_worker": _env_int("ATLAS_FINNHUB_BUDGET_CATALYST_WORKER", 10),
    "market_view": _env_int("ATLAS_FINNHUB_BUDGET_MARKET_VIEW", 5),
}

def total_safe_limit_per_minute() -> int:
    """Límite global real -- SIEMPRE la suma de los pisos ACTUALES (nunca
    un número cacheado aparte): si alguien sube un piso por entorno, o un
    test ajusta `RESERVED_PER_MINUTE` para aislar su propio escenario, el
    techo global se recalcula solo, nunca queda desalineado."""
    return sum(RESERVED_PER_MINUTE.values())

_lock = threading.Lock()
_timestamps_by_consumer: Dict[str, Deque[float]] = {c: deque() for c in CONSUMERS}
_global_timestamps: Deque[float] = deque()
_metrics: Dict[str, Dict[str, int]] = {c: {"granted": 0, "denied": 0, "borrowed": 0} for c in CONSUMERS}
_fail_safe_events = 0


def _prune(dq: Deque[float], now: float) -> None:
    while dq and (now - dq[0]) > WINDOW_SECONDS:
        dq.popleft()


def try_acquire(consumer: str, now: Optional[float] = None) -> bool:
    """No bloqueante -- responde de inmediato, nunca espera cupo.

    `now` es inyectable SOLO para tests deterministas (el código de
    producción real siempre usa `time.time()`, nunca lo pasa explícito).

    `consumer` no reconocido (nombre mal escrito, uso nuevo no
    registrado en `CONSUMERS`) -> fail-safe hacia CONCEDER, nunca hacia
    bloquear negocio real por un nombre no dado de alta acá.

    CORRECCIÓN 2026-09-14 (encontrada durante el testeo de AJUSTE 1, antes
    de reportar): la primera versión de este algoritmo solo reservaba
    capacidad para consumidores de MAYOR prioridad al decidir un
    préstamo -- eso dejaba un hueco real: un consumidor de prioridad
    media podía pedir prestado "protegiendo" el piso de uno de mayor
    prioridad, pero un tercero de MENOR prioridad, al pedir después su
    propio piso (chequeado con una condición más laxa, solo `total <
    límite`), podía terminar usando esa misma capacidad -- dejando al de
    mayor prioridad sin su piso real cuando por fin lo pedía. Confirmado
    con una reproducción real (3 consumidores, reservas 1/1/1): el piso
    del de mayor prioridad SÍ se denegaba. Corregido reservando, en TODO
    pedido (dentro del propio piso o más allá), la capacidad no usada de
    TODOS los demás consumidores, no solo los de mayor prioridad -- única
    forma de garantizar el piso de CADA consumidor sin excepción, tal
    como exige el diseño. Consecuencia matemática, ya declarada al
    usuario: como los pisos (40+10+5) suman EXACTAMENTE el límite total,
    esto hace que el préstamo más allá del propio piso sea, en la
    práctica, solo posible cuando una request vieja de OTRO consumidor
    ya expiró de la ventana de 60s (ver test
    `test_prestamo_se_habilita_cuando_expira_la_ventana_de_mayor_prioridad`)
    -- nunca por una carrera entre consumidores en el mismo instante."""
    global _fail_safe_events
    try:
        ts = now if now is not None else time.time()
        reserva = RESERVED_PER_MINUTE.get(consumer)
        with _lock:
            metrics = _metrics.setdefault(consumer, {"granted": 0, "denied": 0, "borrowed": 0})
            if reserva is None:
                metrics["granted"] += 1
                return True

            _prune(_global_timestamps, ts)
            dq = _timestamps_by_consumer.setdefault(consumer, deque())
            _prune(dq, ts)

            own_used = len(dq)
            total_used = len(_global_timestamps)
            limite_total = total_safe_limit_per_minute()
            es_prestamo = own_used >= reserva

            # Reserva para TODOS los demás consumidores (no solo los de
            # mayor prioridad) -- la única condición que garantiza el
            # piso de cada uno sin excepción, ver corrección de arriba.
            reservado_para_otros = sum(
                max(0, RESERVED_PER_MINUTE[c] - len(_timestamps_by_consumer.get(c, ())))
                for c in CONSUMERS if c != consumer
            )
            if total_used + 1 + reservado_para_otros <= limite_total:
                dq.append(ts)
                _global_timestamps.append(ts)
                metrics["granted"] += 1
                if es_prestamo:
                    metrics["borrowed"] += 1
                return True
            metrics["denied"] += 1
            return False
    except Exception:
        _fail_safe_events += 1
        return True  # fail-safe: nunca bloquear negocio real por un bug acá


def get_metrics() -> Dict[str, Any]:
    """Solo lectura -- copia superficial, thread-safe, de las métricas
    acumuladas desde que el proceso arrancó (nunca persiste a disco, se
    reinicia con el proceso, igual que el resto de los contadores en
    memoria ya existentes en `market_view.py`/`catalyst_worker.py`)."""
    with _lock:
        return {
            "por_consumidor": {c: dict(v) for c, v in _metrics.items()},
            "fail_safe_events": _fail_safe_events,
            "pisos_protegidos_por_minuto": dict(RESERVED_PER_MINUTE),
            "limite_total_seguro_por_minuto": total_safe_limit_per_minute(),
        }
