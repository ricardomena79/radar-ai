"""Segunda pasada de análisis, solo lectura -- refina la comparación EARLY/LATE
excluyendo el bucket 'antes_del_movimiento' (74.6% del dataset, mayormente días
sin nada relevante) y agrega un chequeo de estabilidad temporal (mitad
cronológica 1 vs 2) para las señales más prometedoras, como insumo honesto para
decidir qué hipótesis merecen pasar a experimento."""

import sqlite3
import pandas as pd
from atlas_live.reference.reference_registry import DB_PATH

conn = sqlite3.connect(DB_PATH)
feat = pd.read_sql_query("SELECT * FROM daily_features", conn)
out = pd.read_sql_query("SELECT * FROM daily_outcome", conn)
conn.close()
df = feat.merge(out, on=["symbol", "date"], suffixes=("", "_out"))
alc = df[df["direction"] == "ALCISTA"].copy()

GENUINE_EARLY = ["al_comienzo", "expansion_temprana"]  # excluye antes_del_movimiento (ruido)
LATE = ["recorrido_significativo_ya_hecho", "demasiado_tarde", "agotamiento"]

print("=" * 90)
print("A) EARLY genuino (al_comienzo+expansion_temprana) vs LATE (recorrido/tarde/agotamiento)")
print("   -- excluye 'antes_del_movimiento' a propósito (ver nota metodológica)")
print("=" * 90)
early = alc[alc["timing_deteccion"].isin(GENUINE_EARLY)]
late = alc[alc["timing_deteccion"].isin(LATE)]
for label, sub in [("EARLY genuino", early), ("LATE", late)]:
    n = len(sub)
    print(f"  {label}: n={n}  reached_20={(sub['max_advance_pct']>=20).mean()*100:5.1f}%  "
          f"reached_50={(sub['max_advance_pct']>=50).mean()*100:5.1f}%  "
          f"reached_100={(sub['max_advance_pct']>=100).mean()*100:5.1f}%  "
          f"dias_a_max={sub['days_to_max'].mean():5.2f}  drawdown={sub['max_drawdown_pct'].mean():7.2f}%  "
          f"relative_volume_media={sub['relative_volume'].mean():5.2f}")
print()

print("=" * 90)
print("B) Estabilidad temporal: mitad 1 (fechas más antiguas) vs mitad 2 -- relative_volume y volatility_14d_pct")
print("=" * 90)
fechas = sorted(alc["date"].unique())
corte = fechas[len(fechas) // 2]
mitad1 = alc[alc["date"] < corte]
mitad2 = alc[alc["date"] >= corte]
print(f"  Corte en {corte} -- mitad1: {mitad1['date'].min()}..{mitad1['date'].max()} (n={len(mitad1)}), "
      f"mitad2: {mitad2['date'].min()}..{mitad2['date'].max()} (n={len(mitad2)})")
for label, sub in [("mitad1", mitad1), ("mitad2", mitad2)]:
    hi_rvol = sub[sub["relative_volume"] >= sub["relative_volume"].quantile(0.75)]
    lo_rvol = sub[sub["relative_volume"] < sub["relative_volume"].quantile(0.25)]
    hi_vol = sub[sub["volatility_14d_pct"] >= sub["volatility_14d_pct"].quantile(0.75)]
    lo_vol = sub[sub["volatility_14d_pct"] < sub["volatility_14d_pct"].quantile(0.25)]
    print(f"  {label}: RVOL top25%  reached_20={(hi_rvol['max_advance_pct']>=20).mean()*100:5.1f}%   "
          f"RVOL bottom25% reached_20={(lo_rvol['max_advance_pct']>=20).mean()*100:5.1f}%")
    print(f"  {label}: volat14d top25% reached_20={(hi_vol['max_advance_pct']>=20).mean()*100:5.1f}%   "
          f"volat14d bottom25% reached_20={(lo_vol['max_advance_pct']>=20).mean()*100:5.1f}%")
print()

print("=" * 90)
print("C) Dentro de 'antes_del_movimiento' -- ¿algo distingue los que SÍ terminan con recorrido de los que no?")
print("=" * 90)
antes = alc[alc["timing_deteccion"] == "antes_del_movimiento"]
hit20 = antes[antes["max_advance_pct"] >= 20]
miss20 = antes[antes["max_advance_pct"] < 20]
print(f"  n(antes_del_movimiento, ALCISTA)={len(antes)}  reached_20={len(hit20)} ({len(hit20)/len(antes)*100:.1f}%)")
for col in ["relative_volume", "gap_pct", "volatility_14d_pct", "daily_range_pct"]:
    print(f"    {col:22s} hit_media={hit20[col].mean():7.3f}  miss_media={miss20[col].mean():7.3f}")
print()

print("=" * 90)
print("D) Combinación: relative_volume alto (>=2x) DENTRO de 'antes_del_movimiento' -- ¿mejor selector de 'a punto de arrancar'?")
print("=" * 90)
antes_rvol_hi = antes[antes["relative_volume"] >= 2.0]
antes_rvol_lo = antes[antes["relative_volume"] < 2.0]
print(f"  antes_del_movimiento + RVOL>=2x: n={len(antes_rvol_hi)}  reached_20={(antes_rvol_hi['max_advance_pct']>=20).mean()*100:5.1f}%  reached_50={(antes_rvol_hi['max_advance_pct']>=50).mean()*100:5.1f}%")
print(f"  antes_del_movimiento + RVOL<2x:  n={len(antes_rvol_lo)}  reached_20={(antes_rvol_lo['max_advance_pct']>=20).mean()*100:5.1f}%  reached_50={(antes_rvol_lo['max_advance_pct']>=50).mean()*100:5.1f}%")
