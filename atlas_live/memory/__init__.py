"""Memory Engine de Atlas.

Estudia TODO el mercado escaneado -- no solo las explosiones -- para que
Radar Explosivo pueda recalibrar su ranking contra tasas base reales de
toda la población, no solo contra la pequeña muestra sesgada de ganadoras
ya vistas. Vive enteramente en atlas_live, nunca en /atlas.

Portado desde una rama paralela (2026-08-05) junto con sus 3 bases SQLite
reales (73.123 observaciones de backtest ya procesadas). Los módulos
`backfill.py` y `demo_ranking.py` de esa rama no se portaron -- el primero
solo poblaba estas bases desde JSON de backtest (139 MB, no versionados
acá); el segundo era un script de ejemplo, no parte del paquete en sí.
"""
