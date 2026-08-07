"""Reconstruye, para una fecha de mercado pasada real, qué habría visto el
Radar Explosivo si hubiera corrido `snapshot_minutes_after_open` minutos
después de la apertura de ese día -- y corre `explosive_engine.evaluate()`
(sin modificar) sobre esa reconstrucción para TODO el Universo Racional
(~2577 símbolos), no solo la muestra de 200 que usa el escaneo en vivo.

Metodología (para que la validación sea honesta, no optimista):
  - Los indicadores de tendencia/volatilidad (RSI, EMA, ATR, volumen
    promedio) se calculan SOLO con velas diarias anteriores a la fecha
    objetivo -- nunca con datos del día que se está evaluando, para no
    "espiar el futuro".
  - El gap%, el precio, el volumen acumulado y el VWAP se reconstruyen a
    partir de velas intradía reales de 5 minutos de ESE día específico,
    cortadas en el minuto `snapshot_minutes_after_open` -- exactamente el
    mismo tipo de dato (parcial, del momento) que ve el radar en vivo, no
    el cierre del día completo.
  - Yahoo Finance solo conserva velas de 5 minutos de los últimos ~60 días,
    así que esto solo funciona para fechas recientes (por eso se valida
    contra el viernes 2026-07-31, la última sesión completa).
  - El "ganador del día" (para la verdad de referencia) sí usa el cierre
    completo del día -- ahí no hay problema de mirar el futuro, porque es
    el resultado real que se busca predecir, no un insumo del radar.
  - Simplificaciones explícitas: no se reconstruye sector/money-flow
    histórico (se pasa `sector_money_flow_score=None`, ese factor queda
    fuera y su peso se redistribuye automáticamente, como está diseñado
    para cualquier factor opcional ausente) ni float histórico. El market
    cap usa acciones en circulación actuales × precio histórico (aproximación
    razonable a semanas de distancia, no exacta).
"""

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf
from loguru import logger

from atlas.data.models.quote import Quote
from atlas.data.universe import Asset, load_universe
from atlas.engine.momentum_engine import WEIGHTS as MOMENTUM_WEIGHTS
from atlas.engine.momentum_engine import MomentumResult
from atlas.engine.momentum_engine import ScoredComponent as MomentumScoredComponent
from atlas.engine.momentum_engine import score_change_percent, score_gap_pct, score_rsi
from atlas.engine.score_engine import (
    score_atr,
    score_ema_trend,
    score_liquidity,
    score_market_cap,
    score_relative_volume,
    score_vwap_distance,
)

from atlas_live import explosive_engine
from atlas_live.explosive_config import load_config
from atlas_live.memory import market_hours

DAILY_LOOKBACK_DAYS = 400  # bien por encima de los ~260 días hábiles que cubren 6mo+margen
TRAILING_AVG_VOLUME_DAYS = 20
CHUNK_SIZE = 100
MARKET_CAP_WORKERS = 20
DOWNLOAD_RETRIES = 2


