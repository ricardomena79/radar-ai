"""Análisis exploratorio, SOLO LECTURA, sobre `historical_reference.db` completo
(2.575/2.575 símbolos procesados) -- para armar la PROPUESTA DE EXPERIMENTOS
pedida (2026-08-15). No modifica nada, no toca candidate_gates/phase_classifier/
score/DecisionEngine. Cada número impreso viene de una consulta real sobre la
base ya construida -- nada se inventa acá.
"""

import sqlite3
from pathlib import Path

import pandas as pd

from atlas_live.reference.reference_registry import DB_PATH

conn = sqlite3.connect(DB_PATH)
feat = pd.read_sql_query("SELECT * FROM daily_features", conn)
out = pd.read_sql_query("SELECT * FROM daily_outcome", conn)
conn.close()

df = feat.merge(out, on=["symbol", "date"], suffixes=("", "_out"))
print(f"Filas con features+outcome unidos: {len(df)} (de {len(feat)} features y {len(out)} outcomes totales)")
print(f"Símbolos distintos: {df['symbol'].nunique()}  Fechas distintas: {df['date'].nunique()}")
print()

EARLY = ["antes_del_movimiento", "al_comienzo", "expansion_temprana"]
LATE = ["recorrido_significativo_ya_hecho", "demasiado_tarde"]

print("=" * 90)
print("A) Tabla completa por timing_deteccion (todas direcciones)")
print("=" * 90)
g = df.groupby("timing_deteccion").agg(
    n=("symbol", "count"),
    pct_reached_20=("reached_20" if "reached_20" in df.columns else "max_advance_pct", "count"),
).reset_index()
for timing in df["timing_deteccion"].dropna().unique():
    sub = df[df["timing_deteccion"] == timing]
    n = len(sub)
    r20 = (sub["max_advance_pct"] >= 20).mean() * 100
    r50 = (sub["max_advance_pct"] >= 50).mean() * 100
    r100 = (sub["max_advance_pct"] >= 100).mean() * 100
    avg_days = sub["days_to_max"].mean()
    avg_dd = sub["max_drawdown_pct"].mean()
    print(f"  {timing:38s} n={n:6d}  +20%={r20:5.1f}%  +50%={r50:5.1f}%  +100%={r100:5.1f}%  "
          f"dias_a_max={avg_days:5.2f}  drawdown_prom={avg_dd:7.2f}%")
print()

print("=" * 90)
print("B) EARLY (antes/comienzo/expansion) vs LATE (recorrido hecho/tarde) -- solo direction=ALCISTA")
print("=" * 90)
alc = df[df["direction"] == "ALCISTA"]
early = alc[alc["timing_deteccion"].isin(EARLY)]
late = alc[alc["timing_deteccion"].isin(LATE)]
for label, sub in [("EARLY", early), ("LATE", late)]:
    n = len(sub)
    print(f"  {label}: n={n}")
    for col in ["relative_volume", "gap_pct", "volatility_14d_pct", "peak_gain_10d_pct",
                "daily_range_pct", "rebound_from_trough_pct"]:
        print(f"      {col:26s} media={sub[col].mean():8.3f}  mediana={sub[col].median():8.3f}")
    print(f"      {'reached_20':26s} {(sub['max_advance_pct']>=20).mean()*100:6.1f}%")
    print(f"      {'reached_50':26s} {(sub['max_advance_pct']>=50).mean()*100:6.1f}%")
    print(f"      {'reached_100':26s} {(sub['max_advance_pct']>=100).mean()*100:6.1f}%")
    print(f"      {'dias_a_max (prom)':26s} {sub['days_to_max'].mean():6.2f}")
print()

print("=" * 90)
print("C) Diferenciadores de +20% / +50% / +100% (direction=ALCISTA, cualquier timing)")
print("=" * 90)
for umbral in (20, 50, 100):
    hit = alc[alc["max_advance_pct"] >= umbral]
    miss = alc[alc["max_advance_pct"] < umbral]
    print(f"  --- Umbral +{umbral}% --- (hit n={len(hit)}, miss n={len(miss)})")
    for col in ["relative_volume", "gap_pct", "volatility_14d_pct", "peak_gain_10d_pct", "daily_range_pct"]:
        print(f"      {col:26s} hit_media={hit[col].mean():8.3f}   miss_media={miss[col].mean():8.3f}")
