"""Tests del Detector Unificado en modo Shadow (2026-08-26, U3-C2). Con
fakes (sin red real, sin DB compartida con producción) -- mismo patrón ya
probado en `test_radar_worker.py`."""

import tempfile
import uuid as _uuid
from pathlib import Path
from types import SimpleNamespace

from atlas.data.models.quote import Quote
from atlas_live.memory import market_hours
from atlas_live.radar import candidate_gates as gates
from atlas_live.radar import candidate_registry as reg
from atlas_live.radar import radar_worker
from atlas_live.radar import shadow_detector_registry as sreg
from atlas_live.radar import unified_detector as ud
from atlas_live.radar.sweep_history import SweepHistory, SweepSnapshot

_ORIG_SHADOW_DB = sreg.DB_PATH
_ORIG_REG_DB = reg.DB_PATH


def _fresh():
    sreg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_shadow_{_uuid.uuid4().hex}.db"
    reg.DB_PATH = Path(tempfile.gettempdir()) / f"atlas_test_reg_{_uuid.uuid4().hex}.db"
    reg._schema_ready_for = None
    ud._history.reset_for_new_day("__reset__")
    ud._last_afterhours_sweep_at = None


def _restore():
    sreg.DB_PATH = _ORIG_SHADOW_DB
    reg.DB_PATH = _ORIG_REG_DB


def _fake_quote(symbol, change_pct=6.0, price=10.0, volume=500_000, avg_volume=100_000,
                 source="tradier", price_basis="tradier_last", price_is_stale=False):
    return Quote(symbol=symbol, name=symbol, last_price=price, change_percent=change_pct,
                 volume=volume, open=price, high=price, low=price, previous_close=price * 0.94,
                 average_volume=avg_volume, relative_volume=volume / avg_volume if avg_volume else None,
                 source=source, price_basis=price_basis, price_is_stale=price_is_stale)


# --- B: piggyback en premarket/regular -- CERO llamadas nuevas -------------

def test_B_piggyback_premarket_nunca_llama_al_proveedor():
    _fresh()
    orig_session = market_hours.get_session
    orig_last_quotes = radar_worker.get_last_quotes
    orig_fetch = ud.fetch_universe_quotes
    orig_build = ud.build_tradier_provider
    try:
        market_hours.get_session = lambda now=None: "premarket"
        radar_worker.get_last_quotes = lambda: {"AAPL": _fake_quote("AAPL")}

        def _boom(*a, **kw):
            raise AssertionError("piggyback no debe llamar a fetch_universe_quotes")

        ud.fetch_universe_quotes = _boom
        ud.build_tradier_provider = _boom

        resultado = ud.run_shadow_sweep_once()
        assert resultado is not None
        assert resultado["universe_source"] == "piggyback_radar"
        assert resultado["universe_size"] == 1
    finally:
        market_hours.get_session = orig_session
        radar_worker.get_last_quotes = orig_last_quotes
        ud.fetch_universe_quotes = orig_fetch
        ud.build_tradier_provider = orig_build
        _restore()


# --- C: afterhours respeta la cadencia minima -------------------------------

def test_C_afterhours_respeta_cadencia_minima():
    _fresh()
    orig_session = market_hours.get_session
    orig_fetch = ud.fetch_universe_quotes
    orig_build = ud.build_tradier_provider
    orig_fallback = ud.get_default_provider
    llamadas = []
    try:
        market_hours.get_session = lambda now=None: "afterhours"
        ud.build_tradier_provider = lambda: SimpleNamespace()
        ud.get_default_provider = lambda: SimpleNamespace()

        def _fake_fetch(symbols, tradier_provider=None, fallback_provider=None):
            llamadas.append(len(symbols))
            return SimpleNamespace(quotes={})

        ud.fetch_universe_quotes = _fake_fetch

        r1 = ud.run_shadow_sweep_once()
        r2 = ud.run_shadow_sweep_once()  # inmediatamente después -- debe saltearse
        assert r1 is not None
        assert r2 is None
        assert len(llamadas) == 1
    finally:
        market_hours.get_session = orig_session
        ud.fetch_universe_quotes = orig_fetch
        ud.build_tradier_provider = orig_build
        ud.get_default_provider = orig_fallback
        _restore()


# --- D: stale queda marcado, nunca tratado como fresco ----------------------

def test_D_stale_queda_marcado():
    _fresh()
    orig_session = market_hours.get_session
    orig_last_quotes = radar_worker.get_last_quotes
    try:
        market_hours.get_session = lambda now=None: "regular"
        radar_worker.get_last_quotes = lambda: {
            "STAL": _fake_quote("STAL", change_pct=5.0, price_basis="tradier_regular_close_stale", price_is_stale=True)
        }
        ud.run_shadow_sweep_once()
        detecciones = sreg.list_shadow_detections(market_hours.market_date())
        assert len(detecciones) == 1
        assert detecciones[0]["price_is_stale"] == 1
        assert detecciones[0]["price_basis"] == "tradier_regular_close_stale"
    finally:
        market_hours.get_session = orig_session
        radar_worker.get_last_quotes = orig_last_quotes
        _restore()


# --- E: sesion closed -- no corre nada --------------------------------------

def test_E_sesion_closed_no_corre_nada():
    _fresh()
    orig_session = market_hours.get_session
    try:
        market_hours.get_session = lambda now=None: "closed"
        resultado = ud.run_shadow_sweep_once()
        assert resultado is None
        assert sreg.count_shadow_detections(market_hours.market_date()) == 0
    finally:
        market_hours.get_session = orig_session
        _restore()


