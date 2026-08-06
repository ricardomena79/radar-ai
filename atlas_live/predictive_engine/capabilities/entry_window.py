"""Motor Predictivo -- capacidad `entry_window` ("ventana óptima de
entrada"), aprobada 2026-08-06 (ver DECISIONES.md, Fase 1.1).

Pregunta que responde, con evidencia y nunca con un número inventado:
dado un candidato que Radar Explosivo ya marcó `eligible=True`, ¿cuántos
minutos faltan, históricamente, para que comience el movimiento? Dos
hechos distintos, ambos ya derivables de una trayectoria del Exit Journal
(`atlas_live/memory/exit_journal.py`):
  - "señal" = primer muestreo con `eligible=1` (`derive_signal_start`).
  - "movimiento" = primer muestreo con |rendimiento| >= umbral
    (`derive_movement_start`, mismo umbral que ya usa Radar Explosivo --
    `min_abs_gap_or_change_pct` de `explosive_config.json`, no uno nuevo).
La ventana óptima de entrada es la distancia entre esos dos hechos
(`movimiento - señal`, en minutos -- puede ser NEGATIVA cuando el
movimiento ya empezó antes de que Atlas detectara la elegibilidad; ese
caso real y honesto queda en la muestra, no se descarta), agregada
(mediana, P25/P75) sobre trayectorias históricas agrupadas por
`evidence_condition` (decisión explícita del usuario, 2026-08-05: no
dividir todavía por sesión premarket/regular/after-hours).

**`evidence_condition` no se guarda en `trajectory_samples`.** Se deriva
bajo demanda, reutilizando `demo_ranking.build_ranked_candidate()` (misma
lógica que ya clasifica el ranking en vivo, no una reimplementación) sobre
la fila original del símbolo/día en `atlas_live/backtest/results_v1/*.json`
-- así se evita tocar el esquema de `exit_journal.py` o su ruta de
escritura mientras la reconstrucción retroactiva (Sprint 2) puede seguir
corriendo en paralelo sin ninguna coordinación especial.

**Se actualiza sola cuando crece la base histórica.** La agregación por
condición se cachea por proceso, invalidada por
`exit_journal.count_trajectory_samples()` (Sprint 3, 2026-08-06): cuando
ese número crece -- porque el lote de reconstrucción del Sprint 2 sigue
escribiendo, o porque el Exit Journal en vivo agrega un día más -- la
próxima llamada recalcula sola, sin ningún cambio de código ni reinicio
manual.

Sprint 1 (2026-08-06): solo la estructura del envelope, siempre
"insuficiente" (la base histórica todavía no existía). Sprint 3
(2026-08-06): el algoritmo real -- mediana, P25, P75, confianza por
tamaño de muestra, y una recomendación ("esperar" / "comprar_ahora" /
"movimiento_pudo_haber_empezado") comparando cuánto hace que este
candidato es elegible hoy contra ese rango histórico.
"""

import json
import os
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from atlas_live.explosive_config import load_config as load_explosive_config
from atlas_live.memory import demo_ranking
from atlas_live.memory import exit_journal as ej
from atlas_live.predictive_engine import prediction_log
from atlas_live.predictive_engine.engine import CapabilityResult

MIN_SAMPLE_SIZE = 10  # mismo umbral que atlas_live.memory.base_rates.MIN_SAMPLE_SIZE -- mismo criterio de rigor del proyecto, no uno nuevo inventado

CATEGORY = "EXPLOSION"  # mismo valor que atlas_live.memory.live_integration.CATEGORY -- duplicado a propósito para no importar ese módulo (que ya importa este) y no crear un ciclo

HISTORICAL_SOURCE_DIR = "atlas_live/backtest/results_v1"  # mismo directorio que usa reconstruct_trajectories.py -- ya versionado en git, disponible en Railway sin migración aparte

_day_file_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
_windows_cache: Dict[str, Any] = {"total_samples": None, "by_condition": {}}


def _movement_threshold_pct() -> float:
    return load_explosive_config()["gates"]["min_abs_gap_or_change_pct"]


