"""Calibración read-only de `pm_early_signal` (Hito 4, PLAN Radar/Finnhub,
2026-09-14) contra datos reales de producción. Corrido vía `railway ssh`,
solo lectura (`PRAGMA query_only=ON`), nunca escribe nada.

Uso: `railway ssh -- python3 scripts/diagnostico_pm_volumen_calibracion.py`

Resultado real de esta sesión (2026-09-14, dejado registrado acá para no
tener que repetir la consulta):

    n_evaluable_con_pm_valid: 5555 (candidate_detection con
    premarket_volume_percentile_state_at_detection='VALID', JOIN
    candidate_outcome con is_final=1 AND confiable_para_aprendizaje=1)

    Buckets de PM-percentil, TODO el universo (mezcla temprano/tardío):
      50-80:    n=227  reached20=0.4%  mediana_max_return=1.22%
      80-95:    n=3036 reached20=1.5%  mediana_max_return=1.57%
      95-99:    n=1832 reached20=1.8%  mediana_max_return=1.63%
      99-100:   n=460  reached20=0.4%  mediana_max_return=2.15%

    Comparación temprano (|change_pct_at_detection|<8%) vs. tardío (>=8%):
      EARLY:  n=4945 reached20=0.97%  mediana_max_return=1.67%
      LATE:   n=168  reached20=19.64% mediana_max_return=7.98%

    Dentro de EARLY, PM-percentil alto vs. bajo:
      EARLY + PM>=90: n=3889 reached20=0.98% mediana_max_return=1.73%
      EARLY + PM<90:  n=1056 reached20=0.95% mediana_max_return=1.50%

CONCLUSIÓN (honesta, no ajustada a lo esperado): dentro del subconjunto
temprano, PM-percentil>=90 NO mostró una tasa de +20% distinta de
PM-percentil<90 -- 0.98% vs 0.95%, prácticamente idéntico. La señal, en
este dataset, NO discrimina de forma incremental. Consistente con la baja
precisión general ya documentada en otras auditorías de esta sesión.

Por eso `ATLAS_PREMARKET_VOLUME_SIGNAL_ENABLED` nace en `False` -- el
mecanismo queda construido, calibrado (umbral EARLY_SIGNAL_MAX_CHANGE_PCT=8.0,
EARLY_SIGNAL_PM_PERCENTILE_MIN=90.0, derivados de esta misma consulta) y
disponible para recalibración futura, pero sin evidencia suficiente para
activarlo silenciosamente."""

import os
import sqlite3
import statistics


def _db_path() -> str:
    data_dir = os.environ.get("ATLAS_DATA_DIR", "/data")
    return f"{data_dir}/radar_candidates.db"


def run() -> None:
    conn = sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")

    query = """
        SELECT d.ticker, d.market_date, d.premarket_volume_percentile_at_detection AS pm_pct,
               d.change_pct_at_detection, d.relative_volume_at_detection, d.phase_tag,
               d.dollar_volume_at_detection,
               o.max_return_after_detection_pct, o.reached_20, o.reached_50
        FROM candidate_detection d
        JOIN candidate_outcome o ON o.ticker = d.ticker AND o.market_date = d.market_date
        WHERE d.premarket_volume_percentile_state_at_detection = 'VALID'
          AND o.is_final = 1 AND o.confiable_para_aprendizaje = 1
    """
    rows = conn.execute(query).fetchall()
    print("n_evaluable_con_pm_valid", len(rows))

    def _stats(sub, label):
        n = len(sub)
        if n == 0:
            print(f"{label}: n=0")
            return
        reached20 = sum(1 for r in sub if r[8]) / n * 100
        med = statistics.median((r[7] or 0) for r in sub)
        print(f"{label}: n={n} reached20={reached20:.2f}% mediana_max_return={med:.2f}%")

    buckets = [(0, 50), (50, 80), (80, 95), (95, 99), (99, 100.001)]
    for lo, hi in buckets:
        sub = [r for r in rows if r[2] is not None and lo <= r[2] < hi]
        _stats(sub, f"PM-pct {lo}-{hi}")

    early = [r for r in rows if r[3] is not None and abs(r[3]) < 8.0]
    late = [r for r in rows if r[3] is not None and abs(r[3]) >= 8.0]
    _stats(early, "EARLY(|chg|<8%)")
    _stats(late, "LATE(|chg|>=8%)")

    early_high = [r for r in early if r[2] is not None and r[2] >= 90]
    early_low = [r for r in early if r[2] is not None and r[2] < 90]
    _stats(early_high, "EARLY+PM>=90")
    _stats(early_low, "EARLY+PM<90")


if __name__ == "__main__":
    run()
