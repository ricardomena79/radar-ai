# Changelog

Registro cronológico de cambios importantes en Atlas. Reconstruido a partir del historial real de `git log` y del estado real de archivos del repositorio -- no contiene entradas especulativas.

## [Sin commit] - 2026-08-01 (sesión en curso)

Todo lo listado aquí existe como archivos reales en el repositorio pero **todavía no se confirmó con `git commit`** al momento de escribir esta entrada -- se actualizará esta sección o se moverá a una entrada con fecha de commit real cuando corresponda.

### Added

- `atlas_live/explosive_engine.py`, `explosive_factors.py`, `explosive_config.py` + `explosive_config.json`: Radar Explosivo, motor de puntaje de momentum intradía independiente de Decision Engine, 100% dentro de `atlas_live` (cero cambios en `/atlas`). Ver [DECISION_LOG.md](DECISION_LOG.md).
- `atlas_live/explosive_diagnostics.py` + vista "🔬 Diagnóstico" en el dashboard: embudo de filtros y tabla de auditoría por símbolo del último escaneo.
- `atlas_live/backtest/` (`historical_scan.py`, `validation_report.py`, `run_validation.py`, `explosive_dna.py`, `run_explosive_dna.py`): suite de validación histórica del Radar Explosivo contra sesiones reales de mercado (Universo Racional completo, sin lookahead) y perfilado estadístico ("Explosive DNA") de las acciones realmente explosivas.
- `ATLAS_CONSTITUTION.md`, `ATLAS_ROADMAP.md`, `VALIDATION_RESULTS.md`, `DECISION_LOG.md`: documentación permanente de gobernanza del proyecto.

### Changed

- `atlas_live/static/index.html`, `app.js`, `style.css`: dashboard reorganizado en 4 secciones navegables desde un menú lateral (🔥 Radar Explosivo, 📈 Radar General, 📋 Watchlist, 🔬 Diagnóstico), con Radar Explosivo como pantalla principal.
- `atlas_live/scan_worker.py`: cada símbolo escaneado ahora también se evalúa con `explosive_engine.evaluate()` (llamada adicional, reutiliza el `quote`/`momentum_result` ya calculados -- no repite ninguna llamada a Atlas Core) y expone el resultado del diagnóstico completo (no truncado a `TOP_N`) para la vista Diagnóstico.
- `atlas_live/server.py`: nuevo endpoint `/api/explosive-diagnostics`.
- `atlas_live/__init__.py`: docstring actualizado para documentar `explosive_engine.py` como la única excepción explícita a "consume Atlas Core sin reimplementar lógica propia".

---

## [fcadbd1] - 2026-07-31

### Added

- Atlas Live: dashboard operable con lenguaje natural para el usuario final (primera versión, previa a la reorganización en secciones de esta sesión).

## [9f65194] - 2026-07-30

### Added

- Auditoría final de Atlas Core v1.0 -- cobertura completa de documentación del Core, marcando su congelamiento como base estable.

## [4cb4ddd..8ef9523] - 2026-07-30

### Added

- Learning Engine, Etapas 1 a 8: Decision Journal, Pattern Store, Calibration Manager, Decision Recorder, Operator Learning Engine, Accuracy Tracker, Pattern Evolution, y Calibration Advisor (fachada final, cierre de Atlas Core v1.0).

## [892c3f1] - 2026-07-30

### Added

- Arquitectura de Research Lab, Strategy Lab y Decision Journal.

## [8f86b3d] - 2026-07-30

### Added

- Ampliación final del Atlas Core: trazabilidad de decisiones y preparación para funcionalidad de Replay.

## [71a26f6] - 2026-07-30

### Added

- Ampliación de Knowledge Base -- Market Context Engine.

## [98f9c5c] - 2026-07-30

### Added

- Fase 10 y Knowledge Base -- Decision Engine y núcleo de conocimiento.

## [ff763bc] - 2026-07-30

### Added

- Fase 9 completada -- Money Flow Engine.

## [Unreleased] - 2026-07-29

### Changed

- Reorganización completa de la arquitectura del proyecto: eliminación de carpetas duplicadas (`atlas/atlas/scanner/atlas/`), documentación centralizada en `atlas/docs/`, y creación del esqueleto de paquetes (`data`, `engine`, `indicators`, `scanners`, `backtesting`, `strategies`, `utils`, `dashboard`, `alerts`, `storage`, `config`, `tests`) listo para desarrollo futuro.
- `config.py` corregido: contenía texto inválido, ahora es un módulo Python válido.
- `investigator.py` reubicado en `atlas/data/collectors/`, sin cambios en su lógica.

## [d353180] - 2026-07-29

### Added

- Fase 6 completada (Data Collector / indicadores base, según historial de commits).

## [1c9cc3c] - 2026-07-29

### Added

- Commit inicial: scaffold de Atlas, proveedor de datos Yahoo Finance, y biblioteca de indicadores.