def _load_historical_row(date_str: str, symbol: str) -> Optional[Dict[str, Any]]:
    """Fila original (`row['explosive']['metrics'/'score'/'eligible']`) de
    `symbol` en el escaneo histórico de `date_str` -- para poder derivarle
    `evidence_condition` con la misma lógica que ya usa el ranking en vivo.
    `None` si esa trayectoria no vino de una reconstrucción histórica (ej.
    viene del seguimiento en vivo) -- se excluye de la agregación por
    ahora, no se fabrica una condición para ella."""
    if date_str not in _day_file_cache:
        path = os.path.join(HISTORICAL_SOURCE_DIR, f"{date_str}.json")
        if not os.path.exists(path):
            _day_file_cache[date_str] = {}
        else:
            with open(path, "r", encoding="utf-8") as f:
                scan = json.load(f)
            _day_file_cache[date_str] = {row["symbol"]: row for row in scan["rows"]}
    return _day_file_cache[date_str].get(symbol)


def _signal_to_movement_minutes(trajectory: List[Dict[str, Any]], movement_threshold_pct: float) -> Optional[float]:
    signal = ej.derive_signal_start(trajectory)
    if signal.timestamp is None:
        return None
    movement = ej.derive_movement_start(trajectory, movement_threshold_pct)
    if movement.timestamp is None:
        return None
    t0 = datetime.fromisoformat(signal.timestamp)
    t1 = datetime.fromisoformat(movement.timestamp)
    return (t1 - t0).total_seconds() / 60.0


def _compute_all_windows(memory_evidence: Dict[str, Any]) -> Dict[Optional[str], List[float]]:
    """Recorre TODA la base histórica del Exit Journal una vez, agrupando
    la ventana señal->movimiento de cada símbolo/día por su
    `evidence_condition`. Cara de recalcular (recorre todo) a propósito --
    se invoca solo cuando `_windows_cache` detecta que la base creció."""
    threshold = _movement_threshold_pct()
    by_condition: Dict[Optional[str], List[float]] = {}
    for symbol, date_str in ej.get_all_symbol_dates():
        row = _load_historical_row(date_str, symbol)
        if row is None:
            continue
        ranked = demo_ranking.build_ranked_candidate(
            row, memory_evidence["proposals"], memory_evidence["condition_value_cache"],
            memory_evidence["baseline"], CATEGORY,
        )
        trajectory = ej.get_trajectory(symbol, date_str)
        window = _signal_to_movement_minutes(trajectory, threshold)
        if window is None:
            continue
        by_condition.setdefault(ranked.evidence_condition, []).append(window)
    return by_condition


def _windows_for_condition(evidence_condition: Optional[str], memory_evidence: Dict[str, Any]) -> List[float]:
    total_samples = ej.count_trajectory_samples()
    if _windows_cache["total_samples"] != total_samples:
        _windows_cache["by_condition"] = _compute_all_windows(memory_evidence)
        _windows_cache["total_samples"] = total_samples
    return _windows_cache["by_condition"].get(evidence_condition, [])


def _elapsed_minutes_since_first_seen(symbol: str, date: str, now: datetime) -> float:
    """Cuántos minutos hace que Atlas viene registrando a este candidato
    como elegible hoy -- proxy real de "hace cuánto apareció la señal" para
    un candidato EN VIVO (no reconstruido): la primera vez que
    `prediction_log` lo registró hoy (Sprint 1 ya deja esa traza). Igual
    granularidad de ~5 minutos (un ciclo de escaneo) que ya acepta
    `prediction_journal.py` para `first_detected_at` -- no un dato nuevo
    inventado, el mismo límite de precisión del resto del proyecto. `0.0`
    si es la primera vez que se ve hoy (dato real: recién detectado)."""
    previas = prediction_log.get_predictions(symbol=symbol, date=date, capability="entry_window", limit=1000)
    if not previas:
        return 0.0
    primera_at = min(p["predicted_at"] for p in previas)
    return (now - datetime.fromisoformat(primera_at)).total_seconds() / 60.0


