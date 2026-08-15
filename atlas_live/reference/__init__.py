"""Base histórica de referencia (2026-08-15).

Parámetros diarios (~3 meses, universo Racional completo, vía Tradier) para
que Atlas compare el mercado en vivo contra comportamientos reales
anteriores -- no para prometer que "aprendió" de 3 meses de golpe, sino
para tener un punto de referencia real desde el primer día operativo.

Anti-leakage estricto: `daily_reference.py` calcula cada feature de un día
D usando SOLO datos hasta D; `reference_registry.py` separa features de
resultado en tablas distintas, y el resultado de un día D solo se calcula
si existen de verdad >=10 días de mercado posteriores en los datos (nunca
se trunca ni se rellena).
"""
