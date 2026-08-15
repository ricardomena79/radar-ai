"""SIMULACIÓN -- demuestra cómo evoluciona el Aprendizaje en Vivo y la
Madurez a medida que se acumulan observaciones (2026-08-15).

Datos 100% SINTÉTICOS, generados acá mismo, sobre una base de datos
TEMPORAL (nunca la real de Atlas -- `candidate_registry.DB_PATH` se
redirige a un archivo temporal antes de escribir nada, y se restaura al
salir). Nada de esto representa observaciones reales de Atlas; sirve
únicamente para mostrar el comportamiento de la arquitectura de madurez
(los 11 ejes + cuello de botella) con volumen creciente.

Uso:
    python scripts/demo_maturity_evolution.py
"""

import json
import random
import tempfile
import uuid
from datetime import date, timedelta
from pathlib import Path

from atlas_live.learning import maturity as mat
from atlas_live.learning import thresholds as th
from atlas_live.radar import candidate_registry as reg

random.seed(2026_08_15)

TIMING_WEIGHTS = {
    "antes_del_movimiento": 0.45, "al_comienzo": 0.12, "expansion_temprana": 0.08,
    "recorrido_significativo_ya_hecho": 0.15, "demasiado_tarde": 0.08, "agotamiento": 0.12,
}
DIRECTIONS = ["ALCISTA", "BAJISTA", "NEUTRAL"]
N_SYMBOLS_POOL = 800  # símbolos sintéticos distintos disponibles para el sorteo


def _business_days(n: int, start: date):
    d = start
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _weighted_timing() -> str:
    r = random.random()
    acc = 0.0
    for k, w in TIMING_WEIGHTS.items():
        acc += w
        if r <= acc:
            return k
    return "antes_del_movimiento"


def _seed_n_casos(n_total: int, ya_sembrados: int, dias: list) -> None:
    """Agrega `n_total - ya_sembrados` casos NUEVOS (detección + outcome),
    repartidos con diversidad real (símbolo, día, timing, dirección,
    sesión) -- y recalcula/graba `daily_summary` para cada día tocado."""
    por_dia = {}
    CASES_PER_DAY = 15  # ritmo realista de candidatas/día (orden de magnitud ya visto en CAPA 2 real)
    for i in range(ya_sembrados, n_total):
        dia_idx, slot_en_dia = divmod(i, CASES_PER_DAY)
        dia = dias[dia_idx % len(dias)]
        # (ticker, día) único por construcción: dentro de un mismo día, los
        # `CASES_PER_DAY` slots mapean a símbolos distintos entre sí (nunca
        # colisiona con UNIQUE(ticker, market_date)); entre días distintos
        # el mismo símbolo SÍ puede repetirse -- realista, no es un error.
        ticker = f"SIM{(dia_idx * CASES_PER_DAY + slot_en_dia) % N_SYMBOLS_POOL}"
        timing = _weighted_timing()
        direction = DIRECTIONS[i % 3] if timing != "demasiado_tarde" else random.choice(DIRECTIONS)
        session = "premarket" if (i % 5 == 0) else "regular"
        change_pct = round(random.uniform(3.0, 35.0), 2)
        detected_at = f"{dia}T{13 + (i % 5):02d}:{(i * 7) % 60:02d}:00Z"

        reg.record_detection(
            ticker, dia, session, detected_at, f"sweep-{i}",
            price_at_detection=round(random.uniform(2, 400), 2), change_pct_at_detection=change_pct,
            volume_at_detection=int(random.uniform(1e5, 5e7)), average_volume_at_detection=int(1e6),
            relative_volume_at_detection=round(random.uniform(1.5, 12.0), 2),
            dollar_volume_at_detection=round(random.uniform(1e6, 5e8), 2), gates_fired=[],
        )
        post_apertura = None
        if session == "premarket":
            post_apertura = "continua" if random.random() < 0.55 else "colapsa"
        reg.set_phase_tag(ticker, dia, timing, direction_at_detection=direction, comportamiento_post_apertura=post_apertura)

        # tasa base de continuación decreciente por rareza del umbral --
        # NO usa datos reales, solo una distribución sintética razonable.
        r20 = random.random() < 0.22
        r50 = r20 and random.random() < 0.22
        r100 = r50 and random.random() < 0.10
        max_return = (100 if r100 else 55 if r50 else 25 if r20 else round(random.uniform(-15, 18), 1))
        categoria = "deteccion_tardia" if timing == "demasiado_tarde" and random.random() < 0.6 else (
            "buena_oportunidad" if r20 else "oportunidad_moderada"
        )
        direccion_correcta = random.random() < 0.55

        reg.record_outcome(
            ticker, dia, run_up_before_detection_pct=change_pct, max_price_after_detection=None,
            max_return_after_detection_pct=max_return, minutes_to_max=round(random.uniform(2, 180), 1),
            reached_20=r20, reached_50=r50, reached_100=r100, category=categoria,
            direccion_correcta=direccion_correcta,
        )
        acierto = r20 and categoria != "deteccion_tardia"
        d = por_dia.setdefault(dia, {"evaluables": 0, "aciertos": 0, "tardias": 0, "r20": 0, "r50": 0, "r100": 0})
        d["evaluables"] += 1
        d["aciertos"] += int(acierto)
        d["tardias"] += int(categoria == "deteccion_tardia")
        d["r20"] += int(r20)
        d["r50"] += int(r50)
        d["r100"] += int(r100)

    for dia, d in por_dia.items():
        prev = reg.get_daily_summary(dia) or {}
        reg.record_daily_summary(
            dia,
            n_estudiadas=2575,
            n_candidatas=(prev.get("n_candidatas") or 0) + d["evaluables"],
            n_senales=(prev.get("n_senales") or 0) + d["evaluables"],
            n_evaluables=(prev.get("n_evaluables") or 0) + d["evaluables"],
            n_aciertos=(prev.get("n_aciertos") or 0) + d["aciertos"],
            n_falsos_positivos=(prev.get("n_falsos_positivos") or 0),
            n_tardias=(prev.get("n_tardias") or 0) + d["tardias"],
            n_reached_20=(prev.get("n_reached_20") or 0) + d["r20"],
            n_reached_50=(prev.get("n_reached_50") or 0) + d["r50"],
            n_reached_100=(prev.get("n_reached_100") or 0) + d["r100"],
        )