def gather_evidence(candidate: Dict[str, Any], memory_evidence: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """Reúne, para este candidato, su `evidence_condition` de hoy (misma
    lógica del ranking en vivo) y las ventanas señal->movimiento ya
    observadas para esa condición en toda la base histórica disponible en
    este momento -- crece sola a medida que el Sprint 2 (y luego el
    seguimiento en vivo) sigan escribiendo el Exit Journal."""
    row = {
        "symbol": candidate["symbol"],
        "explosive": {
            "metrics": candidate["metrics"],
            "score": candidate.get("score"),
            "eligible": candidate.get("eligible", True),
        },
    }
    ranked = demo_ranking.build_ranked_candidate(
        row, memory_evidence["proposals"], memory_evidence["condition_value_cache"],
        memory_evidence["baseline"], CATEGORY,
    )
    windows = _windows_for_condition(ranked.evidence_condition, memory_evidence)
    return {
        "evidence_condition": ranked.evidence_condition,
        "sample_size": len(windows),
        "windows_minutes": windows,
        "elapsed_minutes_since_signal": _elapsed_minutes_since_first_seen(candidate["symbol"], candidate["date"], now),
    }


def _confidence_tier(sample_size: int) -> str:
    """Umbrales de tamaño de muestra, no un intervalo estadístico formal
    (a diferencia del límite de Wilson que ya usa `base_rates.py` para
    proporciones -- una mediana de minutos no es una proporción, no se le
    aplica esa fórmula). Documentado como lo que es: un criterio simple de
    orden de magnitud, no una afirmación de rigor que no se tiene."""
    if sample_size < MIN_SAMPLE_SIZE:
        return "insuficiente"
    if sample_size < 30:
        return "baja"
    if sample_size < 100:
        return "media"
    return "alta"


def _percentiles(windows: List[float]) -> Tuple[float, float, float]:
    """(P25, mediana, P75) -- `statistics.quantiles` de la librería
    estándar, sin reimplementar la interpolación a mano."""
    p25, p50, p75 = statistics.quantiles(windows, n=4, method="inclusive")
    return p25, p50, p75


def _recommendation(elapsed_minutes: float, p25: float, p75: float) -> str:
    if elapsed_minutes < p25:
        return "esperar"
    if elapsed_minutes <= p75:
        return "comprar_ahora"
    return "movimiento_pudo_haber_empezado"


class EntryWindowCapability:
    name = "entry_window"

    def compute(self, candidate: Dict[str, Any], evidence: Dict[str, Any]) -> CapabilityResult:
        sample_size = evidence.get("sample_size", 0)
        evidence_condition = evidence.get("evidence_condition")

        if sample_size < MIN_SAMPLE_SIZE:
            return CapabilityResult(
                capability=self.name,
                recommendation=None,
                value=None,
                unit="minutos",
                confidence="insuficiente",
                sample_size=sample_size,
                evidence_condition=evidence_condition,
                explanation=(
                    f"Ventana óptima de entrada: solo {sample_size} caso(s) histórico(s) para la "
                    f"condición {evidence_condition or 'sin condición confiable'!r} (mínimo "
                    f"{MIN_SAMPLE_SIZE}) -- sin evidencia suficiente todavía para estimar mediana o "
                    f"rango. Crece automáticamente con la reconstrucción histórica y el seguimiento "
                    f"en vivo."
                ),
            )

        p25, p50, p75 = _percentiles(evidence["windows_minutes"])
        elapsed = evidence["elapsed_minutes_since_signal"]
        recommendation = _recommendation(elapsed, p25, p75)

        return CapabilityResult(
            capability=self.name,
            recommendation=recommendation,
            value=p50,
            unit="minutos",
            confidence=_confidence_tier(sample_size),
            sample_size=sample_size,
            range_low=p25,
            range_high=p75,
            evidence_condition=evidence_condition,
            explanation=(
                f"Ventana óptima de entrada (mediana): {p50:.1f} min desde que Atlas detecta "
                f"elegibilidad hasta que históricamente comienza el movimiento (rango P25-P75: "
                f"{p25:.1f} a {p75:.1f} min, n={sample_size}, condición="
                f"{evidence_condition or 'sin condición confiable'!r}). Este candidato lleva "
                f"{elapsed:.1f} min como elegible hoy -> {recommendation}."
            ),
        )
