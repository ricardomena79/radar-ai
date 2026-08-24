"""Motor de Catalizadores/Noticias (2026-08-23, plan aprobado -- ver
ethereal-mixing-anchor.md).

Capa PARALELA e independiente al radar técnico (`atlas_live/radar/`):
`FinnhubProvider (2 métodos nuevos) -> catalyst_collector -> CatalystEvent
-> catalyst_registry`, cruzada solo al final (catalyst_score) con datos
que el radar técnico YA calcula. Nunca importa ni modifica
`candidate_gates.py`, el score en vivo ni `atlas/engine/decision_engine.py`."""
