# DECISIONES DE ATLAS

## Filosofía

Atlas no busca la acción que más sube.

Atlas busca la acción con mayor probabilidad de generar una operación rentable durante los primeros 5 a 20 minutos.

---

## Reglas

- Solo acciones disponibles en Racional.
- No perseguir FOMO.
- El volumen es más importante que el precio.
- El mercado siempre tiene la última palabra.
- Una operación sin ventaja estadística no se toma.

---

## Objetivo

Cada mañana Atlas deberá entregar:

🥇 Mejor oportunidad

🥈 Segunda oportunidad

🥉 Tercera oportunidad

Cada una con:

- Índice de Explosión
- Riesgo
- Probabilidad
- Explicación

---

## Interfaz oficial

**Decisión (2026-08-05):** Atlas Live -- la versión con navegación lateral
y lenguaje natural, en `atlas_live/static/` sobre la rama `master` -- es
la única interfaz oficial de Atlas. Toda modificación futura de UI ocurre
únicamente sobre esa base.

Existieron en paralelo, construidas por sesiones de Claude Code sin
coordinación entre sí, dos versiones más:

- El "cockpit" de trading (3 columnas, gráfico TradingView embebido) --
  rama `origin/main`.
- "Radar Explosivo" + "Cabina del Piloto" -- rama
  `origin/claude/setup-atlas-live-env-a5425c`, nunca mergeada a `main`.

Ambas quedan **DEPRECATED**: no se ejecutan, no se modifican, no reciben
desarrollo nuevo. Se conservan sin tocar (branches remotos + worktrees de
auditoría) únicamente hasta confirmar que la interfaz oficial cubre toda
la funcionalidad real que vale la pena conservar de cada una -- recién
ahí se eliminan físicamente. Ninguna eliminación ocurre antes de esa
confirmación.

De esas dos versiones se rescata **funcionalidad real, no la interfaz**:

- **Radar Explosivo** (`explosive_engine.py`/`explosive_factors.py`,
  motor de detección de momentum intradía) y **Memory Engine**
  (`atlas_live/memory/`, 73.123 observaciones reales de backtest) -- de
  la rama huérfana. Ambos viven en `atlas_live/`, no tocan Atlas Core, y
  siguen el mismo principio de "propone, nunca aplica solo" que
  Calibration Manager.
- El componente de gráfico **TradingView** -- del cockpit, adaptado a la
  interfaz oficial (no su layout de 3 columnas).

Lo que **no** se rescata: Cabina del Piloto (secciones marcadas
explícitamente `MOCK` en el código, nunca enlazada desde ninguna otra
pantalla) y cualquier otro contenido simulado o no verificado contra
datos reales.

---

## Memory Engine: preservación de datos (seed vs. base viva)

**Decisión (2026-08-05):** las 73.123 observaciones de backtest de Memory
Engine no se versionan como base SQLite (`memory_store.db` nunca entra a
Git -- sigue excluido por `.gitignore`, igual que el resto de las bases
del proyecto). Se versiona un **seed** comprimido
(`atlas_live/memory/seed/observations_seed.csv.gz`, 4.3 MB, mismo dato
que la base, sin el overhead binario del motor SQLite) que se importa
solo automáticamente cuando hace falta.

Flujo:

```
Seed (Git)
     |
     v
Bootstrap automático (seed.ensure_seeded(), llamado a nivel de módulo en
atlas_live/server.py -- corre en cualquier arranque del proceso, local o
bajo gunicorn en Railway)
     |
     v
SQLite local (atlas/cache/memory_store.db, o el Volume de Railway si
ATLAS_DATA_DIR apunta ahí)
     |
     v
Railway Volume (persiste ese SQLite entre deploys)
     |
     v
Nuevas observaciones (una vez que atlas_live/memory/live_integration.py
se enganche al ciclo de scan_worker.py -- todavía no implementado)
```

Garantías, verificadas con datos reales (no solo con pruebas unitarias):

- **Nunca sobrescribe.** `ensure_seeded()` solo importa si
  `store.count_observations() == 0`. Si ya hay una fila -- del seed en un
  arranque anterior, o de observaciones reales ya escritas en producción
  -- no toca nada.
- **Idempotente.** Mismo guard de arriba: correr el bootstrap una vez o
  cien veces da el mismo resultado. Confirmado arrancando el servidor real
  dos veces seguidas contra una base vacía: la primera vez importó
  73.123 filas; la segunda no importó nada y el conteo siguió en 73.123.
- **Automático de punta a punta.** Probado eliminando el `memory_store.db`
  local (simulando una máquina/clon/deploy nuevo) y arrancando
  `python -m atlas_live.server` sin ningún paso manual: las 73.123
  observaciones aparecieron solas en `/api/memory-engine` antes de que
  terminara de levantar el servidor.

El seed es de solo lectura desde la app: nunca se regenera solo. Se
vuelve a exportar (`seed.export_seed()`) y re-comitear a mano únicamente
si en el futuro se corre un backtest nuevo mucho más grande -- una
decisión deliberada, no un efecto secundario de correr Atlas.
