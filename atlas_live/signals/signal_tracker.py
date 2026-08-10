"""Seguimiento de señales -- conecta el scanner existente con el registro.

`track_cycle(results, now)` se llama al final de cada ciclo del scanner
(desde `live_integration.run_live_cycle`, un solo punto de integración): NO
escanea nada por su cuenta, solo LEE `results` -- la misma lista que el
scanner ya produjo -- y, para cada candidato que Radar Explosivo marcó
elegible (`explosive.eligible`), registra la señal (una vez por
ticker/día) o agrega un punto de seguimiento. La detección original nunca se
reescribe (ver signal_registry.py).

`resolve_due(now)` cierra las señales de días de mercado ya terminados:
calcula hitos, máximo, fin de impulso y resultado a partir de las
observaciones REALES seguidas -- nunca de un valor futuro inyectado en la
señal. Solo tras resolverse una señal podría alimentar el aprendizaje
(learning_writeback), y eso es un paso aparte y explícito, no automático.

Cero mock: si un ciclo no trae candidatos elegibles, no se registra nada.
Si una feature falta, se guarda None -- jamás un valor inventado.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from atlas_live.memory import explosion_history as eh
from atlas_live.memory import market_hours
from atlas_live.signals import signal_registry as reg
from atlas_live.signals import versions

# Umbral de "acierto" de una señal: alcanzó una explosión real (+30%), la
# misma definición que usa todo el proyecto (Clasificador/Marcador).
ACIERTO_PCT = 30.0
MILESTONES = [10, 30, 50, 100, 150, 200]
MOMENTUM_END_DROP_FRAC = 0.20  # igual criterio que explosion_history

_group_cache: Dict[str, Any] = {}


def _session_label(now: datetime) -> str:
    s = market_hours.get_session(now)
    return {"premarket": "PREMARKET", "regular": "REGULAR",
            "afterhours": "AFTERHOURS", "closed": "CLOSED"}.get(s, s.upper())


def _historical_similarity(session: str, now: Optional[datetime]) -> Dict[str, Any]:
    """Compara el SETUP de la señal (no su futuro) con los grupos del Marcador
    Histórico. Usa solo información histórica, disponible ANTES de la
    detección. Premarket -> "similar a A/B (premarket fuerte)"; en/ tras la
    apertura -> "similar a C (empezó tras apertura)". Nunca afirma que la
    señal pertenece a un grupo: es solo similitud de setup, con su n."""
    today = market_hours.market_date(now)
    if _group_cache.get("date") != today:
        try:
            gs = eh.group_study()
            _group_cache.clear()
            _group_cache["date"] = today
            _group_cache["counts"] = {k: gs["grupos"][k]["n"] for k in ("A", "B", "C", "D")}
        except Exception:
            _group_cache.clear()
            _group_cache["date"] = today
            _group_cache["counts"] = {}
    counts = _group_cache.get("counts", {})
    if session == "PREMARKET":
        n = counts.get("A", 0) + counts.get("B", 0)
        return {"historical_group": "similar a A/B (premarket fuerte)", "similar_historical_cases": n}
    n = counts.get("C", 0)
    return {"historical_group": "similar a C (empezó tras apertura)", "similar_historical_cases": n}


def _identity(ticker: str) -> Dict[str, Optional[str]]:
    """Identidad real del instrumento (exchange + nombre) para no confundir
    tickers homónimos. Nombre desde Racional (si está); exchange desde la caché
    del universo amplio (sin red). Best-effort: None si no se conoce, nunca
    inventado. No puede tumbar el ciclo."""
    exchange = name = None
    try:
        from atlas_live.market_study import universe
        ident = universe.lookup_identity(ticker)
        exchange, name = ident.get("exchange"), ident.get("name")
    except Exception:
        pass
    if not name:
        try:
            from atlas.data.universe import get_asset
            asset = get_asset(ticker)
            if asset is not None:
                name = asset.name
        except Exception:
            pass
    return {"exchange": exchange, "name": name}


def _row_features(row: Dict[str, Any]) -> Dict[str, Any]:
    """Features de detección desde la fila del scanner. Solo lo disponible EN
    ESE MOMENTO -- gap, cambio, RVOL, $volumen, volatilidad, market cap. None
    si el proveedor no lo entregó (nunca inventado)."""
    m = (row.get("explosive") or {}).get("metrics") or {}
    keys = ("gap_pct", "change_pct", "relative_volume", "dollar_volume",
            "volatility_score", "market_cap", "price")
    return {k: m.get(k) for k in keys}


def track_cycle(results: List[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Registra/actualiza señales a partir de `results` del scanner. Nunca
    lanza hacia el llamador -- devuelve un resumen. Solo considera candidatos
    elegibles por Radar Explosivo."""
    now = now or datetime.now(timezone.utc)
    session = _session_label(now)
    market_date = market_hours.market_date(now)
    creadas = observadas = 0
    try:
        # Solo se registran señales en sesiones observables de mercado.
        if session not in ("PREMARKET", "REGULAR"):
            return {"session": session, "creadas": 0, "observadas": 0, "nota": "fuera de premarket/apertura"}

        elegibles = [r for r in results
                     if (r.get("explosive") or {}).get("eligible") and r.get("symbol")]
        for row in elegibles:
            ticker = row["symbol"]
            existing = reg.get_signal_by_opportunity(ticker, market_date)
            metrics = (row.get("explosive") or {}).get("metrics") or {}
            return_pct = metrics.get("change_pct")
            price = row.get("price") if row.get("price") is not None else metrics.get("price")
            if existing is None:
                sim = _historical_similarity(session, now)
                v = versions.current_versions()
                reasons = []
                cond = row.get("explosive", {}).get("stage_trace") or []
                ident = _identity(ticker)
                out = reg.register_signal(
                    ticker=ticker, market_date=market_date, session=session,
                    detected_at=now.isoformat(),
                    price_at_detection=price,
                    price_as_of=metrics.get("price_as_of"),
                    provider=metrics.get("source"),
                    features=_row_features(row),
                    score=row.get("explosive", {}).get("score"),
                    reasons=reasons or None,
                    conditions=list(cond) or None,
                    historical_group=sim["historical_group"],
                    similar_historical_cases=sim["similar_historical_cases"],
                    detector_version=v["detector_version"],
                    feature_version=v["feature_version"],
                    data_version=v["data_version"],
                    exchange=ident["exchange"],
                    name=ident["name"],
                )
                if out.get("created"):
                    creadas += 1
                    signal_uuid = out["signal_uuid"]
                else:
                    signal_uuid = out["signal_uuid"]
                # Primer punto de seguimiento (mismo instante de detección).
                if reg.record_observation(signal_uuid, now.isoformat(), return_pct, price=price, session=session):
                    observadas += 1
            else:
                if reg.record_observation(existing["signal_uuid"], now.isoformat(), return_pct, price=price, session=session):
                    observadas += 1
        return {"session": session, "market_date": market_date,
                "candidatos_elegibles": len(elegibles), "creadas": creadas, "observadas": observadas}
    except Exception as exc:
        return {"session": session, "creadas": creadas, "observadas": observadas, "error": str(exc)}


