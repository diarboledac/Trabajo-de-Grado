# Compilación del documento de grado (LaTeX)

## Requisitos
- Distribución TeX completa (TeX Live o MiKTeX) con:
  - `IEEEtran`, `biblatex` + `biber`, `csquotes`, `pgfplots`, `siunitx`, `cleveref`, `caption`, `placeins`.
- Opcional: TeXstudio o editor compatible con raíces de proyecto LaTeX.

## Orden de compilación (CLI)
Ejecutar desde la carpeta `doc/`:
```
pdflatex DocumentoGrado.tex
biber DocumentoGrado
pdflatex DocumentoGrado.tex
pdflatex DocumentoGrado.tex
```

## Configuración sugerida en TeXstudio
1. Abrir `doc/DocumentoGrado.tex` como proyecto raíz.
2. Motor principal: `pdfLaTeX`.
3. Backend de bibliografía: `biber`.
4. Secuencia de compilación recomendada (Build & View): `pdflatex -> biber -> pdflatex -> pdflatex`.
5. Asegúrate de que las rutas sean relativas (ya configurado: `\addbibresource{refs.bib}`, `\input{resultados_auto.tex}`, figuras en `doc/figures/`).

## Resultados automáticos
- `doc/resultados_auto.tex` se genera con `py -3 scripts/procesar_resultados.py` desde la raíz del repo.
- Si no hay corridas, el archivo queda con un mensaje mínimo y compila sin figuras/tablas.
- Coloca las imágenes que quieras incluir manualmente en `doc/figures/` y referencia con `\includegraphics{figures/<nombre>.png}`.

## Limpieza
- Los artefactos de compilación (`*.aux`, `*.log`, `*.toc`, `*.bbl`, `*.bcf`, `*.run.xml`, etc.) están listados en `.gitignore`. Borra manualmente si necesitas una limpieza completa.
