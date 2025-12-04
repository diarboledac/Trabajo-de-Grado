# ThingsBoard Telemetry Load Lab

Repositorio para generar y medir carga MQTT sobre ThingsBoard, guardar métricas y producir insumos del documento de grado.

## Estructura rápida
- `scripts/` scripts de orquestación y simulación (ver `scripts/mqtt/run_stress_suite.py`).
- `data/` artefactos de pruebas (vacío por defecto, con `.gitkeep`):
  - `data/metrics/` y `data/metrics/reports/` para CSV/PNG/TEX de cada corrida.
  - `data/logs/` eventos JSONL por dispositivo.
  - `data/runs/` resúmenes JSON de cada ejecución.
- `doc/` proyecto LaTeX autocontenido (`DocumentoGrado.tex`, `refs.bib`, `resultados_auto.tex`, figuras en `doc/figures/`).

## Cómo ejecutar pruebas (pipeline básico)
1. Completa `.env` a partir de `.env.example` (credenciales TB, broker MQTT, etc.).
2. Provisión opcional de dispositivos: `py -3 scripts/mqtt/create_devices.py`.
3. Ejecutar una prueba predefinida: `py -3 scripts/run_experiments.py <1-6>`
   - Resultados quedan en `data/metrics/` y `data/metrics/reports/`.
4. Postproceso:
   - `py -3 scripts/procesar_resultados.py` genera/actualiza `doc/resultados_auto.tex` con el formato estándar.
   - `py -3 scripts/verificar_pruebas.py` valida métricas y genera `data/metrics/reports/reporte_pruebas.txt`.

## Compilar el documento de grado
Ubicado en `doc/`. Dependencias: pdfLaTeX, biber, paquetes IEEEtran, biblatex, pgfplots, siunitx, cleveref.

Pasos (desde `doc/`):
```
pdflatex DocumentoGrado.tex
biber DocumentoGrado
pdflatex DocumentoGrado.tex
pdflatex DocumentoGrado.tex
```
Consulta `doc/README-LaTeX.md` para detalles y uso en TeXstudio.

## Notas
- Se eliminaron carpetas duplicadas (Overleaf) y resultados antiguos para iniciar una fase final limpia.
- `data/` está listo para nuevas corridas; los archivos de datos no se versionan. Usa las rutas por defecto o ajusta `--log-dir`/`--metrics-dir` en los scripts si necesitas otro destino.