def _print_tier(n: int) -> None:
    reporte = mat.compute_maturity()
    live = {
        "casos_cerrados": len(reg.list_all_evaluated_candidates()),
        "dias": len({e["market_date"] for e in reg.list_all_evaluated_candidates()}),
    }
    acumulada = reg.cumulative_precision()
    prec = (f"{acumulada.get('aciertos') or 0}/{acumulada.get('evaluables') or 0} = "
            f"{acumulada.get('precision_pct')}%") if acumulada.get("evaluables") else "No disponible"

    print(f"\n{'='*78}\nSIMULACIÓN -- n={n} observaciones cerradas (datos sintéticos, NO reales)\n{'='*78}")
    print(f"casos_cerrados_reales_en_db={live['casos_cerrados']}  dias_distintos={live['dias']}")
    print(f"Precisión acumulada: {prec}")
    print(f"MADUREZ GLOBAL: {reporte.global_level_label} (nivel {reporte.global_level}/6)")
    print(f"Eje que limita: {reporte.limiting_axis.label} -- {reporte.limiting_axis.explanation}")
    print("-- detalle de los 11 ejes --")
    for a in reporte.axes:
        marca = " <== LIMITA" if a is reporte.limiting_axis else ""
        print(f"  [{a.level}/6] {a.label:42s} {a.level_label:28s}{marca}")


def main() -> None:
    tmp_db = Path(tempfile.gettempdir()) / f"atlas_demo_maturity_{uuid.uuid4().hex}.db"
    orig_db_path = reg.DB_PATH
    reg.DB_PATH = tmp_db
    reg._schema_ready_for = None
    print(f"Base TEMPORAL de la simulación: {tmp_db} (se borra al terminar; NUNCA toca la base real)")

    dias = _business_days(400, date(2026, 8, 17))  # ~400 días hábiles sintéticos, arrancan el lunes

    try:
        _print_tier(0)  # estado cero, sin sembrar nada
        sembrados = 0
        for objetivo in (10, 100, 1000, 5000):
            _seed_n_casos(objetivo, sembrados, dias)
            sembrados = objetivo
            _print_tier(objetivo)

        print(f"\n{'='*78}")
        print("Nota real (no simulada): si la evidencia sintética de arriba estuviera")
        print("concentrada en pocos símbolos/buckets en vez de repartida, la madurez NO")
        print("subiría aunque 'n' fuera enorme -- ver atlas_live/learning/test_maturity.py::")
        print("test_compute_maturity_es_el_minimo_no_un_promedio, que reproduce exactamente eso.")
    finally:
        reg.DB_PATH = orig_db_path
        reg._schema_ready_for = None
        try:
            tmp_db.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm"):
                Path(str(tmp_db) + suffix).unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    main()
