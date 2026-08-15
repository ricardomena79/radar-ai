"""Tests del motor de Madurez del Aprendizaje (2026-08-15). Datos
sintéticos, sin red, sin tocar la base real -- mismo criterio que el resto
del proyecto."""

from atlas_live.learning import maturity as mat
from atlas_live.learning import thresholds as th


# --------------------------- thresholds.py ---------------------------

def test_level_from_breakpoints_bordes():
    bps = [1, 10, 30, 75, 150, 300]
    assert th.level_from_breakpoints(0, bps) == 0
    assert th.level_from_breakpoints(1, bps) == 1
    assert th.level_from_breakpoints(9, bps) == 1
    assert th.level_from_breakpoints(10, bps) == 2
    assert th.level_from_breakpoints(300, bps) == 6
    assert th.level_from_breakpoints(10_000, bps) == 6


def test_wilson_interval_mas_muestra_reduce_el_ancho():
    chico = th.wilson_interval(7, 10)
    grande = th.wilson_interval(700, 1000)
    assert (chico.high - chico.low) > (grande.high - grande.low)


def test_intervals_overlap_casos_obvios():
    a = th.wilson_interval(9, 10)   # ~90%, intervalo ancho por n chico
    b = th.wilson_interval(1, 10)   # ~10%
    assert th.intervals_overlap(a, a) is True
    # con n=10 cada uno el intervalo de Wilson es ancho -- para un caso
    # claramente disjunto hace falta más separación real
    c = th.wilson_interval(950, 1000)
    d = th.wilson_interval(50, 1000)
    assert th.intervals_overlap(c, d) is False


# --------------------------- helpers ---------------------------

def _caso(ticker="AAA", date="2026-08-15", session="regular", timing="al_comienzo",
          direction="ALCISTA", r20=False, r50=False, r100=False, post_apertura=None):
    return {
        "ticker": ticker, "market_date": date, "session": session,
        "change_pct_at_detection": 8.0, "direction_at_detection": direction,
        "phase_tag": timing, "comportamiento_post_apertura": post_apertura,
        "reached_20": r20, "reached_50": r50, "reached_100": r100,
        "category": "buena_oportunidad", "direccion_correcta": True,
    }


# --------------------------- eje 1: volumen ---------------------------

def test_axis_volumen_cero():
    a = mat.axis_volumen([])
    assert a.level == 0


def test_axis_volumen_progresa_con_n():
    assert mat.axis_volumen([_caso() for _ in range(5)]).level == 1
    assert mat.axis_volumen([_caso() for _ in range(10)]).level == 2
    assert mat.axis_volumen([_caso() for _ in range(300)]).level == 6


# --------------------------- eje 3: símbolos + concentración ---------------------------

def test_axis_simbolos_concentracion_baja_el_nivel():
    # 50 casos, pero TODOS del mismo símbolo -- concentración 100%, debe
    # capar el nivel aunque el conteo de símbolos distintos sea bajo (1)
    casos = [_caso(ticker="AAA") for _ in range(50)]
    a = mat.axis_simbolos(casos)
    assert a.evidence["simbolos_distintos"] == 1
    assert a.level <= 1  # con 1 símbolo, el propio conteo de símbolos ya lo limita


def test_axis_simbolos_diversificado_sube_mas():
    casos = [_caso(ticker=f"S{i % 150}") for i in range(1500)]
    a = mat.axis_simbolos(casos)
    assert a.evidence["simbolos_distintos"] == 150
    assert a.evidence["concentracion_top3_pct"] < 10
    assert a.level >= 5


# --------------------------- eje 5/6: timing y dirección (peor bucket) ---------------------------

def test_axis_timing_un_bucket_vacio_capa_el_eje():
    # 5 de los 6 buckets con mucha evidencia, 1 bucket SIN ningún caso
    casos = []
    for b in th.TIMING_BUCKETS[:5]:
        casos += [_caso(timing=b) for _ in range(100)]
    a = mat.axis_timing(casos)
    assert a.evidence["peor_bucket"] == th.TIMING_BUCKETS[5]
    assert a.evidence["peor_n"] == 0
    assert a.level == 0  # el eje entero queda en "sin evidencia" por el bucket faltante


