"""Operator Learning Engine: aprende exclusivamente del comportamiento del operador.

Completamente independiente del Learning Engine (que aprende solo del
mercado). La única fuente de datos es Decision Journal, y solo a través de
su API de lectura (`get_trades()`) -- nunca toca su base directamente, y
nunca importa nada de `atlas.knowledge`. Esa ausencia de dependencia es la
prueba de que el conocimiento del operador nunca se mezcla con el
conocimiento del mercado.

Sin IA: cada análisis es una regla determinista y transparente sobre los
campos que ya registra Decision Journal. Igual que el resto de Atlas, evita
sacar conclusiones de muestras chicas (`MIN_SAMPLE_SIZE`).

De las 7 categorías de análisis definidas, dos (ventas demasiado tempranas
y ventas demasiado tardías) requieren saber qué hizo el precio *después*
de la operación -- un dato que Decision Journal no captura (solo guarda
buy_price/sell_price, no el máximo posterior). Implementarlas ahora
significaría inventar una señal que no existe en los datos reales, así que
quedan declaradas como interfaz (NotImplementedError) hasta que exista una
fuente de datos legítima para responderlas.
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from atlas.decision_journal.decision_journal import DecisionJournal, Trade

MIN_SAMPLE_SIZE = 5

ATLAS_TOP_RANK_THRESHOLD = 3


@dataclass(frozen=True)
class OperatorInsight:
    """Un hallazgo sobre el comportamiento del operador. Autoconocimiento, no una orden."""

    category: str
    title: str
    description: str
    evidence: Dict[str, Any]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def _closed_trades(trades: List[Trade]) -> List[Trade]:
    """Operaciones con resultado conocido (profit_loss_percent no nulo)."""
    return [t for t in trades if t.profit_loss_percent is not None]


def _win_rate(trades: List[Trade]) -> Optional[float]:
    if not trades:
        return None
    wins = sum(1 for t in trades if t.profit_loss_percent > 0)
    return round(wins / len(trades), 4)


def _avg_pl(trades: List[Trade]) -> Optional[float]:
    if not trades:
        return None
    return round(statistics.fmean(t.profit_loss_percent for t in trades), 4)


class OperatorLearningEngine:
    """Analiza el comportamiento del operador a partir de Decision Journal, y solo de eso."""

    def __init__(self, decision_journal: DecisionJournal) -> None:
        self._journal = decision_journal

    def _all_trades(self) -> List[Trade]:
        return self._journal.get_trades(limit=1_000_000)

    def analyze_time_windows(self) -> List[OperatorInsight]:
        """Horarios donde el operador obtiene mejores y peores resultados."""
        trades = _closed_trades(self._all_trades())
        if len(trades) < MIN_SAMPLE_SIZE:
            return [self._insufficient_evidence("horarios", len(trades))]

        by_hour: Dict[int, List[Trade]] = defaultdict(list)
        for trade in trades:
            hour = int(trade.time.split(":")[0])
            by_hour[hour].append(trade)

        stats = {
            hour: {"n": len(group), "win_rate": _win_rate(group), "avg_pl": _avg_pl(group)}
            for hour, group in by_hour.items()
            if len(group) >= MIN_SAMPLE_SIZE
        }
        if not stats:
            return [self._insufficient_evidence("horarios", len(trades), note="ningún horario alcanza la muestra mínima por separado")]

        best_hour = max(stats, key=lambda h: stats[h]["avg_pl"])
        worst_hour = min(stats, key=lambda h: stats[h]["avg_pl"])

        insights = [
            OperatorInsight(
                category="horarios",
                title=f"Mejor horario: {best_hour:02d}:00",
                description=f"En {stats[best_hour]['n']} operaciones a las {best_hour:02d}:00, "
                f"ganancia promedio {stats[best_hour]['avg_pl']:+.2f}% (win rate {stats[best_hour]['win_rate']*100:.0f}%).",
                evidence={"hour": best_hour, **stats[best_hour]},
            ),
        ]
        if worst_hour != best_hour:
            insights.append(
                OperatorInsight(
                    category="horarios",
                    title=f"Peor horario: {worst_hour:02d}:00",
                    description=f"En {stats[worst_hour]['n']} operaciones a las {worst_hour:02d}:00, "
                    f"ganancia promedio {stats[worst_hour]['avg_pl']:+.2f}% (win rate {stats[worst_hour]['win_rate']*100:.0f}%).",
                    evidence={"hour": worst_hour, **stats[worst_hour]},
                )
            )
        return insights

    def detect_recurring_errors(self) -> List[OperatorInsight]:
        """Motivos que se repiten en operaciones perdedoras."""
        trades = _closed_trades(self._all_trades())
        losses = [t for t in trades if t.profit_loss_percent < 0]
        if len(losses) < MIN_SAMPLE_SIZE:
            return [self._insufficient_evidence("errores_repetitivos", len(losses))]

        by_reason: Dict[str, List[Trade]] = defaultdict(list)
        for trade in losses:
            reason = trade.sell_reason or trade.buy_reason or "Sin motivo registrado"
            by_reason[reason].append(trade)

        insights = []
        for reason, group in sorted(by_reason.items(), key=lambda kv: len(kv[1]), reverse=True):
            if len(group) < 2:
                continue
            insights.append(
                OperatorInsight(
                    category="errores_repetitivos",
                    title=f"Motivo repetido en pérdidas: \"{reason}\"",
                    description=f"Este motivo aparece en {len(group)} de {len(losses)} operaciones perdedoras "
                    f"(pérdida promedio {_avg_pl(group):+.2f}%).",
                    evidence={"reason": reason, "count": len(group), "avg_pl": _avg_pl(group)},
                )
            )
        return insights or [OperatorInsight(
            category="errores_repetitivos", title="Sin motivos repetidos",
            description=f"Ninguno de los {len(losses)} motivos de pérdida se repite más de una vez.",
            evidence={"loss_count": len(losses)},
        )]

    def analyze_discipline(self) -> List[OperatorInsight]:
        """Disciplina, medida por el nivel de evidencia registrado en cada operación."""
        trades = _closed_trades(self._all_trades())
        if len(trades) < MIN_SAMPLE_SIZE:
            return [self._insufficient_evidence("disciplina", len(trades))]

        by_level: Dict[str, List[Trade]] = defaultdict(list)
        for trade in trades:
            level = trade.evidence_level or "Sin registrar"
            by_level[level].append(trade)

        unregistered = len(by_level.get("Sin registrar", []))
        insights = [
            OperatorInsight(
                category="disciplina",
                title="Nivel de evidencia por operación",
                description=f"{unregistered} de {len(trades)} operaciones ({unregistered/len(trades)*100:.0f}%) "
                "no tienen nivel de evidencia registrado.",
                evidence={
                    level: {"n": len(group), "win_rate": _win_rate(group), "avg_pl": _avg_pl(group)}
                    for level, group in by_level.items()
                },
            )
        ]
        return insights

    def analyze_atlas_compliance(self) -> List[OperatorInsight]:
        """Compara resultados de operaciones dentro vs. fuera del top del ranking de Atlas."""
        trades = _closed_trades(self._all_trades())
        if len(trades) < MIN_SAMPLE_SIZE:
            return [self._insufficient_evidence("cumplimiento_atlas", len(trades))]

        within_top = [
            t for t in trades
            if t.atlas_rank_at_time is not None and t.atlas_rank_at_time <= ATLAS_TOP_RANK_THRESHOLD
        ]
        within_top_ids = {t.id for t in within_top}
        outside_top = [t for t in trades if t.id not in within_top_ids]

        return [
            OperatorInsight(
                category="cumplimiento_atlas",
                title=f"Operaciones dentro del Top {ATLAS_TOP_RANK_THRESHOLD} de Atlas vs. fuera",
                description=(
                    f"Top {ATLAS_TOP_RANK_THRESHOLD}: {len(within_top)} operaciones, "
                    f"win rate {(_win_rate(within_top) or 0)*100:.0f}%. "
                    f"Fuera del Top {ATLAS_TOP_RANK_THRESHOLD} (o sin ranking registrado): {len(outside_top)} operaciones, "
                    f"win rate {(_win_rate(outside_top) or 0)*100:.0f}%."
                ),
                evidence={
                    "within_top": {"n": len(within_top), "win_rate": _win_rate(within_top), "avg_pl": _avg_pl(within_top)},
                    "outside_top": {"n": len(outside_top), "win_rate": _win_rate(outside_top), "avg_pl": _avg_pl(outside_top)},
                },
            )
        ]

    def analyze_performance_evolution(self) -> List[OperatorInsight]:
        """Evolución del desempeño a lo largo del tiempo, agrupado por mes."""
        trades = _closed_trades(self._all_trades())
        if len(trades) < MIN_SAMPLE_SIZE:
            return [self._insufficient_evidence("evolucion_desempeno", len(trades))]

        by_month: Dict[str, List[Trade]] = defaultdict(list)
        for trade in trades:
            by_month[trade.date[:7]].append(trade)  # "YYYY-MM"

        timeline = {
            month: {"n": len(group), "win_rate": _win_rate(group), "avg_pl": _avg_pl(group)}
            for month, group in sorted(by_month.items())
        }
        months = list(timeline.keys())
        trend = "insuficiente para determinar tendencia"
        if len(months) >= 2:
            first_half = [timeline[m]["avg_pl"] for m in months[: len(months) // 2] if timeline[m]["avg_pl"] is not None]
            second_half = [timeline[m]["avg_pl"] for m in months[len(months) // 2 :] if timeline[m]["avg_pl"] is not None]
            if first_half and second_half:
                trend = "mejorando" if statistics.fmean(second_half) > statistics.fmean(first_half) else "empeorando"

        return [
            OperatorInsight(
                category="evolucion_desempeno",
                title=f"Evolución mensual del desempeño ({trend})",
                description=f"{len(months)} mes(es) con operaciones cerradas. Tendencia: {trend}.",
                evidence={"timeline": timeline, "trend": trend},
            )
        ]

    def detect_early_exits(self) -> List[OperatorInsight]:
        """Ventas demasiado tempranas: requiere saber qué hizo el precio después de vender."""
        raise NotImplementedError(
            "Operator Learning Engine: detectar ventas tempranas requiere el precio máximo "
            "posterior a la venta, un dato que Decision Journal no captura hoy (solo guarda "
            "buy_price/sell_price). No implementado para evitar inventar una señal inexistente."
        )

    def detect_late_exits(self) -> List[OperatorInsight]:
        """Ventas demasiado tardías: mismo problema de datos que detect_early_exits()."""
        raise NotImplementedError(
            "Operator Learning Engine: detectar ventas tardías requiere el precio posterior "
            "a la venta, un dato que Decision Journal no captura hoy. No implementado para "
            "evitar inventar una señal inexistente."
        )

    def generate_report(self) -> List[OperatorInsight]:
        """Corre todos los análisis disponibles (los dos que requieren datos inexistentes
        se omiten del reporte, no se fuerzan)."""
        insights: List[OperatorInsight] = []
        for method in (
            self.analyze_time_windows,
            self.detect_recurring_errors,
            self.analyze_discipline,
            self.analyze_atlas_compliance,
            self.analyze_performance_evolution,
        ):
            insights.extend(method())
        return insights

    @staticmethod
    def _insufficient_evidence(category: str, sample_size: int, note: str = "") -> OperatorInsight:
        return OperatorInsight(
            category=category,
            title="Evidencia insuficiente",
            description=f"Solo hay {sample_size} operación(es) con resultado cerrado "
            f"(mínimo requerido: {MIN_SAMPLE_SIZE}).{' ' + note if note else ''}",
            evidence={"sample_size": sample_size, "min_sample_size": MIN_SAMPLE_SIZE},
        )
