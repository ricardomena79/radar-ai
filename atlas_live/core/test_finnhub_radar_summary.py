"""Tests de `finnhub_radar_summary.build_finnhub_radar_summary()` (Hito 2,
2026-09-14) -- mismo patrón de aislamiento por bloque que
`test_learning_safety_summary.py`: cada fuente se mockea por separado,
un fallo en una nunca vacía a la otra."""

from atlas_live.core import finnhub_radar_summary as frs


class _FakeDiag:
    def to_dict(self):
        return {"tradier_chunks_ok": 3, "tradier_chunks_error": 1, "tradier_chunk_errors": ["boom"]}


def test_nunca_lanza_sin_ninguna_fuente_disponible(monkeypatch):
    # Sin mockear nada -- las DBs/módulos reales pueden no existir en el
    # entorno de test, el resumen debe seguir siendo un dict válido.
    resumen = frs.build_finnhub_radar_summary()
    assert isinstance(resumen, dict)
    assert "generated_at" in resumen
    assert "finnhub_budget" in resumen
    assert "ultimo_sweep_radar" in resumen


def test_budget_real_se_expone_tal_cual(monkeypatch):
    import atlas_live.data_fusion.finnhub_shared_budget as fsb

    monkeypatch.setattr(fsb, "get_metrics", lambda: {
        "por_consumidor": {"hot_quote": {"requests_ultimo_minuto": 12}},
        "pisos_protegidos_por_minuto": {"hot_quote": 40, "catalyst_worker": 10, "market_view": 5},
        "limite_total_seguro_por_minuto": 55,
        "fail_safe_events": 0,
    })
    resumen = frs.build_finnhub_radar_summary()
    assert resumen["finnhub_budget"]["ok"] is True
    assert resumen["finnhub_budget"]["limite_total_seguro_por_minuto"] == 55
    assert resumen["finnhub_budget"]["pisos_protegidos_por_minuto"]["hot_quote"] == 40


def test_budget_roto_no_afecta_al_bloque_de_sweep(monkeypatch):
    import atlas_live.data_fusion.finnhub_shared_budget as fsb
    import atlas_live.radar.candidate_registry as reg
    import atlas_live.radar.radar_worker as rw

    def _boom():
        raise RuntimeError("DB caída")

    monkeypatch.setattr(fsb, "get_metrics", _boom)
    monkeypatch.setattr(reg, "get_meta", lambda: {"ultimo_sweep_at": "2026-09-14T10:00:00Z", "sweeps_ok": 5})
    monkeypatch.setattr(rw, "get_last_diagnostics", lambda: None)

    resumen = frs.build_finnhub_radar_summary()
    assert resumen["finnhub_budget"]["ok"] is False
    assert resumen["ultimo_sweep_radar"]["ok"] is True
    assert resumen["ultimo_sweep_radar"]["sweeps_ok"] == 5


def test_diagnostics_del_ultimo_sweep_llega_intacto_nunca_inventado(monkeypatch):
    import atlas_live.radar.candidate_registry as reg
    import atlas_live.radar.radar_worker as rw

    monkeypatch.setattr(reg, "get_meta", lambda: {
        "ultimo_sweep_at": "2026-09-14T10:00:00Z", "ultimo_sweep_duracion_s": 12.5,
        "sweeps_total": 100, "sweeps_ok": 99, "sweeps_error": 1, "ultimo_tradier_error": None,
    })
    monkeypatch.setattr(rw, "get_last_diagnostics", lambda: _FakeDiag())

    resumen = frs.build_finnhub_radar_summary()
    bloque = resumen["ultimo_sweep_radar"]
    assert bloque["disponible"] is True
    assert bloque["diagnostics"]["tradier_chunks_ok"] == 3
    assert bloque["diagnostics"]["tradier_chunks_error"] == 1
    assert "boom" in bloque["diagnostics"]["tradier_chunk_errors"][0]


def test_sin_sweep_todavia_diagnostics_es_none_no_inventado(monkeypatch):
    import atlas_live.radar.candidate_registry as reg
    import atlas_live.radar.radar_worker as rw

    monkeypatch.setattr(reg, "get_meta", lambda: {})
    monkeypatch.setattr(rw, "get_last_diagnostics", lambda: None)

    resumen = frs.build_finnhub_radar_summary()
    assert resumen["ultimo_sweep_radar"]["disponible"] is False
    assert resumen["ultimo_sweep_radar"]["diagnostics"] is None


def test_nunca_expone_tokens_o_keys():
    resumen = frs.build_finnhub_radar_summary()
    import json

    texto = json.dumps(resumen, default=str)
    assert "TRADIER_API_TOKEN" not in texto
    assert "FINNHUB_API_KEY" not in texto
