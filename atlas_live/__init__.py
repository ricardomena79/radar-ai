"""Atlas Live: interfaz web sobre Atlas Core.

No es parte de Atlas Core (que quedó congelado en v1.0). Es una capa de
presentación separada: consume Atlas Core (Data Collector, los 5 motores,
Knowledge Base, Decision Recorder) tal cual está, sin modificarlo.

Única excepción, explícita y aislada: `explosive_engine.py` (Radar
Explosivo). Es un motor de puntuación propio de esta capa, independiente
de Decision Engine, que reutiliza (sin modificar) datos ya calculados por
Atlas Core -- Quote, Momentum Engine, Money Flow -- para responder una
pregunta distinta a la de Atlas Core: qué probabilidad tiene un símbolo de
un movimiento fuerte en los próximos 5-10 minutos, no si es una buena
inversión. Configurable vía `explosive_config.json`.
"""
