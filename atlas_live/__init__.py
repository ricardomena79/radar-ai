"""Atlas Live: interfaz web sobre Atlas Core.

No es parte de Atlas Core (que quedó congelado en v1.0). Es una capa de
presentación separada: consume Atlas Core (Data Collector, los 5 motores,
Knowledge Base, Decision Recorder) tal cual está, sin modificarlo, y sin
reimplementar ninguna lógica de puntuación o decisión propia. Todo lo que
esta capa hace es orquestar llamadas a Atlas Core y servir el resultado
como JSON/HTML.
"""