print()

print("=" * 90)
print("D) RVOL alto + change_pct todavía moderado (volumen antes que precio) -- hipótesis H1")
print("=" * 90)
alc_valid = alc.dropna(subset=["relative_volume", "change_pct"])
rvol_hi = alc_valid[alc_valid["relative_volume"] >= alc_valid["relative_volume"].quantile(0.75)]
grupo_temprano = rvol_hi[rvol_hi["timing_deteccion"].isin(EARLY)]
grupo_tarde = rvol_hi[rvol_hi["timing_deteccion"].isin(LATE)]
print(f"  RVOL top cuartil (>= {alc_valid['relative_volume'].quantile(0.75):.2f}x): n={len(rvol_hi)}")
print(f"    de esos, EARLY: n={len(grupo_temprano)}  reached_20={( (grupo_temprano['max_advance_pct']>=20).mean()*100 if len(grupo_temprano) else float('nan')):5.1f}%")
print(f"    de esos, LATE:  n={len(grupo_tarde)}  reached_20={( (grupo_tarde['max_advance_pct']>=20).mean()*100 if len(grupo_tarde) else float('nan')):5.1f}%")
print()

print("=" * 90)
print("E) Gap_pct alto (movimiento por gap de apertura) vs bajo -- hipótesis H2")
print("=" * 90)
gap_hi = alc.dropna(subset=["gap_pct"])
mediana_gap = gap_hi["gap_pct"].median()
gap_alto = gap_hi[gap_hi["gap_pct"] >= gap_hi["gap_pct"].quantile(0.75)]
gap_bajo = gap_hi[gap_hi["gap_pct"] < gap_hi["gap_pct"].quantile(0.25)]
for label, sub in [("gap alto (top cuartil)", gap_alto), ("gap bajo (cuartil inferior)", gap_bajo)]:
    print(f"  {label}: n={len(sub)}  reached_20={(sub['max_advance_pct']>=20).mean()*100:5.1f}%  "
          f"reached_50={(sub['max_advance_pct']>=50).mean()*100:5.1f}%  "
          f"%EARLY={sub['timing_deteccion'].isin(EARLY).mean()*100:5.1f}%")
print()

print("=" * 90)
print("F) Validación cruzada dirección: direction (detección) vs outcome_direction (resultado)")
print("=" * 90)
print(pd.crosstab(df["direction"], df["outcome_direction"]))
print()
print("  -- fracción de casos ALCISTA en detección cuyo resultado posterior también fue ALCISTA:")
alc_all = df[df["direction"] == "ALCISTA"]
print(f"     n={len(alc_all)}  outcome_direction==ALCISTA: {(alc_all['outcome_direction']=='ALCISTA').mean()*100:.1f}%  "
      f"outcome_direction==BAJISTA: {(alc_all['outcome_direction']=='BAJISTA').mean()*100:.1f}%")
print()

print("=" * 90)
print("G) BAJISTA: confirmar que un +20/+50/+100 de un movimiento BAJISTA no se mezcla como oportunidad alcista")
print("=" * 90)
baj = df[df["direction"] == "BAJISTA"]
print(f"  n(BAJISTA en detección)={len(baj)}  de esos, max_advance_pct>=20% (subida posterior, no la caída): "
      f"{(baj['max_advance_pct']>=20).mean()*100:.1f}%  -- esto sería un REBOTE, no continuación de la caída")
print(f"  max_drawdown_pct promedio (profundidad de la caída posterior) en BAJISTA: {baj['max_drawdown_pct'].mean():.2f}%")
print(f"  max_drawdown_pct promedio en ALCISTA (para comparar): {alc_all['max_drawdown_pct'].mean():.2f}%")
