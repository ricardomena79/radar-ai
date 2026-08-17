"""Tests de precursor_analysis.py (2026-08-17, Fase 3b). Series sintéticas
controladas por símbolo (no aleatorias) para poder verificar a mano los
números exactos que debe producir el módulo: onset = primer día de la
racha, ventana T-1..T-5, derivadas de volumen/aceleración, continuación
anidada +50/+100, y el cruce con racional_available."""

from atlas_live.learning import precursor_analysis as pa


def _row(symbol, date, max_advance_pct, timing, volatility, volume, change_pct,
         max_drawdown_pct=None, racional_available=None, direction="ALCISTA", relative_volume=1.0):
    return {
        "symbol": symbol, "date": date, "max_advance_pct": max_advance_pct,
        "max_drawdown_pct": max_drawdown_pct, "timing_deteccion": timing,
        "direction": direction, "volatility_14d_pct": volatility, "daily_range_pct": volatility,
        "relative_volume": relative_volume, "gap_pct": 0.0, "volume": volume, "change_pct": change_pct,
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


def test_categorize_onsets_particion_mutuamente_excluyente():
    rows = _make_dataset()  # SIM1 llega a 25 (A), SIM2 llega a 120 (C)
    by_symbol = pa.group_by_symbol_sorted(rows)
    onsets_20 = pa.find_episode_onsets(by_symbol, 20)
    categories = pa.categorize_onsets(by_symbol, onsets_20)

    assert [r["symbol"] for r in categories["A_20_49"]] == ["SIM1"]
    assert categories["B_50_99"] == []
    assert [r["symbol"] for r in categories["C_100_mas"]] == ["SIM2"]


def test_distribution_stats_mediana_y_percentiles_no_solo_promedio():
    # 1..10 -- promedio=5.5, mediana por índice round(0.5*9)=4 -> valor 5.0
    stats = pa.distribution_stats([float(i) for i in range(1, 11)])
    assert stats["n"] == 10
    assert stats["promedio"] == 5.5
    assert stats["mediana"] == 5.0
    assert stats["min"] == 1.0 and stats["max"] == 10.0
    assert stats["p90"] >= stats["p75"] >= stats["mediana"] >= stats["p25"] >= stats["p10"]


def test_distribution_stats_vacio_no_inventa_nada():
    assert pa.distribution_stats([]) == {
        "n": 0, "promedio": None, "mediana": None, "p10": None, "p25": None,
        "p75": None, "p90": None, "min": None, "max": None,
    }


def _make_volume_dataset():
    """3 símbolos, cada uno onset de +20% en día 10, con relative_volume
    controlado en T-5..T-1 para probar persistencia/aceleración:
    VOLA (categoría A, 25%): volumen bajo y constante (nunca elevado).
    VOLB (categoría B, 60%): volumen sube gradual, elevado los últimos 3 días.
    VOLC (categoría C, 150%): volumen elevado los 5 días, con fuerte aceleración."""
    def _series(symbol, onset_advance, rv_by_offset):
        rows = []
        for d in range(1, 5):
            rows.append(_row(symbol, f"2026-07-{d:02d}", 5, "antes_del_movimiento", 2, 1000, 0.3, relative_volume=1.0))
        for offset, d in zip([5, 4, 3, 2, 1], range(5, 10)):
            rows.append(_row(symbol, f"2026-07-{d:02d}", 15, "expansion_temprana", 4, 1500, 1.0,
                              relative_volume=rv_by_offset[offset]))
        rows.append(_row(symbol, "2026-07-10", onset_advance, "al_comienzo", 8, 4000, 5.0, racional_available=1))
        rows.append(_row(symbol, "2026-07-11", 5, "demasiado_tarde", 3, 1500, -1.0))
        return rows

    vola = _series("VOLA", 25, {5: 1.0, 4: 1.1, 3: 1.0, 2: 1.2, 1: 1.1})       # nunca >= 2.0
    volb = _series("VOLB", 60, {5: 1.0, 4: 1.5, 3: 2.5, 2: 3.0, 1: 3.5})       # elevado T-3..T-1 (3 días)
    volc = _series("VOLC", 150, {5: 3.0, 4: 4.0, 3: 5.0, 2: 6.0, 1: 9.0})      # elevado los 5 días, fuerte aceleración
    return vola + volb + volc


def test_volume_persistence_cuenta_dias_elevados_por_categoria():
    rows = _make_volume_dataset()
    by_symbol = pa.group_by_symbol_sorted(rows)
    onsets_20 = pa.find_episode_onsets(by_symbol, 20)
    categories = pa.categorize_onsets(by_symbol, onsets_20)
    precursor_rows = pa.precursor_rows_for_onsets(by_symbol, onsets_20, lookback_days=5)

    persistence = pa.volume_persistence(precursor_rows, categories, threshold=2.0)
    assert persistence["A_20_49"]["distribucion_dias_con_volumen_elevado"] == {"0": 1}   # VOLA: nunca elevado
    assert persistence["B_50_99"]["distribucion_dias_con_volumen_elevado"] == {"3": 1}   # VOLB: 3 días (T-3,T-2,T-1)
    assert persistence["C_100_mas"]["distribucion_dias_con_volumen_elevado"] == {"5": 1}  # VOLC: los 5 días


def test_volume_acceleration_t1_menos_t5_por_categoria():
    rows = _make_volume_dataset()
    by_symbol = pa.group_by_symbol_sorted(rows)
    onsets_20 = pa.find_episode_onsets(by_symbol, 20)
    categories = pa.categorize_onsets(by_symbol, onsets_20)
    precursor_rows = pa.precursor_rows_for_onsets(by_symbol, onsets_20, lookback_days=5)

    accel = pa.volume_acceleration(precursor_rows, categories)
    assert accel["A_20_49"]["promedio"] == round(1.1 - 1.0, 3)   # VOLA: casi sin aceleración
    assert accel["B_50_99"]["promedio"] == round(3.5 - 1.0, 3)   # VOLB
    assert accel["C_100_mas"]["promedio"] == round(9.0 - 3.0, 3)  # VOLC: la mayor aceleración


def test_category_racional_split_separa_por_categoria_y_racional():
    rows = _make_dataset()  # SIM1 (racional=1) -> A, SIM2 (racional=0) -> C
    by_symbol = pa.group_by_symbol_sorted(rows)
    onsets_20 = pa.find_episode_onsets(by_symbol, 20)
    categories = pa.categorize_onsets(by_symbol, onsets_20)
    precursor_rows = pa.precursor_rows_for_onsets(by_symbol, onsets_20, lookback_days=5)

    split = pa.category_racional_split(precursor_rows, categories, ["volatility_14d_pct"])
    assert split["A_20_49"]["true"]["n"] == 1    # SIM1
    assert split["A_20_49"]["false"]["n"] == 0
    assert split["C_100_mas"]["false"]["n"] == 1  # SIM2
    assert split["C_100_mas"]["true"]["n"] == 0


def test_generate_separation_report_estructura_no_falla_sin_db(monkeypatch):
    rows = _make_volume_dataset()
    monkeypatch.setattr(pa, "_load_rows_from_db", lambda: rows)
    report = pa.generate_separation_report(feature_cols=["relative_volume"], lookback_days=5)
    assert report["n_onsets_por_categoria"] == {"A_20_49": 1, "B_50_99": 1, "C_100_mas": 1}
    assert set(report["por_categoria"].keys()) == set(pa.CATEGORY_LABELS)
    assert "persistencia_volumen" in report and "aceleracion_volumen_t1_menos_t5" in report
    assert "comparacion_racional_por_categoria" in report


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
