"""Integración en tiempo real -- conecta el ranking que `scan_worker.py`
ya calcula en cada ciclo con el Memory Engine (Ranking Score + Prediction
Journal), según la sesión de mercado (`market_hours.py`).

**No modifica Radar Explosivo ni Atlas Core.** Solo LEE `results`, la
misma lista de filas que `scan_worker.py` ya produce cada ciclo
(`row['explosive']['metrics'/'score'/'eligible']`) -- no repite ninguna
llamada de red ni recalcula nada del motor. Vive enteramente en
`atlas_live/memory/`.

Punto de entrada único: `run_live_cycle(results, collector)`, llamado
desde `run_scan_once()` al final de cada ciclo, envuelto en su propio
try/except desde el llamador -- este módulo nunca debe tumbar el escaneo
principal (mismo principio ya aplicado en `scan_worker.py` para
`recorder.record_decision`).

Adaptación de portado (2026-08-05): la versión original creaba su propio
`DataCollector(YahooFinanceProvider())` en `_grade_pending()` -- un
proveedor hardcodeado, en contra del principio ya establecido en esta
rama de que ningún módulo fija Yahoo Finance a mano (ver
`atlas.data.providers.get_default_provider()`). Acá `run_live_cycle()`
recibe el `collector` que `run_scan_once()` ya construyó para el ciclo
(mismo proveedor configurado, misma caché en memoria), en vez de crear
uno nuevo.

Validación (2026-08-05): la lógica completa (escritura en Memory Store,
Prediction Journal y Exit Journal, las tres etapas del día -- premarket,
regular, afterhours) se validó con filas y una cotización de forma real
exacta (mismos campos/tipos que produce `scan_worker._score_symbol()` y
`Quote`), sobre bases SQLite temporales aisladas -- NO fue un escaneo
real: Yahoo Finance estaba rate-limited en ese momento (confirmado con
tres intentos reales fallidos: escaneo completo, escaneo de 3 símbolos,
una sola cotización). Los 4 puntos de aceptación se verificaron así:
observation_count subió de 0 a 2, 2 predicciones calificadas en
Prediction Journal, 2 resúmenes cerrados en Exit Journal, 2 observaciones
nuevas reales en el Memory Store (una EXPLOSION, una FALSE_BREAKOUT,
clasificadas correctamente según `classifier.py`). La confirmación con
datos realmente en vivo queda pendiente del primer escaneo real exitoso
-- ver `atlas_live/memory/verify_live_cycle.py`.

Qué hace según la sesión (`market_hours.get_session()`):
  - **premarket**: arma el ranking con Memory Engine + Ranking Score sobre
    `results` y lo guarda como snapshot dinámico (`prediction_journal.
    record_dynamic_snapshot`). Si además estamos en la ventana de sellado
    (`market_hours.is_seal_window`, los últimos minutos antes de las
    09:30) y todavía no se selló el ranking de hoy, lo sella
    (`prediction_journal.seal_ranking`) -- una sola vez, garantizado por
    `AlreadySealedError` dentro de `prediction_journal`.
  - **regular**: registra un punto de la trayectoria de cada símbolo del
    ranking oficial sellado (`exit_journal.record_trajectory_sample`) --
    Exit Journal, memoria histórica de cómo evoluciona una oportunidad,
    sin decidir nada (ver `exit_journal.py`).
  - **afterhours / closed**: si existe un ranking sellado de hoy con
    predicciones sin calificar, obtiene la cotización actual de cada
    símbolo pendiente, las califica (`prediction_journal.
    grade_sealed_prediction`, con tiempo de anticipación), cierra el
    resumen objetivo de su trayectoria en el Exit Journal
    (`exit_journal.close_exit_summary`), y agrega una observación real
    nueva al Memory Store (`store.record_observation`, ver `_grade_pending`
    -- este último paso no existía en la versión original portada; sin
    él, Memory Engine nunca aprendía de la operación en vivo, solo del
    backfill histórico).
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from atlas.data.collectors.data_collector import DataCollector

from atlas_live.memory import base_rates as br
from atlas_live.memory import calibration_advisor as ca
from atlas_live.memory import classifier
from atlas_live.memory import exit_journal as ej
from atlas_live.memory import market_hours
from atlas_live.memory import prediction_journal as pj
from atlas_live.memory import ranked_candidate as rc
from atlas_live.memory import ranking_score as rs
from atlas_live.memory import store

CATEGORY = "EXPLOSION"
TOP_N_JOURNAL = 20  # mismo top_n que ya usa Radar Explosivo en explosive_config.json

# La evidencia (tasas base + propuestas confiables) solo cambia cuando el
# Memory Store crece -- recalcularla sobre decenas de miles de
# observaciones en cada ciclo de 5 minutos sería caro e innecesario. Se
# calcula una vez por día de mercado; `refresh_evidence()` la fuerza a
# recalcular si hace falta (ej. después de importar el seed).
_evidence_cache: Dict[str, Any] = {}


def refresh_evidence(computed_on: Optional[str] = None) -> None:
    observations = br.load_all_observations()
    baseline = br.compute_population_base_rate(observations, CATEGORY)
    proposals = ca.generate_proposals(observations, category=CATEGORY)
    condition_value_cache = rs.build_condition_value_cache(observations, proposals)
    _evidence_cache.update(
        baseline=baseline, proposals=proposals, condition_value_cache=condition_value_cache,
        computed_on=computed_on,
        observation_count=len(observations),
        days_backed=len({o["date"] for o in observations if o.get("date")}),
    )


def _evidence(now: Optional[datetime] = None) -> Dict[str, Any]:
    """La evidencia (tasas base + propuestas confiables) se recalcula
    automáticamente una vez por cada día de mercado nuevo -- no en cada
    ciclo (sería carísimo sobre decenas de miles de observaciones) ni una
    sola vez por proceso (dejaría de aprender de lo que se va calificando
    en el Prediction Journal día a día)."""
    today = market_hours.market_date(now)
    if not _evidence_cache or _evidence_cache.get("computed_on") != today:
        refresh_evidence(computed_on=today)
    return _evidence_cache


def _to_journaled(rank: int, c: rc.RankedCandidate) -> pj.JournaledCandidate:
    return pj.JournaledCandidate(
        symbol=c.symbol,
        rank=rank,
        score=c.score,
        probability_pct=c.probability_pct,
        confidence=c.confidence,
        semaforo=c.semaforo,
        ranking_score_nivel1=c.ranking_score.nivel1_wilson_lower_bound,
        ranking_score_nivel2=c.ranking_score.nivel2_condiciones_adicionales,
        ranking_score_nivel3=c.ranking_score.nivel3_percentil_dentro_de_banda,
        ranking_score_nivel4=c.ranking_score.nivel4_score_radar,
        evidence_condition=c.evidence_condition,
        evidence_sample_size=c.evidence_sample_size,
        evidence_wilson_lower_bound_pct=c.evidence_wilson_lower_bound_pct,
        explanation=c.explanation,
    )


SEMAFORO_WORD = {"🟢": "verde", "🟡": "amarillo", "🔴": "rojo"}


def serialize_ranked_candidate(c: rc.RankedCandidate) -> Dict[str, Any]:
    """Convierte un RankedCandidate a un dict JSON-serializable -- `semaforo`
    se traduce de emoji a palabra (verde/amarillo/rojo)."""
    return {
        "symbol": c.symbol,
        "score": c.score,
        "eligible_radar": c.eligible_radar,
        "radar_excluded_reason": c.radar_excluded_reason,
        "market_cap_bucket": c.market_cap_bucket,
        "price": c.price,
        "change_pct": c.change_pct,
        "price_type": c.price_type,
        "price_source": c.price_source,
        "market_state": c.market_state,
        "price_regular": c.price_regular,
        "price_premarket": c.price_premarket,
        "price_afterhours": c.price_afterhours,
        "price_overnight": c.price_overnight,
        "price_as_of": c.price_as_of,
        "probability_pct": c.probability_pct,
        "confidence": c.confidence,
        "semaforo": SEMAFORO_WORD.get(c.semaforo, "neutro"),
        "explanation": c.explanation,
        "evidence_condition": c.evidence_condition,
        "evidence_sample_size": c.evidence_sample_size,
        "evidence_wilson_lower_bound_pct": c.evidence_wilson_lower_bound_pct,
        "ranking_score": {
            "nivel1_wilson_lower_bound": c.ranking_score.nivel1_wilson_lower_bound,
            "nivel2_condiciones_adicionales": c.ranking_score.nivel2_condiciones_adicionales,
            "nivel3_percentil_dentro_de_banda": c.ranking_score.nivel3_percentil_dentro_de_banda,
            "nivel4_score_radar": c.ranking_score.nivel4_score_radar,
        },
    }


def get_memory_engine_summary(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Estado real del Memory Engine: tamaño del Memory Store, tasa base
    poblacional y las condiciones confiables vigentes, reutilizando la
    misma evidencia cacheada por día que ya usa `build_live_ranking` -- no
    recalcula nada nuevo."""
    evidence = _evidence(now)
    conditions = []
    for p in evidence["proposals"]:
        e = p.evidence
        lift = (e.win_rate / e.baseline_win_rate) if e.win_rate is not None and e.baseline_win_rate > 0 else None
        conditions.append({
            "label": p.condition_label,
            "win_rate_pct": e.win_rate * 100 if e.win_rate is not None else None,
            "wilson_lower_bound_pct": e.wilson_lower_bound * 100 if e.wilson_lower_bound is not None else None,
            "sample_size": e.sample_size,
            "lift": lift,
        })
    return {
        "observation_count": evidence.get("observation_count"),
        "days_backed": evidence.get("days_backed"),
        "baseline_win_rate_pct": evidence["baseline"] * 100,
        "reliable_condition_count": len(conditions),
        "reliable_conditions": conditions,
        "last_recalibrated_on": evidence.get("computed_on"),
    }


