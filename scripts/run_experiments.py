#!/usr/bin/env python3
"""Orquesta pruebas predefinidas ejecutando run_stress_suite.py."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts" / "mqtt"
RUN_SUITE = SCRIPTS_DIR / "run_stress_suite.py"
METRICS_DIR = ROOT / "data" / "metrics"
REPORTS_DIR = METRICS_DIR / "reports"
MANIFEST = REPORTS_DIR / "manifest.json"


def short_label(prefix: str) -> str:
    """Crea una etiqueta compacta para usar en nombres de archivo."""
    label = prefix.lower()
    if label.startswith("prueba"):
        label = "p" + label[len("prueba") :]
    label = label.replace("_", "-")
    replacements = {
        "sin-aggregate": "sa",
        "con-rate-limit": "rl",
        "medium": "m",
        "small": "s",
        "large": "l",
    }
    for old, new in replacements.items():
        label = label.replace(old, new)
    return label.strip("-")


def extract_core(name: str) -> str:
    """Extrae timestamp y n de nodos para construir ids cortos."""
    m = re.search(r"(\d{8}-\d{6})(?:-s\d+)?(?:-n(\d+))?", name)
    if m:
        ts = m.group(1)
        nodos = m.group(2)
        if nodos:
            return f"{ts}-n{int(nodos)}"
        return ts
    return name


def move_to(src: Path, dest: Path) -> Path:
    """Renombra/mueve el archivo a dest, sobrescribiendo si ya existe."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() == dest.resolve():
        return dest
    if dest.exists():
        dest.unlink()
    src.rename(dest)
    return dest


def snapshot(paths: Iterable[Path]) -> set[Path]:
    return {p.resolve() for p in paths}


def list_metrics_files() -> set[Path]:
    return snapshot(METRICS_DIR.glob("*-metrics.csv")) | snapshot(METRICS_DIR.glob("halow_*.csv"))


def list_report_files() -> set[Path]:
    return snapshot(REPORTS_DIR.glob("*-report.tex")) | snapshot(REPORTS_DIR.glob("*-overview.png"))


def load_manifest() -> list:
    if MANIFEST.exists():
        try:
            return json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_manifest(entries: list) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def tag_artifacts(prefix: str, new_csv: set[Path], new_reports: set[Path]) -> None:
    """Renombra/mueve artefactos nuevos con nombres cortos y actualiza manifest."""
    manifest = load_manifest()
    short = short_label(prefix)

    def core_from_path(path: Path) -> str:
        stem = path.stem
        if stem.startswith("halow_"):
            stem = stem.replace("halow_", "", 1)
        stem = stem.replace("-report", "").replace("-overview", "").replace("-metrics", "")
        return extract_core(stem)

    metrics_by_core: dict[str, Path] = {}
    halow_by_core: dict[str, Path] = {}
    for csv_path in new_csv:
        core = core_from_path(csv_path)
        if csv_path.name.startswith("halow_") or csv_path.name.endswith("-halow.csv"):
            halow_by_core[core] = csv_path
        else:
            metrics_by_core[core] = csv_path

    report_pairs = {}
    for tex in (p for p in new_reports if p.suffix == ".tex"):
        core = core_from_path(tex)
        png = tex.with_name(f"{tex.stem.replace('-report', '')}-overview.png")
        report_pairs[core] = (tex, png if png in new_reports else None)

    for core, (tex, png) in report_pairs.items():
        session_id = f"{short}-{core}"
        tex_target = REPORTS_DIR / f"{session_id}-report.tex"
        tex_final = move_to(tex, tex_target)

        png_target = REPORTS_DIR / f"{session_id}-overview.png" if png else None
        if png and png.exists():
            move_to(png, png_target)  # type: ignore[arg-type]

        metrics_src = metrics_by_core.pop(core, None)
        metrics_target = None
        if metrics_src:
            metrics_target = METRICS_DIR / f"{session_id}-metrics.csv"
            move_to(metrics_src, metrics_target)

        halow_src = halow_by_core.pop(core, None)
        halow_target = None
        if halow_src:
            halow_target = METRICS_DIR / f"{session_id}-halow.csv"
            move_to(halow_src, halow_target)

        manifest.append(
            {
                "scenario": prefix,
                "session": session_id,
                "report_tex": tex_final.as_posix(),
                "report_png": png_target.as_posix() if png_target and png_target.exists() else None,
                "csv": metrics_target.as_posix() if metrics_target else None,
                "halow_csv": halow_target.as_posix() if halow_target else None,
                "timestamp": datetime.now().isoformat(),
            }
        )

    for core, metrics_src in metrics_by_core.items():
        session_id = f"{short}-{core}"
        metrics_target = METRICS_DIR / f"{session_id}-metrics.csv"
        metrics_final = move_to(metrics_src, metrics_target)
        halow_src = halow_by_core.pop(core, None)
        halow_target = None
        if halow_src:
            halow_target = METRICS_DIR / f"{session_id}-halow.csv"
            move_to(halow_src, halow_target)
        manifest.append(
            {
                "scenario": prefix,
                "session": session_id,
                "report_tex": None,
                "report_png": None,
                "csv": metrics_final.as_posix(),
                "halow_csv": halow_target.as_posix() if halow_target else None,
                "timestamp": datetime.now().isoformat(),
            }
        )

    for core, halow_src in halow_by_core.items():
        session_id = f"{short}-{core}"
        halow_target = METRICS_DIR / f"{session_id}-halow.csv"
        halow_final = move_to(halow_src, halow_target)
        manifest.append(
            {
                "scenario": prefix,
                "session": session_id,
                "report_tex": None,
                "report_png": None,
                "csv": None,
                "halow_csv": halow_final.as_posix(),
                "timestamp": datetime.now().isoformat(),
            }
        )

    save_manifest(manifest)


