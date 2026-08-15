"""Corrida real de los Experimentos A/B/C (2026-08-16) sobre
`historical_reference.db` completo -- walk-forward, sin leakage, comparación
explícita contra el baseline. Solo lectura: no escribe nada en la base
histórica, no toca candidate_gates/phase_classifier/score/DecisionEngine.
"""

import json
import sqlite3

import pandas as pd

from atlas_live.learning import experiments as exp
from atlas_live.reference.reference_registry import DB_PATH

conn = sqlite3.connect(DB_PATH)
feat = pd.read_sql_query("SELECT * FROM daily_features", conn)
out = pd.read_sql_query("SELECT * FROM daily_outcome", conn)
conn.close()

df = feat.merge(out, on=["symbol", "date"], suffixes=("", "_out"))
rows = df.to_dict("records")
print(f"Filas unidas (features+outcome): {len(rows)}  fechas distintas: {df['date'].nunique()}")
print()

MIN_CAL = 10
DIAS_RECIENTE = 5

resultados = {}
for nombre, cols in [
    ("VOLATILIDAD_14D", ["volatility_14d_pct"]),
    ("DAILY_RANGE", ["daily_range_pct"]),
    ("COMBINADA", ["volatility_14d_pct", "daily_range_pct"]),
]:
    report = exp.run_walk_forward_experiment(rows, cols, nombre, min_calibration_dates=MIN_CAL, dias_reciente=DIAS_RECIENTE)
    resultados[nombre] = report.to_dict()

print("=" * 100)
print(f"Calibración: {resultados['VOLATILIDAD_14D']['rango_calibracion']} "
      f"({resultados['VOLATILIDAD_14D']['n_fechas_calibracion']} fechas, NUNCA evaluadas)")
print(f"Evaluado (walk-forward): {resultados['VOLATILIDAD_14D']['rango_evaluado']} "
      f"({resultados['VOLATILIDAD_14D']['n_fechas_evaluadas']} fechas)")
print("=" * 100)
print()

for nombre, r in resultados.items():
    print("-" * 100)
    print(f"EXPERIMENTO: {nombre}  (feature: {r['feature_cols']})")
    print("-" * 100)
    for direction in ("ALCISTA", "BAJISTA", "NEUTRAL"):
        d = r["por_direccion"][direction]
        print(f"  [{direction}]")
        for bucket in ("poblacion_total", "alto", "medio", "bajo"):
            b = d[bucket]
            print(f"    {bucket:18s} n={b['n']:6d}  +20%={b['pct_20']}  +50%={b['pct_50']}  +100%={b['pct_100']}")
    rv = r["reciente_vs_acumulada"]
    print(f"  ALCISTA reciente (últimas {DIAS_RECIENTE} fechas evaluadas, {rv['reciente']['fechas']}): "
          f"{rv['reciente']['aciertos_20']}/{rv['reciente']['n']} = {rv['reciente']['pct_20']}%")
    print(f"  ALCISTA acumulada (todo el walk-forward): "
          f"{rv['acumulada']['aciertos_20']}/{rv['acumulada']['n']} = {rv['acumulada']['pct_20']}%")
    print()

print("=" * 100)
print("¿VOLATILIDAD_14D y DAILY_RANGE miden lo mismo? -- correlación real (todo el dataset ALCISTA)")
print("=" * 100)
alc = df[df["direction"] == "ALCISTA"].dropna(subset=["volatility_14d_pct", "daily_range_pct"])
corr_pearson = alc["volatility_14d_pct"].corr(alc["daily_range_pct"], method="pearson")
# Spearman = Pearson sobre los RANGOS -- evita depender de scipy (no instalado acá).
corr_spearman = alc["volatility_14d_pct"].rank().corr(alc["daily_range_pct"].rank(), method="pearson")
print(f"  n={len(alc)}  correlación de Pearson={corr_pearson:.3f}  Spearman={corr_spearman:.3f}")
print()

print("=" * 100)
print("HIPÓTESIS B -- EARLY genuino vs LATE vs antes_del_movimiento (histórico completo)")
print("=" * 100)
b = exp.early_vs_late_historical(rows)
for direction in ("ALCISTA", "BAJISTA", "NEUTRAL"):
    print(f"  [{direction}]")
    for grupo in ("early_genuino", "late", "antes_del_movimiento"):
        g = b[direction][grupo]
        print(f"    {grupo:22s} n={g['n']:6d}  +20%={g['pct_20']}  +50%={g['pct_50']}  +100%={g['pct_100']}")

with open("experiments_abc_resultado.json", "w", encoding="utf-8") as fh:
    json.dump({"resultados": resultados, "correlacion": {"pearson": corr_pearson, "spearman": corr_spearman, "n": len(alc)},
               "hipotesis_b": b}, fh, indent=2, ensure_ascii=False)
print()
print("Resultado completo guardado en experiments_abc_resultado.json")
