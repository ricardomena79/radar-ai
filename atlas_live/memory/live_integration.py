"""Integración en tiempo real -- conecta el ranking que `scan_worker.py`
ya calcula en cada ciclo con el Memory Engine (Ranking Score + Prediction
Journal), según la sesión de mercado (`market_hours.py`).

**No modifica Radar Explosivo ni Atlas Core.** Solo LEE `results`, la
misma lista de filas que `scan_worker.py` ya produce cada 5 minutos
(`row['explosive']['metrics'/'score'/'eligible']`) -- no repite ninguna
llamada de red ni recalcula nada del motor. Vive enteramente en
`atlas_live/memory/`.

Punto de entrada único: `run_live_cycle(results)`, pensado para llamarse
desde `run_scan_once()` al final de cada ciclo, envuelto en su propio
try/except desde el llamador -- este módulo nunca debe tumbar el escaneo
principal (mismo principio ya aplicado en `scan_worker.py` para
`recorder.record_decision`).

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
    grade_sealed_prediction`, con tiempo de anticipación), y cierra el
    resumen objetivo de su trayectoria en el Exit Journal
    (`exit_journal.close_exit_summary`).
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.providers.yahoo_finance import YahooFinanceProvider

from atlas_live.memory import base_rates as br
from atlas_live.memory import calibration_advisor as ca
from atlas_live.memory import classifier
from atlas_live.memory import demo_ranking
from atlas_live.memory import exit_journal as ej
from atlas_live.memory import market_hours
from atlas_live.memory import prediction_journal as pj
from atlas_live.memory import ranking_score as rs
from atlas_live.predictive_engine import prediction_log
from atlas_live.predictive_engine.capabilities import entry_window
from atlas_live.predictive_engine.capabilities.entry_window import EntryWindowCapability
from atlas_live.predictive_engine.engine import PredictionEngine

# Motor Predictivo (Fase 1.1, Sprint 1, 2026-08-06, ver DECISIONES.md) --
# una sola instancia de proceso, igual que `_evidence_cache`: registrar
# una capacidad nueva más adelante es una línea acá, no un rediseño de
# este módulo.
_prediction_engine = PredictionEngine()
_prediction_engine.register(EntryWindowCapability())

CATEGORY = "EXPLOSION"
TOP_N_JOURNAL = 20  # mismo top_n que ya usa Radar Explosivo en explosive_config.json

# La evidencia (tasas base + propuestas confiables) solo cambia cuando el
# Memory Store crece (Entregable 7, todavía no conectado a este flujo) --
# recalcularla sobre 73.123 observaciones en cada ciclo de 5 minutos sería
# caro e innecesario. Se calcula una vez por proceso; `refresh_evidence()`
# la fuerza a recalcular si hace falta (ej. después de un backfill nuevo).
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
    """"Recalibrar sus estadísticas" (decisión de arquitectura, 2026-08-02):
    la evidencia (tasas base + propuestas confiables) se recalcula
    automáticamente una vez por cada día de mercado nuevo -- no en cada
    ciclo de 5 minutos (sería carísimo sobre decenas de miles de
    observaciones) ni una sola vez por proceso (dejaría de "aprender" de
    lo que se va calificando en el Prediction Journal día a día)."""
    today = market_hours.market_date(now)
    if not _evidence_cache or _evidence_cache.get("computed_on") != today:
        refresh_evidence(computed_on=today)
    return _evidence_cache


def _to_journaled(rank: int, c: demo_ranking.RankedCandidate) -> pj.JournaledCandidate:
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


def serialize_ranked_candidate(c: demo_ranking.RankedCandidate) -> Dict[str, Any]:
    """Convierte un RankedCandidate a un dict JSON-serializable, para la
    Cabina del Piloto (Panel 2 en adelante) -- `semaforo` se traduce de
    emoji a palabra (verde/amarillo/rojo) porque así es como ya lo espera
    el frontend (mismas claves que usaba mock_data.js)."""
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
    """Estado real del Memory Engine para la Cabina del Piloto (Panel 9):
    tamaño del Memory Store, tasa base poblacional y las condiciones
    confiables vigentes (Entregable 5), reutilizando la misma evidencia
    cacheada por día que ya usa `build_live_ranking` -- no recalcula nada
    nuevo, solo la sirve en un formato JSON-serializable."""
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


def build_live_ranking(results: List[Dict[str, Any]], now: Optional[datetime] = None) -> List[demo_ranking.RankedCandidate]:
    """Aplica Memory Engine + Ranking Score sobre `results` -- el ranking
    que `scan_worker.py` ya calculó en este ciclo. No recalcula nada de
    Radar Explosivo, solo lo lee (mismo mecanismo que `demo_ranking.py`,
    aplicado a filas en vivo en vez de un archivo histórico)."""
    evidence = _evidence(now)
    ranked = [
        demo_ranking.build_ranked_candidate(
            row, evidence["proposals"], evidence["condition_value_cache"], evidence["baseline"], CATEGORY
        )
        for row in results
        if row.get("explosive") is not None
    ]
    # Regla de consenso (2026-08-03, ver demo_ranking.build_ranking() para
    # el detalle completo): `eligible_radar` ordena primero -- esto
    # también protege el sellado del Prediction Journal (`_to_journaled`
    # toma los primeros TOP_N_JOURNAL de este mismo `ranked`), no solo la
    # Cabina. Un símbolo que Radar Explosivo rechazó nunca puede quedar
    # sellado como la predicción oficial del día.
    ranked.sort(key=lambda c: (c.eligible_radar, c.ranking_score), reverse=True)
    return ranked


def _track_trajectory(date: str, now: datetime, results: List[Dict[str, Any]]) -> str:
    """Exit Journal -- durante la sesión regular, registra un punto de la
    trayectoria (rendimiento observado en este ciclo) para cada símbolo
    del ranking oficial ya sellado. No calcula nada -- solo guarda el dato
    crudo (ver `exit_journal.py`, propuesta del Exit Journal, 2026-08-02)."""
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


def _predict_entries(date: str, now: datetime, results: List[Dict[str, Any]]) -> str:
    """Motor Predictivo (Fase 1.1) -- para cada candidato que Radar
    Explosivo ya marcó `eligible=True` este ciclo, invoca la capacidad
    `entry_window` y registra automáticamente la predicción
    (`prediction_log.record_prediction`). Sprint 3 (2026-08-06): pasa la
    misma evidencia del Memory Engine que ya usa `build_live_ranking` (sin
    recalcularla dos veces) para que `entry_window` pueda derivar la
    `evidence_condition` de cada candidato y agregar sobre la base
    histórica. No repite ningún cálculo de Radar Explosivo -- solo lee
    `results`, igual que `_track_trajectory`. Un candidato que falla no
    debe bloquear al resto."""
    memory_evidence = _evidence(now)
    elegibles = [
        row for row in results
        if row.get("explosive") is not None and row["explosive"].get("eligible")
    ]
    predichas = 0
    for row in elegibles:
        try:
            metrics = row["explosive"]["metrics"]
            candidate = {
                "symbol": row["symbol"],
                "date": date,
                "score": row["explosive"].get("score"),
                "eligible": True,
                "metrics": metrics,
            }
            evidence = entry_window.gather_evidence(candidate, memory_evidence, now)
            result = _prediction_engine.compute("entry_window", candidate, evidence)
            prediction_log.record_prediction(
                symbol=row["symbol"],
                date=date,
                predicted_at=now.isoformat(),
                result=result,
                feature_snapshot=prediction_log.build_feature_snapshot(metrics, now),
            )
            predichas += 1
        except Exception:
            continue
    return f"predicciones_registradas={predichas}/{len(elegibles)}"


def _grade_pending(date: str, now: datetime) -> str:
    sealed = pj.get_sealed_predictions(date)
    pendientes = [row for row in sealed if row["graded_at"] is None]
    if not pendientes:
        return "sin_pendientes"

    collector = DataCollector(YahooFinanceProvider())
    calificados = 0
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

    return f"calificados={calificados}/{len(pendientes)}"


def run_live_cycle(results: List[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Punto de entrada único. Nunca lanza una excepción hacia el
    llamador -- cualquier error queda en el campo `error` del resumen que
    devuelve, para que `scan_worker.py` pueda loguearlo sin que el
    escaneo principal se vea afectado."""
    now = now or datetime.now(timezone.utc)
    session = market_hours.get_session(now)
    date = market_hours.market_date(now)
    accion = "ninguna"
    prediccion = "ninguna"

    try:
        if session == "premarket":
            ranking = build_live_ranking(results, now=now)
            # Regla de consenso (2026-08-03): el Prediction Journal entero
            # -- no solo el candidato #1 -- excluye a quien Radar Explosivo
            # rechazó. `ranking` ya viene ordenado con los elegibles
            # primero, pero si hay menos de TOP_N_JOURNAL elegibles, un
            # slice ciego arrastraría inelegibles a las posiciones bajas
            # -- se filtran acá antes de tomar el top N.
            elegibles = [c for c in ranking if c.eligible_radar]
            journaled = [_to_journaled(i, c) for i, c in enumerate(elegibles[:TOP_N_JOURNAL], start=1)]
            pj.record_dynamic_snapshot(date, now.isoformat(), journaled)
            accion = "snapshot_dinamico"

            if market_hours.is_seal_window(now) and not pj.is_sealed(date):
                pj.seal_ranking(date, now.isoformat(), journaled)
                accion = "snapshot_dinamico+sellado"

            # Motor Predictivo (Fase 1.1, Sprint 1): "Atlas debe medir desde
            # el primer momento en que detecta una oportunidad, no desde la
            # apertura del mercado regular" -- se invoca ya en premarket,
            # no solo durante la sesión regular. Campo nuevo y aparte de
            # `accion` -- no se toca el contrato ya existente de ese string
            # (varios tests lo comparan con `==`).
            prediccion = _predict_entries(date, now, results)

        elif session == "regular":
            accion = _track_trajectory(date, now, results)
            prediccion = _predict_entries(date, now, results)

        elif session in ("afterhours", "closed") and pj.is_sealed(date):
            accion = _grade_pending(date, now)

        return {"session": session, "date": date, "accion": accion, "prediccion": prediccion, "error": None}
    except Exception as exc:
        return {"session": session, "date": date, "accion": "error", "prediccion": "error", "error": str(exc)}
