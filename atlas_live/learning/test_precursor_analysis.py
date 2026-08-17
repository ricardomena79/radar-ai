"""Tests de precursor_analysis.py (2026-08-17, Fase 3b). Series sintéticas
controladas por símbolo (no aleatorias) para poder verificar a mano los
números exactos que debe producir el módulo: onset = primer día de la
racha, ventana T-1..T-5, derivadas de volumen/aceleración, continuación
anidada +50/+100, y el cruce con racional_available."""

from atlas_live.learning import precursor_analysis as pa


def _row(symbol, date, max_advance_pct, timing, volatility, volume, change_pct,
         max_drawdown_pct=None, racional_available=None, direction="ALCISTA"):
    return {
        "symbol": symbol, "date": date, "max_advance_pct": max_advance_pct,
        "max_drawdown_pct": max_drawdown_pct, "timing_deteccion": timing,
        "direction": direction, "volatility_14d_pct": volatility, "daily_range_pct": volatility,
        "relative_volume": 1.0, "gap_pct": 0.0, "volume": volume, "change_pct": change_pct,
        "racional_available": racional_available,
    }


def _make_symbol_series(symbol, onset_max_advance, onset_drawdown, racional_available):
    """12 días: 1-4 tranquilos (baseline), 5-9 = T-5..T-1 (precursor real
    con vol/volumen/change_pct creciendo), 10 = onset, 11-12 = después,
    tranquilos de nuevo (cierra la racha)."""
    rows = []
    for d in range(1, 5):
        rows.append(_row(symbol, f"2026-06-{d:02d}", 5, "antes_del_movimiento", 2, 1000, 0.3))
    # T-5..T-1 (días 5..9): vol 3,4,5,6,8 -- volumen 1100,1300,1600,2000,3000 -- change_pct 1.0,1.5,2.0,2.5,3.0
    precursor_plan = [
        (5, "antes_del_movimiento", 3, 1100, 1.0),
        (6, "expansion_temprana", 4, 1300, 1.5),
        (7, "expansion_temprana", 5, 1600, 2.0),
        (8, "al_comienzo", 6, 2000, 2.5),
        (9, "al_comienzo", 8, 3000, 3.0),
    ]
    for d, timing, vol, volume, chg in precursor_plan:
        rows.append(_row(symbol, f"2026-06-{d:02d}", 15, timing, vol, volume, chg))
    rows.append(_row(symbol, "2026-06-10", onset_max_advance, "al_comienzo", 10, 5000, 8.0,
                      max_drawdown_pct=onset_drawdown, racional_available=racional_available))
    rows.append(_row(symbol, "2026-06-11", 5, "demasiado_tarde", 4, 2000, -1.0))
    rows.append(_row(symbol, "2026-06-12", 3, "antes_del_movimiento", 2, 1500, 0.2))
    # racional_available viaja en TODAS las filas del símbolo (viene de
    # reference_checkpoint, es por símbolo, no por día) -- se completa acá.
    for r in rows:
        r["racional_available"] = racional_available
    return rows


def _make_dataset():
    sim1 = _make_symbol_series("SIM1", onset_max_advance=25, onset_drawdown=-15.0, racional_available=1)
    sim2 = _make_symbol_series("SIM2", onset_max_advance=120, onset_drawdown=-2.0, racional_available=0)
    return sim1 + sim2


def test_find_episode_onsets_detecta_solo_el_primer_dia_de_la_racha():
    rows = _make_dataset()
    by_symbol = pa.group_by_symbol_sorted(rows)
    onsets_20 = pa.find_episode_onsets(by_symbol, 20)
    assert onsets_20 == {"SIM1": ["2026-06-10"], "SIM2": ["2026-06-10"]}


def test_find_episode_onsets_100_solo_sim2():
    rows = _make_dataset()
    by_symbol = pa.group_by_symbol_sorted(rows)
    onsets_100 = pa.find_episode_onsets(by_symbol, 100)
    assert onsets_100 == {"SIM2": ["2026-06-10"]}