def test_axis_timing_los_6_cubiertos_sube():
    casos = []
    for b in th.TIMING_BUCKETS:
        casos += [_caso(timing=b) for _ in range(25)]
    a = mat.axis_timing(casos)
    assert a.level == 3  # peor_n=25 -> entre 20 y 30 -> nivel 3


def test_axis_direccion_peor_bucket():
    casos = [_caso(direction="ALCISTA") for _ in range(50)] + [_caso(direction="BAJISTA") for _ in range(2)]
    a = mat.axis_direccion(casos)
    assert a.evidence["peor_bucket"] == "NEUTRAL"
    assert a.level == 0


# --------------------------- eje 7: post-apertura ---------------------------

def test_axis_post_apertura_sin_datos():
    a = mat.axis_post_apertura([_caso(session="regular")])
    assert a.level == 0


def test_axis_post_apertura_desbalanceado_capa_bajo():
    casos = [_caso(session="premarket", post_apertura="continua") for _ in range(100)] + \
            [_caso(session="premarket", post_apertura="colapsa") for _ in range(1)]
    a = mat.axis_post_apertura(casos)
    assert a.evidence["colapsa"] == 1
    assert a.level == 1  # peor=1 < 5 -> nivel 1 (evidencia inicial)


def test_axis_post_apertura_balanceado_sube():
    casos = [_caso(session="premarket", post_apertura="continua") for _ in range(20)] + \
            [_caso(session="premarket", post_apertura="colapsa") for _ in range(20)]
    a = mat.axis_post_apertura(casos)
    assert a.level == 4  # peor=20 -> nivel 1+3


# --------------------------- eje 8: objetivos ---------------------------

def test_axis_objetivos_escalera_completa():
    assert mat.axis_objetivos([]).level == 0
    assert mat.axis_objetivos([_caso(r20=True)]).level == 1
    assert mat.axis_objetivos([_caso(r20=True) for _ in range(10)]).level == 2
    casos_l4 = [_caso(r20=True) for _ in range(10)] + [_caso(r20=True, r50=True) for _ in range(5)]
    assert mat.axis_objetivos(casos_l4).level == 3
    casos_l5 = [_caso(r20=True) for _ in range(5)] + [_caso(r20=True, r50=True) for _ in range(10)]
    assert mat.axis_objetivos(casos_l5).level == 4
    casos_l7 = [_caso(r20=True, r50=True, r100=True) for _ in range(10)]
    assert mat.axis_objetivos(casos_l7).level == 6


def test_axis_objetivos_no_confunde_mucha_muestra_de_20_con_evidencia_de_100():
    # 1000 positivos de +20% pero CERO de +100% -- no debe llegar a nivel alto
    casos = [_caso(r20=True) for _ in range(1000)]
    a = mat.axis_objetivos(casos)
    assert a.evidence["positivos_100"] == 0
    assert a.level == 2  # r20 >= piso pero r50 < intermedio


# --------------------------- eje 9: consistencia ---------------------------

def _resumen(date, evaluables, aciertos):
    return {"market_date": date, "n_evaluables": evaluables, "n_aciertos": aciertos}


def test_axis_consistencia_pocas_ventanas():
    ds = [_resumen(f"2026-08-{i:02d}", 3, 1) for i in range(1, 6)]
    a = mat.axis_consistencia(ds)
    assert a.level <= 2


def test_axis_consistencia_ventanas_consistentes_solapan():
    # misma precisión real (~50%) sostenida en 6 ventanas de 5 días (30 días, 10 casos/día)
    ds = []
    for w in range(6):
        for d in range(5):
            ds.append(_resumen(f"2026-{(w+1):02d}-{d+1:02d}", 10, 5))
    a = mat.axis_consistencia(ds)
    assert a.level == 6


