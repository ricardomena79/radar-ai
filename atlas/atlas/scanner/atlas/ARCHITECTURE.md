# ATLAS ARCHITECTURE

## Módulos

### 1. Data Collector

Obtiene información del mercado.

Entradas:

- Precio
- Volumen
- RVOL
- Float
- Noticias
- Premarket
- After Hours

Salida:

Datos limpios.

---

### 2. Atlas Engine

Analiza los datos.

Calcula:

- Atlas Score
- PE (Probabilidad de Explosión)
- PX (Probabilidad de Éxito)

Entrega:

COMPRAR

ESPERAR

DESCARTAR

---

### 3. Learning Engine

Aprende de todas las operaciones.

Guarda:

- Resultado
- Acierto
- Error

Ajusta el modelo.

---

### 4. Dashboard

Muestra:

Ranking

Top 3

Alertas

Historial

Estadísticas