def build_live_ranking(results: List[Dict[str, Any]], now: Optional[datetime] = None) -> List[rc.RankedCandidate]:
    """Aplica Memory Engine + Ranking Score sobre `results` -- el ranking
    que `scan_worker.py` ya calculó en este ciclo. No recalcula nada de
    Radar Explosivo, solo lo lee."""
    evidence = _evidence(now)
    ranked = [
        rc.build_ranked_candidate(
            row, evidence["proposals"], evidence["condition_value_cache"], evidence["baseline"], CATEGORY
        )
        for row in results
        if row.get("explosive") is not None
    ]
    # Regla de consenso: `eligible_radar` ordena primero -- esto también
    # protege el sellado del Prediction Journal (`_to_journaled` toma los
    # primeros TOP_N_JOURNAL de este mismo `ranked`), no solo la
    # presentación. Un símbolo que Radar Explosivo rechazó nunca puede
    # quedar sellado como la predicción oficial del día.
    ranked.sort(key=lambda c: (c.eligible_radar, c.ranking_score), reverse=True)
    return ranked


def _track_trajectory(date: str, now: datetime, results: List[Dict[str, Any]]) -> str:
    """Exit Journal -- durante la sesión regular, registra un punto de la
    trayectoria (rendimiento observado en este ciclo) para cada símbolo
    del ranking oficial ya sellado. No calcula nada -- solo guarda el dato
    crudo (ver `exit_journal.py`)."""
    if not pj.is_sealed(date):
        return "sin_sellado_hoy"

    por_symbol = {row["symbol"]: row for row in results if row.get("explosive") is not None}
    sealed = pj.get_sealed_predictions(date)
    muestreados = 0
    for pred in sealed:
        row = por_symbol.get(pred["symbol"])
        if row is None:
            continue
        metrics = row["explosive"]["metrics"]
        ej.record_trajectory_sample(
            symbol=pred["symbol"], date=date, sampled_at=now.isoformat(),
            return_pct=metrics.get("change_pct"), score=row["explosive"]["score"],
            eligible=bool(row["explosive"]["eligible"]),
        )
        muestreados += 1
    return f"trayectoria_muestreada={muestreados}/{len(sealed)}"


