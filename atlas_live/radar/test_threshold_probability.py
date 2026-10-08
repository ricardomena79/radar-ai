from atlas_live.radar import threshold_probability as tp


def _rows(direction, stage, values):
    return [{"direction": direction, "timing_deteccion": stage, "max_advance_pct": v} for v in values]


def test_mide_fraccion_que_llego_a_cada_umbral():
    # 10 casos: 8 >= 2, 5 >= 5, 2 >= 10
    vals = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 10.0, 12.0]
    t = tp.compute_threshold_table(_rows("ALCISTA", "INICIO", vals))
    g = t[("ALCISTA", "INICIO")]
    assert g["n"] == 10
    assert g["umbrales"]["2"]["aciertos"] == 8 and g["umbrales"]["2"]["pct"] == 80.0
    assert g["umbrales"]["5"]["aciertos"] == 5 and g["umbrales"]["5"]["pct"] == 50.0
    assert g["umbrales"]["10"]["aciertos"] == 2 and g["umbrales"]["10"]["pct"] == 20.0


def test_el_umbral_es_inclusivo():
    t = tp.compute_threshold_table(_rows("ALCISTA", "INICIO", [2.0, 5.0, 10.0]))
    u = t[("ALCISTA", "INICIO")]["umbrales"]
    assert (u["2"]["aciertos"], u["5"]["aciertos"], u["10"]["aciertos"]) == (3, 2, 1)


def test_grupos_separados_por_direccion_y_etapa():
    rows = _rows("ALCISTA", "INICIO", [3.0]) + _rows("BAJISTA", "INICIO", [0.5]) + _rows("ALCISTA", "PREPARACION", [9.0])
    t = tp.compute_threshold_table(rows)
    assert set(t) == {("ALCISTA", "INICIO"), ("BAJISTA", "INICIO"), ("ALCISTA", "PREPARACION")}


def test_descarta_filas_incompletas_sin_imputar():
    rows = [
        {"direction": "ALCISTA", "timing_deteccion": "INICIO", "max_advance_pct": None},
        {"direction": None, "timing_deteccion": "INICIO", "max_advance_pct": 5.0},
        {"direction": "ALCISTA", "timing_deteccion": None, "max_advance_pct": 5.0},
    ]
    assert tp.compute_threshold_table(rows) == {}


def test_estado_de_validacion_y_wilson_segun_muestra():
    chica = tp.compute_threshold_table(_rows("ALCISTA", "INICIO", [3.0] * 10))[("ALCISTA", "INICIO")]
    assert chica["validation_state"] == "MUESTRA_INSUFICIENTE"
    grande = tp.compute_threshold_table(_rows("ALCISTA", "INICIO", [3.0] * 300 + [0.0] * 300))[("ALCISTA", "INICIO")]
    assert grande["validation_state"] == "VALIDACION_ROBUSTA"
    u = grande["umbrales"]["2"]
    assert u["pct"] == 50.0 and u["ci_inferior"] < 50.0 < u["ci_superior"]
    assert (u["ci_superior"] - u["ci_inferior"]) < (
        chica["umbrales"]["2"]["ci_superior"] - chica["umbrales"]["2"]["ci_inferior"]
    )


def test_lookup_sin_grupo_o_sin_datos_devuelve_none():
    t = tp.compute_threshold_table(_rows("ALCISTA", "INICIO", [3.0]))
    assert tp.lookup(t, "ALCISTA", "INICIO") is not None
    assert tp.lookup(t, "BAJISTA", "INICIO") is None
    assert tp.lookup(t, None, "INICIO") is None
    assert tp.lookup(t, "ALCISTA", None) is None


def test_cache_respeta_ttl_y_cambio_de_fecha(monkeypatch):
    tp._reset_cache_for_tests()
    llamadas = []

    def fake_loader(as_of):
        llamadas.append(as_of)
        return _rows("ALCISTA", "INICIO", [3.0])

    monkeypatch.setattr(tp.les, "_load_rows_from_db_by_stage", fake_loader)
    tp.get_cached_threshold_table("2026-10-08")
    tp.get_cached_threshold_table("2026-10-08")
    assert llamadas == ["2026-10-08"]
    tp.get_cached_threshold_table("2026-10-09")
    assert llamadas == ["2026-10-08", "2026-10-09"]
    tp._reset_cache_for_tests()


def test_no_toca_decisiones_ni_gates():
    import inspect
    src = inspect.getsource(tp)
    for prohibido in ("apply_recalibration", "evaluate_all_gates", "set_mechanism_state", "activation_registry"):
        assert prohibido not in src