def test_axis_consistencia_ventanas_erraticas_no_solapan():
    # ventanas grandes (50 casos c/u) con resultados claramente disjuntos --
    # con muestra chica el intervalo de Wilson es tan ancho que casi
    # cualquier par "solapa" (comportamiento conservador esperado, ver
    # PROPUESTA_MADUREZ_APRENDIZAJE.md 8.0); con muestra grande y una
    # diferencia real, el solapamiento debe desaparecer.
    ds = []
    for w in range(6):
        aciertos_por_dia = 9 if w % 2 == 0 else 0  # alterna ~90% y 0% de precisión
        for d in range(5):
            ds.append(_resumen(f"2026-{(w+1):02d}-{d+1:02d}", 10, aciertos_por_dia))
    a = mat.axis_consistencia(ds)
    assert a.level < 6


# --------------------------- eje 10: recencia ---------------------------

def test_axis_recencia_alerta_cuando_cae_fuera_del_intervalo():
    historico = [_resumen(f"2026-01-{d:02d}", 5, 4) for d in range(1, 21)]  # ~80% histórico, 100 casos
    reciente_malo = [_resumen(f"2026-03-{d:02d}", 5, 0) for d in range(1, 21)]  # 0% reciente, 100 casos
    a = mat.axis_recencia(historico + reciente_malo)
    assert a.evidence["alerta_caida_fuera_de_intervalo"] is True
    assert a.level <= 4


def test_axis_recencia_sin_casos_recientes():
    a = mat.axis_recencia([])
    assert a.level == 0


# --------------------------- eje 11: validación fuera de muestra ---------------------------

def test_axis_validacion_pocos_dias():
    ds = [_resumen(f"2026-08-{d:02d}", 5, 3) for d in range(1, 5)]
    a = mat.axis_validacion(ds, [])
    assert a.level == 0


def test_axis_validacion_holdout_con_evidencia():
    ds = [_resumen(f"2026-{m:02d}-{d:02d}", 4, 2) for m in range(1, 4) for d in range(1, 11)]
    evaluated = []
    for row in ds[-10:]:
        for b in th.TIMING_BUCKETS[:4]:
            evaluated.append(_caso(date=row["market_date"], timing=b, direction="ALCISTA"))
    a = mat.axis_validacion(ds, evaluated)
    assert a.evidence["holdout_n"] > 0
    assert a.level >= 4


# --------------------------- orquestador / cuello de botella ---------------------------

def test_compute_maturity_cero():
    r = mat.compute_maturity(evaluated=[], daily_summaries=[])
    assert r.global_level == 0
    assert r.global_level_label == "Sin evidencia"


def test_compute_maturity_es_el_minimo_no_un_promedio():
    # 10 ejes con muchísima evidencia, 1 eje (timing) deliberadamente roto
    # (un bucket en cero) -- la madurez global debe quedar atada a ESE eje.
    casos = []
    for b in th.TIMING_BUCKETS[:5]:  # falta 1 bucket a propósito
        for i in range(200):
            casos.append(_caso(
                ticker=f"S{i % 80}", date=f"2026-{(i % 6) + 1:02d}-{(i % 28) + 1:02d}",
                timing=b, direction=["ALCISTA", "BAJISTA", "NEUTRAL"][i % 3],
                session="premarket" if i % 2 == 0 else "regular",
                post_apertura="continua" if i % 2 == 0 else "colapsa",
                r20=(i % 5 == 0), r50=(i % 25 == 0), r100=(i % 100 == 0),
            ))
    ds = [_resumen(f"2026-{(i % 6) + 1:02d}-{(i % 28) + 1:02d}", 20, 10) for i in range(60)]

    r = mat.compute_maturity(evaluated=casos, daily_summaries=ds)
    assert r.limiting_axis.key == "timing"
    assert r.global_level == r.limiting_axis.level
    assert r.global_level < 6  # NO puede llegar a "madurez alta" solo por volumen
    assert all(r.global_level <= a.level for a in r.axes)


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
            p += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
            traceback.print_exc()
            f += 1
    print(f"--- {p} passed, {f} failed ---")
