# ThingsBoard Telemetry Load Lab

Herramientas para generar carga MQTT contra ThingsBoard, recolectar metricas (MQTT y HaLow) y producir insumos opcionales para un documento en LaTeX.

## Estructura rapida
- `scripts/` orquestacion y simulacion (`scripts/mqtt/run_stress_suite.py`, `run_experiments.py`, utils de provision y verificaciones).
- `data/` artefactos de pruebas (no versionados, salvo `.gitkeep`):
  - `data/metrics/` CSV de MQTT y HaLow + reportes (`reports/`).
  - `data/logs/` eventos JSONL por dispositivo.
  - `data/runs/` resmenes JSON por corrida.
- `doc/` proyecto LaTeX (opcional). No es necesario para ejecutar pruebas o Docker.

## Convencion de nombres
- Escenario corto: `p1..p6`. Variantes: `n50/100/200/400`, `int10/5/2/1`, `s/m/l`, `sa/rl`.
- Patrones de archivo:
  - `<escenario>-<timestamp>-n<clientes>-metrics.csv` -> metricas MQTT.
  - `<escenario>-<timestamp>-halow.csv` -> metricas HaLow (si aplica).
  - `<escenario>-<timestamp>-n<clientes>-report.{tex,png}` -> reporte rapido.

## Ejecutar pruebas (local)
1. Copia `.env.example` a `.env` y ajusta credenciales (TB, broker, SSH HaLow, etc.).
2. Opcional: provisiona dispositivos (`py -3 scripts/mqtt/create_devices.py`).
3. Corre una prueba: `py -3 scripts/run_experiments.py <1-6>`.
   - Artefactos quedan en `data/metrics/` y `data/metrics/reports/` con nombres cortos.
4. Postproceso:
   - `py -3 doc/tools/procesar_resultados.py` genera `doc/resultados_auto.tex`, tablas y figuras auto.
   - `py -3 scripts/verificar_pruebas.py` valida metricas y escribe `data/metrics/reports/reporte_pruebas.txt`.

## Ejecutar en Docker
Prereqs: Docker/Compose, `.env` listo.
- Build: `docker compose build`
- Correr (usa config de `docker-compose.yml`): `docker compose up`
  - Monta `./data` en `/app/data` para persistir artefactos fuera del contenedor.
  - Ajusta `command:` en `docker-compose.yml` si quieres otros parametros (device-count, duration, etc.).
Image base: `python:3.11-slim` con dependencias de `requirements.txt`.

## LaTeX (opcional)
Si necesitas el documento, compila desde `doc/`:
```
latexmk -pdf DocumentoGrado.tex
```
o la secuencia pdflatex/biber habitual. Si solo te interesa el codigo y las metricas, puedes ignorar `doc/`.

## Notas
- Los sufijos `nXXX` en los archivos reflejan el numero de dispositivos activos al final de la corrida (lo reporta el metrics server).
- HaLow recolecta RSSI, ruido, tasas TX/RX, bytes, retries/drops y (si el driver lo expone) utilizacion de canal.
- `doc/` se mantiene por compatibilidad, pero no es requerido para correr pruebas ni para Docker; puedes omitirlo en despliegues si no necesitas el PDF.***