# --- F: SweepHistory propia, aislada de radar_worker ------------------------

def test_F_sweep_history_propia_aislada():
    assert ud._history is not radar_worker._history
    assert isinstance(ud._history, SweepHistory)


# --- G/J: aislamiento estructural -- sin referencias a candidate_registry --

def test_G_shadow_registry_nunca_referencia_candidate_registry():
    """AST-based -- ignora docstrings/comentarios (que SÍ nombran
    `candidate_registry.py` a propósito, para documentar la decisión de
    aislamiento) y chequea solo imports/llamadas reales."""
    import ast
    import inspect

    for modulo in (sreg, ud):
        tree = ast.parse(inspect.getsource(modulo))
        modulos_importados = set()
        llamadas = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modulos_importados.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modulos_importados.add(node.module)
                modulos_importados.update(a.name for a in node.names)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                llamadas.add(node.func.attr)
        assert not any("candidate_registry" in m for m in modulos_importados)
        assert "record_detection" not in llamadas


def test_J_evaluate_all_gates_es_la_misma_funcion_no_una_copia():
    import atlas_live.radar.candidate_gates as candidate_gates_module

    assert ud.gates.evaluate_all_gates is candidate_gates_module.evaluate_all_gates
    # El módulo shadow no define ninguna función `gate_*` propia -- toda la
    # lógica de puertas sigue viviendo exclusivamente en candidate_gates.py.
    import inspect

    funciones_propias = [name for name, obj in vars(ud).items() if inspect.isfunction(obj) and obj.__module__ == ud.__name__]
    assert not any(name.startswith("gate_") for name in funciones_propias)


# --- H: el detector shadow nunca escribe candidate_detection real ----------

def test_H_shadow_nunca_escribe_candidate_detection_real():
    _fresh()
    orig_session = market_hours.get_session
    orig_last_quotes = radar_worker.get_last_quotes
    try:
        market_hours.get_session = lambda now=None: "regular"
        # Quote garantizada para disparar gate_price_change (>=3%).
        radar_worker.get_last_quotes = lambda: {"BOOM": _fake_quote("BOOM", change_pct=9.0)}
        resultado = ud.run_shadow_sweep_once()
        assert resultado["detecciones"] == 1
        # La detección quedó en la base SHADOW...
        assert sreg.count_shadow_detections(market_hours.market_date()) == 1
        # ...pero la base REAL de candidatas (candidate_detection) sigue en cero.
        assert reg.count_candidates_for_date(market_hours.market_date()) == 0
    finally:
        market_hours.get_session = orig_session
        radar_worker.get_last_quotes = orig_last_quotes
        _restore()


# --- I: ALL_GATES no cambia ---------------------------------------------------

def test_I_all_gates_no_cambia():
    assert len(gates.ALL_GATES) == 7


# --- K: misma evaluacion de gates para el mismo SweepSnapshot ---------------

def test_K_misma_evaluacion_de_gates_via_directo_y_via_detector():
    current = SweepSnapshot(sweep_id="s1", observed_at="2026-08-26T14:31:00Z", price=10.5,
                             change_pct=6.0, volume=500_000, average_volume=100_000,
                             relative_volume=5.0, dollar_volume=5_250_000.0, session="regular")
    history = [
        SweepSnapshot(sweep_id="s0", observed_at="2026-08-26T14:30:00Z", price=10.0,
                       change_pct=1.0, volume=400_000, average_volume=100_000,
                       relative_volume=4.0, dollar_volume=4_000_000.0, session="regular"),
    ]
    directo = gates.evaluate_all_gates(current, history, "regular")
    via_detector = ud.gates.evaluate_all_gates(current, history, "regular")
    assert directo == via_detector


# --- L: Racional no participa en la deteccion -------------------------------

def test_L_racional_no_participa_en_la_deteccion():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(ud.run_shadow_sweep_once))
    llamadas = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    llamadas |= {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "is_available" not in llamadas
    assert "racional_available" not in inspect.getsource(ud.run_shadow_sweep_once)

    # Racional solo se usa para elegir el UNIVERSO del barrido nuevo de
    # afterhours (_dedup_universe) -- nunca como filtro de si una gate
    # dispara o no.
    src_snapshot = inspect.getsource(ud._quote_to_snapshot)
    assert "racional" not in src_snapshot.lower()


# --- A: deduplicacion --------------------------------------------------------

def test_A_dedup_universe_no_duplica_simbolos():
    from atlas.data.universe.universe import Asset

    orig_equities = ud.racional_universe.get_equities
    orig_etfs = ud.racional_universe.get_etfs
    try:
        ud.racional_universe.get_equities = lambda: [Asset(symbol="AAPL", name="Apple", type="EQUITY"),
                                                       Asset(symbol="DUPE", name="Dupe", type="EQUITY")]
        ud.racional_universe.get_etfs = lambda: [Asset(symbol="DUPE", name="Dupe ETF", type="ETF"),
                                                  Asset(symbol="QQQ", name="Invesco QQQ", type="ETF")]
        universo = ud._dedup_universe()
        assert universo == sorted(["AAPL", "DUPE", "QQQ"])
        assert len(universo) == len(set(universo))
    finally:
        ud.racional_universe.get_equities = orig_equities
        ud.racional_universe.get_etfs = orig_etfs


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
