"""Tests del filtro Racional en GET /api/radar-universo (2026-08-18,
pedido explícito del usuario -- caso real XOS/PFSA/SEZL sin Racional
apareciendo en "Candidatas Detectadas Hoy").

Universo de aprendizaje vs. universo operable: Atlas sigue detectando y
guardando TODO para aprendizaje (`candidate_registry.list_candidates_for_date`
nunca se toca, sigue devolviendo todo sin filtrar -- se mockea acá solo
para el test). Lo que cambia es la LISTA que expone este endpoint (la que
llena la tabla de la Cabina): debe excluir server-side lo que no está
disponible en Racional, mismo criterio ya usado en
`/api/radar-oportunidades`."""

import atlas_live.backtest.seed_import as _si
import atlas_live.radar.radar_worker as _rw
import atlas_live.scan_worker as _sw

_orig_seed = _si.import_all_seeds
_orig_refresh = _sw.start_background_refresh
_orig_radar = _rw.start_universe_radar
_si.import_all_seeds = lambda *a, **k: None
_sw.start_background_refresh = lambda *a, **k: None
_rw.start_universe_radar = lambda *a, **k: None
try:
    from atlas_live import server  # noqa: E402
finally:
    _si.import_all_seeds = _orig_seed
    _sw.start_background_refresh = _orig_refresh
    _rw.start_universe_radar = _orig_radar

from atlas_live.radar import candidate_registry as reg  # noqa: E402


def _client():
    return server.app.test_client()


def _candidatas():
    return [
        {"ticker": "XOS", "detected_at": "t1", "price_at_detection": 2.09,
         "change_pct_at_detection": 0.0, "relative_volume_at_detection": 1.7,
         "gates_fired": [], "session": "premarket", "source": "tradier"},
        {"ticker": "AAPL", "detected_at": "t2", "price_at_detection": 220.0,
         "change_pct_at_detection": 1.0, "relative_volume_at_detection": 2.0,
         "gates_fired": [], "session": "regular", "source": "tradier"},
    ]


def test_candidatas_hoy_excluye_lo_que_no_esta_en_racional(monkeypatch):
    """Caso real: XOS no está en Racional, AAPL sí -- solo AAPL debe
    aparecer en la lista que consume la Cabina."""
    monkeypatch.setattr(reg, "list_candidates_for_date", lambda market_date: _candidatas())
    monkeypatch.setattr("atlas.data.universe.is_available", lambda symbol: symbol == "AAPL")

    r = _client().get("/api/radar-universo")
    body = r.get_json()
    tickers = [c["ticker"] for c in body["candidatas_hoy"]]

    assert "XOS" not in tickers
    assert "AAPL" in tickers
    assert body["total_detectadas_hoy"] == 2
    assert body["total_disponibles_racional"] == 1


def test_candidatas_hoy_conteo_total_no_cambia_aunque_se_filtre_la_lista(monkeypatch):
    """`status.candidatas_hoy` (el contador del universo de aprendizaje)
    nunca se toca -- Atlas sigue reportando TODO lo que detectó, aunque la
    lista mostrada en la tabla esté filtrada."""
    monkeypatch.setattr(reg, "list_candidates_for_date", lambda market_date: _candidatas())
    monkeypatch.setattr(reg, "radar_status", lambda: {"candidatas_hoy": 2, "state": "RUNNING"})
    monkeypatch.setattr("atlas.data.universe.is_available", lambda symbol: symbol == "AAPL")

    r = _client().get("/api/radar-universo")
    body = r.get_json()
    assert body["status"]["candidatas_hoy"] == 2  # el contador de aprendizaje, sin filtrar
    assert len(body["candidatas_hoy"]) == 1  # la lista mostrada, sí filtrada


def test_fallo_de_is_available_excluye_en_vez_de_romper_o_mostrar_de_mas(monkeypatch):
    """Si `is_available()` lanza para un ticker puntual, el endpoint no
    debe romper (500) -- y, por seguridad ("nunca mostrar algo que no se
    puede verificar como operable"), ese ticker queda EXCLUIDO, no incluido
    por defecto."""
    monkeypatch.setattr(reg, "list_candidates_for_date", lambda market_date: _candidatas())

    def _boom(symbol):
        raise RuntimeError("simulado")
    monkeypatch.setattr("atlas.data.universe.is_available", _boom)

    r = _client().get("/api/radar-universo")
    assert r.status_code == 200
    body = r.get_json()
    assert body["candidatas_hoy"] == []
    assert body["total_detectadas_hoy"] == 2
    assert body["total_disponibles_racional"] == 0


def test_todas_disponibles_en_racional_no_excluye_nada(monkeypatch):
    monkeypatch.setattr(reg, "list_candidates_for_date", lambda market_date: _candidatas())
    monkeypatch.setattr("atlas.data.universe.is_available", lambda symbol: True)

    r = _client().get("/api/radar-universo")
    body = r.get_json()
    assert len(body["candidatas_hoy"]) == 2
    assert body["total_detectadas_hoy"] == body["total_disponibles_racional"] == 2
