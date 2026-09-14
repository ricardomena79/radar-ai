"""Tests de `finnhub_shared_budget.py` -- modelo de piso protegido +
excedente compartido (AJUSTE 1, 2026-09-14, autorizado explícitamente).
Cada test resetea el estado global del módulo (mismo patrón que
cualquier módulo con estado en memoria a nivel de proceso, ver
`market_view.py`/`catalyst_worker.py`) para quedar aislado del resto de
la suite.

Hallazgo matemático relevante para leer estos tests (ya declarado al
usuario antes de implementar, no oculto acá): con las reservas reales
(40+10+5=55=el límite total exacto), un consumidor de menor prioridad
NUNCA puede pedir prestado más allá de su propio piso en un instante
estático, aunque los de mayor prioridad estén en 0 uso -- porque
garantizar el piso completo de ambos, siempre, exige reservarles el
100% de su capacidad no usada TODAVÍA dentro de la ventana de 60s
(no se puede distinguir "inactivo" de "a punto de rafagear" sin
adivinar el futuro). El préstamo real solo se habilita cuando una
entrada vieja de un consumidor de mayor prioridad EXPIRA de la ventana
deslizante (pasan >60s desde esa request) -- eso libera capacidad
genuina, sin arriesgar el piso de nadie. Se prueba explícitamente
abajo (`test_prestamo_se_habilita_cuando_expira_la_ventana_de_mayor_prioridad`)."""

import threading
import time

from atlas_live.data_fusion import finnhub_shared_budget as budget

# Snapshot de las reservas REALES (producción) tomado una sola vez, al
# importar este archivo -- `_reset()` siempre restaura desde acá, nunca
# acumula mutaciones de un test sobre otro. Sin esto, un test que pide
# reservas chicas (ej. {"hot_quote": 1}) dejaría ese valor pisado para
# TODA la sesión de pytest, incluidos módulos de producción (`hot_quote.py`/
# `catalyst_worker.py`/`market_view.py`) que corren en la MISMA suite y
# dependen de las reservas reales (40/10/5) para su propio comportamiento.
_RESERVAS_ORIGINALES = dict(budget.RESERVED_PER_MINUTE)


def _reset(reservas=None):
    with budget._lock:
        for c in budget.CONSUMERS:
            budget._timestamps_by_consumer[c] = budget.deque()
            budget._metrics[c] = {"granted": 0, "denied": 0, "borrowed": 0}
        budget._global_timestamps = budget.deque()
        budget._fail_safe_events = 0
    budget.RESERVED_PER_MINUTE.clear()
    budget.RESERVED_PER_MINUTE.update(_RESERVAS_ORIGINALES)
    if reservas is not None:
        budget.RESERVED_PER_MINUTE.update(reservas)


# --------------------------- pisos protegidos ---------------------------

def test_cada_consumidor_mantiene_su_piso_protegido():
    # Escenario B del razonamiento: hot_quote 20, catalyst 10, market 5 --
    # todos dentro de (o exactamente en) su propio piso, sin conflicto.
    _reset({"hot_quote": 40, "catalyst_worker": 10, "market_view": 5})
    try:
        for _ in range(20):
            assert budget.try_acquire("hot_quote") is True
        for _ in range(10):
            assert budget.try_acquire("catalyst_worker") is True
        for _ in range(5):
            assert budget.try_acquire("market_view") is True
    finally:
        _reset()


def test_un_consumidor_no_puede_comerse_el_piso_de_otro():
    # market_view agota su piso (5) primero -- catalyst_worker y
    # hot_quote siguen pudiendo alcanzar el SUYO propio completo, sin
    # ningún rechazo, aunque market_view ya haya consumido capacidad
    # global primero.
    _reset({"hot_quote": 40, "catalyst_worker": 10, "market_view": 5})
    try:
        for _ in range(5):
            assert budget.try_acquire("market_view") is True
        for _ in range(10):
            assert budget.try_acquire("catalyst_worker") is True
        for _ in range(40):
            assert budget.try_acquire("hot_quote") is True
    finally:
        _reset()


def test_pedido_mas_alla_del_piso_se_rechaza_si_no_hay_margen_real():
    # Escenario C del razonamiento: los 3 en su propio piso exacto ->
    # total=55=techo. Un pedido adicional de CUALQUIERA se rechaza -- no
    # queda margen real, ni con préstamo.
    _reset({"hot_quote": 40, "catalyst_worker": 10, "market_view": 5})
    try:
        for _ in range(40):
            assert budget.try_acquire("hot_quote") is True
        for _ in range(10):
            assert budget.try_acquire("catalyst_worker") is True
        for _ in range(5):
            assert budget.try_acquire("market_view") is True
        assert budget.try_acquire("hot_quote") is False
        assert budget.try_acquire("catalyst_worker") is False
        assert budget.try_acquire("market_view") is False
    finally:
        _reset()


