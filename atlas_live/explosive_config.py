"""Configuración del Radar Explosivo.

Todos los umbrales, pesos y referencias del motor viven en
`explosive_config.json`, a propósito separados de la lógica
(`explosive_engine.py` / `explosive_factors.py`). Para ajustar el motor
(ser más o menos estricto, cambiar cuánto pesa cada factor, mover el techo
de market cap, etc.) alcanza con editar ese JSON -- no hace falta tocar
ningún archivo de código.

Si el JSON falta, está corrupto o le faltan claves, se usan los valores
por defecto de aquí abajo (idénticos a los del JSON de fábrica) para que
el motor nunca se caiga por un archivo de configuración mal editado.

Claves:
  gates: filtros de elegibilidad (pasa/no pasa) -- ver explosive_engine.py.
  weights: peso de cada factor en el puntaje ponderado (no hace falta que
    sumen 1.0: el motor normaliza por el total de pesos de los factores
    que sí pudieron calcularse para ese símbolo).
  size_factor: curva de penalización continua por tamaño (market cap).
  float_factor: referencias de float bajo/alto para el factor "float".
  top_n: cuántos resultados exponer como máximo.
"""

import json
from pathlib import Path
from typing import Any, Dict

from loguru import logger

CONFIG_PATH = Path(__file__).parent / "explosive_config.json"

_DEFAULTS: Dict[str, Any] = {
    "gates": {
        "min_price": 1.0,
        "min_dollar_volume": 2_000_000,
        "min_rvol": 2.0,
        "min_abs_gap_or_change_pct": 2.0,
        "min_volatility_score": 50.0,
        "large_cap_ceiling": 10_000_000_000,
        "mega_cap_exception_gap_pct": 5.0,
        "mega_cap_exception_rvol": 5.0,
    },
    "weights": {
        "relative_volume": 0.25,
        "volatility": 0.15,
        "momentum": 0.15,
        "gap": 0.15,
        "vwap_distance": 0.10,
        "sector_money_flow": 0.10,
        "float": 0.10,
    },
    "size_factor": {
        "small_cap_reference": 300_000_000,
        "mega_cap_reference": 200_000_000_000,
        "min_factor": 0.5,
        "max_factor": 1.0,
    },
    "float_factor": {
        "low_float_reference": 20_000_000,
        "high_float_reference": 500_000_000,
    },
    "top_n": 20,
}


def load_config() -> Dict[str, Any]:
    """Lee `explosive_config.json`; si falta o está corrupto, usa los valores por defecto."""
    if not CONFIG_PATH.exists():
        logger.warning(f"{CONFIG_PATH} no existe; Radar Explosivo usa valores por defecto")
        return _DEFAULTS

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        logger.warning(f"No se pudo leer {CONFIG_PATH} ({exc}); Radar Explosivo usa valores por defecto")
        return _DEFAULTS

    merged: Dict[str, Any] = dict(_DEFAULTS)
    for section in ("gates", "weights", "size_factor", "float_factor"):
        merged[section] = {**_DEFAULTS[section], **data.get(section, {})}
    merged["top_n"] = data.get("top_n", _DEFAULTS["top_n"])
    return merged
