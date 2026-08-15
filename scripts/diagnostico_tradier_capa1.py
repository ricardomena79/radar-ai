"""Diagnóstico real de la CAPA 1 (quotes del universo completo vía Tradier
+ normalización + fallback) -- NO toca scan_worker, ranking, señales ni
ningún otro subsistema operativo de Atlas. Corre un ciclo real completo
contra el universo Racional (~2.575 símbolos) y reporta los contadores
exigidos por la arquitectura aprobada (2026-08-14): total consultado,
resuelto por Tradier, resuelto por normalización, enviado a fallback,
resuelto por fallback, sin datos final, tiempo total del ciclo.

Nunca imprime el token -- solo lo usa `build_tradier_provider()`
internamente.

Uso: python scripts/diagnostico_tradier_capa1.py
"""

import json
import sys

from atlas.data.universe import get_equities, get_etfs
from atlas_live.data_fusion.universe_quotes import fetch_universe_quotes


def main() -> int:
    equities = get_equities()
    etfs = get_etfs()
    symbols = sorted({a.symbol for a in equities + etfs})

    print(f"Universo real: {len(equities)} equities + {len(etfs)} ETFs = {len(symbols)} símbolos únicos")
    print("Ejecutando fetch_universe_quotes()... (Tradier + normalización + fallback Yahoo/Finnhub)")

    result = fetch_universe_quotes(symbols)

    print()
    print("=== DIAGNÓSTICO DEL CICLO ===")
    print(json.dumps(result.diagnostics.to_dict(), indent=2, ensure_ascii=False))

    print()
    print("=== VERIFICACIÓN ===")
    print(f"quotes obtenidas: {len(result.quotes)}")
    print(f"símbolos con estado no-ACTIVE: {sum(1 for s in result.states.values() if s != 'ACTIVE')}")

    # Muestra de símbolos que quedaron finalmente sin datos, con su estado
    sin_datos = [s for s in symbols if s not in result.quotes]
    print(f"sin datos final: {len(sin_datos)}")
    if sin_datos:
        print("muestra (primeros 20) con estado:")
        for s in sin_datos[:20]:
            print(f"  {s}: {result.states.get(s)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
