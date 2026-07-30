"""Market Context Engine: fotografía del contexto general del mercado en un momento dado.

No genera señales ni decide nada: solo mide el entorno (índices, VIX,
cripto, ETF sectorial, liderazgo sectorial, calendario) para que la
Knowledge Base pueda guardarlo junto a cada evento y predicción. El
objetivo es acumular historia para descubrir correlaciones más adelante
(ej. "¿las explosiones ocurren más con VIX alto?"), no para actuar hoy.

Reutiliza (sin modificarlos) DataCollector y, si se provee, un
MoneyFlowEngine ya escaneado -- igual que hace Decision Engine con el
Money Flow Score de un símbolo.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from atlas.data.collectors.data_collector import DataCollector
from atlas.data.providers.yahoo_finance import YahooFinanceProvider
from atlas.engine.money_flow_engine import MoneyFlowEngine

SPY = "SPY"
QQQ = "QQQ"
IWM = "IWM"
VIX = "^VIX"
BTC = "BTC-USD"

# SPDR sector ETFs, mapeo fijo y público (no es IA, es una tabla de referencia).
SECTOR_ETF_MAP = {
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Financials": "XLF",
    "Energy": "XLE",
    "Healthcare": "XLV",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Utilities": "XLU",
    "Communication Services": "XLC",
    "Real Estate": "XLRE",
}


def _is_earnings_season(reference: datetime) -> bool:
    """Heurística aproximada (no un calendario real): temporada de resultados en
    EE.UU. suele concentrarse ~4 semanas a partir de mediados de enero/abril/
    julio/octubre. Es una regla fija, no una consulta a una fuente real."""
    month, day = reference.month, reference.day
    if month in (1, 4, 7, 10) and day >= 10:
        return True
    if month in (2, 5, 8, 11) and day <= 15:
        return True
    return False


@dataclass(frozen=True)
class MarketContext:
    """Fotografía del contexto de mercado en un instante dado."""

    spy_price: Optional[float] = None
    spy_change_percent: Optional[float] = None
    qqq_price: Optional[float] = None
    qqq_change_percent: Optional[float] = None
    iwm_price: Optional[float] = None
    iwm_change_percent: Optional[float] = None
    vix_price: Optional[float] = None
    vix_change_percent: Optional[float] = None
    btc_price: Optional[float] = None
    btc_change_percent: Optional[float] = None
    sector_etf_symbol: Optional[str] = None
    sector_etf_change_percent: Optional[float] = None
    leading_sector: Optional[str] = None
    leading_industry: Optional[str] = None
    sector_money_flow_score: Optional[float] = None
    day_of_week: Optional[str] = None
    month: Optional[str] = None
    earnings_season: Optional[bool] = None


class MarketContextEngine:
    """Calcula el contexto general de mercado; no persiste nada por sí mismo."""

    def __init__(self, collector: Optional[DataCollector] = None) -> None:
        self._collector = collector or DataCollector(YahooFinanceProvider())

    def _safe_quote(self, symbol: str):
        try:
            return self._collector.get_quote(symbol)
        except Exception:
            # Un índice/cripto sin datos disponibles no debe impedir capturar
            # el resto del contexto.
            return None

    def get_context(
        self,
        sector: Optional[str] = None,
        money_flow_engine: Optional[MoneyFlowEngine] = None,
        reference_time: Optional[datetime] = None,
    ) -> MarketContext:
        """Captura el contexto de mercado actual, opcionalmente centrado en un sector."""
        spy = self._safe_quote(SPY)
        qqq = self._safe_quote(QQQ)
        iwm = self._safe_quote(IWM)
        vix = self._safe_quote(VIX)
        btc = self._safe_quote(BTC)

        sector_etf_symbol = SECTOR_ETF_MAP.get(sector) if sector else None
        sector_etf_quote = self._safe_quote(sector_etf_symbol) if sector_etf_symbol else None

        leading_sector: Optional[str] = None
        leading_industry: Optional[str] = None
        sector_money_flow_score: Optional[float] = None

        if money_flow_engine is not None:
            top_sectors = money_flow_engine.top(1)
            if top_sectors:
                leading_sector = top_sectors[0].name

            top_industries = money_flow_engine.top_industries(1)
            if top_industries:
                leading_industry = top_industries[0].name

            if sector:
                for group in money_flow_engine.top(10_000):
                    if group.name == sector:
                        sector_money_flow_score = group.money_flow_score
                        break

        dt = reference_time or datetime.now()

        return MarketContext(
            spy_price=spy.last_price if spy else None,
            spy_change_percent=spy.change_percent if spy else None,
            qqq_price=qqq.last_price if qqq else None,
            qqq_change_percent=qqq.change_percent if qqq else None,
            iwm_price=iwm.last_price if iwm else None,
            iwm_change_percent=iwm.change_percent if iwm else None,
            vix_price=vix.last_price if vix else None,
            vix_change_percent=vix.change_percent if vix else None,
            btc_price=btc.last_price if btc else None,
            btc_change_percent=btc.change_percent if btc else None,
            sector_etf_symbol=sector_etf_symbol,
            sector_etf_change_percent=sector_etf_quote.change_percent if sector_etf_quote else None,
            leading_sector=leading_sector,
            leading_industry=leading_industry,
            sector_money_flow_score=sector_money_flow_score,
            day_of_week=dt.strftime("%A"),
            month=dt.strftime("%B"),
            earnings_season=_is_earnings_season(dt),
        )
