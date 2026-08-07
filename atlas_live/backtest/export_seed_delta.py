"""Investigación 4 -- Persistencia y sincronización del conocimiento de
Atlas (Etapa 3, 2026-08-06, ver DECISION_LOG.md).

Exporta a JSONL SOLO las trayectorias que existen en la base local y
todavía no existen en la base oficial (Railway) -- nunca la base
completa, para que cada sincronización quede chica y auditable en el
diff del commit. Formato JSONL, no SQLite: el repositorio nunca
almacena bases SQLite (regla permanente del proyecto, ver la decisión
de arquitectura de esta investigación) -- solo el archivo de intercambio,
independiente del motor de base de datos.

**Solo lectura sobre ambas bases.** Este script nunca escribe en la base
local ni en la oficial -- solo lee (`exit_journal.get_all_symbol_dates`/
`get_trajectory` local, `/api/exit-journal/inventory` remoto) y produce
un archivo. La escritura del lado oficial la hace `seed_import.py`, en
un paso completamente separado.

Uso:
    python -m atlas_live.backtest.export_seed_delta --base-url https://jubilant-healing-production.up.railway.app
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import requests

from atlas_live.memory import exit_journal as ej

SEED_DIR = Path(__file__).parent / "seeds"


def _local_inventory() -> Set[Tuple[str, str]]:
    return set(ej.get_all_symbol_dates())


def _remote_inventory(base_url: str) -> Set[Tuple[str, str]]:
    resp = requests.get(f"{base_url.rstrip('/')}/api/exit-journal/inventory", timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return {(p[0], p[1]) for p in data["pairs"]}


def compute_delta(base_url: str) -> Tuple[List[Tuple[str, str]], Dict[str, Any]]:
    """Devuelve (pares_faltantes, resumen) -- resumen para la validación
    "antes" del diseño aprobado (cuántos pares locales, cuántos ya están
    en la base oficial, cuántos entran en el delta)."""
    local = _local_inventory()
    remoto = _remote_inventory(base_url)
    faltantes = sorted(local - remoto)
    resumen = {
        "pares_locales": len(local),
        "pares_ya_en_oficial": len(local & remoto),
        "pares_en_delta": len(faltantes),
    }
    return faltantes, resumen


def _sample_to_row(symbol: str, date: str, sample: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "date": date,
        "sampled_at": sample["sampled_at"],
        "return_pct": sample["return_pct"],
        "score": sample["score"],
        "eligible": bool(sample["eligible"]),
    }


def export_delta(base_url: str, out_dir: Path = SEED_DIR) -> Dict[str, Any]:
    faltantes, resumen = compute_delta(base_url)
    if not faltantes:
        resumen["archivo"] = None
        return resumen

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    out_path = out_dir / f"exit_journal_seed_{stamp}.jsonl"

    filas_escritas = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for symbol, date in faltantes:
            for sample in ej.get_trajectory(symbol, date):
                f.write(json.dumps(_sample_to_row(symbol, date, sample), ensure_ascii=False) + "\n")
                filas_escritas += 1

    resumen["archivo"] = str(out_path)
    resumen["filas_escritas"] = filas_escritas
    return resumen


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="URL pública de la base oficial (Railway)")
    args = parser.parse_args()

    resultado = export_delta(args.base_url)
    print(f"Pares en la base local: {resultado['pares_locales']}")
    print(f"Pares que ya existían en la base oficial: {resultado['pares_ya_en_oficial']}")
    print(f"Pares en el delta (nuevos): {resultado['pares_en_delta']}")
    if resultado.get("archivo"):
        print(f"Seed escrito: {resultado['archivo']} ({resultado['filas_escritas']} filas)")
    else:
        print("Nada que sincronizar -- la base oficial ya tiene todo lo que hay en local.")