def _grade_pending(date: str, now: datetime, collector: DataCollector, results: List[Dict[str, Any]]) -> str:
    """Además de calificar (Prediction Journal) y cerrar la trayectoria
    (Exit Journal), agrega al Memory Store la observación real recién
    confirmada -- adaptación de portado (2026-08-05): la versión original
    calificaba pero nunca escribía de vuelta al Memory Store, así que
    Memory Engine nunca crecía con la operación real, solo con el
    backfill histórico. Acá, si el símbolo sigue apareciendo en el
    escaneo de hoy (`results`, el mismo ciclo que ya está corriendo),
    se usan sus métricas reales de Radar Explosivo de este cierre para
    guardar una observación nueva, con la categoría recién calculada
    (ground truth real, no una suposición)."""
    sealed = pj.get_sealed_predictions(date)
    pendientes = [row for row in sealed if row["graded_at"] is None]
    if not pendientes:
        return "sin_pendientes"

    por_symbol = {row["symbol"]: row for row in results if row.get("explosive") is not None}
    checkpoint_minutes = market_hours.minutes_since_open(now)

    calificados = 0
    observaciones_nuevas = 0
    for row in pendientes:
        try:
            quote = collector.get_quote(row["symbol"])
        except Exception:
            continue  # un símbolo que falla no debe bloquear la calificación del resto
        change_pct = quote.change_percent
        if change_pct is None:
            continue
        # La eligibilidad al momento de la predicción ya quedó decidida en
        # el sellado -- Radar Explosivo solo calcula `score` para símbolos
        # elegibles (corta antes de la Etapa B si no lo son), así que
        # `score is not None` es la misma señal, sin volver a consultarla.
        obs = {"ground_truth_change_pct": change_pct, "explosive": {"eligible": row["score"] is not None}}
        category = classifier.classify_observation(obs) or "NORMAL"
        pj.grade_sealed_prediction(date, row["symbol"], change_pct, category, now.isoformat())
        calificados += 1

        # Exit Journal: cierra el resumen objetivo de la trayectoria (si
        # se venía registrando durante la sesión regular) junto con la
        # calificación -- mismo momento, sin depender de un disparador
        # aparte. Protegido: si ya se cerró antes, no se recalcula.
        if not ej.is_closed(row["symbol"], date):
            ej.close_exit_summary(row["symbol"], date, entry_at=row["sealed_at"], window_closed_at=now.isoformat())

        # Memory Store: la observación real, con la categoría recién
        # confirmada. Solo si el símbolo sigue en el escaneo de hoy (si
        # salió del universo/candidatos, no hay métricas frescas de Radar
        # Explosivo para guardar -- se omite en vez de guardar datos
        # incompletos o de otro día).
        live_row = por_symbol.get(row["symbol"])
        if live_row is not None:
            metrics = live_row["explosive"]["metrics"]
            store.record_observation(
                symbol=row["symbol"],
                date=date,
                checkpoint_minutes=checkpoint_minutes,
                category=category,
                metrics=metrics,
                sector=None,  # Radar Explosivo no trae sector en `metrics` (viene de Quote.sector, no incluido ahí)
                industry=None,
                market_cap_bucket=rc.market_cap_bucket(metrics.get("market_cap")),
                session="regular",  # se califica en afterhours/closed, sobre el cierre de la sesión regular
                source_version="live_integration",
            )
            observaciones_nuevas += 1

    return f"calificados={calificados}/{len(pendientes)} observaciones_nuevas={observaciones_nuevas}"


