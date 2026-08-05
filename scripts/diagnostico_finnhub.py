"""Diagnóstico independiente de la API de Finnhub -- NO toca Atlas.

No importa nada de `atlas/` ni `atlas_live/`. Objetivo único: confirmar,
antes de escribir ningún código de integración, que `FINNHUB_API_KEY`
carga correctamente desde `.env` y que Finnhub responde con datos reales
-- no supone nada, no fabrica nada.

Por qué se conserva en el repo (2026-08-04): este script aisló y
resolvió dos fallas reales antes de tocar código de integración -- un
archivo guardado como `.env.txt` en vez de `.env`, y una clave del
dashboard equivocada (Webhook Secret en vez de API Key) -- ninguna de
las dos se hubiera visto con la misma claridad probando directamente
contra `FinnhubProvider`, con todo el resto de Atlas alrededor. Queda
como plantilla de referencia para el mismo chequeo con futuros
proveedores (Alpaca, Polygon, etc.) -- específico de Finnhub tal como
está escrito, se adapta cambiando la URL y el nombre de la variable de
entorno, no es un framework genérico todavía.

Uso: python scripts/diagnostico_finnhub.py
"""

import os
import sys

import requests
from dotenv import load_dotenv

FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"
SYMBOL = "AAPL"


def main() -> int:
    load_dotenv()
    api_key = os.environ.get("FINNHUB_API_KEY")

    if not api_key:
        print("FINNHUB_API_KEY no está definida -- ni en .env ni en el entorno.")
        print("No se hizo ninguna llamada a Finnhub. Nunca se imprime la clave.")
        return 1

    response = requests.get(
        FINNHUB_QUOTE_URL,
        params={"symbol": SYMBOL, "token": api_key},
        timeout=10,
    )

    print(f"HTTP {response.status_code}")
    print(response.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