# ---------------------------------------------------------------------------
# Resolución
# ---------------------------------------------------------------------------

def _minutes(a: str, b: str) -> Optional[float]:
    try:
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 60.0
    except Exception:
        return None


def _compute_result(signal: Dict[str, Any], obs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calcula el desenlace REAL a partir de las observaciones seguidas. Sin
    datos con return_pct -> RESUELTA_SIN_DATOS (nunca se inventa)."""
    detected_at = signal["detected_at"]
    series = [(o["observed_at"], o["return_pct"]) for o in obs if o.get("return_pct") is not None]
    if not series:
        return {"result_state": reg.RESUELTA_SIN_DATOS,
                "fields": {"result": "SIN_DATOS", "resolution_reason": "sin observaciones con return_pct"}}
    series.sort(key=lambda x: x[0])
    peak_idx = max(range(len(series)), key=lambda i: series[i][1])
    max_at, max_ret = series[peak_idx]

    minutes_to = {}
    for m in MILESTONES:
        cross = next((ts for ts, r in series if r >= m), None)
        minutes_to[m] = _minutes(detected_at, cross) if cross else None

    # return ~10 min tras la detección (primera obs con >=10 min de antigüedad)
    ret_10min = None
    for ts, r in series:
        dt = _minutes(detected_at, ts)
        if dt is not None and dt >= 10:
            ret_10min = r
            break

    # fin de impulso: primer retroceso relativo sostenido tras el pico
    momentum_end_at = None
    retracement = None
    umbral = max_ret * (1 - MOMENTUM_END_DROP_FRAC)
    for i in range(peak_idx + 1, len(series) - 1):
        if series[i][1] <= umbral and series[i + 1][1] <= umbral:
            momentum_end_at = series[i][0]
            retracement = max_ret - series[i][1]
            break

    acierto = max_ret >= ACIERTO_PCT
    result_state = reg.RESUELTA_ACIERTO if acierto else reg.RESUELTA_FALLO
    return {
        "result_state": result_state,
        "fields": {
            "max_return_pct": round(max_ret, 2), "max_at": max_at,
            "return_at_10min": round(ret_10min, 2) if ret_10min is not None else None,
            "minutes_to_10pct": minutes_to[10], "minutes_to_30pct": minutes_to[30],
            "minutes_to_50pct": minutes_to[50], "minutes_to_100pct": minutes_to[100],
            "minutes_to_150pct": minutes_to[150], "minutes_to_200pct": minutes_to[200],
            "continued_after_open": None,  # se completa si hay contexto de apertura
            "lost_momentum": 1 if momentum_end_at is not None else 0,
            "momentum_end_at": momentum_end_at,
            "retracement_pct": round(retracement, 2) if retracement is not None else None,
            "result": "ACIERTO" if acierto else "FALLO",
            "resolution_reason": "trayectoria cerrada (día de mercado finalizado)",
        },
    }


def resolve_due(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Resuelve las señales activas cuyo día de mercado ya terminó. Idempotente:
    una señal ya resuelta no se re-resuelve."""
    now = now or datetime.now(timezone.utc)
    today = market_hours.market_date(now)
    resueltas = 0
    for signal in reg.list_active():
        if signal["market_date"] >= today:
            continue  # el día todavía no cerró -> se sigue observando
        obs = reg.get_observations(signal["signal_uuid"])
        outcome = _compute_result(signal, obs)
        try:
            reg.record_result(signal["signal_uuid"], now.isoformat(), outcome["result_state"], **outcome["fields"])
            resueltas += 1
        except reg.AlreadyResolvedError:
            continue
    return {"resueltas": resueltas}


# ---------------------------------------------------------------------------
# Estadísticas reales
# ---------------------------------------------------------------------------

_MIN_RELIABLE_N = 20


def stats() -> Dict[str, Any]:
    """Estadísticas reales de las señales registradas. Siempre con n. Si la
    muestra es chica, se marca 'Evidencia insuficiente' -- nunca se presenta
    una tasa como confiable sin respaldo."""
    import statistics
    signals = reg.list_signals(limit=100000)
    results = reg.list_results(limit=100000)
    total = len(signals)
    activas = sum(1 for s in signals if s["state"] in (reg.DETECTADA, reg.OBSERVANDO))
    resueltas = len(results)
    aciertos = sum(1 for r in results if r.get("result") == "ACIERTO")
    fallos = sum(1 for r in results if r.get("result") == "FALLO")
    sin_datos = sum(1 for r in results if r.get("result") == "SIN_DATOS")

    con_dato = [r for r in results if r.get("result") in ("ACIERTO", "FALLO")]
    tasa_acierto = round(aciertos / len(con_dato) * 100, 1) if con_dato else None

    def _pct_reached(minutes_field: str):
        base = len(con_dato)
        if base == 0:
            return None
        alcanzaron = sum(1 for r in con_dato if r.get(minutes_field) is not None)
        return round(alcanzaron / base * 100, 1)

    # Anticipación = minutos desde la detección hasta +30% (solo aciertos).
    antic = [r["minutes_to_30pct"] for r in con_dato if r.get("minutes_to_30pct") is not None]
    antic.sort()
    n_antic = len(antic)
    def _p(p):
        return round(antic[min(n_antic - 1, int(n_antic * p))], 0) if n_antic else None

    reliable = len(con_dato) >= _MIN_RELIABLE_N
    return {
        "total_senales": total,
        "activas": activas,
        "resueltas": resueltas,
        "aciertos": aciertos,
        "fallos": fallos,
        "sin_datos": sin_datos,
        "n_evaluadas": len(con_dato),
        "tasa_acierto_pct": tasa_acierto,
        "muestra_suficiente": reliable,
        "aviso_muestra": None if reliable else f"Evidencia insuficiente (n={len(con_dato)} < {_MIN_RELIABLE_N})",
        "pct_alcanzo": {
            "10pct": _pct_reached("minutes_to_10pct"),
            "30pct": _pct_reached("minutes_to_30pct"),
            "50pct": _pct_reached("minutes_to_50pct"),
            "100pct": _pct_reached("minutes_to_100pct"),
            "150pct": _pct_reached("minutes_to_150pct"),
            "200pct": _pct_reached("minutes_to_200pct"),
        },
        "anticipacion_a_30pct": {
            "n": n_antic,
            "media_min": round(statistics.mean(antic), 0) if antic else None,
            "mediana_min": round(statistics.median(antic), 0) if antic else None,
            "p25_min": _p(0.25), "p75_min": _p(0.75),
            "aviso": None if n_antic >= _MIN_RELIABLE_N else "Evidencia insuficiente",
        },
    }
