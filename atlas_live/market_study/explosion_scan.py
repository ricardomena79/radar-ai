"""Detección de explosiones diarias sobre datos históricos (2026-08-10).

`detect_explosions_from_daily` es una FUNCIÓN PURA (sin red, testeable): dado
el historial diario de un símbolo, encuentra los días en que el máximo
intradía superó +30% respecto del cierre previo, y arma para cada uno:
  - FEATURES leakage-safe (conocidas EN/ANTES de la apertura de ese día):
    prev_close, open, gap_open_pct, prior_avg_volume (media de volumen de los
    N días ANTERIORES, nunca incluye el día de la explosión).
  - OUTCOME (resultado): max_intraday_pct, close_change_pct, day_volume.

REGLA DE LEAKAGE: `max_intraday_pct` (el pico del día) es RESULTADO, jamás
feature. `prior_avg_volume` usa solo días previos. `market_cap` viene del
proveedor como valor ACTUAL (aprox. de tamaño; no es point-in-time histórico
-- limitación documentada, no interviene en el resultado del movimiento).

`scan_symbol` envuelve fetch (yfinance) + detección + persistencia idempotente.
"""

from typing import Any, Dict, List, Optional, Set

EXPLOSION_THRESHOLD_PCT = 30.0
PRIOR_VOLUME_WINDOW = 20


def detect_explosions_from_daily(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """`bars`: lista de barras diarias {date, open, high, low, close, volume}
    ordenadas por fecha ascendente. Devuelve una lista de explosiones (>=+30%
    intradía vs cierre previo) con features leakage-safe + outcome."""
    out: List[Dict[str, Any]] = []
    for i in range(1, len(bars)):
        prev_close = bars[i - 1].get("close")
        b = bars[i]
        high = b.get("high")
        if not prev_close or prev_close <= 0 or high is None:
            continue
        max_intraday_pct = (high / prev_close - 1) * 100
        if max_intraday_pct < EXPLOSION_THRESHOLD_PCT:
            continue
        open_price = b.get("open")
        gap_open_pct = ((open_price / prev_close - 1) * 100) if open_price else None
        close = b.get("close")
        close_change_pct = ((close / prev_close - 1) * 100) if close else None
        # Volumen previo: SOLO días anteriores al de la explosión (anti-leakage).
        prior_vols = [x.get("volume") for x in bars[max(0, i - PRIOR_VOLUME_WINDOW):i]
                      if x.get("volume") is not None]
        prior_avg_volume = (sum(prior_vols) / len(prior_vols)) if prior_vols else None
        out.append({
            "date": b.get("date"),
            "prev_close": prev_close,
            "open_price": open_price,
            "gap_open_pct": gap_open_pct,
            "prior_avg_volume": prior_avg_volume,
            "max_intraday_pct": round(max_intraday_pct, 2),
            "close_change_pct": round(close_change_pct, 2) if close_change_pct is not None else None,
            "day_volume": b.get("volume"),
        })
    return out


def _fetch_daily_bars(ticker: str, period: str = "6mo") -> List[Dict[str, Any]]:
    """Historial diario vía yfinance. Devuelve barras normalizadas; [] si no
    hay datos. Solo lectura, sin efectos."""
    import yfinance as yf
    hist = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
    bars = []
    for idx, row in hist.iterrows():
        bars.append({
            "date": idx.date().isoformat(),
            "open": float(row["Open"]) if row["Open"] == row["Open"] else None,
            "high": float(row["High"]) if row["High"] == row["High"] else None,
            "low": float(row["Low"]) if row["Low"] == row["Low"] else None,
            "close": float(row["Close"]) if row["Close"] == row["Close"] else None,
            "volume": float(row["Volume"]) if row["Volume"] == row["Volume"] else None,
        })
    return bars


def _fetch_market_cap(ticker: str) -> Optional[float]:
    """Market cap ACTUAL (aprox. de tamaño, no point-in-time). None si falla."""
    import yfinance as yf
    try:
        mc = yf.Ticker(ticker).fast_info.get("marketCap")
        return float(mc) if mc else None
    except Exception:
        return None


def scan_symbol(ticker: str, racional_symbols: Set[str], period: str = "6mo") -> Dict[str, Any]:
    """Escanea UN símbolo: fetch diario + detección + persistencia idempotente.
    Devuelve {status, explosions}. status in {ok, sin_datos, error}. Nunca
    lanza -- un símbolo que falla no puede tumbar el lote."""
    from atlas_live.market_study import study_registry as reg
    try:
        bars = _fetch_daily_bars(ticker, period=period)
    except Exception as exc:
        return {"status": "error", "explosions": 0, "note": f"{type(exc).__name__}: {exc}"}
    if not bars:
        return {"status": "sin_datos", "explosions": 0}

    explosions = detect_explosions_from_daily(bars)
    market_cap = _fetch_market_cap(ticker) if explosions else None
    available = ticker.upper() in racional_symbols
    nuevas = 0
    for e in explosions:
        if reg.record_explosion(
            ticker=ticker, date=e["date"], prev_close=e["prev_close"],
            open_price=e["open_price"], gap_open_pct=e["gap_open_pct"],
            prior_avg_volume=e["prior_avg_volume"], market_cap=market_cap,
            available_in_racional=available, max_intraday_pct=e["max_intraday_pct"],
            close_change_pct=e["close_change_pct"], day_volume=e["day_volume"],
        ):
            nuevas += 1
    return {"status": "ok", "explosions": len(explosions), "nuevas": nuevas}