def test_precursor_rows_calculan_derivadas_correctas_en_t1():
    rows = _make_dataset()
    by_symbol = pa.group_by_symbol_sorted(rows)
    onsets = pa.find_episode_onsets(by_symbol, 20)
    precursor_rows = pa.precursor_rows_for_onsets(by_symbol, onsets, lookback_days=5)

    t1_sim1 = next(r for r in precursor_rows if r["symbol"] == "SIM1" and r["offset"] == 1)
    assert t1_sim1["date"] == "2026-06-09"
    assert t1_sim1["volume_change_pct"] == round(100 * (3000 - 2000) / 2000, 3)  # 50.0
    assert t1_sim1["change_pct_delta"] == round(3.0 - 2.5, 3)  # 0.5

    t5_sim1 = next(r for r in precursor_rows if r["symbol"] == "SIM1" and r["offset"] == 5)
    assert t5_sim1["date"] == "2026-06-05"
    assert t5_sim1["volume_change_pct"] == round(100 * (1100 - 1000) / 1000, 3)  # 10.0


def test_precursor_rows_se_corta_si_no_hay_suficiente_historial_previo():
    rows = [_row("CORTA", "2026-06-01", 5, "antes_del_movimiento", 2, 1000, 0.3),
            _row("CORTA", "2026-06-02", 25, "al_comienzo", 5, 2000, 3.0)]
    by_symbol = pa.group_by_symbol_sorted(rows)
    onsets = pa.find_episode_onsets(by_symbol, 20)
    precursor_rows = pa.precursor_rows_for_onsets(by_symbol, onsets, lookback_days=5)
    assert [r["offset"] for r in precursor_rows] == [1]  # solo hay 1 día previo, no 5


def test_precursor_summary_promedios_y_distribucion_timing_en_t1():
    rows = _make_dataset()
    by_symbol = pa.group_by_symbol_sorted(rows)
    onsets = pa.find_episode_onsets(by_symbol, 20)
    precursor_rows = pa.precursor_rows_for_onsets(by_symbol, onsets, lookback_days=5)
    summary = pa.precursor_summary(rows, precursor_rows, ["volatility_14d_pct"], lookback_days=5)

    t1 = summary["por_offset"]["T-1"]
    assert t1["n_episodios"] == 2
    assert t1["features"]["volatility_14d_pct"] == {"n": 2, "promedio": 8.0}  # SIM1 y SIM2 dan vol=8 en T-1
    assert t1["timing_deteccion"] == {"al_comienzo": 2}


def test_onset_outcome_breakdown_continuacion_anidada_y_drawdown():
    rows = _make_dataset()
    by_symbol = pa.group_by_symbol_sorted(rows)
    onsets_20 = pa.find_episode_onsets(by_symbol, 20)
    breakdown = pa.onset_outcome_breakdown(by_symbol, onsets_20)

    assert breakdown["n_onsets_20"] == 2
    assert breakdown["tambien_llega_50"] == {"n": 1, "pct": 50.0}   # solo SIM2 (120)
    assert breakdown["tambien_llega_100"] == {"n": 1, "pct": 50.0}  # solo SIM2 (120)
    assert breakdown["se_queda_solo_en_20_49"]["n"] == 1            # solo SIM1 (25)
    assert breakdown["se_queda_solo_en_20_49"]["drawdown_promedio"] == -15.0


def test_racional_comparison_separa_true_false():
    rows = _make_dataset()
    by_symbol = pa.group_by_symbol_sorted(rows)
    onsets_20 = pa.find_episode_onsets(by_symbol, 20)
    precursor_rows = pa.precursor_rows_for_onsets(by_symbol, onsets_20, lookback_days=5)
    t1_rows = [r for r in precursor_rows if r["offset"] == 1]
    onset_rows = []
    for sym, dates in onsets_20.items():
        by_date = {r["date"]: r for r in by_symbol[sym]}
        onset_rows.extend(by_date[d] for d in dates)

    comparison = pa.racional_comparison(t1_rows, onset_rows, ["volatility_14d_pct"])

    assert comparison["true"]["n_onsets"] == 1
    assert comparison["true"]["pct_llega_100"] == 0.0     # SIM1 (racional) no llega a 100
    assert comparison["false"]["n_onsets"] == 1
    assert comparison["false"]["pct_llega_100"] == 100.0  # SIM2 (no racional) sí llega
    assert comparison["desconocido"]["n_onsets"] == 0


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