def test_market_view_solo_alcanza_su_piso_cuando_los_demas_estan_en_cero():
    # Escenario E del razonamiento, resultado real (no el ideal): con
    # hot_quote y catalyst_worker en 0 uso, market_view puede usar
    # exactamente SU piso (5) -- ni una unidad más, porque el algoritmo
    # reserva el 100% del piso no usado de los de mayor prioridad dentro
    # de la ventana (garantía de "nunca les quito piso", documentada
    # explícitamente como el costo de esa garantía con estos números).
    _reset({"hot_quote": 40, "catalyst_worker": 10, "market_view": 5})
    try:
        concedidos = sum(1 for _ in range(20) if budget.try_acquire("market_view"))
        assert concedidos == 5
    finally:
        _reset()


def test_total_global_nunca_excede_el_limite_configurado():
    _reset({"hot_quote": 4, "catalyst_worker": 2, "market_view": 1})
    try:
        concedidos = 0
        for _ in range(50):
            for consumidor in ("hot_quote", "catalyst_worker", "market_view"):
                if budget.try_acquire(consumidor):
                    concedidos += 1
        assert concedidos == budget.total_safe_limit_per_minute()  # 7, nunca más
    finally:
        _reset()


def test_prestamo_se_habilita_cuando_expira_la_ventana_de_mayor_prioridad():
    # Prueba el mecanismo real de "excedente compartido": mientras la
    # request de hot_quote (t0) sigue dentro de su ventana de 60s, su
    # piso completo queda reservado -- catalyst_worker no puede pedir
    # prestado. Una vez que esa request EXPIRA (pasan >60s), esa
    # capacidad queda genuinamente libre y catalyst_worker sí puede
    # pedirla prestada -- sin haber arriesgado nunca el piso real de
    # hot_quote mientras estuvo vigente. Verificado con la implementación
    # real (no a mano): reservas hot_quote=2/catalyst=1/market=1 (techo=4).
    _reset({"hot_quote": 2, "catalyst_worker": 1, "market_view": 1})
    try:
        t0 = 1000.0
        # hot_quote usa su piso completo en t0.
        assert budget.try_acquire("hot_quote", now=t0) is True
        assert budget.try_acquire("hot_quote", now=t0) is True
        # catalyst_worker y market_view usan su propio piso, un poco después.
        assert budget.try_acquire("catalyst_worker", now=t0 + 5) is True
        assert budget.try_acquire("market_view", now=t0 + 5) is True
        # catalyst_worker pide una 2da (préstamo) mientras hot_quote sigue
        # en ventana -- se reserva su piso completo, sin margen real.
        assert budget.try_acquire("catalyst_worker", now=t0 + 10) is False
        # Pasan >60s desde t0 -- las 2 requests de hot_quote ya expiraron
        # de su ventana, liberando esa capacidad de verdad.
        assert budget.try_acquire("catalyst_worker", now=t0 + 61) is True
    finally:
        _reset()


def test_piso_de_mayor_prioridad_nunca_lo_puede_comer_un_prestamo_de_menor_prioridad():
    # Regresión del bug real encontrado y corregido durante el testeo de
    # AJUSTE 1: con una primera versión del algoritmo (que solo reservaba
    # capacidad para consumidores de MAYOR prioridad al decidir un
    # préstamo), esta secuencia exacta terminaba negando el piso de
    # hot_quote -- catalyst_worker "pedía prestado" protegiendo solo a
    # hot_quote, y luego market_view (al pedir SU propio piso con una
    # condición más laxa) terminaba usando esa misma capacidad que debía
    # quedar reservada. Con la corrección (reservar para TODOS los
    # demás), hot_quote siempre obtiene su piso, sin importar el orden.
    _reset({"hot_quote": 1, "catalyst_worker": 1, "market_view": 1})
    try:
        assert budget.try_acquire("catalyst_worker") is True  # su propio piso
        assert budget.try_acquire("catalyst_worker") is False  # préstamo: sin margen real
        assert budget.try_acquire("market_view") is True  # su propio piso
        assert budget.try_acquire("hot_quote") is True  # su piso SIEMPRE garantizado
    finally:
        _reset()