def run_suite(args: List[str], *, escenario: str) -> None:
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    before_csv = list_metrics_files()
    before_reports = list_report_files()
    cmd = [sys.executable, str(RUN_SUITE)] + args
    print(f"[INFO] Ejecutando {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise SystemExit(f"run_stress_suite fallo con codigo {result.returncode}")
    after_csv = list_metrics_files()
    after_reports = list_report_files()
    new_csv = after_csv - before_csv
    new_reports = after_reports - before_reports
    if not new_csv:
        print("[WARN] No se detectaron CSV nuevos en data/metrics.")
    if not new_reports:
        print("[WARN] No se detectaron reportes nuevos en data/metrics/reports.")
    tag_artifacts(escenario, new_csv, new_reports)
    print(f"[OK] Prueba {escenario} completada. CSV nuevos: {len(new_csv)}, reportes: {len(new_reports)}")


def run_prueba_1() -> None:
    run_suite(
        [
            "--duration",
            "150",
            "--device-count",
            "20",
            "--interval",
            "10",
            "--ramp-percentages",
            "25",
            "50",
            "100",
            "--deactivate-after",
        ],
        escenario="Prueba1",
    )


def run_prueba_2() -> None:
    for count in (50, 100, 200, 400):
        run_suite(
            [
                "--duration",
                "180",
                "--device-count",
                str(count),
                "--interval",
                "5",
                "--deactivate-after",
            ],
            escenario=f"Prueba2_n{count}",
        )


def run_prueba_3() -> None:
    for interval in (10, 5, 2, 1):
        run_suite(
            [
                "--duration",
                "180",
                "--device-count",
                "200",
                "--interval",
                str(interval),
                "--deactivate-after",
            ],
            escenario=f"Prueba3_int{interval}",
        )


def run_prueba_4() -> None:
    for label, padding in (("small", 0), ("medium", 256), ("large", 1024)):
        run_suite(
            [
                "--duration",
                "180",
                "--device-count",
                "200",
                "--interval",
                "5",
                "--payload-padding",
                str(padding),
                "--deactivate-after",
            ],
            escenario=f"Prueba4_{label}",
        )


def run_prueba_5() -> None:
    for label, interval in (("sin_aggregate", 5), ("con_rate_limit", 10)):
        run_suite(
            [
                "--duration",
                "240",
                "--device-count",
                "200",
                "--interval",
                str(interval),
                "--deactivate-after",
            ],
            escenario=f"Prueba5_{label}",
        )


def run_prueba_6() -> None:
    run_suite(
        [
            "--duration",
            "1800",
            "--device-count",
            "1000",
            "--interval",
            "5",
            "--ramp-percentages",
            "25",
            "50",
            "100",
            "--deactivate-after",
        ],
        escenario="Prueba6",
    )


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Uso: py -3 scripts/run_experiments.py <1-6>")
    mapping = {
        "1": run_prueba_1,
        "2": run_prueba_2,
        "3": run_prueba_3,
        "4": run_prueba_4,
        "5": run_prueba_5,
        "6": run_prueba_6,
    }
    choice = sys.argv[1]
    if choice not in mapping:
        raise SystemExit("Prueba no soportada. Usa 1..6")
    mapping[choice]()


if __name__ == "__main__":
    main()