def run_live_cycle(
    results: List[Dict[str, Any]],
    collector: Optional[DataCollector] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Punto de entrada único. Nunca lanza una excepción hacia el
    llamador -- cualquier error queda en el campo `error` del resumen que
    devuelve, para que `scan_worker.py` pueda loguearlo sin que el
    escaneo principal se vea afectado.

    `collector` debería ser el mismo `DataCollector` que `run_scan_once()`
    ya construyó para este ciclo (mismo proveedor configurado, misma
    caché) -- si no se pasa ninguno, se crea uno nuevo con el proveedor
    por defecto configurado (nunca Yahoo hardcodeado)."""
    now = now or datetime.now(timezone.utc)
    session = market_hours.get_session(now)
    date = market_hours.market_date(now)
    accion = "ninguna"

    try:
        collector = collector or DataCollector()

        if session == "premarket":
            ranking = build_live_ranking(results, now=now)
            # Regla de consenso: el Prediction Journal entero -- no solo el
            # candidato #1 -- excluye a quien Radar Explosivo rechazó.
            # `ranking` ya viene ordenado con los elegibles primero, pero
            # si hay menos de TOP_N_JOURNAL elegibles, un slice ciego
            # arrastraría inelegibles a las posiciones bajas -- se filtran
            # acá antes de tomar el top N.
            elegibles = [c for c in ranking if c.eligible_radar]
            journaled = [_to_journaled(i, c) for i, c in enumerate(elegibles[:TOP_N_JOURNAL], start=1)]
            pj.record_dynamic_snapshot(date, now.isoformat(), journaled)
            accion = "snapshot_dinamico"

            if market_hours.is_seal_window(now) and not pj.is_sealed(date):
                pj.seal_ranking(date, now.isoformat(), journaled)
                accion = "snapshot_dinamico+sellado"

        elif session == "regular":
            accion = _track_trajectory(date, now, results)

        elif session in ("afterhours", "closed") and pj.is_sealed(date):
            accion = _grade_pending(date, now, collector, results)

        return {"session": session, "date": date, "accion": accion, "error": None}
    except Exception as exc:
        return {"session": session, "date": date, "accion": "error", "error": str(exc)}