def _chunks(items: List[str], size: int) -> List[List[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _download_with_retry(**kwargs) -> pd.DataFrame:
    last_exc: Optional[Exception] = None
    for attempt in range(DOWNLOAD_RETRIES + 1):
        try:
            return yf.download(**kwargs, progress=False, threads=True)
        except Exception as exc:  # noqa: BLE001 - tolerancia deliberada, se reintenta
            last_exc = exc
            time.sleep(1.5 * (attempt + 1))
    logger.warning(f"Descarga en lote falló tras reintentos: {last_exc}")
    return pd.DataFrame()


def fetch_daily_bars(symbols: List[str], target_date: date) -> Dict[str, pd.DataFrame]:
    """Velas diarias por símbolo, desde `DAILY_LOOKBACK_DAYS` antes de la
    fecha objetivo hasta la fecha objetivo inclusive (para tener tanto el
    historial previo como la vela del propio día objetivo)."""
    return fetch_daily_bars_range(symbols, target_date, target_date)


def fetch_daily_bars_range(symbols: List[str], earliest_target_date: date, latest_target_date: date) -> Dict[str, pd.DataFrame]:
    """Igual que `fetch_daily_bars`, pero cubre un RANGO de fechas objetivo
    en una sola descarga (se reutiliza para validar muchos días sin volver
    a pedir el mismo historial una y otra vez -- lo único que cambia día a
    día es la vela intradía, no el historial de indicadores)."""
    start = earliest_target_date - timedelta(days=DAILY_LOOKBACK_DAYS)
    end = latest_target_date + timedelta(days=1)  # yfinance `end` es exclusivo

    result: Dict[str, pd.DataFrame] = {}
    for chunk in _chunks(symbols, CHUNK_SIZE):
        df = _download_with_retry(tickers=chunk, start=str(start), end=str(end), interval="1d", group_by="ticker")
        if df.empty:
            continue
        for symbol in chunk:
            try:
                sub = df[symbol].dropna(how="all")
            except (KeyError, Exception):
                continue
            if not sub.empty:
                result[symbol] = sub
    return result


INTRADAY_DOWNLOAD_WORKERS = 4


def fetch_intraday_bars(symbols: List[str], target_date: date) -> Dict[str, pd.DataFrame]:
    """Velas de 5 minutos del propio día objetivo (solo funciona si la fecha
    está dentro de la ventana reciente que conserva Yahoo Finance). Es el
    cuello de botella real de una validación multi-día (no se puede
    reutilizar entre días como el historial diario), así que se paraleliza
    con un pool moderado -- no agresivo, para no forzar el rate limit de
    Yahoo Finance.

    `prepost=True` (2026-08-06, ver DECISIONES.md, Fase 1.1): sin esto, el
    rango devuelto para un día excluye premarket y after-hours -- mismo
    problema ya encontrado y corregido en el escaneo en vivo (A2) y en el
    proveedor de Yahoo Finance. Necesario para reconstruir la trayectoria
    completa desde la primera señal, no solo desde la apertura regular."""
    start = target_date
    end = target_date + timedelta(days=1)
    chunks = _chunks(symbols, CHUNK_SIZE)

    result: Dict[str, pd.DataFrame] = {}

    def _fetch_chunk(chunk: List[str]) -> Dict[str, pd.DataFrame]:
        df = _download_with_retry(tickers=chunk, start=str(start), end=str(end), interval="5m", prepost=True, group_by="ticker")
        out: Dict[str, pd.DataFrame] = {}
        if df.empty:
            return out
        for symbol in chunk:
            try:
                sub = df[symbol].dropna(how="all")
            except (KeyError, Exception):
                continue
            if not sub.empty:
                out[symbol] = sub
        return out

    with ThreadPoolExecutor(max_workers=INTRADAY_DOWNLOAD_WORKERS) as executor:
        futures = [executor.submit(_fetch_chunk, chunk) for chunk in chunks]
        for future in as_completed(futures):
            result.update(future.result())
    return result


def _fetch_market_cap_one(symbol: str) -> Tuple[str, Optional[float], Optional[float]]:
    try:
        fi = yf.Ticker(symbol).fast_info
        shares = getattr(fi, "shares", None)
        market_cap = getattr(fi, "market_cap", None)
        return symbol, shares, market_cap
    except Exception:
        return symbol, None, None


def fetch_shares_and_market_cap(symbols: List[str]) -> Dict[str, Tuple[Optional[float], Optional[float]]]:
    """Acciones en circulación actuales y market cap actual (fast_info, no
    histórico) por símbolo -- la única forma barata de estimar tamaño;
    aproximación explícita, ver docstring del módulo."""
    result: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
    with ThreadPoolExecutor(max_workers=MARKET_CAP_WORKERS) as executor:
        futures = [executor.submit(_fetch_market_cap_one, s) for s in symbols]
        for future in as_completed(futures):
            symbol, shares, market_cap = future.result()
            result[symbol] = (shares, market_cap)
    return result


def _weighted_momentum(components, weights) -> MomentumResult:
    scored = [
        MomentumScoredComponent(
            name=c.name, score=c.score, weight=weights[c.name],
            weighted_score=c.score * weights[c.name], explanation=c.explanation,
        )
        for c in components
    ]
    total = round(sum(c.weighted_score for c in scored), 2)
    return MomentumResult(symbol="", momentum_score=total, components=scored)


def reconstruct_symbol(
    symbol: str,
    daily: pd.DataFrame,
    intraday: Optional[pd.DataFrame],
    target_date: date,
    snapshot_minutes_after_open: int,
    shares: Optional[float],
    fallback_market_cap: Optional[float],
) -> Optional[Dict[str, Any]]:
    """Devuelve None si no hay datos suficientes para reconstruir este
    símbolo en esta fecha (mismo criterio de tolerancia a fallos que usa
    scan_worker.py: un símbolo problemático no debe tumbar el escaneo)."""
    try:
        daily = daily.sort_index()
        target_ts = pd.Timestamp(target_date)
        before = daily[daily.index.tz_localize(None) < target_ts] if daily.index.tz is not None else daily[daily.index < target_ts]
        target_rows = daily[daily.index.tz_localize(None) == target_ts] if daily.index.tz is not None else daily[daily.index == target_ts]

        if before.empty or target_rows.empty or len(before) < 21:
            return None  # no hay suficiente historial previo para RSI/EMA21/ATR, o no hay vela del día objetivo

        target_row = target_rows.iloc[-1]
        day_open = float(target_row["Open"])
        day_close = float(target_row["Close"])  # solo para la verdad de referencia
        previous_close = float(before["Close"].iloc[-1])

        if not day_open or not previous_close:
            return None

        ground_truth_change_pct = (day_close - previous_close) / previous_close * 100

        avg_volume = float(before["Volume"].tail(TRAILING_AVG_VOLUME_DAYS).mean())

        # --- Snapshot intradía (los primeros `snapshot_minutes_after_open` minutos) ---
        vwap_available = intraday is not None and not intraday.empty
        if vwap_available:
            intraday_sorted = intraday.sort_index()
            n_candles = max(1, snapshot_minutes_after_open // 5)
            snapshot = intraday_sorted.iloc[:n_candles]
            if snapshot.empty:
                vwap_available = False

        if vwap_available:
            snapshot_price = float(snapshot["Close"].iloc[-1])
            snapshot_volume = float(snapshot["Volume"].sum())
            snapshot_high = float(snapshot["High"].max())
            snapshot_low = float(snapshot["Low"].min())
            if snapshot_volume > 0:
                vwap_component = score_vwap_distance(snapshot_price, snapshot["High"], snapshot["Low"], snapshot["Close"], snapshot["Volume"])
            else:
                # Velas de premarket con volumen 0 (dato real de Yahoo, no un
                # faltante -- ver DECISIONES.md, "Bug encontrado y corregido
                # durante la implementación", 2026-08-04): `calc_vwap` da
                # NaN en todas las filas, y `_last()` revienta con
                # "single positional indexer is out-of-bounds" al hacer
                # `.dropna().iloc[-1]` sobre una serie vacía -- esto tumbaba
                # en silencio TODA la reconstrucción de esa vela (Fase 1.1,
                # Sprint 2, 2026-08-06), justo el tramo de premarket que esta
                # fase necesita medir. Mismo valor neutro que ya usa la rama
                # `else` de abajo para "sin datos intradía".
                from atlas.engine.score_engine import ComponentScore
                vwap_component = ComponentScore(
                    name="vwap_distance", score=50.0,
                    explanation="Sin volumen en la ventana (premarket ilíquido) -- VWAP no calculable",
                )
        else:
            # Sin velas intradía para este día (fuera de la ventana de ~60
            # días de Yahoo, o dato faltante): mismo valor neutro que usa
            # Atlas Core en vivo cuando no hay datos intradía disponibles.
            snapshot_price = day_open
            snapshot_volume = 0.0
            snapshot_high = day_open
            snapshot_low = day_open
            from atlas.engine.score_engine import ComponentScore
            vwap_component = ComponentScore(name="vwap_distance", score=50.0, explanation="Datos intradía no disponibles")

        change_percent = (snapshot_price - previous_close) / previous_close * 100

        market_cap = None
        if shares:
            market_cap = shares * snapshot_price
        elif fallback_market_cap:
            market_cap = fallback_market_cap

        # market_state real (Investigación 3, 2026-08-06, ver DECISIONES.md):
        # antes quedaba siempre None acá -- el gate de liquidez/RVOL
        # premarket-aware de explosive_engine.py necesita saberlo para
        # activarse solo en premarket, igual que ya lo sabe el escaneo en
        # vivo vía Yahoo. Se deriva de la hora real de la última vela del
        # snapshot (no inventada -- mismo dato que ya usa
        # reconstruct_trajectories.py para el timestamp real de cada punto),
        # reutilizando `market_hours.get_session()` sin duplicar la lógica
        # de horarios.
        market_state = None
        if vwap_available:
            sesion = market_hours.get_session(snapshot.index[-1])
            market_state = {"premarket": "PRE", "regular": "REGULAR", "afterhours": "POST", "closed": "CLOSED"}[sesion]

        quote = Quote(
            symbol=symbol, name=None, last_price=snapshot_price, change_percent=change_percent,
            volume=int(snapshot_volume), open=day_open, high=snapshot_high, low=snapshot_low,
            previous_close=previous_close, market_cap=market_cap, sector=None, industry=None,
            float_shares=None, average_volume=int(avg_volume) if avg_volume else None,
            relative_volume=None, timestamp=datetime.combine(target_date, datetime.min.time()),
            market_state=market_state,
        )

        components = [
            score_relative_volume(quote.volume, quote.average_volume),
            score_gap_pct(day_open, previous_close),
            vwap_component,
            score_rsi(before["Close"]),
            score_ema_trend(before["Close"]),
            score_liquidity(snapshot_price, snapshot_volume),
            score_atr(before["High"], before["Low"], before["Close"], snapshot_price),
            score_change_percent(change_percent),
            score_market_cap(market_cap),
        ]
        momentum_result = _weighted_momentum(components, MOMENTUM_WEIGHTS)

        return {
            "quote": quote,
            "momentum_result": momentum_result,
            "ground_truth_change_pct": ground_truth_change_pct,
            "vwap_available": vwap_available,
        }
    except Exception as exc:
        logger.debug(f"No se pudo reconstruir {symbol}: {exc}")
        return None


def run_historical_scan(
    target_date: date,
    snapshot_minutes_after_open: int = 10,
    universe: Optional[List[Asset]] = None,
) -> Dict[str, Any]:
    """Corre la reconstrucción + explosive_engine.evaluate() (sin modificar)
    sobre TODO el Universo Racional para una fecha pasada. No modifica
    explosive_config.json ni ningún parámetro del motor."""
    assets = universe if universe is not None else list(load_universe().values())
    symbols = [a.symbol for a in assets]

    logger.info(f"Descargando velas diarias para {len(symbols)} símbolos ({target_date})...")
    daily_bars = fetch_daily_bars(symbols, target_date)
    logger.info(f"  -> {len(daily_bars)} símbolos con historial diario suficiente")

    logger.info("Descargando velas intradía de 5m del día objetivo...")
    intraday_bars = fetch_intraday_bars(symbols, target_date)
    logger.info(f"  -> {len(intraday_bars)} símbolos con datos intradía ese día")

    logger.info("Consultando market cap / acciones en circulación (fast_info, por símbolo)...")
    caps = fetch_shares_and_market_cap(list(daily_bars.keys()))
    logger.info("  -> listo")

    cfg = load_config()  # SIN modificar: exactamente el mismo config que usa el radar en vivo

    rows: List[Dict[str, Any]] = []
    data_errors = 0
    for symbol in daily_bars:
        shares, fallback_cap = caps.get(symbol, (None, None))
        reconstructed = reconstruct_symbol(
            symbol=symbol, daily=daily_bars[symbol], intraday=intraday_bars.get(symbol),
            target_date=target_date, snapshot_minutes_after_open=snapshot_minutes_after_open,
            shares=shares, fallback_market_cap=fallback_cap,
        )
        if reconstructed is None:
            data_errors += 1
            continue

        explosive_result = explosive_engine.evaluate(
            quote=reconstructed["quote"], momentum_result=reconstructed["momentum_result"],
            sector_money_flow_score=None, config=cfg,
        )

        rows.append({
            "symbol": symbol,
            "ground_truth_change_pct": reconstructed["ground_truth_change_pct"],
            "vwap_available": reconstructed["vwap_available"],
            "explosive": {
                "eligible": explosive_result.eligible,
                "score": explosive_result.score,
                "reasons": explosive_result.reasons,
                "excluded_reason": explosive_result.excluded_reason,
                "is_size_exception": explosive_result.is_size_exception,
                "failed_stage": explosive_result.failed_stage,
                "stage_trace": explosive_result.stage_trace,
                "metrics": explosive_result.metrics,
            },
        })

    return {
        "target_date": str(target_date),
        "snapshot_minutes_after_open": snapshot_minutes_after_open,
        "universe_total": len(symbols),
        "reconstructed_ok": len(rows),
        "data_errors": data_errors + (len(symbols) - len(daily_bars)),
        "rows": rows,
    }


def save_scan(result: Dict[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def _evaluate_day(
    target_date: date,
    daily_bars: Dict[str, pd.DataFrame],
    intraday_bars: Dict[str, pd.DataFrame],
    caps: Dict[str, Tuple[Optional[float], Optional[float]]],
    snapshot_minutes_after_open: int,
    cfg: Dict[str, Any],
    universe_total: int,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    data_errors = 0
    for symbol, daily in daily_bars.items():
        shares, fallback_cap = caps.get(symbol, (None, None))
        reconstructed = reconstruct_symbol(
            symbol=symbol, daily=daily, intraday=intraday_bars.get(symbol),
            target_date=target_date, snapshot_minutes_after_open=snapshot_minutes_after_open,
            shares=shares, fallback_market_cap=fallback_cap,
        )
        if reconstructed is None:
            data_errors += 1
            continue

        explosive_result = explosive_engine.evaluate(
            quote=reconstructed["quote"], momentum_result=reconstructed["momentum_result"],
            sector_money_flow_score=None, config=cfg,
        )

        rows.append({
            "symbol": symbol,
            "ground_truth_change_pct": reconstructed["ground_truth_change_pct"],
            "vwap_available": reconstructed["vwap_available"],
            "explosive": {
                "eligible": explosive_result.eligible,
                "score": explosive_result.score,
                "reasons": explosive_result.reasons,
                "excluded_reason": explosive_result.excluded_reason,
                "is_size_exception": explosive_result.is_size_exception,
                "failed_stage": explosive_result.failed_stage,
                "stage_trace": explosive_result.stage_trace,
                "metrics": explosive_result.metrics,
            },
        })

    return {
        "target_date": str(target_date),
        "snapshot_minutes_after_open": snapshot_minutes_after_open,
        "universe_total": universe_total,
        "reconstructed_ok": len(rows),
        "data_errors": data_errors + (universe_total - len(daily_bars)),
        "rows": rows,
    }


def run_historical_scan_multi(
    target_dates: List[date],
    results_dir: str,
    snapshot_minutes_after_open: int = 10,
    universe: Optional[List[Asset]] = None,
) -> List[str]:
    """Corre la validación para VARIOS días de mercado. El historial diario
    (para RSI/EMA/ATR/volumen promedio) y el market cap se descargan UNA
    sola vez para todo el rango -- no dependen del día objetivo específico,
    solo la vela intradía sí. Guarda el resultado de cada día apenas
    termina (no espera a que terminen los 30 días) para que un corte a
    mitad de camino no pierda el trabajo ya hecho.

    Devuelve la lista de rutas de archivo guardadas, una por día."""
    import os

    os.makedirs(results_dir, exist_ok=True)

    assets = universe if universe is not None else list(load_universe().values())
    symbols = [a.symbol for a in assets]
    universe_total = len(symbols)

    earliest, latest = min(target_dates), max(target_dates)

    logger.info(f"[1/3] Descargando historial diario para {universe_total} símbolos, rango {earliest}..{latest}...")
    daily_bars = fetch_daily_bars_range(symbols, earliest, latest)
    logger.info(f"  -> {len(daily_bars)} símbolos con historial diario suficiente (se reutiliza para los {len(target_dates)} días)")

    logger.info("[2/3] Consultando market cap / acciones en circulación (una sola vez, se reutiliza)...")
    caps = fetch_shares_and_market_cap(list(daily_bars.keys()))
    logger.info("  -> listo")

    cfg = load_config()  # SIN modificar: el mismo config que usa el radar en vivo

    saved_paths: List[str] = []
    logger.info(f"[3/3] Procesando {len(target_dates)} días (esto es lo que toma tiempo: intradía no se puede reutilizar entre días)...")
    for i, target_date in enumerate(sorted(target_dates), start=1):
        out_path = os.path.join(results_dir, f"{target_date}.json")
        if os.path.exists(out_path):
            logger.info(f"  ({i}/{len(target_dates)}) {target_date}: ya existe, se omite")
            saved_paths.append(out_path)
            continue

        t0 = time.monotonic()
        intraday_bars = fetch_intraday_bars(symbols, target_date)
        day_result = _evaluate_day(target_date, daily_bars, intraday_bars, caps, snapshot_minutes_after_open, cfg, universe_total)
        save_scan(day_result, out_path)
        saved_paths.append(out_path)
        elapsed = time.monotonic() - t0
        n_eligible = sum(1 for r in day_result["rows"] if r["explosive"]["eligible"])
        logger.info(
            f"  ({i}/{len(target_dates)}) {target_date}: {day_result['reconstructed_ok']} reconstruidos, "
            f"{n_eligible} elegibles, {len(intraday_bars)}/{universe_total} con intradía -- {elapsed:.0f}s"
        )

    return saved_paths
