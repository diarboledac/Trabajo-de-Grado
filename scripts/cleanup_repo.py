#!/usr/bin/env python3
"""Limpia artefactos de compilación LaTeX y archiva resultados antiguos.

Se ejecuta desde la raíz del repositorio y deja listas las carpetas de datos
para nuevas pruebas (métricas, logs, reports) sin borrar fuentes ni refs.bib.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
METRICS_DIR = DATA_DIR / "metrics"
REPORTS_DIR = METRICS_DIR / "reports"
LOGS_DIR = DATA_DIR / "logs"
RUNS_DIR = DATA_DIR / "runs"

LATEX_PATTERNS: List[str] = [
    "*.aux",
    "*.log",
    "*.out",
    "*.toc",
    "*.bbl",
    "*.blg",
    "*.bcf",
    "*.run.xml",
    "*.synctex.gz",
    "*.fdb_latexmk",
    "*.fls",
    "*.lof",
    "*.lot",
    "*.nav",
    "*.snm",
    "*.vrb",
]

PDF_TARGETS = {"DocumentoGrado.pdf", "main.pdf", "output.pdf"}
LATEX_DIRS: List[Path] = [ROOT / "doc", ROOT / "docs", ROOT / "overleaf"]


def delete_latex_artifacts() -> list[Path]:
    removed: list[Path] = []
    for base in LATEX_DIRS:
        if not base.exists():
            continue
        for pattern in LATEX_PATTERNS:
            for path in base.rglob(pattern):
                if path.suffix == ".tex" or path.name == "refs.bib":
                    continue
                if path.is_file():
                    path.unlink()
                    removed.append(path)
        for target in PDF_TARGETS:
            pdf_path = base / target
            if pdf_path.exists() and pdf_path.is_file():
                pdf_path.unlink()
                removed.append(pdf_path)

    # Limpieza puntual en la raíz para archivos del documento principal
    for pattern in LATEX_PATTERNS:
        for path in ROOT.glob(pattern):
            if not path.is_file():
                continue
            if not path.name.lower().startswith(("documentograd", "document")):
                continue
            path.unlink()
            removed.append(path)
    for target in PDF_TARGETS:
        pdf_path = ROOT / target
        if pdf_path.exists() and pdf_path.is_file():
            pdf_path.unlink()
            removed.append(pdf_path)
    return removed


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def move_all(files: Iterable[Path], dest_dir: Path) -> list[Path]:
    moved: list[Path] = []
    ensure_dir(dest_dir)
    for path in files:
        if not path.exists() or path.is_dir():
            continue
        dest = dest_dir / path.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), dest)
        moved.append(dest)
    return moved


def archive_old_data() -> list[Path]:
    """Mueve métricas/logs previos a data/archive/ANTES_RECTA_FINAL_YYYYMMDD/."""
    today = datetime.now().strftime("%Y%m%d")
    archive_root = DATA_DIR / "archive" / f"ANTES_RECTA_FINAL_{today}"
    moved: list[Path] = []

    # Métricas y reportes
    if METRICS_DIR.exists():
        moved += move_all(METRICS_DIR.glob("*.*"), archive_root / "metrics")
    if REPORTS_DIR.exists():
        moved += move_all(REPORTS_DIR.glob("*"), archive_root / "metrics" / "reports")

    # Logs JSONL
    if LOGS_DIR.exists():
        moved += move_all(LOGS_DIR.glob("*"), archive_root / "logs")

    # Runs: conservar latest.json
    if RUNS_DIR.exists():
        latest = RUNS_DIR / "latest.json"
        others = [p for p in RUNS_DIR.glob("*") if p != latest]
        moved += move_all(others, archive_root / "runs")

    # Variantes antiguas de resultados automáticos
    for extra in (ROOT / "docs").glob("resultados_auto*.tex"):
        moved += move_all([extra], archive_root / "docs")

    return moved


def reset_data_dirs() -> None:
    """Garantiza estructura vacía para nuevas pruebas."""
    for path in (METRICS_DIR, REPORTS_DIR, LOGS_DIR, RUNS_DIR):
        ensure_dir(path)


def main() -> None:
    removed = delete_latex_artifacts()
    moved = archive_old_data()
    reset_data_dirs()

    print(f"[OK] Limpieza LaTeX: {len(removed)} archivos eliminados.")
    print(f"[OK] Datos archivados: {len(moved)} archivos movidos.")
    print(f"[OK] Estructura lista en {DATA_DIR}")


if __name__ == "__main__":
    main()
