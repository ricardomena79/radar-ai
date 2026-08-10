"""Versionado del registro de señales (2026-08-09).

Cada señal guarda las versiones vigentes al momento de detectarla, para poder
saber después "esta señal fue generada por la versión X del detector usando
las features Y sobre los datos Z". Cuando una mejora cambie el detector, las
features o el origen de datos, se sube la versión correspondiente y las
señales nuevas quedan trazables aparte de las viejas -- sin mezclar métricas
de versiones distintas.

Semver simple. NO se hardcodean estos valores en otros módulos: se importan
de acá para que haya un único lugar donde subirlos.
"""

# Versión del proceso de DETECCIÓN (qué candidatos se registran y cómo se
# arma la señal). Hoy: se registran los candidatos que el scanner existente
# marcó `eligible_radar` (regla de consenso Radar + Memory Engine), sin
# ningún algoritmo predictivo nuevo -- solo REGISTRO. Ver signal_tracker.py.
DETECTOR_VERSION = "0.1.0"

# Versión del conjunto de FEATURES congeladas en la detección (qué campos se
# guardan en features_json y con qué significado).
FEATURE_VERSION = "0.1.0"

# Versión del origen/estructura de DATOS (esquema de la base de señales).
DATA_VERSION = "0.1.0"


def current_versions() -> dict:
    return {
        "detector_version": DETECTOR_VERSION,
        "feature_version": FEATURE_VERSION,
        "data_version": DATA_VERSION,
    }
