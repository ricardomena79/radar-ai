"""Validación histórica del Radar Explosivo.

No es parte de Atlas Core ni del Radar Explosivo "en vivo" -- es una
herramienta de medición, aparte, que reconstruye qué habría visto
`explosive_engine.py` en una fecha pasada real y compara ese resultado
contra lo que efectivamente pasó ese día (las acciones que más subieron).

No modifica /atlas, no modifica explosive_engine.py, no modifica
explosive_config.json. Reutiliza en modo lectura las funciones puras de
indicadores de Atlas Core (atlas.engine.score_engine, gap_pct/change_percent/
rsi de atlas.engine.momentum_engine) para que la reconstrucción histórica
use exactamente las mismas fórmulas que el motor en vivo -- si se
reimplementaran los indicadores por separado, la validación mediría una
aproximación, no el motor real.
"""
