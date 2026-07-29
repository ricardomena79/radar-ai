# Changelog

## [Unreleased] - 2026-07-29

### Changed

- Reorganización completa de la arquitectura del proyecto: eliminación de carpetas duplicadas (`atlas/atlas/scanner/atlas/`), documentación centralizada en `atlas/docs/`, y creación del esqueleto de paquetes (`data`, `engine`, `indicators`, `scanners`, `backtesting`, `strategies`, `utils`, `dashboard`, `alerts`, `storage`, `config`, `tests`) listo para desarrollo futuro.
- `config.py` corregido: contenía texto inválido, ahora es un módulo Python válido.
- `investigator.py` reubicado en `atlas/data/collectors/`, sin cambios en su lógica.