def test_prioridad_hot_quote_mayor_que_catalyst_que_market_view():
    assert budget.RESERVED_PER_MINUTE["hot_quote"] > budget.RESERVED_PER_MINUTE["catalyst_worker"]
    assert budget.RESERVED_PER_MINUTE["catalyst_worker"] > budget.RESERVED_PER_MINUTE["market_view"]
    assert budget.PRIORITY_ORDER == ["hot_quote", "catalyst_worker", "market_view"]
    # Margen de seguridad real bajo el límite nominal de Finnhub (60/min).
    assert budget.total_safe_limit_per_minute() < 60


def test_ventana_deslizante_libera_cupo_pasado_el_minuto():
    _reset({"hot_quote": 1, "catalyst_worker": 1, "market_view": 1})
    try:
        t0 = 1000.0
        assert budget.try_acquire("catalyst_worker", now=t0) is True
        assert budget.try_acquire("catalyst_worker", now=t0 + 1) is False  # ya en su piso, sin margen global
        assert budget.try_acquire("catalyst_worker", now=t0 + 61) is True  # ventana ya pasó
    finally:
        _reset()


def test_no_bloquea_ni_espera():
    _reset({"hot_quote": 0, "catalyst_worker": 0, "market_view": 0})
    try:
        t0 = time.monotonic()
        resultado = budget.try_acquire("hot_quote")
        duracion = time.monotonic() - t0
        assert resultado is False
        assert duracion < 0.05  # respuesta inmediata, nunca una espera real
    finally:
        _reset()


def test_metricas_correctas_granted_denied():
    _reset({"hot_quote": 1, "catalyst_worker": 1, "market_view": 1})
    try:
        budget.try_acquire("hot_quote")  # granted, dentro del piso
        budget.try_acquire("hot_quote")  # sin margen (piso=techo con 3 consumidores en 1/1/1)
        m = budget.get_metrics()
        assert m["por_consumidor"]["hot_quote"]["granted"] == 1
        assert m["por_consumidor"]["hot_quote"]["denied"] == 1
        assert m["pisos_protegidos_por_minuto"]["hot_quote"] == 1
        assert m["limite_total_seguro_por_minuto"] == 3
    finally:
        _reset()


def test_consumidor_desconocido_falla_hacia_conceder():
    _reset()
    try:
        assert budget.try_acquire("consumidor_inventado_typo") is True
        assert budget.try_acquire("consumidor_inventado_typo") is True  # siempre concede, sin reserva
    finally:
        _reset()


def test_fail_safe_ante_excepcion_interna_concede_y_cuenta_el_evento(monkeypatch):
    _reset()

    def _prune_roto(dq, now):
        raise RuntimeError("bug simulado")

    monkeypatch.setattr(budget, "_prune", _prune_roto)
    try:
        assert budget.try_acquire("hot_quote") is True  # fail-safe: concede igual
        m = budget.get_metrics()
        assert m["fail_safe_events"] >= 1
    finally:
        _reset()


def test_thread_safe_concurrencia_mantiene_el_limite_global():
    # 100 hilos compitiendo por un techo global de 10 -- exactamente 10
    # deben ganar, sin condiciones de carrera que concedan de más.
    _reset({"hot_quote": 10, "catalyst_worker": 0, "market_view": 0})
    try:
        resultados = []
        lock_resultados = threading.Lock()

        def _worker():
            r = budget.try_acquire("hot_quote")
            with lock_resultados:
                resultados.append(r)

        hilos = [threading.Thread(target=_worker) for _ in range(100)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

        assert sum(1 for r in resultados if r is True) == 10
        assert sum(1 for r in resultados if r is False) == 90
    finally:
        _reset()


def test_thread_safe_concurrencia_multi_consumidor_no_excede_el_total():
    # Los 3 consumidores reales, disparando concurrentemente muchas más
    # solicitudes que el techo global -- el total concedido nunca debe
    # superar `total_safe_limit_per_minute()`, sin importar el orden real
    # de ejecución de los hilos.
    _reset({"hot_quote": 4, "catalyst_worker": 2, "market_view": 1})
    try:
        resultados = []
        lock_resultados = threading.Lock()

        def _worker(consumidor):
            r = budget.try_acquire(consumidor)
            with lock_resultados:
                resultados.append(r)

        hilos = []
        for _ in range(30):
            hilos.append(threading.Thread(target=_worker, args=("hot_quote",)))
            hilos.append(threading.Thread(target=_worker, args=("catalyst_worker",)))
            hilos.append(threading.Thread(target=_worker, args=("market_view",)))
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

        concedidos = sum(1 for r in resultados if r is True)
        assert concedidos == budget.total_safe_limit_per_minute()  # 7, nunca más
    finally:
        _reset()
