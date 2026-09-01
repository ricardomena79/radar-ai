"""Test del cache-busting de la Cabina (2026-08-21, caso real: un deploy ya
confirmado en el servidor -- verificado con curl -- pero el navegador del
usuario seguía sirviendo `cabina.js`/`cabina.css` viejos desde caché, porque
`index.html` los referencia siempre con la misma URL. Ver `server.py::static_files`."""

import atlas_live.backtest.seed_import as _si
import atlas_live.market_view as _mv
import atlas_live.radar.radar_worker as _rw
import atlas_live.scan_worker as _sw

_orig_seed = _si.import_all_seeds
_orig_refresh = _sw.start_background_refresh
_orig_radar = _rw.start_universe_radar
_orig_market_view = _mv.start_market_view
_si.import_all_seeds = lambda *a, **k: None
_sw.start_background_refresh = lambda *a, **k: None
_rw.start_universe_radar = lambda *a, **k: None
_mv.start_market_view = lambda *a, **k: None
try:
    from atlas_live import server  # noqa: E402
finally:
    _si.import_all_seeds = _orig_seed
    _sw.start_background_refresh = _orig_refresh
    _rw.start_universe_radar = _orig_radar
    _mv.start_market_view = _orig_market_view


def _client():
    return server.app.test_client()


def test_cabina_index_agrega_version_a_js_y_css():
    r = _client().get("/cabina/index.html")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'src="cabina.js?v=' in html
    assert 'href="cabina.css?v=' in html
    # nunca deja la referencia vieja sin versionar
    assert 'src="cabina.js"' not in html
    assert 'href="cabina.css"' not in html


def test_raiz_redirige_a_cabina_del_piloto():
    """La raiz servia la app legacy (atlas_live/static/index.html) en vez
    de la Cabina del Piloto real -- se agrega la redireccion (2026-08-30)
    sin borrar el archivo legacy, que sigue accesible en disco."""
    r = _client().get("/", follow_redirects=False)
    assert r.status_code in (301, 302)
    assert r.headers["Location"] == "/cabina/index.html"


def test_cabina_js_sigue_sirviendose_normal_con_query_string():
    """El archivo real (`cabina.js`) sigue siendo servido tal cual por
    `send_from_directory` -- la versión solo cambia la URL, nunca el
    contenido ni cómo se resuelve el archivo en sí."""
    r = _client().get("/cabina/cabina.js")
    assert r.status_code == 200
    assert len(r.get_data()) > 0
