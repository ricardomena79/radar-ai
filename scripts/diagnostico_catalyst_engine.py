"""Smoke test EN VIVO del Motor de Catalizadores (2026-08-23, Fase 11 del
plan aprobado) -- consulta Finnhub real para MRNA/BNTX/ZYME/GRRR/NSSC/
IOVA/IBRX e **imprime** lo que encuentra ese día real, nunca afirma de
antemano qué debería haber (a diferencia de los tests unitarios de
`atlas_live/catalyst/`, que sí usan fixtures sintéticas con expectativas
fijas). No modifica ninguna base -- usa `FinnhubProvider` y el
clasificador/scorer directamente, sin pasar por `catalyst_registry.py`.

Uso: python scripts/diagnostico_catalyst_engine.py
"""

import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

from atlas_live.catalyst import catalyst_classifier as ccl
from atlas_live.catalyst import catalyst_provider as prov
from atlas_live.catalyst import catalyst_score as csc

TICKERS = ["MRNA", "BNTX", "ZYME", "GRRR", "NSSC", "IOVA", "IBRX"]
NEWS_LOOKBACK_DAYS = 14
CALENDAR_DAYS_AHEAD = 14


def main() -> int:
    provider = prov.build_catalyst_provider()
    if provider is None:
        print("FINNHUB_API_KEY no configurada -- no se puede correr el smoke test en vivo.")
        return 1

    now = datetime.now(timezone.utc)
    desde_noticias = (now - timedelta(days=NEWS_LOOKBACK_DAYS)).date().isoformat()
    hasta = now.date().isoformat()

    print(f"=== Smoke test Motor de Catalizadores -- {now.isoformat()} ===\n")

    print(f"--- Calendario de earnings, {hasta} a {(now + timedelta(days=CALENDAR_DAYS_AHEAD)).date().isoformat()} (universo completo, 1 llamada) ---")
    try:
        calendario = provider.get_earnings_calendar(hasta, (now + timedelta(days=CALENDAR_DAYS_AHEAD)).date().isoformat())
    except Exception as exc:  # noqa: BLE001 -- diagnóstico, se quiere ver cualquier fallo real
        print(f"  ERROR consultando calendario: {exc}")
        calendario = []
    calendario_por_ticker = {row.get("symbol"): row for row in calendario}
    relevantes = [row for row in calendario if row.get("symbol") in TICKERS]
    print(f"  Filas totales en el rango: {len(calendario)}")
    if relevantes:
        for row in relevantes:
            print(f"  {row.get('symbol')}: earnings {row.get('date')} ({row.get('hour')})")
    else:
        print(f"  Ninguno de {TICKERS} tiene earnings programado en este rango.")
    print()

    for ticker in TICKERS:
        print(f"--- {ticker} ---")
        try:
            noticias = provider.get_company_news(ticker, desde_noticias, hasta)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR consultando noticias: {exc}")
            continue

        if not noticias:
            print(f"  Sin noticias en los últimos {NEWS_LOOKBACK_DAYS} días.")
        else:
            print(f"  {len(noticias)} noticia(s) encontrada(s), mostrando hasta 5 más recientes:")
            for item in sorted(noticias, key=lambda n: n.get("datetime", 0), reverse=True)[:5]:
                headline = item.get("headline") or "(sin titular)"
                clasificado = ccl.classify_catalyst_type(headline, item.get("summary"))
                published_at = (
                    datetime.fromtimestamp(item["datetime"], tz=timezone.utc)
                    if item.get("datetime") else None
                )
                lifecycle = ccl.classify_catalyst_lifecycle(
                    event_date=None, published_at=published_at, now=now,
                    price_change_since_published_pct=None,  # sin cruce de precio en este script standalone
                )
                score = csc.catalyst_score(
                    catalyst_type=clasificado.catalyst_type, importance=clasificado.importance,
                    lifecycle_state=lifecycle, direction=clasificado.direction,
                )
                print(f"    [{published_at.isoformat() if published_at else '??'}] {headline}")
                print(f"      -> tipo={clasificado.catalyst_type} direccion={clasificado.direction} "
                      f"importancia={clasificado.importance} confianza={clasificado.confidence} "
                      f"lifecycle={lifecycle} catalyst_score={score:.1f}"
                      "  (SIN cruce de precio real -- este script no consulta cotizaciones)")

        if ticker in calendario_por_ticker:
            row = calendario_por_ticker[ticker]
            print(f"  Calendario: earnings {row.get('date')} ({row.get('hour')})")
        print()

    print("=== Fin del smoke test -- resultados reales de hoy, no una expectativa fija. ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
