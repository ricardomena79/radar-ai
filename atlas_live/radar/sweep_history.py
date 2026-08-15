"""Historial en memoria de barridos, por símbolo (2026-08-14).

Varias puertas de detección (`candidate_gates.py`) necesitan comparar el
barrido actual contra barridos anteriores del mismo día -- aceleración,
"recién empieza a despertar", "pierde fuerza y luego recupera", "viene
subiendo desde el premarket". Nada de esto se persiste en disco: es un
buffer corto (por defecto ~40 barridos por símbolo) que vive mientras el
proceso está arriba y se reinicia cada día de mercado nuevo -- no es la
fuente de verdad del día (eso es `candidate_registry`, que sí persiste),
es solo la ventana de comparación que necesitan los gates.
"""

import threading
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional

DEFAULT_MAX_PER_SYMBOL = 40  # a ~45s de cadencia, ~30 min de historia


@dataclass(frozen=True)
class SweepSnapshot:
    """Una foto de un símbolo en UN barrido -- solo campos que ya vienen en
    el Quote (nada calculado con red adicional)."""

    sweep_id: str
    observed_at: str  # ISO 8601
    price: Optional[float]
    change_pct: Optional[float]
    volume: Optional[int]
    average_volume: Optional[int]
    relative_volume: Optional[float]
    dollar_volume: Optional[float]


class SweepHistory:
    """Buffer circular por símbolo, protegido por lock (mismo criterio que
    `scan_worker._State`: un solo escritor -- el hilo del radar -- pero
    lecturas posibles desde un endpoint de diagnóstico)."""

    def __init__(self, max_per_symbol: int = DEFAULT_MAX_PER_SYMBOL) -> None:
        self._lock = threading.Lock()
        self._by_symbol: Dict[str, Deque[SweepSnapshot]] = {}
        self.max_per_symbol = max_per_symbol
        self.current_market_date: Optional[str] = None

    def push(self, symbol: str, snapshot: SweepSnapshot) -> None:
        with self._lock:
            dq = self._by_symbol.setdefault(symbol, deque(maxlen=self.max_per_symbol))
            dq.append(snapshot)

    def get(self, symbol: str) -> List[SweepSnapshot]:
        """Historial del símbolo, más viejo primero, SIN incluir el barrido
        que se acaba de empujar (para eso está `push` con el snapshot
        actual aparte) -- el llamador decide el orden de las operaciones."""
        with self._lock:
            return list(self._by_symbol.get(symbol, ()))

    def reset_for_new_day(self, market_date: str) -> None:
        """Se llama al detectar un `market_date` distinto al que traía el
        buffer -- evita comparar el primer barrido de hoy contra el último
        de ayer (aceleración/despertar solo tienen sentido intradía)."""
        with self._lock:
            self._by_symbol.clear()
            self.current_market_date = market_date

    def symbols_tracked(self) -> int:
        with self._lock:
            return len(self._by_symbol)
